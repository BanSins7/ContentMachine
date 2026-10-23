"""Explicitly opted-in Stage D one-batch paid worker.

No background loops or HTTP endpoints. This trusted Python service requires
a persisted human-approved Search Planner consent, a live-version quote,
verified OpenRouter model/prices, a fixed response schema and a byte-envelope
preflight. Only then it reserves funds, commits dispatch_intent, and invokes
ONE completion. Network errors are never automatically retried.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.pipeline_repository import _pid, _connect
from intelligence.search_planner.contracts import publication_uuid
from intelligence.search_planner.generation_engine.worker import planned_next_request
from intelligence.search_planner.generation_engine.model_manager import current_priced_model
from intelligence.search_planner.generation_engine.cost_estimator import (
    money, usd, CENT, SAFETY_MARGIN,
)
from intelligence.search_planner.generation_engine.repository import (
    reserve_batch, record_dispatch_intent, mark_uncertain_attempt,
    settle_known_failure, release_unsubmitted_attempt,
)
from intelligence.search_planner.generation_engine.output_manager import (
    settle_successful_batch, _valid_price,
)
from intelligence.search_planner.generation_engine.provider_port import PossiblyBilled
from .prompts import (
    build_completion_payload, enforce_pins, payload_sha256, conservative_input_upper,
)
from .quality import audit_raw_trace
from .transport import OpenRouterQueryTransport


def preflight_live_batch(project_id, generation_id, *, connect=None):
    """Read-only end-to-end budget and capability verification.

    A displayed estimate is never permission to spend. This function is
    public read-only and safe for UI previews; it never reserves anything.
    """
    pid, gid = _pid(project_id), publication_uuid(generation_id)
    plan = planned_next_request(pid, gid, connect=connect)
    if plan is None:
        return None
    factory = connect or _connect
    with factory() as conn:
        record = conn.execute(
            """SELECT g.config_snapshot,g.quote_id,g.consent_id,g.cancel_requested,
                      c.revoked_at,c.expires_at,c.authorized_usd,c.spent_usd,c.reserved_usd
               FROM sqg_generations g
               LEFT JOIN sqg_consents c ON c.id=g.consent_id AND c.project_id=g.project_id
               WHERE g.id=%s::uuid AND g.project_id=%s""",
            (gid, pid),
        ).fetchone()
        if record is None or record[1] is None or record[2] is None or record[3]:
            raise PipelineError("Separate active Search Planner spending consent required", 409)
        from datetime import datetime, timezone
        if record[4] is not None or record[5] is None or record[5] <= datetime.now(timezone.utc):
            raise PipelineError("Search spending consent expired or revoked", 409)
        model_config = record[0]["model"]
        enforce_pins(model_config)
        live_model = current_priced_model(conn, record[0])
        payload = build_completion_payload(list(plan.contexts), model_config)
        exact_sha = payload_sha256(payload)
        quote = conn.execute(
            """SELECT pricing_snapshot,model_catalog_sha256
               FROM sqg_quotes WHERE id=%s::uuid AND project_id=%s""",
            (str(record[1]), pid),
        ).fetchone()
        if quote is None:
            raise PipelineError("Live spending quote missing", 409)
        data = quote[0]
        if data.get("estimator_version") != "search-query-live-request-envelope-v1":
            raise PipelineError("Legacy byte-only quote cannot authorize live generation", 409)
        if (quote[1] != live_model["catalog_sha256"] or
                data.get("rates_usd_per_token") != live_model["rates"] or
                data.get("generation_id") != gid):
            raise PipelineError("Pinned live model/rates mismatch", 409)
        reservations = [
            b for b in data.get("batch_reservations", []) if b.get("batch_id") == plan.batch_id
        ]
        if len(reservations) != 1 or reservations[0].get("exact_request_sha256") != exact_sha:
            raise PipelineError("Live request differs from user-approved quote", 409)
        upper_input = conservative_input_upper(payload)
        if upper_input + plan.max_output_tokens > live_model["context_length"]:
            raise PipelineError("Live prompt does not fit verified context window", 409)
        rates = {key: money(value, key) for key, value in live_model["rates"].items()}
        maximum = (
            Decimal(upper_input) * rates["prompt"] +
            Decimal(plan.max_output_tokens) * (rates["completion"] + rates["internal_reasoning"]) +
            rates["request"]
        ) * SAFETY_MARGIN
        required = max(CENT, maximum.quantize(CENT, rounding=ROUND_CEILING))
        reserve_cap = money(reservations[0]["reserve_high_usd"], "approved_batch_reserve",
                            positive=True)
        if required > reserve_cap:
            raise PipelineError("Live actual request exceeds quoted upper reservation", 409)
        available = record[6] - record[7] - record[8]
        if required > available:
            raise PipelineError("Approved Search Planner budget insufficient", 409)
        return {"plan": plan, "payload": payload,
                "exact_request_sha256": exact_sha,
                "quoted_reserve_usd": str(reserve_cap),
                "upper_input_token_proxy": upper_input,
                "calculated_reserve_usd": usd(required),
                "provider_call_sent": False}


def execute_one_paid_batch(project_id, generation_id, *,
                           transport: OpenRouterQueryTransport,
                           explicit_paid_execution: bool = False,
                           connect=None):
    """Single externally billed invocation only with explicit opt-in.

    Caller MUST perform owner/session authorization before setting the flag.
    There is no exposed HTTP endpoint/automatic scheduler in this PR.
    """
    if explicit_paid_execution is not True or type(transport) is not OpenRouterQueryTransport:
        raise PipelineError("Explicit owner-authorized paid transport required", 403)
    preview = preflight_live_batch(project_id, generation_id, connect=connect)
    if preview is None:
        return {"generation_id": generation_id, "status": "no_eligible_batch"}
    plan = preview["plan"]
    try:
        reserved = reserve_batch(
            project_id, generation_id, plan.batch_id,
            preview["exact_request_sha256"], connect=connect,
        )
    except PipelineError as exc:
        if exc.http_status == 409:
            return {"generation_id": generation_id, "status": "already_claimed_or_paused"}
        raise
    if not reserved.get("attempt_id"):
        return reserved
    attempt_id = reserved["attempt_id"]
    try:
        record_dispatch_intent(project_id, attempt_id, connect=connect)
    except PipelineError:
        release_unsubmitted_attempt(project_id, attempt_id, connect=connect)
        return {"generation_id": generation_id, "status": "failed_before_dispatch"}
    try:
        reply = transport.invoke_once(preview["payload"])
    except PossiblyBilled:
        return mark_uncertain_attempt(project_id, attempt_id, connect=connect)
    except Exception:
        # Everything after committed dispatch_intent may be billable.
        return mark_uncertain_attempt(project_id, attempt_id, connect=connect)
    try:
        from intelligence.search_planner.generation_engine.output_manager import _validate_provider_reply
        # Cost/finish/model fields AND exact method trace are checked before
        # any output is published, inside the same single paid attempt.
        audit_raw_trace(reply.raw_json, list(plan.contexts),
                        plan.contexts[0]["search_policy"])
        return settle_successful_batch(project_id, attempt_id, reply, connect=connect)
    except PipelineError:
        # Invalid response is STILL chargeable if provider gave consistent,
        # verified cost. Never silently refund and do not retry.
        try:
            charge = _valid_price(reply.charged_usd)
            if type(reply.usage) is not dict or _valid_price(reply.usage.get("cost")) != charge:
                raise PipelineError("Inconsistent provider cost", 422)
        except (PipelineError, TypeError, ValueError):
            return mark_uncertain_attempt(project_id, attempt_id, connect=connect)
        return settle_known_failure(
            project_id, attempt_id, str(charge),
            error_code="invalid_ai_query_response", connect=connect,
        )
