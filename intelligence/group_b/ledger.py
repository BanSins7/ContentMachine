"""GB-MOD-10: atomic quote/approval/reservation journal; NO model dispatch.

Caller MUST own an authorized, transactional PostgreSQL connection.
Project/run budget rows are locked in stable project->run order on reserve,
so concurrent project runs cannot individually pass the same ceiling.
Mutable current attempt state is accompanied by immutable append-only events.
No charge is inferred from a timeout, from success text or from a model stub.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from .budget import BudgetQuote, BudgetDenied, _usd
from .configuration import GroupBConfig
from .contracts import canonical_hash
from .errors import ExecutionNotApproved

BUDGET_SCHEMA = "group-b-budget/v1"


def _deny(code):
    raise BudgetDenied(code)


def _row(cursor):
    value=cursor.fetchone()
    if value is None:
        return None
    if isinstance(value,Mapping):
        return dict(value)
    return dict(zip((col.name for col in cursor.description),value))


def _valid_uuid(value):
    if type(value) is not str:
        return False
    try:
        return str(UUID(value))==value
    except ValueError:
        return False


def _money(value):
    # PG numeric materializes Decimal; never use floating point for USD.
    return _usd(value)


def _enabled_caps(conn,project_id,run_id):
    """Authorize only explicitly installed and enabled cap rows, lock both."""
    project=_row(conn.execute(
        """SELECT enabled,lifetime_ceiling_usd
             FROM group_b_budget_project_limits
            WHERE project_id=%s FOR UPDATE""",(project_id,)
    ))
    run=_row(conn.execute(
        """SELECT enabled,lifetime_ceiling_usd
             FROM group_b_budget_run_limits
            WHERE project_id=%s AND run_id=%s::uuid FOR UPDATE""",
        (project_id,run_id)
    ))
    if (project is None or run is None or project["enabled"] is not True or
        run["enabled"] is not True):
        _deny("project_or_run_budget_disabled")
    return _money(project["lifetime_ceiling_usd"]),_money(run["lifetime_ceiling_usd"])


def _locked_quote(conn, *, project_id: str, run_id: str, quote_id: str):
    quote=_row(conn.execute(
        """SELECT q.id::text AS id,q.project_id,q.run_id::text AS run_id,
                  q.model_id,q.request_sha256,q.evidence_sha256,
                  q.quote_sha256,q.quoted_usd,q.pricing_snapshot,q.quote_details,
                  q.created_at,q.expires_at
             FROM group_b_model_quotes q
             JOIN group_b_runs r ON r.id=q.run_id AND r.project_id=q.project_id
            WHERE q.project_id=%s AND q.run_id=%s::uuid AND q.id=%s::uuid
            FOR UPDATE OF q""",(project_id,run_id,quote_id)
    ))
    if quote is None:
        _deny("quote_not_found")
    return quote


def _must_be_fresh(conn,quote):
    row=conn.execute("SELECT %s > now()", (quote["expires_at"],)).fetchone()
    if row is None or row[0] is not True:
        _deny("quote_expired")


@dataclass(frozen=True,slots=True)
class StoredQuote:
    id: str
    project_id: str
    run_id: str
    quote_sha256: str
    amount_usd: Decimal


@dataclass(frozen=True,slots=True)
class StoredApproval:
    id: str
    quote_id: str
    project_id: str
    run_id: str
    approved_by: str
    authorized_usd: Decimal


@dataclass(frozen=True,slots=True)
class AttemptReservation:
    id: str
    project_id: str
    run_id: str
    quote_id: str
    approval_id: str
    request_key: str
    request_sha256: str
    state: str
    reserved_usd: Decimal
    provider_request_id: str | None


def save_quote(conn, quote: BudgetQuote) -> StoredQuote:
    """Store an immutable, explicitly pinned quote; no approval or reservation."""
    if not isinstance(quote,BudgetQuote) or not quote.verify():
        _deny("quote_digest_invalid")
    if not _valid_uuid(quote.run_id):
        _deny("run_identity_invalid")
    if quote.created_at.tzinfo is None or quote.expires_at.tzinfo is None:
        _deny("quote_clock_invalid")
    _usd(quote.quoted_usd,positive=True)
    if quote.quote_details.get("packet_sha256")!=quote.context_sha256:
        _deny("quote_context_mismatch")
    row=_row(conn.execute(
        """INSERT INTO group_b_model_quotes(
             project_id,run_id,model_id,request_sha256,evidence_sha256,
             quote_sha256,quoted_usd,pricing_snapshot,quote_details,
             created_at,expires_at)
           SELECT %s,%s::uuid,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s
            WHERE EXISTS (
              SELECT 1 FROM group_b_runs
               WHERE project_id=%s AND id=%s::uuid
                 AND state IN ('draft','quoted','needs_review')
                 AND a_snapshot_sha256 IS NOT NULL
                 AND media_manifest_sha256=%s
            )
           ON CONFLICT(project_id,run_id,quote_sha256)
           DO NOTHING
           RETURNING id::text AS id,project_id,run_id::text AS run_id,
                     quote_sha256,quoted_usd""",
        (quote.project_id,quote.run_id,quote.model_id,
         quote.request_sha256,quote.evidence_sha256,quote.quote_sha256,
         quote.quoted_usd,__import__("json").dumps(quote.pricing_snapshot),
         __import__("json").dumps(quote.quote_details),
         quote.created_at,quote.expires_at,quote.project_id,quote.run_id,
         quote.evidence_sha256)
    ))
    if row is None:
        row=_row(conn.execute(
            """SELECT id::text AS id,project_id,run_id::text AS run_id,
                      quote_sha256,quoted_usd
                 FROM group_b_model_quotes
                WHERE project_id=%s AND run_id=%s::uuid AND quote_sha256=%s""",
            (quote.project_id,quote.run_id,quote.quote_sha256)
        ))
    if row is None:
        _deny("quote_run_not_eligible")
    if _money(row["quoted_usd"])!=quote.quoted_usd:
        _deny("quote_persisted_amount_mismatch")
    return StoredQuote(row["id"],row["project_id"],row["run_id"],
                       row["quote_sha256"],row["quoted_usd"])


def approve_quote(conn, *, project_id: str,run_id: str,quote_id: str,
                  approved_by: str,authorized_usd: Decimal,
                  explicit_confirmation: bool) -> StoredApproval:
    """Trusted application approval path; NEVER pass raw unauthenticated ID.

    Caller must authenticate `approved_by` and capture user consent in its
    own audit trail; boolean is NOT proof of identity. No model calls here.
    """
    if (not _valid_uuid(run_id) or not _valid_uuid(quote_id) or
        type(approved_by) is not str or not 1<=len(approved_by.strip())<=160 or
        explicit_confirmation is not True):
        raise ExecutionNotApproved()
    amount=_usd(authorized_usd,positive=True)
    _enabled_caps(conn,project_id,run_id)
    quote=_locked_quote(conn,project_id=project_id,run_id=run_id,quote_id=quote_id)
    _must_be_fresh(conn,quote)
    if amount!=_money(quote["quoted_usd"]):
        _deny("approval_must_cover_exact_quote")
    row=_row(conn.execute(
        """INSERT INTO group_b_model_approvals(
              project_id,run_id,quote_id,approved_by,authorized_usd)
           VALUES(%s,%s::uuid,%s::uuid,%s,%s)
           ON CONFLICT(project_id,quote_id) DO NOTHING
           RETURNING id::text AS id,quote_id::text AS quote_id,project_id,
                     run_id::text AS run_id,approved_by,authorized_usd""",
        (project_id,run_id,quote_id,approved_by,amount)
    ))
    if row is None:
        row=_row(conn.execute(
            """SELECT id::text AS id,quote_id::text AS quote_id,project_id,
                      run_id::text AS run_id,approved_by,authorized_usd
                 FROM group_b_model_approvals
                WHERE project_id=%s AND quote_id=%s::uuid""",
            (project_id,quote_id)
        ))
        if (row is None or row["approved_by"]!=approved_by or
            _money(row["authorized_usd"])!=amount):
            _deny("approval_conflict")
    return StoredApproval(row["id"],row["quote_id"],row["project_id"],
                          row["run_id"],row["approved_by"],row["authorized_usd"])


def _accounted_usd(conn, project_id,run_id=None):
    cond="" if run_id is None else "AND a.run_id=%s::uuid"
    args=(project_id,) if run_id is None else (project_id,run_id)
    value=conn.execute(
        """SELECT COALESCE(SUM(CASE
             WHEN a.charge_state='settled' THEN a.charged_usd
             WHEN a.charge_state='failed' THEN 0
             ELSE a.reserved_usd END),0)
             FROM group_b_model_attempts a
            WHERE a.project_id=%s """+cond,args
    ).fetchone()
    if value is None:
        _deny("ledger_sum_unavailable")
    return _money(value[0])


def _attempt_from(row) -> AttemptReservation:
    return AttemptReservation(row["id"],row["project_id"],row["run_id"],
        row["quote_id"],row["approval_id"],row["request_key"],
        row["request_sha256"],row["charge_state"],row["reserved_usd"],
        row["provider_request_id"])


def _locked_attempt(conn,project_id,attempt_id):
    row=_row(conn.execute(
        """SELECT id::text AS id,project_id,run_id::text AS run_id,
                  quote_id::text AS quote_id,approval_id::text AS approval_id,
                  request_key,request_sha256,charge_state,reserved_usd,
                  charged_usd,provider_request_id
             FROM group_b_model_attempts
            WHERE project_id=%s AND id=%s::uuid FOR UPDATE""",
        (project_id,attempt_id)
    ))
    if row is None:
        _deny("attempt_not_found")
    return row


def _append(conn,row,event_type,amount,provider_id,sha):
    number=conn.execute(
        """SELECT COALESCE(MAX(event_no),0)+1 FROM group_b_budget_events
            WHERE project_id=%s AND attempt_id=%s::uuid""",
        (row["project_id"],row["id"])
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO group_b_budget_events(
          project_id,run_id,attempt_id,event_no,event_type,amount_usd,
          evidence_sha256,provider_request_id)
           VALUES(%s,%s::uuid,%s::uuid,%s,%s,%s,%s,%s)""",
        (row["project_id"],row["run_id"],row["id"],number,event_type,
         amount,sha,provider_id)
    )


