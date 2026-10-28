"""RTB Deep V2 -> Search Planner: real signed DTO semantics, no payments."""
from __future__ import annotations

from copy import deepcopy
import unittest

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.taxonomy_builder.publication_export_v2 import (
    SCHEMA_V2, build_publication_export,
)
from intelligence.search_planner.integration.rtb_v2_pipeline import (
    SOURCE_V2, build_v2_contexts, verified_v2_export,
)
from intelligence.search_planner.generation_engine.batch_manager import make_batches
from test_taxonomy_publication_export_v2 import fixture


POLICY = {
    "languages": ["en"], "markets": [], "allowed_modes": ["keyword", "hashtag"],
    "target_candidates_per_hypothesis": 1,
    "max_candidates_per_hypothesis": 4, "max_total_candidates": 40,
}


def approved_export():
    source, nodes, pub = fixture()
    return build_publication_export(pub, source, accepted_versions=[SCHEMA_V2])


class RTBV2PipelineTests(unittest.TestCase):
    def test_source_pins_exact_v2_export_and_full_nodes(self):
        export = approved_export()
        requested = []
        def read(pid, pub, *, accepted_versions):
            requested.append((pid, pub, accepted_versions))
            return deepcopy(export)
        verified = verified_v2_export(
            export["project_id"], export["publication_id"], supplier=read
        )
        self.assertEqual(verified, export)
        self.assertEqual(requested, [(
            export["project_id"], export["publication_id"], [SCHEMA_V2],
        )])
        source, contexts = build_v2_contexts(verified, POLICY)
        self.assertEqual(source["publication_source"], SOURCE_V2)
        self.assertEqual(source["rtb_export_sha256"], export["export_sha256"])
        self.assertEqual(source["taxonomy_sha256"], export["tree_sha256"])
        self.assertTrue(contexts)
        by_id = {n["id"]: n for n in export["nodes"]}
        for c in contexts:
            self.assertEqual(c["context_schema_version"], "hypothesis-context/v2")
            self.assertEqual(c["taxonomy_sha256"], export["tree_sha256"])
            self.assertEqual(len(c["hypothesis"]), 14)
            self.assertEqual(c["hypothesis"], by_id[c["hypothesis"]["id"]])
            self.assertEqual(c["direction"], by_id[c["direction"]["id"]])
            if c["branch"] is not None:
                self.assertEqual(c["branch"], by_id[c["branch"]["id"]])
            provenance = c["rtb_deep_v2_provenance"]
            self.assertEqual(provenance["export_sha256"], export["export_sha256"])
            self.assertEqual(provenance["source_lineage"], export["source_lineage"])
            self.assertTrue(provenance["original_a_observations_are_not_approved_product_facts"])
            for observation in provenance["original_a_observations"]:
                self.assertIn(observation, export["original_a_observations"])
        # RTB's publisher and the Search Planner builder never mutate input.
        self.assertEqual(verified, export)

    def test_rejects_changed_sha_and_foreign_project(self):
        export = approved_export()
        altered = deepcopy(export)
        altered["nodes"][0]["name"] = "tampered"
        for bad in (altered, {**export, "export_sha256": "f"*64}):
            with self.assertRaises(PipelineError):
                verified_v2_export(export["project_id"], export["publication_id"],
                                   supplier=lambda *a, **k: bad)
        with self.assertRaises(PipelineError):
            verified_v2_export("f"*32, export["publication_id"],
                               supplier=lambda *a, **k: export)

    def test_preserves_policy_and_original_evidence_across_contexts(self):
        export = approved_export()
        source, contexts = build_v2_contexts(export, POLICY)
        for context in contexts:
            self.assertEqual(context["search_policy"], POLICY)
            ids = {o["observation_id"] for o in
                   context["rtb_deep_v2_provenance"]["original_a_observations"]}
            expected = set()
            nodes = {n["id"]: n for n in export["nodes"]}
            for nid in context["ancestry_path"]:
                expected.update(nodes[nid]["source_observation_ids"])
            self.assertEqual(ids, expected)
            facts = {f["id"] for f in context["approved_facts"]}
            self.assertEqual(facts, {
                f["id"] for f in context["rtb_deep_v2_provenance"]["source_fact_lineage"]
            })

    def test_context_not_silent_11_field_projection(self):
        export = approved_export()
        _, contexts = build_v2_contexts(export, POLICY)
        for context in contexts:
            for key in ("hypothesis", "direction"):
                for v2_field in (
                    "source_observation_ids", "search_signals", "validation_focus"
                ):
                    self.assertIn(v2_field, context[key])


if __name__ == "__main__":
    unittest.main()
