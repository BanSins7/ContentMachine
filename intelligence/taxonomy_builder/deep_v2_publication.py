"""RTB FIX-07: atomic standalone publication with immutable H2 + SRE mirror.

Publication is a distinct, nonpaid operator action AFTER approved Review2.
One PostgreSQL transaction inserts SRE taxonomy, V2 14-field tree, V1 11-field
projection and source/SRE links, then marks run published. No LLM calls.
"""
from __future__ import annotations
from copy import deepcopy
from psycopg.types.json import Jsonb

from intelligence.knowledge_extraction.human_review import _auth, _reviewer
from intelligence.project_sources.context_builder import sha as canonical_sha
from .deep_v2_pass2 import _load_review_and_first
from .deep_v2_readiness import search_readiness_gate
from .deep_v2_review2_repository import RTBDeepV2Review2Repository
from .deep_v2_sre_bridge import (
    prepare_deep_v2_sre_projection, mirror_deep_v2_sre_in_transaction,
)
from .deep_v2_quote_repository import _fail
from .research_contracts_v2 import _project, _uuid, project_v2_tree_to_v1_core


def _verified_h2(conn, pid, rid, run, source):
    review = conn.execute(
        """SELECT candidate_sha256,approved_sha256,approved_nodes
           FROM tb_deep_v2_reviews
           WHERE project_id=%s AND run_id=%s AND review_no=2""",
        (pid, rid),
    ).fetchone()
    readiness = conn.execute(
        """SELECT candidate_sha256,approved_sha256,research_input_sha256,
                  report_sha256,report
           FROM tb_deep_v2_readiness_reports
           WHERE project_id=%s AND run_id=%s""",
        (pid, rid),
    ).fetchone()
    if not review or not readiness:
        _fail("rtb_v2_publication_missing_human_review2")
    tree = review[2]
    if (canonical_sha(tree) != review[1]
            or readiness[0] != review[0]
            or readiness[1] != review[1]
            or readiness[2] != source["research_input_sha256"]
            or run["quality"] != readiness[4]
            or readiness[4].get("passed") is not True
            or readiness[3] != canonical_sha({
                k:v for k,v in readiness[4].items()
                if k != "report_sha256"
            })):
        _fail("rtb_v2_publication_review2_or_gate_changed")
    h1, first = _load_review_and_first(conn, pid, rid, source)
    gate = search_readiness_gate(
        tree, source, h1_nodes=h1, first_nodes=first,
        candidate_stage="human_review2",
        model_duplicates=readiness[4]["legacy_quality"]["duplicates"],
    )
    if gate != readiness[4] or gate["report_sha256"] != readiness[3]:
        _fail("rtb_v2_publication_readiness_replay_mismatch")
    return tree, readiness[3]