def reserve_attempt(conn, *,project_id: str,run_id: str,
                    quote_id: str,approval_id: str,
                    request_key: str,request_sha256: str,
                    config: GroupBConfig) -> AttemptReservation:
    """Atomic project+run cap and per-quote one-use lock under PG transaction.

    NEVER means 'send request'. The immutable event journal records reserved
    liability; stage11 must build a separately authorized execution worker.
    """
    if (not isinstance(config,GroupBConfig) or not config.enable_paid_execution):
        raise ExecutionNotApproved()
    if (not all(_valid_uuid(x) for x in (run_id,quote_id,approval_id)) or
        type(request_key) is not str or not 12<=len(request_key)<=200 or
        type(request_sha256) is not str or len(request_sha256)!=64 or
        any(x not in "0123456789abcdef" for x in request_sha256)):
        _deny("reservation_identity_invalid")
    project_cap,run_cap=_enabled_caps(conn,project_id,run_id)
    quote=_locked_quote(conn,project_id=project_id,run_id=run_id,quote_id=quote_id)
    _must_be_fresh(conn,quote)
    if quote["request_sha256"]!=request_sha256:
        _deny("request_quote_digest_mismatch")
    amount=_money(quote["quoted_usd"])
    if amount>_money(config.max_paid_attempt_usd):
        _deny("per_attempt_ceiling_exceeded")
    approval=_row(conn.execute(
        """SELECT a.id::text AS id,a.approved_by,a.authorized_usd
             FROM group_b_model_approvals a
            WHERE a.project_id=%s AND a.run_id=%s::uuid
              AND a.quote_id=%s::uuid AND a.id=%s::uuid""",
        (project_id,run_id,quote_id,approval_id)
    ))
    if approval is None or _money(approval["authorized_usd"])<amount:
        _deny("explicit_approval_missing")
    # Never dispatch a second attempt during submitted/uncertain first charge.
    pending=conn.execute(
        """SELECT 1 FROM group_b_model_attempts
            WHERE project_id=%s AND run_id=%s::uuid
              AND charge_state IN ('submitted','uncertain') LIMIT 1""",
        (project_id,run_id)
    ).fetchone()
    if pending is not None:
        _deny("unreconciled_charge_blocks_retry")
    old=_row(conn.execute(
        """SELECT id::text AS id,project_id,run_id::text AS run_id,
                  quote_id::text AS quote_id,approval_id::text AS approval_id,
                  request_key,request_sha256,charge_state,reserved_usd,
                  provider_request_id
             FROM group_b_model_attempts
            WHERE project_id=%s AND run_id=%s::uuid AND request_key=%s""",
        (project_id,run_id,request_key)
    ))
    if old is not None:
        if (old["quote_id"]!=quote_id or old["approval_id"]!=approval_id or
            old["request_sha256"]!=request_sha256):
            _deny("idempotency_key_collision")
        if old["charge_state"]!="reserved":
            _deny("idempotent_attempt_not_retryable")
        return _attempt_from(old)
    old_quote=conn.execute(
        """SELECT 1 FROM group_b_model_attempts
            WHERE project_id=%s AND quote_id=%s::uuid LIMIT 1""",
        (project_id,quote_id)
    ).fetchone()
    if old_quote is not None:
        _deny("quote_already_consumed")
    if (_accounted_usd(conn,project_id)+amount>project_cap or
        _accounted_usd(conn,project_id,run_id)+amount>run_cap):
        _deny("project_or_run_cap_exceeded")
    row=_row(conn.execute(
        """INSERT INTO group_b_model_attempts(
          project_id,run_id,quote_id,approval_id,request_key,request_sha256,
          charge_state,reserved_usd)
          VALUES(%s,%s::uuid,%s::uuid,%s::uuid,%s,%s,'reserved',%s)
          RETURNING id::text AS id,project_id,run_id::text AS run_id,
                    quote_id::text AS quote_id,approval_id::text AS approval_id,
                    request_key,request_sha256,charge_state,reserved_usd,
                    provider_request_id""",
        (project_id,run_id,quote_id,approval_id,request_key,request_sha256,amount)
    ))
    if row is None:
        _deny("reservation_write_failed")
    _append(conn,row,"reserved",amount,None,request_sha256)
    return _attempt_from(row)


