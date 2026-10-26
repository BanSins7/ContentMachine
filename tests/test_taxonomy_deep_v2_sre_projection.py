"""RTB FIX-07 pure publication/SRE projection contracts, no PostgreSQL/AI."""
from __future__ import annotations

from copy import deepcopy
from uuid import UUID
import unittest

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.taxonomy_builder.deep_v2_sre_bridge import (
    prepare_deep_v2_sre_projection, stable_sre_category_uuid,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.research_contracts_v2 import (
    b9_fact_sre_uuid, project_v2_tree_to_v1_core,
)
from test_taxonomy_deep_v2_validation import sample


class DeepV2SREProjection(unittest.TestCase):
    def test_full_tree_and_exact_11_field_core_without_mutation(self):
        source, nodes = sample()
        original = deepcopy((source, nodes))
        data = prepare_deep_v2_sre_projection(nodes, source)
        self.assertEqual((source, nodes), original)
        self.assertEqual(data["v2_tree"], nodes)
        self.assertTrue(all(len(n) == 14 for n in data["v2_tree"]))
        self.assertTrue(all(len(n) == 11 for n in data["v1_core"]))
        self.assertEqual(data["v1_core"], project_v2_tree_to_v1_core(nodes))
        self.assertEqual(data["v2_tree_sha256"], canonical_sha(nodes))
        self.assertEqual(data["v1_core_sha256"], canonical_sha(data["v1_core"]))
        self.assertEqual(data["sre_topics_sha256"], canonical_sha(data["topics"]))
        self.assertEqual(data["research_input_sha256"], source["research_input_sha256"])
        self.assertEqual(data["provider_calls"], 0)

    def test_fact_sha_mapping_reuses_same_project_stable_uuid(self):
        source, nodes = sample()
        projected = prepare_deep_v2_sre_projection(nodes, source)
        for fact in projected["facts"]:
            stable = b9_fact_sre_uuid(source["project_id"], fact["b9_fact_sha256"])
            self.assertEqual(fact["id"], stable)
            self.assertEqual(source["b9_fact_sha_to_sre_uuid"]
                             [fact["b9_fact_sha256"]], stable)
            self.assertEqual(str(UUID(stable)), stable)
        self.assertEqual(projected["fact_mapping"], source["b9_fact_sha_to_sre_uuid"])
        self.assertEqual(projected, prepare_deep_v2_sre_projection(nodes, source))

    def test_every_sre_category_has_stable_project_key_and_original_parent(self):
        source, nodes = sample()
        output = prepare_deep_v2_sre_projection(nodes, source)
        for node, topic in zip(nodes, output["topics"]):
            self.assertEqual(
                topic["id"], stable_sre_category_uuid(source["project_id"], node["id"]))
            self.assertEqual(topic["source_node_id"], node["id"])
            self.assertEqual(topic["parent_id"], node["parent_id"])
            self.assertEqual(topic["kind"], node["kind"])
            self.assertEqual(topic["relation"], node["relation"])
            self.assertEqual(topic["search_signals"], node["search_signals"])
            self.assertEqual(topic["validation_focus"], node["validation_focus"])
            self.assertEqual(topic["source_observation_ids"],
                             node["source_observation_ids"])
            self.assertEqual(topic["positive_examples"], node["examples"])
            self.assertEqual(topic["negative_examples"], [])
            self.assertEqual(topic["independent"], not bool(node["source_fact_ids"]))

    def test_video_observation_is_only_search_hint_never_fact_or_verified_video(self):
        source, nodes = sample()
        data = prepare_deep_v2_sre_projection(nodes, source)
        obs = {v["observation_id"] for v in source["c2_original_video_observations"]}
        self.assertTrue(obs)
        self.assertTrue(any(set(t["source_observation_ids"]) & obs
                            for t in data["topics"]))
        fact_ids = {f["id"] for f in data["facts"]}
        self.assertFalse(obs & fact_ids)
        for topic in data["topics"]:
            self.assertTrue(set(topic["fact_ids"]).issubset(fact_ids))
            self.assertEqual(topic["negative_examples"], [])

    def test_changed_forged_b9_fact_uuid_is_rejected(self):
        source, nodes = sample()
        bad = deepcopy(source)
        bad["c1_approved_facts"][0]["rtb_fact_id"] = "00000000-0000-4000-8000-000000000000"
        bad["research_input_sha256"] = canonical_sha({
            k:v for k,v in bad.items() if k != "research_input_sha256"
        })
        with self.assertRaises(PipelineError):
            prepare_deep_v2_sre_projection(nodes, bad)

    def test_foreign_or_missing_a_observation_cannot_enter_sre(self):
        source, nodes = sample()
        bad = deepcopy(nodes)
        bad[1]["source_observation_ids"] = ["foreign-evidence"]
        with self.assertRaises(PipelineError):
            prepare_deep_v2_sre_projection(bad, source)

    def test_missing_source_quote_is_not_invented_by_adapter(self):
        source, nodes = sample()
        bad = deepcopy(source)
        bad["c1_approved_facts"][0]["citations"] = []
        bad["research_input_sha256"] = canonical_sha({
            k:v for k,v in bad.items() if k != "research_input_sha256"
        })
        with self.assertRaises(PipelineError):
            prepare_deep_v2_sre_projection(nodes, bad)

    def test_project_specific_node_identity_never_crosses_projects(self):
        self.assertNotEqual(
            stable_sre_category_uuid("a"*32, "hypothesis_one"),
            stable_sre_category_uuid("b"*32, "hypothesis_one"))
        self.assertEqual(
            stable_sre_category_uuid("a"*32, "hypothesis_one"),
            stable_sre_category_uuid("a"*32, "hypothesis_one"))

if __name__ == "__main__":
    unittest.main()
