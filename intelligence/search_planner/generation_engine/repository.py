"""Transactional Search Planner spending ledger (Stage B, no HTTP dispatch).

Every balance mutation locks generation -> consent -> batch -> attempt;
reserve and settlement use PostgreSQL row locks and NUMERIC/Decimal.
Uncertain attempts always retain their reservation. This module does not
make external calls or synthesize OpenRouter results.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, canonical
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid
from intelligence.search_planner.contracts import publication_uuid

from .cost_estimator import money, usd
from .model_manager import current_priced_model
from .contracts import validate_generation_config


def _now():
    return datetime.now(timezone.utc)


def _load_run(conn, pid, gid):
    row = conn.execute(
        """SELECT g.id,g.status,g.cancel_requested,g.quote_id,g.consent_id,
                  g.config_snapshot,g.input_sha256
           FROM sqg_generations g WHERE g.id=%s::uuid AND g.project_id=%s FOR UPDATE""",
        (gid, pid),
    ).fetchone()
    if row is None:
        raise PipelineError("Search generation not found", 404)
    return {"status": row[1], "cancel_requested": row[2],
            "quote_id": str(row[3]) if row[3] else None,
            "consent_id": str(row[4]) if row[4] else None,
            "config": validate_generation_config(row[5]),
            "input_sha256": row[6]}


def reserve_batch(project_id, generation_id, batch_id, request_sha256, *,
                  connect=None, now=None) -> dict:
    """Reserve exact quoted high estimate. No provider call, no implicit retry.

    request_sha256 is the immutable digest supplied by a trusted future
    execution worker, not client-controlled raw input.
    """
    pid = _pid(project_id)
    gid = publication_uuid(generation_id)
    bid = publication_uuid(batch_id)
    if not isinstance(request_sha256, str) or len(request_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in request_sha256
    ):
        raise PipelineError("Invalid pinned request SHA", 422)
    now = _now() if now is None else now
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            run = _load_run(conn, pid, gid)
            if run["status"] not in ("queued", "running") or run["cancel_requested"]:
                raise PipelineError("Generation is not ready for a paid reservation", 409)
            if not run["quote_id"] or not run["consent_id"]:
                raise PipelineError("Search spending consent required", 409)
            consent = conn.execute(
                """SELECT authorized_usd,spent_usd,reserved_usd,expires_at,revoked_at,quote_id
                   FROM sqg_consents WHERE id=%s::uuid AND project_id=%s FOR UPDATE""",
                (run["consent_id"], pid),
            ).fetchone()
            if (consent is None or consent[4] is not None or consent[3] <= now
                    or str(consent[5]) != run["quote_id"]):
                raise PipelineError("Search consent inactive, expired or mismatched", 409)
            batch = conn.execute(
                """SELECT status,input_sha256 FROM sqg_batches
                   WHERE id=%s::uuid AND generation_id=%s::uuid FOR UPDATE""",
                (bid, gid),
            ).fetchone()
            if batch is None:
                raise PipelineError("Batch not found in generation", 404)
            if batch[0] != "pending":
                # No silent re-use/re-dispatch of a previously claimed batch.
                raise PipelineError("Batch has already been reserved or processed", 409)
            # Current read-only provider catalog freshness and price equality.
            model = current_priced_model(conn, run["config"])
            quote = conn.execute(
                """SELECT pricing_snapshot,model_catalog_sha256 FROM sqg_quotes
                   WHERE id=%s::uuid AND project_id=%s""",
                (run["quote_id"], pid),
            ).fetchone()
            if quote is None:
                raise PipelineError("Original quote unavailable", 409)
            prices = quote[0]
            if (quote[1] != model["catalog_sha256"]
                    or prices.get("generation_id") != gid
                    or prices.get("generation_input_sha256") != run["input_sha256"]
                    or prices.get("rates_usd_per_token") != model["rates"]):
                raise PipelineError("Quote stale; no reservation permitted", 409)
            reservations = [entry for entry in prices.get("batch_reservations", [])
                            if entry.get("batch_id") == bid]
            if len(reservations) != 1 or reservations[0].get("batch_input_sha256") != batch[1]:
                raise PipelineError("Batch quote manifest mismatch", 409)
            reserved = money(reservations[0]["reserve_high_usd"], "reservation", positive=True)
            available = consent[0] - consent[1] - consent[2]
            if reserved > available:
                conn.execute(
                    """UPDATE sqg_generations SET status='paused_budget',updated_at=now()
                       WHERE id=%s::uuid AND project_id=%s""", (gid, pid),
                )
                return {"generation_id": gid, "status": "paused_budget",
                        "attempt_id": None, "reserved_usd": "0"}
            attempt = conn.execute(
                """INSERT INTO sqg_attempts
                     (generation_id,batch_id,request_sha256,prompt_sha256,
                      raw_schema_sha256,model_catalog_sha256,status,reservation_usd)
                   VALUES (%s::uuid,%s::uuid,%s,%s,%s,%s,'reserved',%s)
                   RETURNING id""",
                (gid, bid, request_sha256,
                 run["config"]["model"]["prompt_sha256"],
                 run["config"]["model"]["raw_schema_sha256"],
                 model["catalog_sha256"], str(reserved)),
            ).fetchone()
            conn.execute(
                """UPDATE sqg_consents SET reserved_usd=reserved_usd+%s
                   WHERE id=%s::uuid AND project_id=%s""",
                (str(reserved), run["consent_id"], pid),
            )
            conn.execute(
                """UPDATE sqg_batches SET status='reserved',updated_at=now()
                   WHERE id=%s::uuid AND generation_id=%s::uuid""", (bid, gid),
            )
            conn.execute(
                """UPDATE sqg_generations SET status='running',updated_at=now()
                   WHERE id=%s::uuid AND project_id=%s""", (gid, pid),
            )
            conn.execute(
                """INSERT INTO sqg_events(project_id,generation_id,batch_id,attempt_id,
                        event_kind,payload)
                   VALUES (%s,%s::uuid,%s::uuid,%s::uuid,'attempt_reserved',%s::jsonb)""",
                (pid, gid, bid, str(attempt[0]),
                 canonical({"reserved_usd": usd(reserved), "provider_calls": 0})),
            )
            return {"generation_id": gid, "batch_id": bid,
                    "attempt_id": str(attempt[0]), "status": "reserved",
                    "reserved_usd": usd(reserved)}


def _load_attempt_locked(conn, pid, aid):
    row = conn.execute(
        """SELECT a.id,a.generation_id,a.batch_id,a.status,a.reservation_usd,
                  a.charged_usd,g.consent_id
           FROM sqg_attempts a
           JOIN sqg_generations g ON g.id=a.generation_id
           WHERE g.project_id=%s AND a.id=%s::uuid""",
        (pid, aid),
    ).fetchone()
    if row is None:
        raise PipelineError("Search attempt not found in project", 404)
    gid = str(row[1])
    run = _load_run(conn, pid, gid)
    if run["consent_id"] is None:
        raise PipelineError("Attempt missing spending consent", 409)
    # Lock order: generation then consent then batch/attempt.
    conn.execute(
        """SELECT id FROM sqg_consents WHERE id=%s::uuid AND project_id=%s FOR UPDATE""",
        (run["consent_id"], pid),
    ).fetchone()
    conn.execute(
        """SELECT id FROM sqg_batches WHERE id=%s::uuid AND generation_id=%s::uuid
           FOR UPDATE""", (str(row[2]), gid),
    ).fetchone()
    checked = conn.execute(
        """SELECT status,reservation_usd,charged_usd FROM sqg_attempts
           WHERE id=%s::uuid FOR UPDATE""", (aid,),
    ).fetchone()
    return {"id": aid, "gid": gid, "bid": str(row[2]),
            "consent_id": run["consent_id"], "state": checked[0],
            "reservation": checked[1], "charged": checked[2]}


def release_unsubmitted_attempt(project_id, attempt_id, *, connect=None) -> dict:
    """Only a reserved (NEVER dispatch_intent) attempt can release funds."""
    pid = _pid(project_id)
    aid = publication_uuid(attempt_id)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            attempt = _load_attempt_locked(conn, pid, aid)
            if attempt["state"] == "abandoned_before_dispatch":
                return {"attempt_id": aid, "status": attempt["state"], "reused": True}
            if attempt["state"] != "reserved":
                raise PipelineError("Cannot refund a possibly-dispatched attempt", 409)
            conn.execute(
                """UPDATE sqg_attempts SET status='abandoned_before_dispatch',ended_at=now()
                   WHERE id=%s::uuid""", (aid,),
            )
            conn.execute(
                """UPDATE sqg_consents SET reserved_usd=reserved_usd-%s
                   WHERE id=%s::uuid AND project_id=%s""",
                (str(attempt["reservation"]), attempt["consent_id"], pid),
            )
            conn.execute(
                """UPDATE sqg_batches SET status='cancelled',updated_at=now()
                   WHERE id=%s::uuid""", (attempt["bid"],),
            )
            conn.execute(
                """UPDATE sqg_generations SET status='failed',updated_at=now()
                   WHERE id=%s::uuid AND project_id=%s""", (attempt["gid"], pid),
            )
            return {"attempt_id": aid, "status": "abandoned_before_dispatch",
                    "released_usd": str(attempt["reservation"]), "reused": False}


def record_dispatch_intent(project_id, attempt_id, *, connect=None) -> dict:
    """Pure ledger transition; does NOT send an HTTP request.

    Stage C will call this BEFORE an external POST. After this point a crash
    is ambiguous, and the attempt MUST NOT be automatically resent.
    """
    pid = _pid(project_id)
    aid = publication_uuid(attempt_id)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            attempt = _load_attempt_locked(conn, pid, aid)
            if attempt["state"] != "reserved":
                raise PipelineError("Attempt already dispatching or finalized", 409)
            eligibility = conn.execute(
                """SELECT g.cancel_requested,g.status,c.revoked_at,c.expires_at
                   FROM sqg_generations g
                   JOIN sqg_consents c ON c.id=g.consent_id AND c.project_id=g.project_id
                   WHERE g.id=%s::uuid AND g.project_id=%s""",
                (attempt["gid"], pid),
            ).fetchone()
            if (eligibility is None or eligibility[0] or eligibility[1] != "running"
                    or eligibility[2] is not None or eligibility[3] <= _now()):
                raise PipelineError("Consent revoked/expired or generation stopped before dispatch", 409)
            conn.execute(
                """UPDATE sqg_attempts
                   SET status='dispatch_intent',dispatch_intent_at=now()
                   WHERE id=%s::uuid""", (aid,),
            )
            conn.execute(
                """UPDATE sqg_batches SET status='dispatching',updated_at=now()
                   WHERE id=%s::uuid""", (attempt["bid"],),
            )
            return {"attempt_id": aid, "status": "dispatch_intent",
                    "http_request_sent": False}


def settle_known_failure(project_id, attempt_id, charged_usd, *,
                         error_code="fake_provider_failure", connect=None) -> dict:
    """Record verified price on a *known failed* request, without creating output.

    Successful LLM settlement + validation belongs to Stage C OutputManager,
    not this stage; it cannot call this function to fabricate completed output.
    """
    pid = _pid(project_id)
    aid = publication_uuid(attempt_id)
    charge = money(charged_usd, "charged_usd")
    if charge != charge.quantize(Decimal("0.00000001")):
        raise PipelineError("Provider charge exceeds DB precision", 422)
    if not isinstance(error_code, str) or not 1 <= len(error_code) <= 100:
        raise PipelineError("Invalid failure code", 422)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            attempt = _load_attempt_locked(conn, pid, aid)
            if attempt["state"] == "failed_known":
                if charge != attempt["charged"]:
                    raise PipelineError("Settled charge cannot be changed", 409)
                return {"attempt_id": aid, "status": "failed_known", "reused": True}
            if attempt["state"] != "dispatch_intent":
                raise PipelineError("Only dispatched attempt may incur known charge", 409)
            conn.execute(
                """UPDATE sqg_attempts
                   SET status='failed_known',charged_usd=%s,ended_at=now(),error_code=%s
                   WHERE id=%s::uuid""",
                (str(charge), error_code, aid),
            )
            conn.execute(
                """UPDATE sqg_consents
                   SET reserved_usd=reserved_usd-%s,spent_usd=spent_usd+%s
                   WHERE id=%s::uuid AND project_id=%s""",
                (str(attempt["reservation"]), str(charge), attempt["consent_id"], pid),
            )
            conn.execute(
                """UPDATE sqg_batches SET status='failed_known',updated_at=now()
                   WHERE id=%s::uuid""", (attempt["bid"],),
            )
            conn.execute(
                """UPDATE sqg_generations SET status='failed',updated_at=now()
                   WHERE id=%s::uuid AND project_id=%s""", (attempt["gid"], pid),
            )
            conn.execute(
                """INSERT INTO sqg_events(project_id,generation_id,batch_id,attempt_id,
                        event_kind,payload)
                   VALUES (%s,%s::uuid,%s::uuid,%s::uuid,'attempt_charged_failure',
                           %s::jsonb)""",
                (pid, attempt["gid"], attempt["bid"], aid,
                 canonical({"charged_usd": usd(charge),
                            "over_reserved": charge > attempt["reservation"]})),
            )
            return {"attempt_id": aid, "status": "failed_known",
                    "charged_usd": usd(charge), "reused": False}


def mark_uncertain_attempt(project_id, attempt_id, *, connect=None) -> dict:
    """Conservatively retain reservation after ambiguous dispatch."""
    pid = _pid(project_id)
    aid = publication_uuid(attempt_id)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            attempt = _load_attempt_locked(conn, pid, aid)
            if attempt["state"] == "uncertain":
                return {"attempt_id": aid, "status": "uncertain", "reused": True}
            if attempt["state"] != "dispatch_intent":
                raise PipelineError("Only possibly-dispatched attempts become uncertain", 409)
            conn.execute(
                "UPDATE sqg_attempts SET status='uncertain' WHERE id=%s::uuid", (aid,),
            )
            conn.execute(
                """UPDATE sqg_batches SET status='uncertain',updated_at=now()
                   WHERE id=%s::uuid""", (attempt["bid"],),
            )
            conn.execute(
                """UPDATE sqg_generations SET status='uncertain',updated_at=now()
                   WHERE id=%s::uuid AND project_id=%s""", (attempt["gid"], pid),
            )
            return {"attempt_id": aid, "status": "uncertain",
                    "reserved_usd": usd(attempt["reservation"]), "reused": False}
