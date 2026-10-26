"""SP-02: approved C9 V1 provenance and strict Deep V2 fail-closed boundaries.

No external API, database, downloads or paid calls. PG-integrity is exercised
by the existing deep E2E CI suite and by a later dedicated fresh/upgrade gate.
"""
from __future__ import annotations

import copy
import unittest

from intelligence.search_planner import SearchPlannerService, build_hypothesis_contexts
from intelligence.search_planner.context_builder import require_published_context_batch
from intelligence.search_planner.contracts import validate_published_snapshot
from intelligence.search_planner.input_loader import load_approved_publication
from intelligence.search_planner.publication_versions import (
    DEEP_V1_SCHEMA, V1_NODE_FIELDS, V2_EXTENSION_FIELDS, require_deep_v1_tree,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

from test_search_planner_foundation import (
    PID, PUB, POLICY, db_row, snapshot, FakeConnection,
)


class DeepPublicationContractsStage02(unittest.TestCase):
    def test_explicit_contract_revision_is_not_taxonomy_sequence(self):
        source = snapshot()
        source["taxonomy_version"] = 999  # publication sequence, not Deep schema
        self.assertEqual(require_deep_v1_tree(source["tree"]), DEEP_V1_SCHEMA)
        approved = validate_published_snapshot(source)
        self.assertEqual(approved["taxonomy_version"], 999)
        self.assertTrue(all(set(n) == V1_NODE_FIELDS for n in approved["tree"]))

    def test_strict_v1_full_contexts_and_deep_copies(self):
        source = snapshot()
        original = copy.deepcopy(source)
        contexts = build_hypothesis_contexts(source, POLICY)
        self.assertEqual(len(contexts), 3)
        self.assertEqual(require_published_context_batch(source, POLICY, contexts), contexts)
        self.assertEqual(source, original)
        c = contexts[0]
        c["hypothesis"]["name"] = "tampered"
        self.assertEqual(source, original)
        with self.assertRaisesRegex(PipelineError, "differ"):
            require_published_context_batch(source, POLICY, contexts)

    def test_deep_v2_extensions_are_not_silently_dropped(self):
        for key in sorted(V2_EXTENSION_FIELDS):
            source = snapshot()
            source["tree"][3][key] = [] if key == "source_observation_ids" else ["research facet"]
            with self.subTest(key=key), self.assertRaisesRegex(PipelineError, "V2"):
                validate_published_snapshot(source)
            with self.subTest(service=key), self.assertRaises(PipelineError):
                SearchPlannerService(
                    publication_reader=lambda *_: source
                ).preview_contexts(PID, PUB, POLICY)

    def test_v2_extensions_on_direction_or_branch_also_blocked(self):
        for i in (0, 2):
            s = snapshot()
            s["tree"][i]["search_signals"] = []
            with self.subTest(index=i), self.assertRaisesRegex(PipelineError, "V2"):
                validate_published_snapshot(s)

    def test_unknown_fields_are_not_silently_dropped(self):
        for extra in ("synthetic_context", "content_spend_authorization", "ai_suggested"):
            source = snapshot()
            source["tree"][3][extra] = "untrusted"
            with self.subTest(extra=extra), self.assertRaisesRegex(PipelineError, "fields mismatch"):
                validate_published_snapshot(source)

    def test_incomplete_v1_node_is_rejected_before_normalization(self):
        source = snapshot()
        del source["tree"][3]["examples"]
        with self.assertRaisesRegex(PipelineError, "fields mismatch"):
            validate_published_snapshot(source)

    def test_whitespace_normalization_must_not_change_immutable_publication(self):
        source = snapshot()
        source["tree"][3]["name"] = "  Короткие занятия  "
        with self.assertRaisesRegex(PipelineError, "changed during validation"):
            validate_published_snapshot(source)

    def test_context_hypothesis_exclusions_cannot_be_forged(self):
        source = snapshot()
        contexts = build_hypothesis_contexts(source, POLICY)
        forged = copy.deepcopy(contexts)
        forged[0]["hypothesis"]["exclude"] = "all videos"
        with self.assertRaisesRegex(PipelineError, "differ"):
            require_published_context_batch(source, POLICY, forged)
        with self.assertRaises(PipelineError):
            SearchPlannerService(
                publication_reader=lambda *_: source,
                context_builder=lambda *_: forged,
            ).preview_contexts(PID, PUB, POLICY)

    def test_context_neighbor_approved_facts_and_policy_cannot_be_forged(self):
        source = snapshot()
        contexts = build_hypothesis_contexts(source, POLICY)
        changes = (
            lambda cs: cs[0]["neighbor_hypotheses"].clear(),
            lambda cs: cs[0]["approved_facts"][0].__setitem__("text", "untrusted"),
            lambda cs: cs[0]["search_policy"]["languages"].append("de"),
            lambda cs: cs[0]["derived_search_facets"].append("invented"),
        )
        for change in changes:
            forged = copy.deepcopy(contexts)
            change(forged)
            with self.subTest(case=str(change)), self.assertRaises(PipelineError):
                require_published_context_batch(source, POLICY, forged)

    def test_v1_reader_queries_final_review_equals_immutable_published_tree(self):
        source = snapshot()
        conn = FakeConnection(db_row(source))
        actual = load_approved_publication(PID, PUB, connect=lambda: conn)
        self.assertEqual(actual["taxonomy_publication_id"], PUB)
        self.assertEqual(len(conn.queries), 2)
        self.assertIn("REPEATABLE READ READ ONLY", conn.queries[0][0])
        sql, params = conn.queries[1]
        self.assertEqual(params, (PID, PUB))
        for marker in (
            "final_review.review_no=2", "final_review.approved_nodes=d.tree",
            "final_review.project_id=d.project_id",
            "final_review.run_id=d.run_id",
            "first_review.review_no=1",
            "first_review.project_id=d.project_id",
            "first_review.run_id=d.run_id",
            "d.project_id=%s", "d.builder_publication_id=%s::uuid",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, sql)

    def test_foreign_or_draft_v1_must_still_fail_closed(self):
        scenarios = (
            ("project_id", "b" * 32),
            ("taxonomy_publication_id", "55555555-5555-4555-8555-555555555555"),
            ("run_status", "generation_running"),
            ("deep_status", "review2"),
            ("human_reviews", [2]),
        )
        for field, value in scenarios:
            source = snapshot()
            source[field] = value
            with self.subTest(field=field), self.assertRaises(PipelineError):
                SearchPlannerService(publication_reader=lambda *_: source).preview_contexts(
                    PID, PUB, POLICY,
                )

    def test_strict_context_checker_rejects_wrong_collection_type(self):
        with self.assertRaises(PipelineError):
            require_published_context_batch(snapshot(), POLICY, tuple())
        with self.assertRaises(PipelineError):
            require_deep_v1_tree({"not": "a tree"})


if __name__ == "__main__":
    unittest.main()
