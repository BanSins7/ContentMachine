"""RTB-FIX-06: immutable Human Review2 and Search Readiness approval.

Review2 is NOT publication. One authorized transaction verifies the actual
last paid tree, applies explicit human decisions, revalidates original B9/A8
source and the complete locked H1, saves a signed readiness report, saves an
immutable review2 event and transitions review2 -> approved (not published).
No provider invocation is possible from these methods.
"""
from __future__ import annotations

from copy import deepcopy

from psycopg.types.json import Jsonb

from intelligence.knowledge_extraction.human_review import _auth, _reviewer
from intelligence.project_sources.context_builder import sha as canonical_sha
from .deep_v2_expansion import RTBDeepV2ExpansionRepository
from .deep_v2_followup import validate_protected_followup
from .deep_v2_human_review import apply_human_review1
from .deep_v2_pass2 import _load_review_and_first
from .deep_v2_quote_repository import _fail
from .deep_v2_readiness import search_readiness_gate
from .research_contracts_v2 import _project, _uuid


def _candidate(conn, pid, rid, expansion_count, frozen, h1, first):
    stage = "deep_second" if expansion_count == 0 else f"deep_expand_{expansion_count}"
    if stage not in ("deep_second", "deep_expand_1", "deep_expand_2"):
        _fail("rtb_v2_review2_expansion_count_invalid")
    row = conn.execute(
        """SELECT output FROM tb_deep_v2_attempts
           WHERE project_id=%s AND run_id=%s AND stage=%s
             AND status='completed'""",
        (pid, rid, stage),
    ).fetchone()
    if not row or not isinstance(row[0], dict):
        _fail("rtb_v2_review2_missing_paid_candidate")
    source = row[0]
    expected = {"nodes", "duplicates", "quality_report"}
    if set(source) != expected:
        _fail("rtb_v2_review2_candidate_shape_invalid")
    validated = validate_protected_followup(
        {"nodes": source["nodes"], "duplicates": source["duplicates"]},
        frozen, h1, first, role="deep_second",
    )
    if not isinstance(source["quality_report"], dict):
        _fail("rtb_v2_review2_missing_model_quality_report")
    return stage, validated, source["quality_report"]


