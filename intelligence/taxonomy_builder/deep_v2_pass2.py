"""RTB-FIX-05-B: explicit paid Deep V2 SECOND pass after immutable Human Review1.

A deliberate caller executes one bounded, quoted provider POST per run.
Every H1 node (all fourteen keys) is immutable in the accepted response;
rejected Pass1 IDs cannot be restored. Cost unknown = uncertain, hold
reservation, no retries. Neither GET nor the human-review submission calls
this method.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from psycopg.types.json import Jsonb

from intelligence.knowledge_extraction.human_review import _auth, _reviewer
from intelligence.project_sources.context_builder import sha as canonical_sha
from .deep_contracts import quality_gate
from .deep_v2_followup import validate_protected_followup
from .deep_v2_provider import single_v2_request
from .deep_v2_review_repository import RTBDeepV2HumanReviewRepository
from .deep_v2_quote_repository import _fail
from .deep_v2_transport import v2_request
from .pipeline_contracts import PipelineError
from .provider import ProviderUncertain, resolve_key, reservation_cost
from .research_contracts_v2 import _project, _uuid, project_v2_tree_to_v1_core


def _load_review_and_first(conn, pid, rid, frozen):
    review = conn.execute(
        """SELECT approved_nodes,approved_sha256 FROM tb_deep_v2_reviews
           WHERE project_id=%s AND run_id=%s AND review_no=1""",
        (pid, rid),
    ).fetchone()
    first = conn.execute(
        """SELECT output FROM tb_deep_v2_attempts
           WHERE project_id=%s AND run_id=%s
             AND stage='deep_first' AND status='completed'""",
        (pid, rid),
    ).fetchone()
    if (not review or not isinstance(review[0], list)
            or canonical_sha(review[0]) != review[1]
            or not first or not isinstance(first[0], dict)
            or not isinstance(first[0].get("nodes"), list)):
        _fail("rtb_v2_pass2_missing_immutable_h1_or_pass1")
    # Prior first pass is authoritative; a forged reviewer payload is refused.
    from .deep_contracts_v2 import validate_deep_v2_tree
    validate_deep_v2_tree(review[0], frozen)
    validate_deep_v2_tree(first[0]["nodes"], frozen)
    return review[0], first[0]["nodes"]


class RTBDeepV2Pass2Repository(RTBDeepV2HumanReviewRepository):

    def advance_pass2(self, project_id, run_id, reviewer_id, *,
                      sender=None, api_key=None):
        """Explicit one-off Pass2; no re-dispatch after any attempt exists."""
        pid = _project(project_id)
        rid = _uuid(run_id, "rtb_v2_run_id")
        actor = _reviewer(reviewer_id)
        if sender is None and api_key is None:
            api_key = resolve_key()
        if sender is None and not api_key:
            _fail("rtb_v2_provider_key_missing", 503)

        with self.connection_factory() as conn:
            with conn.transaction():
                _auth(conn, pid, actor)
                reference = conn.execute(
                    """SELECT quote_id FROM tb_deep_v2_runs
                       WHERE project_id=%s AND id=%s""", (pid, rid)
                ).fetchone()
                if not reference:
                    _fail("rtb_v2_run_not_found", 404)
                # Consistent acquisition across start / Pass1 / Pass2:
                # quote -> run -> consent -> paid attempt.
                quote = self._read_quote(conn, pid, str(reference[0]), lock=True)
                run = self._locked_run(conn, pid, rid)
                if run["quote_id"] != str(reference[0]):
                    _fail("rtb_v2_run_quote_identity_drift")
                if run["status"] != "pass2_pending":
                    _fail("rtb_v2_pass2_not_pending")
                if self._revoked(conn, pid, run["consent_id"]):
                    _fail("rtb_v2_consent_revoked")
                if datetime.now(timezone.utc) >= quote["expires_at"]:
                    _fail("rtb_v2_pass2_quote_expired")
                if run["binding_id"] != quote["binding_id"]:
                    _fail("rtb_v2_pass2_binding_drift")
                consent = conn.execute(
                    """SELECT authorized_usd FROM tb_deep_v2_consents
                       WHERE project_id=%s AND id=%s AND quote_id=%s
                       FOR UPDATE""",
                    (pid, run["consent_id"], run["quote_id"]),
                ).fetchone()
                if not consent or consent[0] != run["authorized"]:
                    _fail("rtb_v2_pass2_consent_mismatch")
                prepared = self._verified(conn, pid, quote, actor)
                _, frozen = self._binding(pid, run["binding_id"], actor)
                h1, first = _load_review_and_first(conn, pid, rid, frozen)
                coverage = conn.execute(
                    """SELECT quality_report FROM tb_deep_v2_runs
                       WHERE project_id=%s AND id=%s""",
                    (pid, rid),
                ).fetchone()[0]
                if not isinstance(coverage, dict):
                    _fail("rtb_v2_pass2_missing_h1_coverage")
                _, payload = v2_request(
                    frozen, prepared["model"], role="deep_second",
                    max_output_tokens=quote["stage"]["max_output_tokens"],
                    review=h1, coverage=coverage,
                )
                reserve = reservation_cost(payload, prepared["model"])
                cap = Decimal(quote["body"]["required_pass2_reservation_usd"])
                if (reserve > cap
                        or reserve > run["authorized"] - run["spent"] - run["reserved"]):
                    # No reservation and definitely no network if the exact
                    # second request exceeds its explicitly consented cap.
                    _fail("rtb_v2_pass2_exceeds_authorized_reservation")
                if conn.execute(
                    """SELECT 1 FROM tb_deep_v2_attempts
                       WHERE project_id=%s AND run_id=%s
                         AND stage='deep_second'""",
                    (pid, rid),
                ).fetchone():
                    _fail("rtb_v2_pass2_already_attempted")
                request_sha = canonical_sha(payload)
                prompt_sha = canonical_sha(payload["messages"])
                conn.execute(
                    """UPDATE tb_deep_v2_runs
                       SET status='pass2_running',reserved_usd=reserved_usd+%s,
                           updated_at=clock_timestamp()
                       WHERE project_id=%s AND id=%s""",
                    (reserve, pid, rid),
                )
                aid = str(conn.execute(
                    """INSERT INTO tb_deep_v2_attempts(
                         project_id,run_id,stage,status,request_sha256,
                         prompt_sha256,reservation_usd)
                       VALUES(%s,%s,'deep_second','reserved',%s,%s,%s)
                       RETURNING id""",
                    (pid, rid, request_sha, prompt_sha, reserve),
                ).fetchone()[0])

        # Transaction already committed, and the single paid attempt is
        # durable. A crash cannot lead to an implicit second provider call.
        result, known, error, tree, quality = None, False, None, None, None
        try:
            result = single_v2_request(
                payload, sender=sender, api_key=api_key, timeout=300)
            known = True
        except ProviderUncertain:
            error = "rtb_v2_pass2_spend_uncertain"
        except Exception:
            error = "rtb_v2_pass2_provider_unknown"

        if known:
            try:
                if result["model_id"] != quote["model_id"]:
                    _fail("rtb_v2_pass2_model_drift")
                if result.get("finish_reason") != "stop":
                    _fail("rtb_v2_pass2_truncated")
                tree = validate_protected_followup(
                    result["raw_json"], frozen, h1, first, role="deep_second")
                facts = [
                    {"id": f["rtb_fact_id"], "kind": f["kind"],
                     "text": f["reviewed_text"]}
                    for f in frozen["c1_approved_facts"]
                ]
                quality = quality_gate(
                    project_v2_tree_to_v1_core(tree["nodes"]), facts,
                    previous=project_v2_tree_to_v1_core(h1),
                    model_duplicates=tree["duplicates"],
                )
                tree["quality_report"] = quality
            except (PipelineError, TypeError, KeyError, ValueError):
                error = "rtb_v2_pass2_charged_tree_rejected"

        with self.connection_factory() as conn:
            with conn.transaction():
                run = self._locked_run(conn, pid, rid)
                if run["status"] != "pass2_running":
                    _fail("rtb_v2_pass2_settlement_state_drift")
                saved = conn.execute(
                    """SELECT status,reservation_usd,request_sha256
                       FROM tb_deep_v2_attempts
                       WHERE project_id=%s AND run_id=%s AND id=%s
                       FOR UPDATE""",
                    (pid, rid, aid),
                ).fetchone()
                if (not saved or saved[0] != "reserved"
                        or saved[1] != reserve or saved[2] != request_sha):
                    _fail("rtb_v2_pass2_attempt_integrity")
                if not known:
                    conn.execute(
                        """UPDATE tb_deep_v2_attempts
                           SET status='uncertain',error_code=%s,
                               finished_at=clock_timestamp()
                           WHERE project_id=%s AND id=%s""",
                        (error, pid, aid),
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
                over_budget = run["spent"] + charged > run["authorized"]
                revoked = self._revoked(conn, pid, run["consent_id"])
                next_state = (
                    "paused_budget" if over_budget else
                    "cancelled" if revoked else
                    "failed" if error else
                    "review2" if quality["passed"] else
                    "expand_pending"
                )
                conn.execute(
                    """UPDATE tb_deep_v2_attempts SET
                          status=%s,charged_usd=%s,usage=%s,response_id=%s,
                          output=%s,error_code=%s,finished_at=clock_timestamp()
                       WHERE project_id=%s AND id=%s""",
                    ("error" if error else "completed", charged,
                     Jsonb(result["usage"]), result.get("response_id"),
                     Jsonb(tree) if tree is not None else None,
                     error, pid, aid),
                )
                conn.execute(
                    """UPDATE tb_deep_v2_runs SET status=%s,
                          reserved_usd=reserved_usd-%s,spent_usd=spent_usd+%s,
                          quality_report=%s,updated_at=clock_timestamp()
                       WHERE project_id=%s AND id=%s""",
                    (next_state, reserve, charged,
                     Jsonb(quality) if quality is not None else None, pid, rid),
                )
                return {
                    "run_id": rid, "status": next_state,
                    "charged_usd": str(charged),
                    "quality": quality, "provider_calls": 1,
                    "automatic_retry_allowed": False,
                }
