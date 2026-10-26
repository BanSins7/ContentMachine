"""RTB Deep V2 Human Review1: immutable DB approval and Pass2 readiness.

One transaction writes decisions + signed approved 14-field H1 tree and moves
run from review1 to pass2_pending. GETs/approvals never call an LLM.
"""
from __future__ import annotations

from copy import deepcopy

from psycopg.types.json import Jsonb

from intelligence.knowledge_extraction.human_review import _auth, _reviewer
from intelligence.project_sources.context_builder import canonical, sha as canonical_sha
from .deep_contracts import quality_gate
from .deep_v2_human_review import apply_human_review1
from .deep_v2_pass1 import RTBDeepV2Pass1Repository
from .deep_v2_transport import MAX_GATE_BYTES, MAX_H1_BYTES
from .pipeline_contracts import PipelineError
from .research_contracts_v2 import _project, _uuid, project_v2_tree_to_v1_core


class RTBDeepV2HumanReviewRepository(RTBDeepV2Pass1Repository):
    def submit_review1(self, project_id: str, run_id: str, reviewer_id: str,
                       decisions: list) -> dict:
        pid = _project(project_id)
        rid = _uuid(run_id, "rtb_v2_run_id")
        actor = _reviewer(reviewer_id)
        with self.connection_factory() as conn:
            with conn.transaction():
                _auth(conn, pid, actor)
                run = self._locked_run(conn, pid, rid)
                if run["status"] != "review1":
                    raise PipelineError("rtb_v2_review1_not_pending", 409)
                candidate = conn.execute(
                    """SELECT output FROM tb_deep_v2_attempts
                      WHERE project_id=%s AND run_id=%s
                        AND stage='deep_first' AND status='completed'""",
                    (pid, rid),
                ).fetchone()
                if not candidate or not isinstance(candidate[0], dict):
                    raise PipelineError("rtb_v2_review1_missing_validated_pass1", 409)
                proposal = candidate[0].get("nodes")
                _, frozen = self._binding(pid, run["binding_id"], actor)
                reviewed = apply_human_review1(proposal, decisions, frozen)
                fact_list = [
                    {"id": f["rtb_fact_id"], "kind": f["kind"],
                     "text": f["reviewed_text"]}
                    for f in frozen["c1_approved_facts"]
                ]
                coverage = quality_gate(
                    project_v2_tree_to_v1_core(reviewed), fact_list)
                if (len(canonical(reviewed)) > MAX_H1_BYTES
                        or len(canonical(coverage)) > MAX_GATE_BYTES):
                    raise PipelineError("rtb_v2_review1_exceeds_pinned_pass2_capacity", 422)
                proposal_sha = canonical_sha(proposal)
                approved_sha = canonical_sha(reviewed)
                conn.execute(
                    """INSERT INTO tb_deep_v2_reviews(
                         project_id,run_id,review_no,actor,candidate_sha256,
                         approved_sha256,decisions,approved_nodes)
                       VALUES(%s,%s,1,%s,%s,%s,%s,%s)""",
                    (pid, rid, actor, proposal_sha, approved_sha,
                     Jsonb(decisions), Jsonb(reviewed)),
                )
                conn.execute(
                    """UPDATE tb_deep_v2_runs
                       SET status='pass2_pending',quality_report=%s,
                           updated_at=clock_timestamp()
                       WHERE project_id=%s AND id=%s""",
                    (Jsonb(coverage), pid, rid),
                )
        return {
            "run_id": rid, "status": "pass2_pending",
            "approved_nodes_sha256": approved_sha,
            "approved_nodes": deepcopy(reviewed),
            "coverage": coverage, "provider_calls": 0,
        }

    def get_review1(self, project_id: str, run_id: str, reviewer_id: str) -> dict:
        pid = _project(project_id)
        rid = _uuid(run_id, "rtb_v2_run_id")
        actor = _reviewer(reviewer_id)
        with self.connection_factory() as conn:
            _auth(conn, pid, actor)
            review = conn.execute(
                """SELECT candidate_sha256,approved_sha256,decisions,
                          approved_nodes,actor,reviewed_at
                   FROM tb_deep_v2_reviews
                  WHERE project_id=%s AND run_id=%s AND review_no=1""",
                (pid, rid),
            ).fetchone()
        if not review:
            raise PipelineError("rtb_v2_review1_not_found", 404)
        if canonical_sha(review[3]) != review[1]:
            raise PipelineError("rtb_v2_review1_integrity_failed", 409)
        return {
            "run_id": rid, "candidate_sha256": review[0],
            "approved_nodes_sha256": review[1],
            "decisions": review[2], "approved_nodes": review[3],
            "actor": review[4], "reviewed_at": review[5].isoformat(),
            "provider_calls": 0,
        }