class RTBDeepV2PublicationRepository(RTBDeepV2Review2Repository):
    """One-shot publication: repeat requests read the previous signed result."""

    def publish(self, project_id, run_id, reviewer_id):
        pid = _project(project_id)
        rid = _uuid(run_id, "rtb_v2_run_id")
        actor = _reviewer(reviewer_id)
        with self.connection_factory() as conn:
            with conn.transaction():
                _auth(conn, pid, actor)
                run = self._locked_run(conn, pid, rid)
                if run["status"] == "published":
                    prior = self._read_publication(conn, pid, rid)
                    if not prior:
                        _fail("rtb_v2_publication_missing_for_published_run")
                    return {**prior, "created": False, "provider_calls": 0}
                if run["status"] != "approved":
                    _fail("rtb_v2_publication_requires_approved_h2")
                if run["reserved"] != 0:
                    _fail("rtb_v2_publication_unsettled_provider_reservation")
                if conn.execute(
                    """SELECT 1 FROM tb_deep_v2_publications
                       WHERE project_id=%s AND run_id=%s""",
                    (pid, rid),
                ).fetchone():
                    _fail("rtb_v2_publication_run_state_drift")
                binding, source = self._binding(pid, run["binding_id"], actor)
                if binding["research_input_sha256"] != source["research_input_sha256"]:
                    _fail("rtb_v2_publication_binding_changed")
                quality = conn.execute(
                    """SELECT quality_report FROM tb_deep_v2_runs
                       WHERE project_id=%s AND id=%s""",
                    (pid, rid),
                ).fetchone()
                run["quality"] = quality[0] if quality else None
                tree, readiness_sha = _verified_h2(conn, pid, rid, run, source)
                projection = prepare_deep_v2_sre_projection(tree, source)
                # The same DB connection/transaction owns EVERY SRE and RTB
                # row. Any SRE UUID conflict or failed final INSERT rolls back
                # the profile, facts, taxonomy version, active pointer and pub.
                sre = mirror_deep_v2_sre_in_transaction(
                    conn, projection, approved_by=actor)
                pub = conn.execute(
                    """INSERT INTO tb_deep_v2_publications(
                         project_id,run_id,binding_id,research_input_sha256,
                         b9_snapshot_id,b9_snapshot_sha256,
                         a_context_id,a_context_sha256,
                         review2_sha256,readiness_report_sha256,
                         v2_tree,v2_tree_sha256,v1_core,v1_core_sha256,
                         fact_mapping,sre_topics_sha256,
                         sre_taxonomy_version_id,published_by)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                              %s,%s,%s,%s,%s) RETURNING id""",
                    (
                        pid, rid, run["binding_id"],
                        source["research_input_sha256"],
                        source["b9_snapshot_id"], source["b9_snapshot_sha256"],
                        source["a_context_id"], source["a_context_sha256"],
                        projection["v2_tree_sha256"], readiness_sha,
                        Jsonb(projection["v2_tree"]),
                        projection["v2_tree_sha256"],
                        Jsonb(projection["v1_core"]),
                        projection["v1_core_sha256"],
                        Jsonb(projection["fact_mapping"]),
                        projection["sre_topics_sha256"],
                        sre["sre_taxonomy_version_id"], actor,
                    ),
                ).fetchone()[0]
                conn.execute(
                    """INSERT INTO tb_deep_v2_sre_links(
                         project_id,run_id,publication_id,
                         sre_taxonomy_version_id,source_sha256)
                       VALUES(%s,%s,%s,%s,%s)""",
                    (
                        pid, rid, pub, sre["sre_taxonomy_version_id"],
                        projection["sre_topics_sha256"],
                    ),
                )
                updated = conn.execute(
                    """UPDATE tb_deep_v2_runs
                       SET status='published',updated_at=clock_timestamp()
                       WHERE project_id=%s AND id=%s AND status='approved'
                       RETURNING id""", (pid, rid),
                ).fetchone()
                if not updated:
                    _fail("rtb_v2_publication_final_status_conflict")
                return {
                    "publication_id": str(pub), "run_id": rid,
                    "project_id": pid, "created": True,
                    "status": "published",
                    "research_input_sha256": source["research_input_sha256"],
                    "v2_tree_sha256": projection["v2_tree_sha256"],
                    "v1_core_sha256": projection["v1_core_sha256"],
                    "readiness_report_sha256": readiness_sha,
                    "sre_taxonomy_version_id": sre["sre_taxonomy_version_id"],
                    "sre_taxonomy_version": sre["sre_taxonomy_version"],
                    "reused_fact_count": sre["reused_fact_count"],
                    "new_fact_count": sre["new_fact_count"],
                    "sre_category_count": sre["category_count"],
                    "provider_calls": 0,
                }

    @staticmethod
    def _read_publication(conn, pid, rid):
        row = conn.execute(
            """SELECT id,research_input_sha256,v2_tree_sha256,
                      v1_core_sha256,readiness_report_sha256,
                      sre_taxonomy_version_id,v2_tree,v1_core,
                      fact_mapping,sre_topics_sha256
               FROM tb_deep_v2_publications
               WHERE project_id=%s AND run_id=%s""",
            (pid, rid),
        ).fetchone()
        if not row:
            return None
        if (canonical_sha(row[6]) != row[2]
                or canonical_sha(row[7]) != row[3]
                or project_v2_tree_to_v1_core(row[6]) != row[7]):
            _fail("rtb_v2_publication_checksum_or_core_invalid")
        link = conn.execute(
            """SELECT publication_id,sre_taxonomy_version_id,source_sha256
               FROM tb_deep_v2_sre_links
               WHERE project_id=%s AND run_id=%s""",
            (pid, rid),
        ).fetchone()
        if (not link or str(link[0]) != str(row[0]) or link[1] != row[5]
                or link[2] != row[9]):
            _fail("rtb_v2_publication_sre_link_mismatch")
        return {
            "publication_id": str(row[0]), "project_id": pid,
            "run_id": rid, "status": "published",
            "research_input_sha256": row[1],
            "v2_tree_sha256": row[2], "v1_core_sha256": row[3],
            "readiness_report_sha256": row[4],
            "sre_taxonomy_version_id": row[5],
            "v2_tree": deepcopy(row[6]), "v1_core": deepcopy(row[7]),
            "fact_mapping": deepcopy(row[8]),
            "sre_topics_sha256": row[9],
        }

    def get_publication(self, project_id, run_id, reviewer_id, *,
                        projection="deep-v2"):
        """Read-only verified publication; core mode contains exactly 11 keys."""
        pid = _project(project_id)
        rid = _uuid(run_id, "rtb_v2_run_id")
        actor = _reviewer(reviewer_id)
        if projection not in ("deep-v2", "legacy-core-v1"):
            _fail("rtb_v2_publication_projection_unsupported", 422)
        with self.connection_factory() as conn:
            _auth(conn, pid, actor)
            published = self._read_publication(conn, pid, rid)
            if not published:
                _fail("rtb_v2_publication_not_found", 404)
            status = conn.execute(
                """SELECT status FROM tb_deep_v2_runs
                   WHERE project_id=%s AND id=%s""",
                (pid, rid),
            ).fetchone()
            if not status or status[0] != "published":
                _fail("rtb_v2_publication_run_state_invalid")
        key = "v2_tree" if projection == "deep-v2" else "v1_core"
        tree = published.pop(key)
        published.pop("v1_core" if key == "v2_tree" else "v2_tree")
        return {
            **published, "projection": projection,
            "nodes": tree, "node_count": len(tree),
            "provider_calls": 0,
        }
