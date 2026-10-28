"""RTB-FIX-01 regression against real B9 snapshot builder and original A payloads.

All tests are offline, do not contact PostgreSQL or a paid AI provider.
"""
from __future__ import annotations

from copy import deepcopy
import unittest
from uuid import uuid4

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.knowledge_extraction.knowledge_snapshot import build_c_handoff, build_snapshot
from test_ke_stage13_knowledge_snapshot import pure_fixture as real_b9_no_video_fixture
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.research_input import (
    SCHEMA, RTBResearchInputService, build_research_input, _video_pins,
)
from test_taxonomy_c_corrector_contracts import PROJECT, FOREIGN_PROJECT, fixture


def source_fixture(*, no_video=False):
    if no_video:
        args = real_b9_no_video_fixture()
        published = build_snapshot(**args)
        snapshot = published["snapshot"]
        handoff = build_c_handoff(snapshot, str(uuid4()), published["snapshot_sha256"])
        pins, outputs = [], {}
    else:
        snapshot, handoff, pin, original = fixture()
        pins, outputs = [pin], {pin["output_id"]: original}
    approved_envelope = {
        "project_id": PROJECT, "context_id": snapshot["context_id"],
        "context_sha256": snapshot["context_sha256"],
        "approved": True, "approval": {"decision": "approved"},
        "context": {"analysis_refs": deepcopy(pins)},
    }
    return snapshot, handoff, approved_envelope, outputs


def build(snapshot, handoff, envelope, outputs):
    return build_research_input(
        snapshot, handoff, handoff_id="00000000-0000-4000-8000-000000000001",
        handoff_sha256=canonical_sha(handoff),
        context_envelope=envelope, resolved_videos=outputs)


class StoredSnapshots:
    def __init__(self, snapshot, handoff):
        self.snapshot, self.handoff = deepcopy(snapshot), deepcopy(handoff)
        self.calls = []
        self.handoff_id = str(uuid4())

    def export_by_id(self, project_id, snapshot_id, reviewer_id):
        self.calls.append(("export_by_id", project_id, snapshot_id, reviewer_id))
        if project_id != self.snapshot["project_id"] or snapshot_id != self.handoff["snapshot_id"]:
            raise PipelineError("b9_snapshot_not_found", 404)
        return {"snapshot": deepcopy(self.snapshot),
                "snapshot_id": snapshot_id,
                "snapshot_sha256": canonical_sha(self.snapshot)}

    def materialize_c_handoff(self, project_id, session_id, reviewer_id):
        self.calls.append(("materialize_c_handoff", project_id, session_id, reviewer_id))
        if project_id != self.snapshot["project_id"] or session_id != self.snapshot["session_id"]:
            raise PipelineError("c_handoff_not_found_or_changed", 404)
        return {"handoff_id": self.handoff_id,
                "handoff": deepcopy(self.handoff),
                "handoff_sha256": canonical_sha(self.handoff)}


class PinnedA:
    def __init__(self, envelope, outputs):
        self.envelope, self.outputs = deepcopy(envelope), deepcopy(outputs)
        self.calls = []

    def resolve_context(self, project_id, context_id):
        self.calls.append(("resolve_context", project_id, context_id))
        if project_id != self.envelope["project_id"] or context_id != self.envelope["context_id"]:
            raise PipelineError("context_not_found", 404)
        return deepcopy(self.envelope)

    def resolve_video(self, project_id, context_id, pin):
        self.calls.append(("resolve_video", project_id, context_id, pin))
        if (project_id != self.envelope["project_id"]
                or context_id != self.envelope["context_id"]
                or pin.output_id not in self.outputs):
            raise PipelineError("video_not_pinned", 404)
        return deepcopy(self.outputs[pin.output_id])


