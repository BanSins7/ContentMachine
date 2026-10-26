"""Offline Deep V2 strict grounded validation; no provider/database."""
from __future__ import annotations

from copy import deepcopy
import unittest

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.taxonomy_builder.deep_contracts_v2 import (
    validate_deep_v2_tree, validate_deep_v2_response,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from test_taxonomy_research_input import source_fixture, build


def sample():
    source = build(*source_fixture())
    function = next(x for x in source["c1_approved_facts"] if x["kind"] == "function")
    problem = next(x for x in source["c1_approved_facts"] if x["kind"] == "problem")
    observation = source["c2_original_video_observations"][0]["observation_id"]

    def node(ident, parent, kind, relation, source_id):
        return {
            "id": ident, "parent_id": parent, "kind": kind, "relation": relation,
            "source_fact_ids": [source_id],
            "name": ident.replace("_", " ").title(),
            "rationale": "Research this observable content format",
            "definition": "A specific video category with clear inclusion boundaries",
            "include": "Videos covering this practical example",
            "exclude": "General videos not demonstrating this example",
            "examples": ["A concrete short video example"],
            "source_observation_ids": [],
            "search_signals": [],
            "validation_focus": [],
        }

    nodes = [
        node("direction_one", None, "direction", "direct", function["rtb_fact_id"]),
        node("hypothesis_one", "direction_one", "hypothesis", "direct",
             function["rtb_fact_id"]),
        node("direction_two", None, "direction", "problem_related",
             problem["rtb_fact_id"]),
        node("hypothesis_two", "direction_two", "hypothesis", "problem_related",
             problem["rtb_fact_id"]),
    ]
    for n in nodes:
        if n["kind"] == "hypothesis":
            n["search_signals"] = ["Comparison-based hook"]
            n["validation_focus"] = ["Check hook evidence and format"]
    nodes[1]["source_observation_ids"] = [observation]
    return source, nodes


def resign(source):
    source["research_input_sha256"] = canonical_sha({
        k: v for k, v in source.items() if k != "research_input_sha256"
    })
    return source


class DeepV2Validation(unittest.TestCase):

    def test_valid_tree_preserves_fourteen_fields(self):
        inp, nodes = sample()
        original = deepcopy(nodes)
        validated = validate_deep_v2_tree(nodes, inp)
        self.assertEqual(validated, nodes)
        self.assertEqual(nodes, original)
        self.assertTrue(all(len(n) == 14 for n in validated))
        self.assertEqual(validate_deep_v2_response({"nodes": nodes}, inp, "deep_first"),
                         {"nodes": nodes})

    def test_additional_missing_or_invalid_extensions_rejected(self):
        inp, nodes = sample()
        for mutate in (
            lambda ns: ns[0].update({"new_flag": True}),
            lambda ns: ns[1].pop("search_signals"),
            lambda ns: ns[1].update({"search_signals": []}),
            lambda ns: ns[1].update({"validation_focus": []}),
            lambda ns: ns[1].update({"source_observation_ids": ["/invented/time:00"]}),
            lambda ns: ns[0].update({"search_signals": ["not a hypothesis"]}),
            lambda ns: ns[2].update({"source_observation_ids":
                                    ns[1]["source_observation_ids"]}),
        ):
            with self.subTest(mutate=str(mutate)):
                changed = deepcopy(nodes)
                mutate(changed)
                with self.assertRaises(PipelineError):
                    validate_deep_v2_tree(changed, inp)

    def test_hypotheses_must_have_unique_concrete_signals(self):
        inp, nodes = sample()
        changed = deepcopy(nodes)
        changed[1]["search_signals"] = ["Compare real clips", "compare real clips"]
        with self.assertRaisesRegex(PipelineError, "search_signals_duplicate"):
            validate_deep_v2_tree(changed, inp)
        changed = deepcopy(nodes)
        changed[1]["validation_focus"] = [" "]
        with self.assertRaises(PipelineError):
            validate_deep_v2_tree(changed, inp)

    def test_direct_claim_must_come_from_approved_own_product_fact(self):
        inp, nodes = sample()
        fraud = deepcopy(inp)
        first = next(x for x in fraud["c1_approved_facts"] if x["kind"] == "function")
        first["source_role"] = "competitor"
        first["may_support_own_product_claim"] = False
        with self.assertRaisesRegex(PipelineError, "unapproved_direct_product_claim"):
            validate_deep_v2_tree(nodes, resign(fraud))

    def test_legacy_graph_rules_reject_bad_parent_and_fake_facts(self):
        inp, nodes = sample()
        changed = deepcopy(nodes)
        changed[1]["parent_id"] = "invented_parent"
        with self.assertRaises(PipelineError):
            validate_deep_v2_tree(changed, inp)
        changed = deepcopy(nodes)
        changed[1]["source_fact_ids"] = ["fake_sha_or_uuid"]
        with self.assertRaises(PipelineError):
            validate_deep_v2_tree(changed, inp)

    def test_second_pass_duplicate_claims_are_checked(self):
        inp, nodes = sample()
        output = {"nodes": nodes, "duplicates": [
            {"a": "direction_one", "b": "direction_two",
             "reason": "Two overlapping research formats"},
        ]}
        self.assertEqual(validate_deep_v2_response(output, inp, "deep_second"), output)
        changed = deepcopy(output)
        changed["duplicates"][0]["b"] = "invented"
        with self.assertRaises(PipelineError):
            validate_deep_v2_response(changed, inp, "deep_second")
        changed = deepcopy(output)
        changed["duplicates"].append(deepcopy(output["duplicates"][0]))
        with self.assertRaisesRegex(PipelineError, "duplicate_claim_repeated"):
            validate_deep_v2_response(changed, inp, "deep_second")

    def test_role_specific_response_has_no_extra_keys(self):
        inp, nodes = sample()
        with self.assertRaises(PipelineError):
            validate_deep_v2_response({"nodes": nodes, "duplicates": []},
                                      inp, "deep_first")
        with self.assertRaises(PipelineError):
            validate_deep_v2_response({"nodes": nodes}, inp, "deep_second")
        with self.assertRaises(PipelineError):
            validate_deep_v2_response({"nodes": nodes}, inp, "unknown")


if __name__ == "__main__":
    unittest.main()
