"""Offline C-FIX-00 contract tests: two B9 facts, two original A observations,
and a three-level RTB hierarchy. Never contacts PostgreSQL or OpenRouter.
"""
from __future__ import annotations

from copy import deepcopy
import unittest
from uuid import uuid4

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.knowledge_extraction.knowledge_snapshot import build_c_handoff
from intelligence.taxonomy_builder.deep_contracts import validate_tree, NODE_PROPERTIES
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.research_contracts_v2 import (
    b9_fact_sre_uuid, checked_b9_mapping, index_original_a_observations,
    project_v2_tree_to_v1_core,
)

PROJECT = "a" * 32
FOREIGN_PROJECT = "b" * 32
FACT_ONE = "1" * 64
FACT_TWO = "2" * 64


def fixture():
    context_id, output_id, source_id, version_id = (str(uuid4()) for _ in range(4))
    evidence = {
        "speech:job1:seg00001": {
            "kind": "asr_segment",
            "locator": {"start_ms": 0, "end_ms": 1500},
            "exact_text": "Compare these two techniques",
            "trust": "measured_transcription",
        },
        "model_vision:job2:sc001:f001": {
            "kind": "model_visual_description",
            "locator": {"time_ms": 800, "frame_id": "f001"},
            "exact_text": "The presenter points to a comparison",
            "trust": "model_reported_unverified",
            "needs_review": True,
        },
    }
    original = {
        "schema_version": "video-intelligence/v1",
        "project_id": PROJECT,
        "source_id": source_id,
        "video_source_version_id": version_id,
        "evidence": evidence,
    }
    pin = {
        "source_id": source_id,
        "source_version_id": version_id,
        "output_id": output_id,
        "output_sha256": canonical_sha(original),
        "schema_version": "video-intelligence/v1",
    }
    facts = [
        {
            "fact_id": FACT_ONE, "kind": "function",
            "reviewed_text": "A human-approved product capability",
            "source_role": "own_product", "subject_scope": "own_product",
            "subject_id": "product-1", "review_status": "human_approved",
            "citations": [{"source_quote": "A human-approved product capability"}],
        },
        {
            "fact_id": FACT_TWO, "kind": "problem",
            "reviewed_text": "Users want to compare alternatives",
            "source_role": "audience_research",
            "subject_scope": "audience_research",
            "subject_id": "audience-1", "review_status": "human_approved",
            "citations": [{"source_quote": "Users want to compare alternatives"}],
        },
    ]
    snapshot = {
        "schema_version": "knowledge-snapshot/v1",
        "project_id": PROJECT, "context_id": context_id,
        "context_sha256": "f" * 64,
        "facts": facts, "approved_count": 2,
        "video_analysis_refs": [pin],
        "paid_c_authorized": False, "c_run_started": False,
    }
    b9_id = str(uuid4())
    handoff = build_c_handoff(snapshot, b9_id, canonical_sha(snapshot))
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
        self.assertEqual(set(actual["b9_fact_sha_to_sre_uuid"]), {FACT_ONE, FACT_TWO})
        self.assertEqual(snapshot, before)
        self.assertEqual(actual, checked_b9_mapping(snapshot, handoff))

    def test_b9_fact_uuid_is_stable_and_isolated_by_project(self):
        one = b9_fact_sre_uuid(PROJECT, FACT_ONE)
        self.assertEqual(one, b9_fact_sre_uuid(PROJECT, FACT_ONE))
        self.assertNotEqual(one, b9_fact_sre_uuid(PROJECT, FACT_TWO))
        self.assertNotEqual(one, b9_fact_sre_uuid(FOREIGN_PROJECT, FACT_ONE))
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
        f1 = b9_fact_sre_uuid(PROJECT, FACT_ONE)
        f2 = b9_fact_sre_uuid(PROJECT, FACT_TWO)
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
