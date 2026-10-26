"""RTB-FIX-06 deterministic Search Readiness tests, no DB/provider."""
from __future__ import annotations

from copy import deepcopy
import unittest

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.taxonomy_builder.deep_v2_readiness import (
    search_readiness_gate, GATE_VERSION,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from test_taxonomy_deep_v2_validation import sample


def source_tree():
    source, nodes = sample()
    for index, n in enumerate(nodes):
        n["definition"] = (
            f"Distinctly defined grounded research scenario number {index} "
            f"covering {'product features' if index < 2 else 'user problems'}"
        )
        n["rationale"] = f"Evidence-backed format for scenario {index}"
    nodes[3]["search_signals"] = ["Pain-point interviews and demonstrations"]
    nodes[3]["validation_focus"] = ["Verify audience problem evidence quality"]
    return source, nodes


def readiness(source, nodes, *, h1=None, first=None, duplicates=None):
    return search_readiness_gate(
        nodes, source, h1_nodes=h1 or nodes, first_nodes=first or nodes,
        candidate_stage="human_review2", model_duplicates=duplicates,
    )


class DeepV2ReadinessUnit(unittest.TestCase):
    def test_valid_two_direction_tree_reports_signed_pass(self):
        src, tree = source_tree()
        before = deepcopy((src, tree))
        report = readiness(src, tree)
        self.assertTrue(report["passed"], report["blocking_errors"])
        self.assertEqual(report["blocking_errors"], [])
        self.assertEqual(report["schema_version"], GATE_VERSION)
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["tree_sha256"], canonical_sha(tree))
        self.assertEqual(
            report["report_sha256"],
            canonical_sha({k:v for k,v in report.items() if k != "report_sha256"}),
        )
        self.assertEqual((src, tree), before)

    def test_generic_short_search_signal_blocks_without_paid_call(self):
        src, tree = source_tree()
        tree[1]["search_signals"] = ["viral"]
        report = readiness(src, tree)
        self.assertFalse(report["passed"])
        self.assertIn(
            "rtb_v2_readiness_search_term_too_generic",
            {b["code"] for b in report["blocking_errors"]},
        )
        self.assertEqual(report["model_calls"], 0)

    def test_search_signals_cannot_repeat_across_hypotheses(self):
        src, tree = source_tree()
        tree[3]["search_signals"] = deepcopy(tree[1]["search_signals"])
        result = readiness(src, tree)
        self.assertFalse(result["passed"])
        self.assertIn("rtb_v2_readiness_repeated_search_term",
                      {b["code"] for b in result["blocking_errors"]})

    def test_coverage_gap_blocks_a_publishable_tree(self):
        src, tree = source_tree()
        # Remove problem references from the second direction/hypothesis while
        # retaining valid direct source links; search gate checks B9 coverage.
        function_id = src["c1_approved_facts"][0]["rtb_fact_id"]
        for node in tree[2:]:
            node["relation"] = "direct"
            node["source_fact_ids"] = [function_id]
        gate = readiness(src, tree)
        self.assertFalse(gate["passed"])
        self.assertIn("rtb_v2_readiness_evidence_or_direction_gap",
                      {b["code"] for b in gate["blocking_errors"]})

    def test_unapproved_video_observation_fails_closed(self):
        src, tree = source_tree()
        tree[1]["source_observation_ids"] = ["fake_observation_id"]
        with self.assertRaises(PipelineError):
            readiness(src, tree)

    def test_human_review1_lock_cannot_be_edited_in_review2(self):
        src, tree = source_tree()
        changed = deepcopy(tree)
        changed[1]["search_signals"] = ["Changed research format without approval"]
        with self.assertRaisesRegex(PipelineError, "readiness_h1_mutated"):
            readiness(src, changed, h1=tree, first=tree)

    def test_rejected_original_first_pass_cannot_be_revived(self):
        src, tree = source_tree()
        approved = deepcopy(tree)
        approved.pop(3)
        with self.assertRaisesRegex(PipelineError, "readiness_rejected_id_restored"):
            readiness(src, tree, h1=approved, first=tree)

    def test_duplicate_approved_research_nodes_block(self):
        src, tree = source_tree()
        d = {"a": tree[0]["id"], "b": tree[2]["id"],
             "reason": "Both directions describe the same video intent"}
        report = readiness(src, tree, duplicates=[d])
        self.assertFalse(report["passed"])
        self.assertIn("rtb_v2_readiness_duplicate_hypotheses",
                      {b["code"] for b in report["blocking_errors"]})

    def test_direction_novelty_is_a_blocker_not_silent_success(self):
        src, tree = source_tree()
        h1 = deepcopy(tree)
        h1[2]["name"] = "Legacy Problem Research"
        with self.assertRaisesRegex(PipelineError, "readiness_h1_mutated"):
            readiness(src, tree, h1=h1, first=tree)

    def test_stage_and_fact_hash_are_replay_bound(self):
        src, tree = source_tree()
        result = readiness(src, tree)
        self.assertEqual(result["research_input_sha256"], src["research_input_sha256"])
        for stage in ("deep_first", "unknown", ""):
            with self.assertRaises(PipelineError):
                search_readiness_gate(tree, src, h1_nodes=tree,
                                      first_nodes=tree, candidate_stage=stage)


if __name__ == "__main__":
    unittest.main()