class RTBDeepV2Review2Repository(RTBDeepV2ExpansionRepository):
    def preview_review2(self, project_id, run_id, reviewer_id):
        """Read only: current candidate and signed readiness preview."""
        pid = _project(project_id)
        rid = _uuid(run_id, "rtb_v2_run_id")
        actor = _reviewer(reviewer_id)
        with self.connection_factory() as conn:
            _auth(conn, pid, actor)
            row = conn.execute(
                """SELECT status,binding_id,expansion_count,quality_report
                   FROM tb_deep_v2_runs WHERE project_id=%s AND id=%s""",
                (pid, rid),
            ).fetchone()
            if not row:
                _fail("rtb_v2_run_not_found", 404)
            if row[0] != "review2":
                _fail("rtb_v2_review2_not_pending")
            _, frozen = self._binding(pid, str(row[1]), actor)
            h1, first = _load_review_and_first(conn, pid, rid, frozen)
            stage, candidate, prior_quality = _candidate(
                conn, pid, rid, row[2], frozen, h1, first)
            if prior_quality != row[3] or prior_quality.get("passed") is not True:
                _fail("rtb_v2_review2_quality_drift")
            gate = search_readiness_gate(
                candidate["nodes"], frozen, h1_nodes=h1,
                first_nodes=first, candidate_stage=stage,
                model_duplicates=candidate["duplicates"],
            )
        return {
            "run_id": rid, "candidate_stage": stage,
            "candidate_sha256": canonical_sha(candidate["nodes"]),
            "candidate_nodes": deepcopy(candidate["nodes"]),
            "readiness": gate, "provider_calls": 0,
        }

    def submit_review2(self, project_id, run_id, reviewer_id, decisions):
        """Human-reviewed tree + gate must both pass in one SQL transaction."""
        pid = _project(project_id)
        rid = _uuid(run_id, "rtb_v2_run_id")
        actor = _reviewer(reviewer_id)
        with self.connection_factory() as conn:
            with conn.transaction():
                _auth(conn, pid, actor)
                run = self._locked_run(conn, pid, rid)
                if run["status"] != "review2":
                    _fail("rtb_v2_review2_not_pending")
                if run["reserved"] != 0:
                    _fail("rtb_v2_review2_outstanding_reserved_budget")
                _, frozen = self._binding(pid, run["binding_id"], actor)
                h1, first = _load_review_and_first(conn, pid, rid, frozen)
                stage, candidate, prior_quality = _candidate(
                    conn, pid, rid, run["expansion_count"], frozen, h1, first)
                persisted = conn.execute(
                    """SELECT quality_report FROM tb_deep_v2_runs
                       WHERE project_id=%s AND id=%s""",
                    (pid, rid),
                ).fetchone()[0]
                if prior_quality != persisted or prior_quality.get("passed") is not True:
                    _fail("rtb_v2_review2_quality_drift")
                reviewed = apply_human_review1(
                    candidate["nodes"], decisions, frozen)
                # Rejected original Pass1 nodes cannot be revived by a human
                # Review2 edit/add/merge. H1 remains immutable in 14 fields.
                validate_protected_followup(
                    {"nodes": reviewed, "duplicates": []},
                    frozen, h1, first, role="deep_second",
                )
                ids = {node["id"] for node in reviewed}
                duplicates = [
                    p for p in candidate["duplicates"]
                    if p["a"] in ids and p["b"] in ids
                ]
                gate = search_readiness_gate(
                    reviewed, frozen, h1_nodes=h1,
                    first_nodes=first, candidate_stage="human_review2",
                    model_duplicates=duplicates,
                )
                if not gate["passed"]:
                    _fail("rtb_v2_review2_search_readiness_blocked", 422)
                proposed_sha = canonical_sha(candidate["nodes"])
                accepted_sha = canonical_sha(reviewed)
                conn.execute(
                    """INSERT INTO tb_deep_v2_readiness_reports(
                         project_id,run_id,actor,candidate_stage,
                         candidate_sha256,approved_sha256,research_input_sha256,
                         report_sha256,report)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (pid, rid, actor, stage, proposed_sha, accepted_sha,
                     frozen["research_input_sha256"], gate["report_sha256"],
                     Jsonb(gate)),
                )
                conn.execute(
                    """INSERT INTO tb_deep_v2_reviews(
                         project_id,run_id,review_no,actor,candidate_sha256,
                         approved_sha256,decisions,approved_nodes)
                       VALUES(%s,%s,2,%s,%s,%s,%s,%s)""",
                    (pid, rid, actor, proposed_sha, accepted_sha,
                     Jsonb(decisions), Jsonb(reviewed)),
                )
                conn.execute(
                    """UPDATE tb_deep_v2_runs
                       SET status='approved',quality_report=%s,
                           updated_at=clock_timestamp()
                       WHERE project_id=%s AND id=%s""",
                    (Jsonb(gate), pid, rid),
                )
        return {
            "run_id": rid, "status": "approved",
            "published": False, "approved_nodes_sha256": accepted_sha,
            "readiness_report_sha256": gate["report_sha256"],
            "approved_nodes": deepcopy(reviewed),
            "readiness": gate, "provider_calls": 0,
        }

    def get_review2(self, project_id, run_id, reviewer_id):
        """Replay-verified immutable Review2 and readiness report; no side effects."""
        pid = _project(project_id)
        rid = _uuid(run_id, "rtb_v2_run_id")
        actor = _reviewer(reviewer_id)
        with self.connection_factory() as conn:
            _auth(conn, pid, actor)
            reviewed = conn.execute(
                """SELECT candidate_sha256,approved_sha256,decisions,
                          approved_nodes,actor,reviewed_at
                   FROM tb_deep_v2_reviews
                   WHERE project_id=%s AND run_id=%s AND review_no=2""",
                (pid, rid),
            ).fetchone()
            report = conn.execute(
                """SELECT candidate_sha256,approved_sha256,
                          research_input_sha256,report_sha256,report
                   FROM tb_deep_v2_readiness_reports
                   WHERE project_id=%s AND run_id=%s""",
                (pid, rid),
            ).fetchone()
        if not reviewed or not report:
            _fail("rtb_v2_review2_not_found", 404)
        if (reviewed[0] != report[0] or reviewed[1] != report[1]
                or canonical_sha(reviewed[3]) != reviewed[1]
                or canonical_sha({
                    k: v for k, v in report[4].items()
                    if k != "report_sha256"
                }) != report[3]
                or report[4].get("passed") is not True
                or report[4].get("research_input_sha256") != report[2]):
            _fail("rtb_v2_review2_integrity_mismatch")
        return {
            "run_id": rid, "approved_nodes": reviewed[3],
            "approved_sha256": reviewed[1],
            "candidate_sha256": reviewed[0],
            "decisions": reviewed[2], "reviewed_by": reviewed[4],
            "reviewed_at": reviewed[5].isoformat(),
            "readiness": report[4], "published": False, "provider_calls": 0,
        }
