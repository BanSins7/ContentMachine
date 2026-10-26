"""RTB-FIX-02-C2: explicit one-attempt paid Deep V2 FIRST pass.

Only callable by a deliberate server POST (HTTP routing is a separate stage).
Before network request, reserve money and the unique deep_first attempt in one
transaction. No retries. Unknown usage keeps the reservation held forever
until a separately audited reconciliation flow exists. Historical V1 and B9
single-pass execution modules remain untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from psycopg.types.json import Jsonb

from intelligence.knowledge_extraction.human_review import _auth, _reviewer
from intelligence.project_sources.context_builder import sha as canonical_sha
from .deep_contracts_v2 import validate_deep_v2_response
from .deep_v2_provider import single_v2_request
from .deep_v2_quote_repository import (
    RTBDeepV2QuoteRepository, _fail,
)
from .deep_v2_transport import v2_request
from .pipeline_contracts import PipelineError
from .provider import ProviderUncertain, resolve_key, reservation_cost
from .research_contracts_v2 import _project, _uuid


class RTBDeepV2Pass1Repository(RTBDeepV2QuoteRepository):

    @staticmethod
    def _locked_run(conn, project_id, run_id):
        row = conn.execute(
            """SELECT id,project_id,binding_id,quote_id,consent_id,status,
                      expansion_count,authorized_usd,reserved_usd,spent_usd
               FROM tb_deep_v2_runs WHERE project_id=%s AND id=%s FOR UPDATE""",
            (project_id, run_id),
        ).fetchone()
        if row is None:
            _fail("rtb_v2_run_not_found", 404)
        return {
            "run_id": str(row[0]), "binding_id": str(row[2]),
            "quote_id": str(row[3]), "consent_id": str(row[4]),
            "status": row[5], "expansion_count": row[6],
            "authorized": row[7], "reserved": row[8], "spent": row[9],
        }

    @staticmethod
    def _revoked(conn, pid, consent_id):
        return bool(conn.execute(
            """SELECT 1 FROM tb_deep_v2_revocations
               WHERE project_id=%s AND consent_id=%s""",
            (pid, consent_id),
        ).fetchone())

    def inspect_pass1(self, project_id, run_id, reviewer_id) -> dict:
        pid = _project(project_id)
        rid = _uuid(run_id, "rtb_v2_run_invalid")
        actor = _reviewer(reviewer_id)
        with self.connection_factory() as conn:
            _auth(conn, pid, actor)
            row = conn.execute(
                """SELECT status,authorized_usd,reserved_usd,spent_usd
                   FROM tb_deep_v2_runs WHERE project_id=%s AND id=%s""",
                (pid, rid),
            ).fetchone()
            if not row:
                _fail("rtb_v2_run_not_found", 404)
            attempt = conn.execute(
                """SELECT stage,status,request_sha256,reservation_usd,
                          charged_usd,usage,response_id,output,error_code
                   FROM tb_deep_v2_attempts
                  WHERE project_id=%s AND run_id=%s AND stage='deep_first'""",
                (pid, rid),
            ).fetchone()
        return {
            "run_id": rid, "status": row[0],
            "authorized_usd": str(row[1]), "reserved_usd": str(row[2]),
            "spent_usd": str(row[3]), "attempt": None if not attempt else {
                "stage": attempt[0], "status": attempt[1],
                "request_sha256": attempt[2], "reservation_usd": str(attempt[3]),
                "charged_usd": None if attempt[4] is None else str(attempt[4]),
                "usage": attempt[5], "response_id": attempt[6],
                "output": attempt[7], "error_code": attempt[8],
            },
            "provider_calls_during_inspection": 0,
        }

    def advance_pass1(self, project_id, run_id, reviewer_id, *,
                      sender=None, api_key=None) -> dict:
        """One explicit charged pass, no network before committed attempt.

        sender=... is allowed for deterministic test fakes. The HTTP wrapper
        must bind reviewer_id to authenticated server session, not client JSON.
        """
        pid = _project(project_id)
        rid = _uuid(run_id, "rtb_v2_run_invalid")
        actor = _reviewer(reviewer_id)
        if sender is None and api_key is None:
            # Resolve credentials BEFORE money reservation, exactly once.
            api_key = resolve_key()
        if sender is None and not api_key:
            _fail("rtb_v2_provider_key_missing", 503)

        with self.connection_factory() as conn:
            with conn.transaction():
                _auth(conn, pid, actor)
                # Fixed lock order: quote -> run -> consent -> attempt.
                # start() also locks quote before run, avoiding a deadlock
                # between a duplicate start() and this explicit paid advance.
                reference = conn.execute(
                    """SELECT quote_id FROM tb_deep_v2_runs
                       WHERE project_id=%s AND id=%s""", (pid, rid),
                ).fetchone()
                if not reference:
                    _fail("rtb_v2_run_not_found", 404)
                quote = self._read_quote(conn, pid, str(reference[0]), lock=True)
                run = self._locked_run(conn, pid, rid)
                if run["quote_id"] != str(reference[0]):
                    _fail("rtb_v2_run_quote_identity_drift")
                if run["status"] != "pass1_pending":
                    _fail("rtb_v2_pass1_not_pending")
                if self._revoked(conn, pid, run["consent_id"]):
                    _fail("rtb_v2_consent_revoked")
                if datetime.now(timezone.utc) >= quote["expires_at"]:
                    _fail("rtb_v2_quote_expired")
                if quote["binding_id"] != run["binding_id"]:
                    _fail("rtb_v2_quote_binding_mismatch")
                consent = conn.execute(
                    """SELECT authorized_usd FROM tb_deep_v2_consents
                       WHERE project_id=%s AND id=%s AND quote_id=%s FOR UPDATE""",
                    (pid, run["consent_id"], run["quote_id"]),
                ).fetchone()
                if consent is None or consent[0] != run["authorized"]:
                    _fail("rtb_v2_consent_budget_mismatch")
                prepared = self._verified(conn, pid, quote, actor)
                _, frozen = self._binding(pid, run["binding_id"], actor)
                stage, payload = v2_request(
                    frozen, prepared["model"], role="deep_first",
                    max_output_tokens=quote["stage"]["max_output_tokens"])
                request_sha = canonical_sha(payload)
                prompt_sha = canonical_sha(payload["messages"])
                reserve = reservation_cost(payload, prepared["model"])
                if (request_sha != quote["request_sha"]
                        or prompt_sha != quote["prompt_sha"]
                        or stage != quote["stage"]
                        or reserve != prepared["pass1"]
                        or reserve != Decimal(quote["body"]["required_pass1_reservation_usd"])):
                    _fail("rtb_v2_paid_first_pass_quote_drift")
                if conn.execute(
                    """SELECT 1 FROM tb_deep_v2_attempts
                       WHERE project_id=%s AND run_id=%s AND stage='deep_first'""",
                    (pid, rid),
                ).fetchone():
                    _fail("rtb_v2_paid_attempt_already_exists")
                if reserve > run["authorized"] - run["spent"] - run["reserved"]:
                    conn.execute(
                        """UPDATE tb_deep_v2_runs SET status='paused_budget',
                                  updated_at=clock_timestamp()
                           WHERE project_id=%s AND id=%s""",
                        (pid, rid),
                    )
                    return {"status": "paused_budget", "provider_calls": 0}
                # This transaction is committed BEFORE the network call.
                conn.execute(
                    """UPDATE tb_deep_v2_runs
                       SET status='pass1_running',reserved_usd=reserved_usd+%s,
                           updated_at=clock_timestamp()
                       WHERE project_id=%s AND id=%s""",
                    (reserve, pid, rid),
                )
                attempt = conn.execute(
                    """INSERT INTO tb_deep_v2_attempts(
                         project_id,run_id,stage,status,request_sha256,
                         prompt_sha256,reservation_usd)
                       VALUES(%s,%s,'deep_first','reserved',%s,%s,%s)
                       RETURNING id""",
                    (pid, rid, request_sha, prompt_sha, reserve),
                ).fetchone()
                attempt_id = str(attempt[0])

        # A process crash between committed reservation and network transport
        # leaves a RESERVED attempt; it MUST NOT be resent automatically.
        result = None
        known_cost = False
        error_code = None
        output = None
        try:
            result = single_v2_request(
                payload, sender=sender, api_key=api_key, timeout=300)
            known_cost = True
        except ProviderUncertain:
            error_code = "rtb_v2_unknown_spend"
        except Exception:
            error_code = "rtb_v2_provider_outcome_unknown"

        if known_cost:
            try:
                if result["model_id"] != quote["model_id"]:
                    _fail("rtb_v2_provider_model_changed")
                if result.get("finish_reason") != "stop":
                    _fail("rtb_v2_provider_incomplete")
                output = validate_deep_v2_response(
                    result["raw_json"], frozen, "deep_first")
            except (PipelineError, TypeError, KeyError, ValueError):
                error_code = "rtb_v2_charged_response_invalid"

        # This settlement transaction is the *only* allowed final transition
        # of an attempt. No code path retries the remote provider call.
        with self.connection_factory() as conn:
            with conn.transaction():
                _auth(conn, pid, actor)
                run = self._locked_run(conn, pid, rid)
                if run["status"] != "pass1_running":
                    _fail("rtb_v2_pass1_settlement_state_drift")
                saved = conn.execute(
                    """SELECT status,reservation_usd,request_sha256
                       FROM tb_deep_v2_attempts
                       WHERE project_id=%s AND id=%s AND run_id=%s FOR UPDATE""",
                    (pid, attempt_id, rid),
                ).fetchone()
                if (not saved or saved[0] != "reserved"
                        or saved[1] != reserve or saved[2] != request_sha):
                    _fail("rtb_v2_pass1_attempt_state_drift")
                if not known_cost:
                    conn.execute(
                        """UPDATE tb_deep_v2_attempts
                           SET status='uncertain',error_code=%s,
                               finished_at=clock_timestamp()
                           WHERE project_id=%s AND id=%s""",
                        (error_code, pid, attempt_id),
                    )
                    conn.execute(
                        """UPDATE tb_deep_v2_runs SET status='uncertain',
                                  updated_at=clock_timestamp()
                           WHERE project_id=%s AND id=%s""",
                        (pid, rid),
                    )
                    return {
                        "run_id": rid, "status": "uncertain",
                        "reserved_usd": str(run["reserved"]),
                        "provider_calls": 1, "automatic_retry_allowed": False,
                    }

                charged = result["charged_usd"]
                if charged < 0:
                    _fail("rtb_v2_provider_charge_negative")
                over_budget = run["spent"] + charged > run["authorized"]
                revoked_midflight = self._revoked(conn, pid, run["consent_id"])
                status = (
                    "paused_budget" if over_budget else
                    "cancelled" if revoked_midflight else
                    "failed" if error_code else
                    "review1"
                )
                conn.execute(
                    """UPDATE tb_deep_v2_attempts SET
                         status=%s,charged_usd=%s,usage=%s,response_id=%s,
                         output=%s,error_code=%s,finished_at=clock_timestamp()
                       WHERE project_id=%s AND id=%s""",
                    (
                        "error" if error_code else "completed",
                        charged, Jsonb(result["usage"]),
                        result.get("response_id"),
                        Jsonb(output) if output is not None else None,
                        error_code, pid, attempt_id,
                    ),
                )
                conn.execute(
                    """UPDATE tb_deep_v2_runs SET
                         status=%s,reserved_usd=reserved_usd-%s,
                         spent_usd=spent_usd+%s,updated_at=clock_timestamp()
                       WHERE project_id=%s AND id=%s""",
                    (status, reserve, charged, pid, rid),
                )
                return {
                    "run_id": rid, "status": status,
                    "charged_usd": str(charged),
                    "provider_calls": 1, "automatic_retry_allowed": False,
                }