def mark_submission(conn, *,project_id: str,attempt_id: str,
                    provider_request_id: str) -> None:
    """Record a worker's durable submission ID, never call provider directly."""
    if (not _valid_uuid(attempt_id) or type(provider_request_id) is not str or
        not 8<=len(provider_request_id)<=200):
        _deny("provider_request_id_invalid")
    row=_locked_attempt(conn,project_id,attempt_id)
    if row["charge_state"]!="reserved" or row["provider_request_id"] is not None:
        _deny("submission_not_reservable")
    conn.execute(
        """UPDATE group_b_model_attempts
              SET charge_state='submitted',provider_request_id=%s
            WHERE project_id=%s AND id=%s::uuid""",
        (provider_request_id,project_id,attempt_id)
    )
    _append(conn,row,"submitted",row["reserved_usd"],
            provider_request_id,row["request_sha256"])


def mark_ambiguous(conn, *,project_id: str,attempt_id: str) -> None:
    """Timeout/unknown charge keeps FULL reservation and forbids new retries."""
    if not _valid_uuid(attempt_id):
        _deny("attempt_identity_invalid")
    row=_locked_attempt(conn,project_id,attempt_id)
    if row["charge_state"] not in ("reserved","submitted"):
        _deny("ambiguous_transition_invalid")
    conn.execute(
        """UPDATE group_b_model_attempts
              SET charge_state='uncertain',finalized_at=now(),
                  error_code='charge_unverified'
            WHERE project_id=%s AND id=%s::uuid""",
        (project_id,attempt_id)
    )
    _append(conn,row,"uncertain",row["reserved_usd"],
            row["provider_request_id"],row["request_sha256"])


