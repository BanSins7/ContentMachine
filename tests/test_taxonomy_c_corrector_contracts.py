"""Offline C-FIX-00 contract tests: two B9 facts, two original A observations,
and a three-level RTB hierarchy. Never contacts PostgreSQL or OpenRouter.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import unittest
from uuid import uuid4

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.knowledge_extraction.knowledge_snapshot import build_c_handoff, build_snapshot
from intelligence.taxonomy_builder.deep_contracts import validate_tree, NODE_PROPERTIES
from test_ke_stage13_knowledge_snapshot import pure_fixture as b9_source_fixture
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.research_contracts_v2 import (
    b9_fact_sre_uuid, checked_b9_mapping, index_original_a_observations,
    project_v2_tree_to_v1_core,
)

PROJECT = "a" * 32
FOREIGN_PROJECT = "b" * 32
def fixture():
    """Two facts genuinely signed by B9 build_snapshot(), plus pinned A evidence."""
    args = b9_source_fixture()
    original_group = args["submission"]["approved_groups"][0]
    second = deepcopy(original_group)
    second.update({
        "group_id": "8" * 64, "kind": "problem",
        "reviewed_text": "Аудитория сравнивает методы",
        "original_text": "Аудитория сравнивает методы",
        "source_role": "audience_research",
        "subject_scope": "audience_research",
        "subject_id": "audience-1",
        "review_event_sha256": "8" * 64,
        "proposal_ids": ["9" * 64],
    })
    cite = second["citations"][0]
    cite["proposal_id"] = "9" * 64
    cite["source_quote"] = "Аудитория сравнивает методы"
    cite["original_provenance"].update({
        "quote_sha256": sha256(cite["source_quote"].encode("utf-8")).hexdigest(),
        "source_role": "audience_research",
        "subject_scope": "audience_research",
        "subject_id": "audience-1",
    })
    args["submission"]["approved_groups"].append(second)
    args["submission"]["approved_count"] = 2
    args["quality_report"]["counts"]["unique_groups"] = 2

    context_id, output_id, source_id, version_id = (str(uuid4()) for _ in range(4))
    evidence = {
        "speech:job1:seg00001": {
            "kind": "asr_segment",
            "locator": {"start_ms": 0, "end_ms": 1500},
            "exact_text": "Compare these two techniques",
            "trust": "measured_transcription",
            "source_job_id": "job1",
        },
        "model_vision:job2:sc001:f001": {
            "kind": "model_visual_description",
            "locator": {"time_ms": 800, "frame_id": "f001"},
            "exact_text": "The presenter points to a comparison",
            "trust": "model_reported_unverified",
            "needs_review": True,
            "source_job_id": "job2",
        },
    }
    original = {
        "schema_version": "video-intelligence/v1",
        "project_id": PROJECT, "source_id": source_id,
        "video_source_version_id": version_id, "evidence": evidence,
    }
    pin = {
        "source_id": source_id, "source_version_id": version_id,
        "output_id": output_id, "output_sha256": canonical_sha(original),
        "schema_version": "video-intelligence/v1",
    }
    args["bundle"]["video_analysis_refs"] = [pin]
    args["a_video_refs"] = [deepcopy(pin)]
    args["quality_report"]["counts"]["video_refs_available"] = 1
    args["submission"]["gate_report_sha256"] = canonical_sha(args["quality_report"])
    outcome = build_snapshot(**args)
    snapshot = outcome["snapshot"]
    handoff = build_c_handoff(snapshot, str(uuid4()), outcome["snapshot_sha256"])
    return snapshot, handoff, pin, original


def _node(identifier, parent, kind, relation, facts):
    return {
        "id": identifier, "parent_id": parent, "kind": kind,
        "relation": relation, "source_fact_ids": facts,
        "name": identifier.replace("_", " "),
        "rationale": "Explore distinguishable short-form formats",
        "definition": "Videos within a defined research category",
        "include": "Concrete comparisons and demonstrations",
        "exclude": "Unrelated generic videos",
        "examples": [],
    }


class Stage01ResearchContracts(unittest.TestCase):
    def test_b9_handoff_maps_two_approved_facts_without_modifying_inputs(self):
        snapshot, handoff, _, _ = fixture()
        before = deepcopy(snapshot)
        actual = checked_b9_mapping(snapshot, handoff)
        self.assertEqual(actual["knowledge_snapshot_id"], handoff["snapshot_id"])
        self.assertEqual(actual["project_source_snapshot_id"], snapshot["context_id"])
        self.assertEqual(set(actual["b9_fact_sha_to_sre_uuid"]), {f["fact_id"] for f in snapshot["facts"]})
        self.assertEqual(snapshot, before)
        self.assertEqual(actual, checked_b9_mapping(snapshot, handoff))

    def test_b9_fact_uuid_is_stable_and_isolated_by_project(self):
        snapshot, _, _, _ = fixture()
        first, second = (f["fact_id"] for f in snapshot["facts"])
        one = b9_fact_sre_uuid(PROJECT, first)
        self.assertEqual(one, b9_fact_sre_uuid(PROJECT, first))
        self.assertNotEqual(one, b9_fact_sre_uuid(PROJECT, second))
        self.assertNotEqual(one, b9_fact_sre_uuid(FOREIGN_PROJECT, first))
        self.assertEqual(one, str(__import__("uuid").UUID(one)))
        with self.assertRaises(PipelineError):
            b9_fact_sre_uuid(PROJECT, "not-a-sha")

    def test_changed_handoff_and_unapproved_fact_are_blocked(self):
        snapshot, handoff, _, _ = fixture()
        broken = deepcopy(handoff)
        broken["facts"][0]["text"] = "tampered claim"
        with self.assertRaises(PipelineError):
            checked_b9_mapping(snapshot, broken)
        snapshot["facts"][0]["review_status"] = "rejected"
        with self.assertRaises(PipelineError):
            checked_b9_mapping(snapshot, handoff)

    def test_b9_signatures_reject_rewritten_fact_id_even_when_resigned(self):
        snapshot, handoff, _, _ = fixture()
        manipulated = deepcopy(snapshot)
        manipulated["facts"][0]["fact_id"] = "0" * 64
        forged = build_c_handoff(
            manipulated, handoff["snapshot_id"], canonical_sha(manipulated)
        )
        with self.assertRaisesRegex(PipelineError, "c_b9_fact_identity_mismatch"):
            checked_b9_mapping(manipulated, forged)

    def test_b9_snapshot_metadata_is_not_optional(self):
        snapshot, handoff, _, _ = fixture()
        for corrupt in (
            lambda s: s.pop("builder_version"),
            lambda s: s.update({"mimo_calls": 1}),
            lambda s: s.update({"objective_truth_guaranteed": True}),
            lambda s: s.update({"human_review_status": "pending"}),
        ):
            with self.subTest(corrupt=str(corrupt)):
                tampered = deepcopy(snapshot)
                corrupt(tampered)
                # Independent of the C handoff digest, an invalid B9 envelope is rejected.
                with self.assertRaises(PipelineError):
                    checked_b9_mapping(tampered, handoff)

    def test_original_a_evidence_remains_separate_and_identical(self):
        _, _, pin, original = fixture()
        before = deepcopy(original)
        observations = index_original_a_observations(
            PROJECT, [pin], {pin["output_id"]: original}
        )
        self.assertEqual(len(observations), 2)
        self.assertEqual(
            {x["a_evidence_id"] for x in observations.values()},
            set(original["evidence"]),
        )
        for record in observations.values():
            self.assertEqual(record["original_evidence"], original["evidence"][record["a_evidence_id"]])
            self.assertEqual(record["output_sha256"], pin["output_sha256"])
        self.assertEqual(original, before)

    def test_foreign_tampered_missing_video_rejected(self):
        _, _, pin, original = fixture()
        with self.assertRaises(PipelineError):
            index_original_a_observations(FOREIGN_PROJECT, [pin], {pin["output_id"]: original})
        tampered = deepcopy(original)
        tampered["evidence"]["speech:job1:seg00001"]["exact_text"] = "changed"
        with self.assertRaises(PipelineError):
            index_original_a_observations(PROJECT, [pin], {pin["output_id"]: tampered})
        with self.assertRaises(PipelineError):
            index_original_a_observations(PROJECT, [pin], {})
        self.assertEqual(index_original_a_observations(PROJECT, [], {}), {})

    def test_three_level_v2_drops_only_extensions_in_legacy_projection(self):
        snapshot, _, _, _ = fixture()
        ids = {fact["kind"]: fact["fact_id"] for fact in snapshot["facts"]}
        f1 = b9_fact_sre_uuid(PROJECT, ids["function"])
        f2 = b9_fact_sre_uuid(PROJECT, ids["problem"])
        facts = [{"id": f1, "kind": "function", "text": "Approved function"},
                 {"id": f2, "kind": "problem", "text": "Approved problem"}]
        nodes = [
            _node("direction_one", None, "direction", "direct", [f1]),
            _node("branch_one", "direction_one", "branch", "direct", [f1]),
            _node("hypothesis_one", "branch_one", "hypothesis", "direct", [f1]),
            _node("direction_two", None, "direction", "problem_related", [f2]),
            _node("hypothesis_two", "direction_two", "hypothesis", "problem_related", [f2]),
        ]
        v2 = [
            {**n,
             "source_observation_ids": [] if n["kind"] != "hypothesis" else ["a-original-id"],
             "search_signals": [] if n["kind"] != "hypothesis" else ["comparison format"],
             "validation_focus": [] if n["kind"] != "hypothesis" else ["hook"]}
            for n in nodes
        ]
        result = project_v2_tree_to_v1_core(v2)
        self.assertEqual(result, nodes)
        self.assertEqual(len(NODE_PROPERTIES), 11)
        self.assertEqual(validate_tree(result, facts), nodes)
        self.assertEqual(v2[2]["search_signals"], ["comparison format"])
        self.assertEqual(v2[2]["source_observation_ids"], ["a-original-id"])
        with self.assertRaises(PipelineError):
            project_v2_tree_to_v1_core([{"id": "missing"}])


if __name__ == "__main__":
    unittest.main()
