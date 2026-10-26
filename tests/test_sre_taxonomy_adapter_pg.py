"""S2 real PostgreSQL integration: full human-approved RTB deep publication.

Reuses the repository's EXISTING, fully fake-OpenRouter deep flow fixture.
No external HTTP requests or paid model executions. Skipped without disposable
CM_TEST_PG_DSN; full Linux/Windows CI supplies PostgreSQL.
"""
from __future__ import annotations

import os
import unittest
from uuid import uuid4

from intelligence import migrate
from intelligence.relevance.taxonomy_adapter import (
    load_approved_taxonomy, load_classifier_contexts,
)
from intelligence.relevance.v1_contracts import SREContractError
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"), "disposable PostgreSQL required")
class RealPublishedRTBToSRE(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.dsn = os.environ["CM_TEST_PG_DSN"]
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            migrate.apply_migrations(conn)

    def test_approved_deep_publication_to_sre_leaf_classifier_contexts(self):
        import psycopg
        from test_taxonomy_deep_db import DeepDB, make_tree
        from intelligence.taxonomy_builder.deep_repository import (
            advance_deep, first_review, final_review, get_deep,
        )
        from intelligence.search_planner import load_approved_publication
        fixture = DeepDB("test_complete_two_human_reviews_saturation_and_atomic_sre_tree")
        fixture.dsn = self.dsn  # delegate fixture setUp normally receives class setUpClass dsn
        fixture.setUp()
        try:
            pid, rid = fixture.project["id"], fixture.rid
            fixture.prepare()
            self.assertEqual(advance_deep(pid, rid, sender=fixture.sender)["status"], "review1")
            first = get_deep(pid, rid)["nodes"]
            first_review(pid, rid, [{"id": n["id"], "action": "approve"} for n in first])
            self.assertEqual(advance_deep(pid, rid, sender=fixture.sender)["status"], "expand_pending")
            self.assertEqual(advance_deep(pid, rid, sender=fixture.sender)["status"], "review2")
            final = get_deep(pid, rid)["nodes"]
            result = final_review(pid, rid, [
                {"id": n["id"], "action": "approve"} for n in final
            ])
            self.assertEqual(result["status"], "approved")
            self.assertEqual(result["sre_category_count"], len(final))

            with psycopg.connect(self.dsn) as conn:
                pub = str(conn.execute(
                    """SELECT builder_publication_id FROM tb_deep_publications
                       WHERE project_id=%s AND run_id=%s""", (pid, rid)
                ).fetchone()[0])
                pre_audit = conn.execute(
                    "SELECT count(*) FROM sre_classification_runs WHERE project_id=%s",
                    (pid,),
                ).fetchone()[0]

            from intelligence.relevance.ports.taxonomy import load_verified_taxonomy_reference
            port_ref = load_verified_taxonomy_reference(pid, pub)
            self.assertEqual(port_ref['publication_id'], pub)
            self.assertEqual(port_ref['project_id'], pid)
            self.assertEqual(len(port_ref['hypothesis_links']), 3)

            # M06: authoritative C9 v1 bundle, built in a single consistent
            # read from this REAL H1/H2 approved PostgreSQL publication.
            from intelligence.relevance.ports.approved_c9 import (
                load_approved_c9, validate_c9_bundle,
            )
            bundle = load_approved_c9(pid, pub)
            self.assertEqual(bundle, validate_c9_bundle(bundle))
            self.assertEqual(bundle["taxonomy_reference"], port_ref)
            self.assertEqual(bundle["publication_id"], pub)
            self.assertEqual(bundle["project_id"], pid)
            self.assertEqual(bundle["sre_taxonomy_version_id"],
                             result["sre_taxonomy_version_id"])
            self.assertEqual(len(bundle["contexts"]), 3)
            self.assertEqual(len(bundle["node_projections"]), len(final))
            self.assertEqual({c["source_node_id"] for c in bundle["contexts"]},
                             {"hypothesis", "problem_idea", "adjacent_idea"})
            self.assertEqual(bundle["hypothesis_keys"], sorted(
                c["category_key"] for c in bundle["contexts"]))
            self.assertEqual(bundle["contexts"], load_approved_c9(pid, pub)["contexts"])
            with self.assertRaises((PipelineError, SREContractError)):
                load_approved_c9(fixture.other["id"], pub)
            with self.assertRaises((PipelineError, SREContractError)):
                load_approved_c9(pid, str(uuid4()))
            accepted = load_approved_taxonomy(pid, pub)
            contexts = load_classifier_contexts(pid, pub)
            self.assertEqual(accepted["sre_taxonomy_version_id"],
                             result["sre_taxonomy_version_id"])
            self.assertEqual(len(contexts), len([n for n in final if n["kind"] == "hypothesis"]))
            self.assertEqual({x["source_node_id"] for x in contexts},
                             {"hypothesis", "problem_idea", "adjacent_idea"})
            self.assertEqual(len(accepted["nodes"]), len(final))
            self.assertTrue(all(len(c["context_sha256"]) == 64 for c in contexts))
            self.assertTrue(all(c["taxonomy_sha256"] == accepted["tree_sha256"]
                                for c in contexts))
            self.assertTrue(any(c["branch"] is None for c in contexts))
            self.assertTrue(any(c["branch"] is not None for c in contexts))

            # The S2 adapter accepts the same approved publication that Search
            # Planner reads, but cross-checks all mirrored SRE categories.
            planner_snapshot = load_approved_publication(pid, pub)
            self.assertEqual(accepted["tree_sha256"], planner_snapshot["taxonomy_sha256"])
            with psycopg.connect(self.dsn) as conn:
                post_audit = conn.execute(
                    "SELECT count(*) FROM sre_classification_runs WHERE project_id=%s",
                    (pid,),
                ).fetchone()[0]
            self.assertEqual(pre_audit, post_audit)
            self.assertEqual(accepted, load_approved_taxonomy(pid, pub))
            self.assertEqual(contexts, load_classifier_contexts(pid, pub))
            with self.assertRaises((PipelineError, SREContractError)):
                load_approved_taxonomy(fixture.other["id"], pub)
            with self.assertRaises((PipelineError, SREContractError)):
                load_approved_taxonomy(pid, str(uuid4()))

            # Rejection of a historical flat-only publication must not result
            # in silently classifying its categories as deep hypotheses.
            # Coverage is additionally tested through the synthetic DB suite.
            self.assertNotEqual(fixture.sent.count("first"), 0)
            self.assertEqual(fixture.sent.count("second"), 1)
        finally:
            fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
