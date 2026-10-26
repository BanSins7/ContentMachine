"""Deep V2 Pass2/H1 immutable protection; all tests offline."""
from __future__ import annotations

from copy import deepcopy
import unittest

from intelligence.taxonomy_builder.deep_v2_followup import validate_protected_followup
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from test_taxonomy_deep_v2_validation import sample


class RTBDeepV2FollowupProtection(unittest.TestCase):

    def test_same_approved_tree_can_pass_without_rewrite(self):
        source, first = sample()
        body = {"nodes": deepcopy(first), "duplicates": []}
        approved = validate_protected_followup(body, source, first, first)
        self.assertEqual(approved, body)

    def test_model_can_add_grounded_new_hypothesis(self):
        source, first = sample()
        extended = deepcopy(first)
        new = deepcopy(first[3])
        new["id"] = "hypothesis_additional"
        new["name"] = "Additional Reviewed Audience Format"
        extended.append(new)
        output = validate_protected_followup(
            {"nodes": extended, "duplicates": []}, source, first, first)
        self.assertEqual(len(output["nodes"]), len(first)+1)

    def test_removed_or_changed_approved_node_is_rejected(self):
        source, first = sample()
        for mutate in (
            lambda ns: ns.pop(1),
            lambda ns: ns[0].update({"name": "Silently Rewritten"}),
            lambda ns: ns[1].update({"search_signals": ["Other format"]}),
            lambda ns: ns[3].update({"parent_id": "direction_one"}),
        ):
            with self.subTest(mutate=str(mutate)):
                new = deepcopy(first)
                mutate(new)
                with self.assertRaises(PipelineError):
                    validate_protected_followup(
                        {"nodes": new, "duplicates": []}, source, first, first)

    def test_originally_rejected_node_must_not_reappear(self):
        source, first = sample()
        approved = [x for x in first if x["id"] != "hypothesis_two"]
        with self.assertRaisesRegex(PipelineError, "rejected_h1_id_reintroduced"):
            validate_protected_followup(
                {"nodes": deepcopy(first), "duplicates": []},
                source, approved, first)

    def test_human_edits_are_preserved_identically(self):
        source, first = sample()
        edited = deepcopy(first)
        edited[1]["search_signals"] = ["Human approved observable wording"]
        output = validate_protected_followup(
            {"nodes": deepcopy(edited), "duplicates": []},
            source, edited, first)
        self.assertEqual(output["nodes"], edited)
        changed = deepcopy(edited)
        changed[1]["search_signals"] = first[1]["search_signals"]
        with self.assertRaisesRegex(PipelineError, "h1_locked_node"):
            validate_protected_followup(
                {"nodes": changed, "duplicates": []}, source, edited, first)

    def test_expansion_obeys_same_h1_guards(self):
        source, first = sample()
        with self.assertRaisesRegex(PipelineError, "h1_locked_node"):
            validate_protected_followup(
                {"nodes": first[:-1], "duplicates": []},
                source, first, first, role="deep_expand")

    def test_unknown_source_reference_rejected_before_h1_match(self):
        source, first = sample()
        changed = deepcopy(first)
        changed[1]["source_observation_ids"] = ["invented"]
        with self.assertRaises(PipelineError):
            validate_protected_followup(
                {"nodes": changed, "duplicates": []},
                source, first, first)

    def test_no_source_or_h1_mutation(self):
        source, first = sample()
        originals = deepcopy((source, first))
        validate_protected_followup({"nodes": first, "duplicates": []},
                                    source, first, first)
        self.assertEqual((source, first), originals)


if __name__ == "__main__":
    unittest.main()
