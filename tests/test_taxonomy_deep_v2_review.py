"""Pure Deep V2 first human review: complete decisions, no paid calls."""
from __future__ import annotations

from copy import deepcopy
import unittest

from intelligence.taxonomy_builder.deep_v2_human_review import apply_human_review1
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from test_taxonomy_deep_v2_validation import sample


def decisions(nodes):
    return [{"action": "approve", "id": n["id"]} for n in nodes]


class DeepV2ReviewPure(unittest.TestCase):
    def test_approve_all_keeps_14_fields_unmodified(self):
        source, proposed = sample()
        before = deepcopy(proposed)
        out = apply_human_review1(proposed, decisions(proposed), source)
        self.assertEqual(out, before)
        self.assertEqual(proposed, before)
        self.assertTrue(all(len(node) == 14 for node in out))

    def test_all_candidates_require_exactly_one_explicit_decision(self):
        source, proposed = sample()
        with self.assertRaisesRegex(PipelineError, "review_incomplete"):
            apply_human_review1(proposed, decisions(proposed)[:-1], source)
        changed = decisions(proposed)
        changed.append({"action": "reject", "id": proposed[0]["id"]})
        with self.assertRaisesRegex(PipelineError, "each_candidate_once"):
            apply_human_review1(proposed, changed, source)

    def test_edit_stable_id_and_new_evidence_are_revalidated(self):
        source, proposed = sample()
        planned = decisions(proposed)
        edited = deepcopy(proposed[1])
        edited["search_signals"] = ["A specific comparison hook"]
        edited["validation_focus"] = ["Confirm reference footage matches claim"]
        planned[1] = {"action": "edit", "id": proposed[1]["id"], "node": edited}
        out = apply_human_review1(proposed, planned, source)
        self.assertEqual(out[1], edited)
        forged = deepcopy(edited)
        forged["source_observation_ids"] = ["invented_observation"]
        planned[1]["node"] = forged
        with self.assertRaises(PipelineError):
            apply_human_review1(proposed, planned, source)

    def test_reject_does_not_leave_orphans(self):
        source, proposed = sample()
        changed = decisions(proposed)
        changed[0] = {"action": "reject", "id": proposed[0]["id"]}
        with self.assertRaisesRegex(PipelineError, "rejected_parent"):
            apply_human_review1(proposed, changed, source)

    def test_merge_reparents_only_to_explicit_approved_target(self):
        source, proposed = sample()
        changed = decisions(proposed)
        changed[2] = {"action": "merge", "id": "direction_two",
                      "target_id": "direction_one"}
        # Mixed evidence relationships between merged direction and child must
        # be revalidated by V1/V2 graph and B9 role gates.
        out = apply_human_review1(proposed, changed, source)
        by_id = {n["id"]: n for n in out}
        self.assertNotIn("direction_two", by_id)
        self.assertEqual(by_id["hypothesis_two"]["parent_id"], "direction_one")

    def test_merge_target_cannot_be_rejected_or_self(self):
        source, proposed = sample()
        for target in ("direction_two", "nonexistent"):
            changed = decisions(proposed)
            changed[0] = {"action": "merge", "id": "direction_one",
                          "target_id": target}
            with self.assertRaises(PipelineError):
                apply_human_review1(proposed, changed, source)
        changed = decisions(proposed)
        changed[0] = {"action": "merge", "id": "direction_one",
                      "target_id": "direction_two"}
        changed[2] = {"action": "reject", "id": "direction_two"}
        with self.assertRaises(PipelineError):
            apply_human_review1(proposed, changed, source)

    def test_human_addition_keeps_source_boundaries(self):
        source, proposed = sample()
        planned = decisions(proposed)
        original = deepcopy(proposed[1])
        original["id"] = "hypothesis_manual"
        original["name"] = "Manual Comparison Format"
        planned.append({"action": "add", "node": original})
        out = apply_human_review1(proposed, planned, source)
        self.assertEqual(len(out), len(proposed)+1)
        self.assertIn("hypothesis_manual", {x["id"] for x in out})
        planned[-1]["node"]["source_fact_ids"] = ["0" * 36]
        with self.assertRaises(PipelineError):
            apply_human_review1(proposed, planned, source)

    def test_duplicate_manual_nodes_rejected_and_no_mutations(self):
        source, proposed = sample()
        planned = decisions(proposed)
        node = deepcopy(proposed[1])
        node.update({"id": "hypothesis_unique", "name": "Unique New Focus"})
        planned += [{"action": "add", "node": deepcopy(node)},
                    {"action": "add", "node": deepcopy(node)}]
        baseline = deepcopy((proposed, planned))
        with self.assertRaisesRegex(PipelineError, "duplicate_id"):
            apply_human_review1(proposed, planned, source)
        self.assertEqual((proposed, planned), baseline)

    def test_reject_no_new_graph_without_two_research_directions(self):
        source, proposed = sample()
        changed = decisions(proposed)
        changed[2] = {"action": "reject", "id": "direction_two"}
        changed[3] = {"action": "reject", "id": "hypothesis_two"}
        with self.assertRaises(PipelineError):
            apply_human_review1(proposed, changed, source)


if __name__ == "__main__":
    unittest.main()
