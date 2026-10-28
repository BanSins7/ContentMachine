"""RTB FIX-08: versioned publication DTO, legacy SP V1 consumer simulation.

Only pure source fixtures; no database, no paid network, no A/B/SP/SRE writes.
"""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4
import unittest

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.taxonomy_builder.deep_v2_sre_bridge import (
    prepare_deep_v2_sre_projection,
)
from intelligence.taxonomy_builder.publication_export_v2 import (
    SCHEMA_V1, SCHEMA_V2,
    build_publication_export, to_search_planner_v1_snapshot,
    validate_publication_export, to_sre_taxonomy_reference,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.search_planner.contracts import (
    validate_published_snapshot,
)
from intelligence.search_planner.context_builder import (
    build_hypothesis_contexts,
)
from test_taxonomy_deep_v2_validation import sample


def fixture():
    source, nodes = sample()
    projection = prepare_deep_v2_sre_projection(nodes, source)
    publication = {
        **{
            key: projection[key]
            for key in (
                "research_input_sha256", "v2_tree", "v2_tree_sha256",
                "v1_core", "v1_core_sha256", "fact_mapping",
                "sre_topics_sha256",
            )
        },
        "publication_id": str(uuid4()),
        "project_id": source["project_id"],
        "run_id": str(uuid4()),
        "status": "published",
        "sre_taxonomy_version_id": 21,
        "readiness_report_sha256": "b" * 64,
    }
    return source, nodes, publication


def export_v1():
    source, _, pub = fixture()
    return build_publication_export(
        pub, source, accepted_versions=[SCHEMA_V1],
    )


class RTBV2PublicationExportPure(unittest.TestCase):
    def test_legacy_export_has_exactly_11_fields_and_no_video_evidence(self):
        source, nodes, pub = fixture()
        before = deepcopy((source, nodes, pub))
        export = build_publication_export(
            pub, source, accepted_versions=[SCHEMA_V1])
        self.assertEqual(export["schema_version"], SCHEMA_V1)
        self.assertEqual(export["nodes"], pub["v1_core"])
        self.assertTrue(all(len(n) == 11 for n in export["nodes"]))
        self.assertNotIn("original_a_observations", export)
        self.assertEqual(export["provider_calls"], 0)
        self.assertEqual((source, nodes, pub), before)
        self.assertEqual(validate_publication_export(export), export)

    def test_v2_export_keeps_14_keys_and_original_a_references(self):
        source, nodes, pub = fixture()
        dto = build_publication_export(
            pub, source, accepted_versions=[SCHEMA_V2])
        self.assertEqual(dto["schema_version"], SCHEMA_V2)
        self.assertEqual(dto["nodes"], nodes)
        self.assertTrue(all(len(n) == 14 for n in dto["nodes"]))
        self.assertEqual(
            dto["original_a_observations"],
            source["c2_original_video_observations"],
        )
        self.assertTrue(
            dto["source_lineage"]["observations_are_original_a_not_approved_product_facts"]
        )
        self.assertEqual(dto["tree_sha256"], pub["v2_tree_sha256"])
        self.assertEqual(dto["v1_core_sha256"],pub["v1_core_sha256"])
        self.assertEqual(validate_publication_export(dto), dto)

    def test_explicit_version_preference_never_downgrades_implicitly(self):
        source, _, pub = fixture()
        dto = build_publication_export(
            pub, source, accepted_versions=[SCHEMA_V2, SCHEMA_V1])
        self.assertEqual(dto["schema_version"], SCHEMA_V2)
        dto = build_publication_export(
            pub, source, accepted_versions=[SCHEMA_V1, SCHEMA_V2])
        self.assertEqual(dto["schema_version"], SCHEMA_V1)
        for versions in (
            None, [], ["unknown"], ["rtb-publication-export/v3"],
            [SCHEMA_V1, SCHEMA_V1], [SCHEMA_V1, ""],
        ):
            with self.subTest(versions=versions), self.assertRaises(PipelineError):
                build_publication_export(
                    pub, source, accepted_versions=versions)

    def test_existing_search_planner_v1_contract_accepts_adapter(self):
        dto = export_v1()
        snapshot = to_search_planner_v1_snapshot(dto)
        verified = validate_published_snapshot(snapshot)
        self.assertEqual(verified["tree"], dto["nodes"])
        self.assertEqual(verified["taxonomy_publication_id"],
                         dto["publication_id"])
        self.assertEqual(verified["rtb_run_id"], dto["run_id"])
        self.assertEqual(verified["taxonomy_sha256"],
                         canonical_sha(dto["nodes"]))
        self.assertEqual(verified["human_reviews"], [1,2])

    def test_existing_search_planner_builds_full_hypothesis_context(self):
        dto = export_v1()
        legacy = to_search_planner_v1_snapshot(dto)
        contexts = build_hypothesis_contexts(
            legacy, {
                "languages": ["en"],
                "markets": [],
                "allowed_modes": ["keyword", "hashtag"],
                "target_candidates_per_hypothesis": 1,
                "max_candidates_per_hypothesis": 4,
                "max_total_candidates": 40,
            },
        )
        self.assertGreaterEqual(len(contexts), 2)
        for c in contexts:
            self.assertEqual(len(c["hypothesis"]), 11)
            self.assertEqual(c["taxonomy_publication_id"],
                             dto["publication_id"])
            self.assertEqual(c["project_context"]["research_goal"], None)
            self.assertEqual(c["search_policy"]["languages"], ["en"])
            self.assertEqual(c["derived_search_facets"], [])

    def test_change_in_any_11_or_14_fields_breaks_checksum(self):
        source, _, pub = fixture()
        for version in (SCHEMA_V1, SCHEMA_V2):
            dto = build_publication_export(
                pub, source, accepted_versions=[version])
            for name in ("name","definition","include","source_fact_ids"):
                bad = deepcopy(dto)
                bad["nodes"][1][name] = (
                    ["not-approved"] if name == "source_fact_ids" else
                    "quietly altered approved data"
                )
                with self.subTest(version=version,field=name), self.assertRaises(PipelineError):
                    validate_publication_export(bad)

    def test_rejected_video_evidence_and_wrong_fact_mapping_fail_closed(self):
        source, _, pub = fixture()
        v2 = build_publication_export(
            pub, source, accepted_versions=[SCHEMA_V2])
        v2["nodes"][1]["source_observation_ids"] = ["forged"]
        with self.assertRaises(PipelineError):
            validate_publication_export(v2)
        v1 = build_publication_export(
            pub, source, accepted_versions=[SCHEMA_V1])
        v1["source_lineage"]["b9_fact_sha_to_sre_uuid"] = {}
        with self.assertRaisesRegex(PipelineError, "mapping_mismatch"):
            validate_publication_export(v1)

    def test_nontext_a8_id_is_rejected_as_pipeline_error(self):
        source, _, pub = fixture()
        body = build_publication_export(
            pub,source,accepted_versions=[SCHEMA_V2])
        body["original_a_observations"][0]["observation_id"] = {"fake": "id"}
        with self.assertRaises(PipelineError):
            validate_publication_export(body)
        body = build_publication_export(
            pub,source,accepted_versions=[SCHEMA_V2])
        body["nodes"][1]["source_observation_ids"] = [{"fake": "id"}]
        with self.assertRaises(PipelineError):
            validate_publication_export(body)

    def test_sp_v1_context_capacity_never_truncates_approved_b9_text(self):
        dto = export_v1()
        large = deepcopy(dto)
        large["approved_facts"][0]["text"] = "x" * 2600
        large["export_sha256"] = canonical_sha({
            k:v for k,v in large.items() if k!="export_sha256"
        })
        self.assertEqual(validate_publication_export(large),large)
        with self.assertRaisesRegex(PipelineError, "capacity_exceeded"):
            to_search_planner_v1_snapshot(large)
        large = deepcopy(dto)
        large["profile_snapshot"]["product_functions"] = ["x"*301]
        large["export_sha256"] = canonical_sha({
            k:v for k,v in large.items() if k!="export_sha256"
        })
        with self.assertRaisesRegex(PipelineError,"capacity_exceeded"):
            to_search_planner_v1_snapshot(large)
        self.assertEqual(to_search_planner_v1_snapshot(dto)["tree"],dto["nodes"])

    def test_nonapproved_snapshot_and_human_gate_missing_rejected(self):
        source, _, pub = fixture()
        for bad in ("review1", "approved", "uncertain"):
            forged = deepcopy(pub)
            forged["status"] = bad
            with self.subTest(status=bad), self.assertRaises(PipelineError):
                build_publication_export(
                    forged, source, accepted_versions=[SCHEMA_V1])
        for reviews in ([1], [], [2,1], [1,1]):
            with self.subTest(reviews=reviews), self.assertRaises(PipelineError):
                build_publication_export(
                    pub, source, accepted_versions=[SCHEMA_V2],
                    human_review_numbers=reviews)

    def test_sre_reference_uses_existing_approved_version_without_writes(self):
        source, _, pub = fixture()
        pointers = []
        for version in (SCHEMA_V1, SCHEMA_V2):
            dto = build_publication_export(
                pub, source, accepted_versions=[version])
            ref = to_sre_taxonomy_reference(dto)
            self.assertEqual(ref["schema_version"],
                             "rtb-sre-taxonomy-reference/v1")
            self.assertEqual(ref["sre_taxonomy_version_id"], 21)
            self.assertEqual(ref["no_sre_writes"], True)
            self.assertEqual(ref["category_count"], len(dto["nodes"]))
            self.assertEqual(ref["b9_fact_sha_to_sre_uuid"],
                             source["b9_fact_sha_to_sre_uuid"])
            self.assertEqual(ref["reference_sha256"], canonical_sha({
                k:v for k,v in ref.items() if k!="reference_sha256"
            }))
            pointers.append(ref)
        self.assertEqual(pointers[0], pointers[1])

    def test_profile_is_only_approved_b9_never_infers_market_or_goal(self):
        source, _, pub = fixture()
        dto = build_publication_export(
            pub, source, accepted_versions=[SCHEMA_V1])
        profile = dto["profile_snapshot"]
        self.assertEqual(profile["target_audience"], None)
        self.assertEqual(profile["research_goal"], None)
        self.assertNotIn("market", profile)
        self.assertNotIn("language", profile)
        self.assertEqual(
            dto["source_lineage"]["b9_snapshot_sha256"],
            source["b9_snapshot_sha256"],
        )
        self.assertEqual(
            dto["source_lineage"]["a_context_sha256"],
            source["a_context_sha256"],
        )

    def test_project_mismatch_and_forged_approved_facts_fail_closed(self):
        source, _, pub = fixture()
        forged = deepcopy(pub)
        forged["project_id"] = "b"*32
        with self.assertRaises(PipelineError):
            build_publication_export(
                forged, source, accepted_versions=[SCHEMA_V2])
        dto = build_publication_export(
            pub, source, accepted_versions=[SCHEMA_V1])
        dto["approved_facts"][0]["text"] = "invented outcome"
        with self.assertRaises(PipelineError):
            validate_publication_export(dto)

    def test_checksum_is_reproducible_and_ignores_external_mutations(self):
        source, _, pub = fixture()
        first = build_publication_export(pub,source,accepted_versions=[SCHEMA_V2])
        second = build_publication_export(pub,source,accepted_versions=[SCHEMA_V2])
        self.assertEqual(first, second)
        self.assertEqual(
            first["export_sha256"],
            canonical_sha({
                k:v for k,v in first.items() if k != "export_sha256"
            }),
        )

if __name__ == "__main__":
    unittest.main()