class ResearchInputTests(unittest.TestCase):
    def test_two_real_b9_facts_and_original_a_observations(self):
        snap, hand, envelope, outputs = source_fixture()
        first = build(snap, hand, envelope, outputs)
        again = build(snap, hand, envelope, outputs)
        self.assertEqual(first["research_input_sha256"], again["research_input_sha256"])
        self.assertEqual(first, again)
        self.assertEqual(first["schema_version"], SCHEMA)
        self.assertEqual(first["fact_count"], 2)
        self.assertEqual(first["observation_count"], 2)
        self.assertEqual(first["video_count"], 1)
        self.assertFalse(first["paid_execution_authorized"])
        self.assertEqual(first["provider_calls"], 0)
        self.assertIsNone(first["research_goal"])
        self.assertEqual(
            first["research_input_sha256"],
            canonical_sha({key: value for key, value in first.items()
                          if key != "research_input_sha256"}),
        )
        facts = first["c1_approved_facts"]
        self.assertEqual({fact["kind"] for fact in facts}, {"function", "problem"})
        self.assertTrue(next(f for f in facts if f["kind"] == "function")["may_support_own_product_claim"])
        self.assertFalse(next(f for f in facts if f["kind"] == "problem")["may_support_own_product_claim"])
        self.assertEqual(
            {o["a_evidence_id"] for o in first["c2_original_video_observations"]},
            set(next(iter(outputs.values()))["evidence"]),
        )

    def test_original_sources_are_not_mutated(self):
        snap, hand, envelope, outputs = source_fixture()
        original = deepcopy((snap, hand, envelope, outputs))
        payload = build(snap, hand, envelope, outputs)
        payload["c1_approved_facts"][0]["reviewed_text"] = "changed"
        payload["c2_original_video_observations"][0]["original_evidence"]["trust"] = "other"
        self.assertEqual((snap, hand, envelope, outputs), original)

    def test_zero_video_is_valid_and_stable(self):
        snap, hand, envelope, outputs = source_fixture(no_video=True)
        result = build(snap, hand, envelope, outputs)
        self.assertEqual(result["fact_count"], 1)
        self.assertEqual(result["video_count"], 0)
        self.assertEqual(result["observation_count"], 0)
        self.assertEqual(result["c2_original_video_observations"], [])

    def test_wrong_b9_handoff_and_a_video_schema_versions_fail_closed(self):
        snap, hand, envelope, outputs = source_fixture()
        wrong_b9 = deepcopy(snap)
        wrong_b9["schema_version"] = "knowledge-snapshot/v900"
        with self.assertRaisesRegex(PipelineError, "c_knowledge_schema_version"):
            build(wrong_b9, hand, envelope, outputs)
        wrong_handoff = deepcopy(hand)
        wrong_handoff["schema_version"] = "knowledge-c-handoff/v900"
        with self.assertRaisesRegex(PipelineError, "c_handoff_schema_version"):
            build(snap, wrong_handoff, envelope, outputs)
        wrong_a = deepcopy(envelope)
        wrong_a["context"]["analysis_refs"][0]["schema_version"] = "video-intelligence/v900"
        with self.assertRaisesRegex(PipelineError, "a_b_video_set_mismatch"):
            build(snap, hand, wrong_a, outputs)

    def test_rejected_unapproved_and_foreign_context(self):
        snap, hand, envelope, outputs = source_fixture()
        for modifier in (
            lambda e: e.update({"approved": False}),
            lambda e: e["approval"].update({"decision": "rejected"}),
            lambda e: e.update({"context_sha256": "0" * 64}),
            lambda e: e.update({"context_id": str(uuid4())}),
            lambda e: e.update({"project_id": FOREIGN_PROJECT}),
        ):
            with self.subTest(modifier=str(modifier)):
                bad = deepcopy(envelope)
                modifier(bad)
                with self.assertRaises(PipelineError):
                    build(snap, hand, bad, outputs)

    def test_context_analysis_set_exactly_matches_b9(self):
        snap, hand, envelope, outputs = source_fixture()
        bad = deepcopy(envelope)
        bad["context"]["analysis_refs"] = []
        with self.assertRaisesRegex(PipelineError, "a_b_video_set_mismatch"):
            build(snap, hand, bad, outputs)
        bad = deepcopy(envelope)
        bad["context"]["analysis_refs"][0]["output_sha256"] = "0" * 64
        with self.assertRaisesRegex(PipelineError, "a_b_video_set_mismatch"):
            build(snap, hand, bad, outputs)
        bad = deepcopy(envelope)
        bad["context"]["analysis_refs"].append(deepcopy(bad["context"]["analysis_refs"][0]))
        with self.assertRaises(PipelineError):
            build(snap, hand, bad, outputs)

    def test_missing_tampered_and_swapped_video(self):
        snap, hand, envelope, outputs = source_fixture()
        with self.assertRaises(PipelineError):
            build(snap, hand, envelope, {})
        bad = deepcopy(outputs)
        bad[next(iter(bad))]["evidence"]["speech:job1:seg00001"]["exact_text"] = "tampered"
        with self.assertRaises(PipelineError):
            build(snap, hand, envelope, bad)
        bad = deepcopy(outputs)
        bad[next(iter(bad))]["source_id"] = str(uuid4())
        with self.assertRaises(PipelineError):
            build(snap, hand, envelope, bad)

    def test_oversize_observation_rejected_not_truncated(self):
        snap, hand, envelope, outputs = source_fixture()
        key = next(iter(outputs))
        oversized = deepcopy(outputs[key])
        oversized["evidence"]["speech:job1:seg00001"]["exact_text"] = "x" * 55_000
        sha = canonical_sha(oversized)
        pin = deepcopy(envelope["context"]["analysis_refs"][0])
        pin["output_sha256"] = sha
        envelope["context"]["analysis_refs"][0] = pin
        snap["video_analysis_refs"][0]["output_sha256"] = sha
        hand = build_c_handoff(snap, hand["snapshot_id"], canonical_sha(snap))
        with self.assertRaisesRegex(PipelineError, "observation_too_large"):
            build(snap, hand, envelope, {key: oversized})

    def test_refuses_too_many_video_pins(self):
        snap, hand, envelope, outputs = source_fixture()
        many = [deepcopy(envelope["context"]["analysis_refs"][0]) for _ in range(25)]
        for idx, row in enumerate(many):
            row["output_id"] = str(uuid4())
        with self.assertRaisesRegex(PipelineError, "video_count_exceeded"):
            _video_pins(many)

    def test_handoff_digest_mismatch_is_blocking(self):
        snap, hand, envelope, outputs = source_fixture()
        with self.assertRaisesRegex(PipelineError, "handoff_hash_changed"):
            build_research_input(
                snap, hand, handoff_id=str(uuid4()),
                handoff_sha256="0" * 64, context_envelope=envelope,
                resolved_videos=outputs)

    def test_preview_reads_exact_pins_with_no_provider_calls(self):
        snap, hand, envelope, outputs = source_fixture()
        b = StoredSnapshots(snap, hand)
        a = PinnedA(envelope, outputs)
        service = RTBResearchInputService(snapshots=b, a_resolver=a)
        out = service.preview(PROJECT, hand["snapshot_id"], "reviewer-1")
        self.assertEqual(out["fact_count"], 2)
        self.assertEqual(out["video_count"], 1)
        self.assertEqual(out["observation_count"], 2)
        self.assertEqual([call[0] for call in b.calls],
                         ["export_by_id", "materialize_c_handoff"])
        self.assertEqual([call[0] for call in a.calls],
                         ["resolve_context", "resolve_video"])
        self.assertEqual(a.calls[-1][-1].output_id, next(iter(outputs)))

    def test_preview_zero_video_does_not_call_video_reader(self):
        snap, hand, envelope, outputs = source_fixture(no_video=True)
        a = PinnedA(envelope, outputs)
        service = RTBResearchInputService(
            snapshots=StoredSnapshots(snap, hand), a_resolver=a)
        out = service.preview(PROJECT, hand["snapshot_id"], "reviewer-1")
        self.assertEqual(out["video_count"], 0)
        self.assertEqual([call[0] for call in a.calls], ["resolve_context"])

    def test_preview_rejects_foreign_snapshot(self):
        snap, hand, envelope, outputs = source_fixture()
        a = PinnedA(envelope, outputs)
        b = StoredSnapshots(snap, hand)
        service = RTBResearchInputService(snapshots=b, a_resolver=a)
        with self.assertRaises(PipelineError):
            service.preview(FOREIGN_PROJECT, hand["snapshot_id"], "reviewer-1")
        self.assertEqual(a.calls, [])

    def test_preview_rejects_missing_persisted_c_handoff(self):
        snap, hand, envelope, outputs = source_fixture()
        b = StoredSnapshots(snap, hand)
        def unavailable(*_args):
            raise PipelineError("c_handoff_not_found_or_changed", 404)
        b.materialize_c_handoff = unavailable
        a = PinnedA(envelope, outputs)
        service = RTBResearchInputService(snapshots=b, a_resolver=a)
        with self.assertRaises(PipelineError):
            service.preview(PROJECT, hand["snapshot_id"], "reviewer-1")
        self.assertEqual(a.calls, [])

    def test_preview_idempotent_across_two_calls(self):
        snap, hand, envelope, outputs = source_fixture()
        service = RTBResearchInputService(
            snapshots=StoredSnapshots(snap, hand),
            a_resolver=PinnedA(envelope, outputs))
        first = service.preview(PROJECT, hand["snapshot_id"], "reviewer-1")
        second = service.preview(PROJECT, hand["snapshot_id"], "reviewer-1")
        self.assertEqual(first["research_input_sha256"], second["research_input_sha256"])


if __name__ == "__main__":
    unittest.main()
