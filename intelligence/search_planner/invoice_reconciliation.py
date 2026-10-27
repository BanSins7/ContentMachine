"""SP-15D evidence-backed OpenRouter invoice reconciliation contract.

Only explicit, server-authorized, externally verified *per request* invoices
may settle a previously uncertain attempt. No direct OpenRouter API client,
provider credential, public HTTP mutation endpoint, invoice upload parser, or
paid request lives here. By default, no verification port is installed:
uncertain charges stay reserved.

This path can record only 'reconciled_failed': billing evidence cannot recreate
missing LLM outputs nor publish fabricated Query Validation candidates.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from typing import Protocol

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, canonical
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid
from .contracts import publication_uuid
from .generation_workflow import OwnerActionGuard, _guard
from .generation_engine.cost_estimator import money
from .generation_engine.repository import _load_attempt_locked

SCHEMA = "search-planner-openrouter-billing-attestation/v1"
ACTION = "search_planner.generation.reconcile_invoice"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_INVOICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{7,239}\Z")


class VerifiedInvoicePort(Protocol):
    """Injected from trusted application assembly after authenticated billing read.

    Caller MUST independently check that the invoice line exists in the same
    OpenRouter account, matches the *stored* provider_response_id, exactly
    resolves the request, and represents a FINAL billed charge. An absence
    in a sampled/time-bounded export is NOT evidence for USD 0. Returning a
    dictionary is not by itself cryptographic provider verification.
    """
    def verify_attempt_invoice(self, project_id: str, generation_id: str,
                               attempt_id: str, request_sha256: str,
                               provider_response_id: str) -> dict: ...


def _digest(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def _validated_money(value):
    if type(value) is not str or not re.fullmatch(r"(?:0|[1-9][0-9]{0,4})(?:\.[0-9]{1,8})?", value):
        raise PipelineError("Invalid verified invoice amount", 409)
    amount = money(value, "invoice_charge")
    if amount != amount.quantize(Decimal("0.00000001")):
        raise PipelineError("Invoice precision exceeds ledger", 409)
    return amount


def validate_verified_invoice(raw, *, context, observed_at=None):
    """Reject unauthenticated, stale, cross-project or incomplete evidence.

    Structural checks cannot replace the trusted provider-verification port.
    The returned subset is strictly metadata, not a raw invoice document.
    """
    if type(raw) is not dict or set(raw) != {
        "schema_version", "project_id", "generation_id", "attempt_id",
        "request_sha256", "provider_response_id", "invoice_line_id",
        "invoice_sha256", "provider_account_sha256", "verification_method",
        "charge_usd", "provider_verified_at",
    }:
        raise PipelineError("Incomplete verified billing attestation", 409)
    ctx = context
    if (raw["schema_version"] != SCHEMA or
            raw["verification_method"] != "account_billing_export_exact_request" or
            any(raw.get(k) != ctx[k] for k in (
                "project_id", "generation_id", "attempt_id",
                "request_sha256", "provider_response_id",
            ))):
        raise PipelineError("Billing identity/provenance mismatch", 409)
    if (not _digest(raw["invoice_sha256"]) or
            not _digest(raw["provider_account_sha256"]) or
            type(raw["invoice_line_id"]) is not str or
            not _INVOICE.fullmatch(raw["invoice_line_id"])):
        raise PipelineError("Invalid provider billing evidence identity", 409)
    # Exactly one posted per-request invoice line, not a projection from
    # tokens, costs, a browser screenshot or a partial provider usage export.
    amount = _validated_money(raw["charge_usd"])
    stamp = raw["provider_verified_at"]
    if type(stamp) is not str:
        raise PipelineError("Missing provider billing verification time", 409)
    try:
        stamp_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PipelineError("Invalid provider invoice timestamp", 409) from exc
    if stamp_at.tzinfo is None or stamp_at.utcoffset() is None:
        raise PipelineError("Unqualified provider invoice timestamp", 409)
    clock = observed_at or datetime.now(timezone.utc)
    if (stamp_at > clock + timedelta(minutes=5) or
            stamp_at < ctx["dispatch_intent_at"]):
        raise PipelineError("Provider verification predates dispatch or is future dated", 409)
    if amount > ctx["reservation_usd"]:
        # The provider may overbill: do not erase that fact by clamping to the
        # reservation or quietly increasing a consent beyond owner approval.
        raise PipelineError("Invoice exceeds reserved funds; manual financial escalation", 409)
    return {
        "project_id": ctx["project_id"],
        "generation_id": ctx["generation_id"],
        "attempt_id": ctx["attempt_id"],
        "request_sha256": ctx["request_sha256"],
        "provider_response_id": ctx["provider_response_id"],
        "invoice_line_id": raw["invoice_line_id"],
        "invoice_sha256": raw["invoice_sha256"],
        "provider_account_sha256": raw["provider_account_sha256"],
        "verification_method": raw["verification_method"],
        "charge_usd": amount,
        "provider_verified_at": stamp_at.astimezone(timezone.utc),
    }


def read_uncertain_attempt(project_id, generation_id, attempt_id, *, connect=None):
    """One project-scoped, read-only attempt identity before any provider lookup."""
    pid, gid, aid = _pid(project_id), publication_uuid(generation_id), publication_uuid(attempt_id)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = conn.execute(
                """SELECT a.status,a.request_sha256,a.provider_response_id,
                          a.dispatch_intent_at,a.reservation_usd
                   FROM sqg_attempts a
                   JOIN sqg_generations g ON g.id=a.generation_id
                   WHERE g.project_id=%s AND g.id=%s::uuid AND a.id=%s::uuid""",
                (pid, gid, aid),
            ).fetchone()
    if row is None:
        raise PipelineError("Reconciliation attempt not found in project", 404)
    if row[0] not in ("uncertain", "reconciled_failed"):
        raise PipelineError("Only ambiguous or exactly reconciled attempt can be reviewed", 409)
    if (not _digest(row[1]) or type(row[2]) is not str or
            not 1 <= len(row[2]) <= 240 or
            not isinstance(row[3], datetime) or row[3].tzinfo is None or
            type(row[4]) is not Decimal or row[4] <= 0):
        # Most crashed HTTP calls lack a persisted provider response id.
        # A mere request SHA isn't verifiable against OpenRouter invoices.
        raise PipelineError("External provider response ID/evidence unavailable", 409)
    return {
        "project_id": pid, "generation_id": gid, "attempt_id": aid,
        "request_sha256": row[1], "provider_response_id": row[2],
        "dispatch_intent_at": row[3], "reservation_usd": row[4],
    }


def apply_verified_invoice(project_id, generation_id, attempt_id, proof, *,
                           connect=None):
    """Atomic settlement of one uncertain attempt after trusted verification.

    Locks generation -> consent -> batch -> attempt using existing repository
    lock order. Rechecks every immutable identity under the same transaction;
    records an immutable invoice line + SQG event, then moves reserve->spent.
    No completed response/candidate/outbox is fabricated.
    """
    pid, gid, aid = _pid(project_id), publication_uuid(generation_id), publication_uuid(attempt_id)
    if (type(proof) is not dict or
            proof.get("project_id") != pid or
            proof.get("generation_id") != gid or proof.get("attempt_id") != aid):
        raise PipelineError("Untrusted verified invoice scope", 409)
    required = {
        "project_id", "generation_id", "attempt_id", "request_sha256",
        "provider_response_id", "invoice_line_id", "invoice_sha256",
        "provider_account_sha256", "verification_method", "charge_usd",
        "provider_verified_at",
    }
    if set(proof) != required or type(proof["charge_usd"]) is not Decimal or not isinstance(
        proof["provider_verified_at"], datetime
    ):
        raise PipelineError("Incomplete internal billing decision", 409)
    if (not _digest(proof["request_sha256"]) or
            not _digest(proof["invoice_sha256"]) or
            not _digest(proof["provider_account_sha256"]) or
            type(proof["provider_response_id"]) is not str or
            not 1 <= len(proof["provider_response_id"]) <= 240 or
            type(proof["invoice_line_id"]) is not str or
            _INVOICE.fullmatch(proof["invoice_line_id"]) is None or
            proof["verification_method"] != "account_billing_export_exact_request" or
            proof["charge_usd"] < 0 or
            proof["charge_usd"] != proof["charge_usd"].quantize(Decimal("0.00000001")) or
            proof["provider_verified_at"].tzinfo is None or
            proof["provider_verified_at"].utcoffset() is None):
        raise PipelineError("Invalid internal billing provenance", 409)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            state = _load_attempt_locked(conn, pid, aid)
            if state["gid"] != gid:
                raise PipelineError("Attempt generation mismatch", 409)
            if state["state"] == "reconciled_failed":
                existing = conn.execute(
                    """SELECT invoice_line_id,invoice_sha256,charge_usd,
                              provider_response_id,request_sha256,
                              provider_account_sha256,verification_method,
                              provider_verified_at
                       FROM sp_invoice_reconciliations
                       WHERE project_id=%s AND generation_id=%s::uuid
                         AND attempt_id=%s::uuid""",
                    (pid, gid, aid),
                ).fetchone()
                if existing is None or (
                    existing[0], existing[1], existing[2], existing[3], existing[4],
                    existing[5], existing[6], existing[7],
                ) != (
                    proof["invoice_line_id"], proof["invoice_sha256"],
                    proof["charge_usd"], proof["provider_response_id"],
                    proof["request_sha256"], proof["provider_account_sha256"],
                    proof["verification_method"], proof["provider_verified_at"],
                ):
                    raise PipelineError("Immutable invoice already settled differently", 409)
                return {
                    "schema_version": "search-planner-invoice-settlement/v1",
                    "attempt_id": aid, "status": "reconciled_failed",
                    "charged_usd": str(state["charged"]), "reused": True,
                    "paid_requests": 0,
                }
            if state["state"] != "uncertain":
                raise PipelineError("Only uncertain attempt may receive invoice settlement", 409)
            source = conn.execute(
                """SELECT request_sha256,provider_response_id,dispatch_intent_at
                   FROM sqg_attempts WHERE id=%s::uuid FOR UPDATE""",
                (aid,),
            ).fetchone()
            if (source is None or source[0] != proof["request_sha256"] or
                    source[1] != proof["provider_response_id"] or
                    source[2] is None or source[2] > proof["provider_verified_at"] or
                    proof["charge_usd"] < 0 or
                    proof["charge_usd"] > state["reservation"]):
                raise PipelineError("Billing evidence no longer matches frozen attempt", 409)
            wallet = conn.execute(
                """SELECT authorized_usd,spent_usd,reserved_usd
                   FROM sqg_consents WHERE id=%s::uuid AND project_id=%s
                   FOR UPDATE""",
                (state["consent_id"], pid),
            ).fetchone()
            if (wallet is None or any(type(x) is not Decimal for x in wallet) or
                    wallet[2] < state["reservation"] or
                    wallet[1] + proof["charge_usd"] > wallet[0]):
                raise PipelineError("Invoice exceeds owner consent/ledger reserve", 409)
            conn.execute(
                """INSERT INTO sp_invoice_reconciliations
                   (project_id,generation_id,attempt_id,provider_response_id,
                    request_sha256,invoice_line_id,invoice_sha256,
                    provider_account_sha256,charge_usd,verification_method,
                    provider_verified_at,decision,owner_action)
                   VALUES (%s,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,
                           'reconciled_failed',%s)""",
                (pid, gid, aid, proof["provider_response_id"], proof["request_sha256"],
                 proof["invoice_line_id"], proof["invoice_sha256"],
                 proof["provider_account_sha256"], str(proof["charge_usd"]),
                 proof["verification_method"], proof["provider_verified_at"], ACTION),
            )
            conn.execute(
                """UPDATE sqg_attempts
                   SET status='reconciled_failed',charged_usd=%s,
                       ended_at=now(),error_code='invoice_confirmed_missing_output'
                   WHERE id=%s::uuid AND status='uncertain'""",
                (str(proof["charge_usd"]), aid),
            )
            conn.execute(
                """UPDATE sqg_consents
                   SET reserved_usd=reserved_usd-%s,
                       spent_usd=spent_usd+%s
                   WHERE id=%s::uuid AND project_id=%s""",
                (str(state["reservation"]), str(proof["charge_usd"]),
                 state["consent_id"], pid),
            )
            conn.execute(
                """UPDATE sqg_batches SET status='failed_known',updated_at=now()
                   WHERE id=%s::uuid AND status='uncertain'""",
                (state["bid"],),
            )
            conn.execute(
                """UPDATE sqg_generations
                   SET status=CASE WHEN EXISTS (
                       SELECT 1 FROM sqg_attempts
                        WHERE generation_id=%s::uuid
                          AND status IN ('uncertain','dispatch_intent')
                       ) THEN 'uncertain'
                       WHEN cancel_requested THEN 'cancelled'
                       ELSE 'failed' END,
                       updated_at=now()
                   WHERE id=%s::uuid AND project_id=%s""",
                (gid, gid, pid),
            )
            conn.execute(
                """INSERT INTO sqg_events
                   (project_id,generation_id,batch_id,attempt_id,event_kind,payload)
                   VALUES (%s,%s::uuid,%s::uuid,%s::uuid,'invoice_reconciled_failed',
                           %s::jsonb)""",
                (pid, gid, state["bid"], aid, canonical({
                    "invoice_sha256": proof["invoice_sha256"],
                    "invoice_line_id": proof["invoice_line_id"],
                    "charged_usd": str(proof["charge_usd"]),
                    "paid_requests": 0,
                })),
            )
    return {
        "schema_version": "search-planner-invoice-settlement/v1",
        "attempt_id": aid, "status": "reconciled_failed",
        "charged_usd": str(proof["charge_usd"]), "reused": False,
        "paid_requests": 0,
    }


def list_uncertain_invoices(project_id, generation_id, *, limit=25, connect=None):
    """Read-only operator worklist; never assumes an invoice exists."""
    pid, gid = _pid(project_id), publication_uuid(generation_id)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise PipelineError("Invalid invoice worklist limit", 422)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            rows = conn.execute(
                """SELECT a.id,a.reservation_usd,a.provider_response_id IS NOT NULL
                   FROM sqg_attempts a JOIN sqg_generations g
                     ON g.id=a.generation_id
                   WHERE g.project_id=%s AND g.id=%s::uuid
                     AND a.status='uncertain'
                   ORDER BY a.dispatch_intent_at,a.id LIMIT %s""",
                (pid, gid, limit),
            ).fetchall()
    return {
        "schema_version": "search-planner-invoice-worklist/v1",
        "project_id": pid, "generation_id": gid,
        "attempts": [{
            "attempt_id": str(row[0]), "reserved_usd": str(row[1]),
            "has_provider_response_id": row[2],
            "invoice_verified": False,
        } for row in rows],
        "paid_requests": 0,
    }


class InvoiceReconciliationController:
    """Server-assembled, opt-in owner action + independently verified port.

    Default verifier=None means *no settlement is possible*, even with a
    manually typed invoice number or browser-created hash.
    """
    def __init__(self, *, owner_action_guard: OwnerActionGuard | None = None,
                 verified_invoice_port: VerifiedInvoicePort | None = None,
                 attempt_reader=read_uncertain_attempt,
                 settlement_writer=apply_verified_invoice):
        if any(not callable(p) for p in (attempt_reader, settlement_writer)):
            raise PipelineError("Invalid invoice backend ports", 422)
        if verified_invoice_port is not None and not callable(getattr(
            verified_invoice_port, "verify_attempt_invoice", None
        )):
            raise PipelineError("Untrusted provider invoice verification port", 422)
        self._owner = owner_action_guard
        self._provider = verified_invoice_port
        self._read = attempt_reader
        self._write = settlement_writer

    def reconcile(self, project_id, generation_id, attempt_id):
        pid, gid, aid = _pid(project_id), publication_uuid(generation_id), publication_uuid(attempt_id)
        _guard(self._owner, ACTION, pid, gid)
        if self._provider is None:
            raise PipelineError("Trusted OpenRouter invoice verifier not configured", 503)
        ctx = self._read(pid, gid, aid)
        if (type(ctx) is not dict or ctx.get("project_id") != pid or
                ctx.get("generation_id") != gid or ctx.get("attempt_id") != aid or
                not _digest(ctx.get("request_sha256")) or
                type(ctx.get("provider_response_id")) is not str or
                not ctx["provider_response_id"]):
            raise PipelineError("Untrusted reconciliation context", 409)
        # This trusted server-owned port must read *actual* external invoice
        # evidence. A fake verifier is admissible only for offline contract tests.
        raw = self._provider.verify_attempt_invoice(
            pid, gid, aid, ctx["request_sha256"], ctx["provider_response_id"],
        )
        proof = validate_verified_invoice(raw, context=ctx)
        return self._write(pid, gid, aid, proof)