def reconcile_attempt(conn, *,project_id: str,attempt_id: str,
                      authenticated_receipt: object,
                      receipt_verifier: object) -> str:
    """Settle ONLY with a separately authenticated provider receipt.

    receipt_verifier.verify(receipt, expected_request_id, expected_sha256)
    MUST be trusted integration implementation; absent verifier fail-closed.
    Expected receipt shape: {state:'settled'|'no_charge',
    request_id,request_sha256,charged_usd:Decimal,prompt_tokens:int,
    completion_tokens:int}. This is not exposed as a raw HTTP endpoint.
    """
    if not _valid_uuid(attempt_id):
        _deny("attempt_identity_invalid")
    row=_locked_attempt(conn,project_id,attempt_id)
    if row["charge_state"] not in ("uncertain","submitted"):
        _deny("reconciliation_state_invalid")
    verify=getattr(receipt_verifier,"verify",None)
    if not callable(verify):
        _deny("trusted_receipt_verifier_missing")
    try:
        authorized=verify(authenticated_receipt,
                          row["provider_request_id"],row["request_sha256"])
    except Exception:
        _deny("provider_receipt_unverifiable")
    if authorized is not True or type(authenticated_receipt) is not dict:
        _deny("provider_receipt_unverifiable")
    receipt=authenticated_receipt
    if (set(receipt)!={"state","request_id","request_sha256","charged_usd",
                       "prompt_tokens","completion_tokens"} or
        receipt["request_id"]!=row["provider_request_id"] or
        receipt["request_sha256"]!=row["request_sha256"] or
        receipt["state"] not in ("settled","no_charge") or
        type(receipt["prompt_tokens"]) is not int or receipt["prompt_tokens"]<0 or
        type(receipt["completion_tokens"]) is not int or receipt["completion_tokens"]<0):
        _deny("provider_receipt_incompatible")
    actual=_usd(receipt["charged_usd"])
    if actual>row["reserved_usd"]:
        _deny("provider_charge_over_reservation")
    if receipt["state"]=="no_charge":
        if actual!=0 or receipt["prompt_tokens"] or receipt["completion_tokens"]:
            _deny("no_charge_proof_invalid")
        status="failed"
    else:
        status="settled"
    usage={"schema_version":BUDGET_SCHEMA,
           "prompt_tokens":receipt["prompt_tokens"],
           "completion_tokens":receipt["completion_tokens"],
           "provider_request_id":receipt["request_id"]}
    conn.execute(
        """UPDATE group_b_model_attempts
              SET charge_state=%s,charged_usd=%s,usage=%s::jsonb,
                  finalized_at=now(),error_code=NULL
            WHERE project_id=%s AND id=%s::uuid""",
        (status,actual,__import__("json").dumps(usage),project_id,attempt_id)
    )
    _append(conn,row,status,actual,row["provider_request_id"],
            canonical_hash(usage))
    return status
