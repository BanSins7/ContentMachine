"""Explicit, synchronous, fake-only generation worker with DB checkpoints.

No service thread, HTTP client, OpenRouter imports, scheduled calls or retry.
Workers may run concurrently: Stage B transactional reservation rejects a
second claim for the same batch. Every invocation is a caller-initiated step.
"""
from __future__ import annotations

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, canonical, sha256
from intelligence.taxonomy_builder.pipeline_repository import _pid, _connect
from intelligence.search_planner.contracts import publication_uuid

from .provider_port import (
    BatchRequest, FakeProviderPort, PossiblyBilled, KnownProviderFailure,
)
from .repository import (
    reserve_batch, record_dispatch_intent, settle_known_failure,
    mark_uncertain_attempt, release_unsubmitted_attempt,
)
from .output_manager import settle_successful_batch


def planned_next_request(project_id, generation_id, *, connect=None):
    """Read full persisted contexts and return a request plan. No side effects."""
    pid, gid = _pid(project_id), publication_uuid(generation_id)
    factory = connect or _connect
    with factory() as conn:
        gen = conn.execute(
            """SELECT status,cancel_requested,config_snapshot
               FROM sqg_generations WHERE id=%s::uuid AND project_id=%s""",
            (gid, pid),
        ).fetchone()
        if gen is None:
            raise PipelineError("Search generation not found", 404)
        if gen[1] or gen[0] not in ("queued", "running"):
            return None
        active = conn.execute(
            """SELECT 1 FROM sqg_batches WHERE generation_id=%s::uuid AND
               status IN ('reserved','dispatching','uncertain') LIMIT 1""",
            (gid,),
        ).fetchone()
        if active:
            return None
        batch = conn.execute(
            """SELECT id,hypothesis_ids,input_sha256,direction_id,output_token_cap
               FROM sqg_batches WHERE generation_id=%s::uuid AND status='pending'
               ORDER BY batch_index LIMIT 1""",
            (gid,),
        ).fetchone()
        if batch is None:
            return None
        bid, ids, batch_hash, direction, token_cap = batch
        by_id = {}
        for id_, context, context_hash in conn.execute(
            """SELECT hypothesis_id,context,context_sha256
               FROM sqg_contexts WHERE generation_id=%s::uuid""", (gid,),
        ).fetchall():
            if sha256(context) != context_hash:
                raise PipelineError("Stored HypothesisContext checksum mismatch", 409)
            by_id[id_] = context
        if type(ids) is not list or len(ids) != len(set(ids)) or not set(ids).issubset(by_id):
            raise PipelineError("Stored batch manifest invalid", 409)
        contexts = [by_id[i] for i in ids]
        config = gen[2]
        expected_hash = sha256({
            "direction_id": direction,
            "contexts": [sha256(c) for c in contexts],
            "config_model_sha256": sha256(config["model"]),
        })
        if expected_hash != batch_hash:
            raise PipelineError("Batch fingerprint changed", 409)
        payload = {
            "generation_id": gid, "batch_id": str(bid),
            "contexts": contexts, "model_id": config["model"]["model_id"],
            "prompt_sha256": config["model"]["prompt_sha256"],
            "raw_schema_sha256": config["model"]["raw_schema_sha256"],
            "max_output_tokens": token_cap,
        }
        return BatchRequest(
            generation_id=gid, batch_id=str(bid),
            model_id=config["model"]["model_id"],
            contexts=tuple(contexts),
            request_sha256=sha256(payload),
            max_output_tokens=token_cap,
        )


def process_one_batch(project_id, generation_id, *, transport: FakeProviderPort,
                      connect=None):
    """Execute exactly one stage-C fake call; never send actual network traffic.

    Every ambiguous outcome keeps financial reserve. No automatic retry.
    """
    if type(transport) is not FakeProviderPort:
        raise PipelineError("Stage C accepts FakeProviderPort only; no live transport", 422)
    plan = planned_next_request(project_id, generation_id, connect=connect)
    if plan is None:
        return {"generation_id": generation_id, "status": "no_eligible_batch"}
    try:
        attempt = reserve_batch(project_id, generation_id, plan.batch_id,
                                plan.request_sha256, connect=connect)
    except PipelineError as exc:
        if exc.http_status == 409:
            return {"generation_id": generation_id, "status": "already_claimed_or_paused"}
        raise
    if attempt.get("attempt_id") is None:
        return attempt
    aid = attempt["attempt_id"]
    try:
        record_dispatch_intent(project_id, aid, connect=connect)
    except PipelineError:
        # No dispatch intent committed: safe release (which fails this run).
        release_unsubmitted_attempt(project_id, aid, connect=connect)
        return {"generation_id": generation_id, "status": "failed_before_dispatch"}
    try:
        reply = transport.invoke_once(plan)
    except KnownProviderFailure as exc:
        return settle_known_failure(project_id, aid, exc.cost,
                                    error_code=exc.reason, connect=connect)
    except PossiblyBilled:
        return mark_uncertain_attempt(project_id, aid, connect=connect)
    except Exception:
        # Unexpected transport error AFTER dispatch_intent is potentially billed.
        return mark_uncertain_attempt(project_id, aid, connect=connect)
    try:
        return settle_successful_batch(project_id, aid, reply, connect=connect)
    except PipelineError as exc:
        # If we have a verified price, a malformed reply is still chargeable.
        # If usage or price isn't verifiable, never pretend the cost was zero.
        from .output_manager import _valid_price
        try:
            charge = _valid_price(reply.charged_usd)
            if not isinstance(reply.usage, dict) or (
                    _valid_price(reply.usage.get("cost")) != charge):
                raise PipelineError("Provider billing evidence missing", 422)
        except (PipelineError, AttributeError, TypeError):
            return mark_uncertain_attempt(project_id, aid, connect=connect)
        return settle_known_failure(project_id, aid, str(charge),
                                    error_code="invalid_llm_result", connect=connect)


def drain_generation(project_id, generation_id, *, transport: FakeProviderPort,
                     max_batches=100, connect=None):
    """Bounded explicit fake worker loop; no background processing."""
    if type(max_batches) is not int or not 1 <= max_batches <= 100:
        raise PipelineError("max_batches must be 1..100", 422)
    results = []
    for _ in range(max_batches):
        status = process_one_batch(project_id, generation_id, transport=transport,
                                   connect=connect)
        results.append(status)
        if status.get("generation_status") in ("completed", "cancelled"):
            break
        if status.get("status") != "completed":
            break
    return results
