"""Stage01 A/B contract regression: real Stage11 builder output + snapshot wrapper.

No DB mutations, provider calls, media processing or network operations.
"""
from __future__ import annotations

import copy
import unittest
from uuid import uuid4

from jsonschema import Draft202012Validator
from intelligence.project_sources.context_builder import build, sha
from intelligence.knowledge_extraction.contracts import (
    A_CONTEXT_SCHEMA, A_VIDEO_SCHEMA, C_INPUT_MODE,
    LEGACY_RTB_INPUT_MODES, ContractError, default_source_role,
    envelope_validator, pinned_video_analyses, validate_context_envelope,
)


class Stage01ContractTests(unittest.TestCase):
    def setUp(self):
        self.pid = uuid4().hex
        self.sid, self.vid, self.oid, self.eid = (str(uuid4()) for _ in range(4))

    def fixture(self, *, approved=True, reviewed=True, with_video=False):
        document = {
            "schema_version": "document-segments/v1", "parser_version": "local/1",
            "status": "completed", "segments": [],
        }
        sources = [{
            "id": self.sid, "version_id": self.vid,
            "source_type": "research_document", "label": "test source",
            "canonical_url": None, "status": "completed", "initial_status": "completed",
            "content_sha256": "1" * 64, "byte_size": 20,
            "version_no": 1, "media_type": "application/pdf",
            "locator": {"kind": "pdf"}, "recorded_at": None, "tombstoned": False,
        }]
        outputs = [{
            "id": self.oid, "source_id": self.sid, "version_id": self.vid,
            "output_type": "document_segments",
            "schema_version": document["schema_version"],
            "output_sha256": sha(document), "output": document, "created_at": None,
        }]
        quote = "Verbatim excerpt from a test document."
        evidence = [{
            "id": self.eid, "source_id": self.sid, "version_id": self.vid,
            "output_id": self.oid, "quote": quote,
            "kind": "external_claim", "quality": "complete",
            "review_status": "pending", "trust": "local_document_extraction",
            "scope": "general_research",
            "locator": {"kind": "pdf", "page": 1, "start_char": 0, "end_char": len(quote)},
            "citation": "source:" + self.sid + ":page:1",
            "review_decision": "approved" if reviewed else None, "created_at": None,
        }]
        if with_video:
            video_id, video_ver, video_output = (str(uuid4()) for _ in range(3))
            fusion = {"schema_version": A_VIDEO_SCHEMA, "status": "completed"}
            sources.append({
                "id": video_id, "version_id": video_ver, "source_type": "user_video_reference",
                "label": "video", "canonical_url": "https://youtube.com/watch?v=abc12345678",
                "status": "completed", "initial_status": "completed",
                "content_sha256": "2" * 64, "byte_size": 512,
                "version_no": 1, "media_type": "video/mp4", "locator": {},
                "recorded_at": None, "tombstoned": False,
            })
            outputs.append({
                "id": video_output, "source_id": video_id, "version_id": video_ver,
                "output_type": "video_intelligence", "schema_version": A_VIDEO_SCHEMA,
                "output_sha256": sha(fusion), "output": fusion, "created_at": None,
            })
            # Stage11 marks a video without Evidence as incomplete; A allows
            # explicitly approved partial inclusion, not imaginary transcript.
        context = build(
            project_id=self.pid, profile_revision=1, profile_hash="a" * 64,
            profile_data={"profile": {"niche": "Technology", "goal": "Research",
                                      "product_description": "User-provided description."}},
            source_rows=sources, output_rows=outputs, evidence_rows=evidence,
        )
        missing = bool(context["unavailable_or_excluded_sources"])
        approval = None
        if approved:
            approval = {
                "approval_id": str(uuid4()),
                "decision": "approved_partial" if missing else "approved",
                "approved_by": "local_owner",
                "reason": "Explicitly acknowledged excluded sources" if missing else None,
                "approved_at": "2026-10-25T00:00:00+00:00",
            }
        envelope = {
            "context_id": str(uuid4()), "project_id": self.pid, "context_version": 1,
            "context_sha256": sha(context),
            "profile_revision": 1, "profile_hash": "a" * 64,
            "created_at": "2026-10-25T00:00:00+00:00",
            "input_fingerprint": sha(context), "approved": approved,
            "approval": approval, "context": context,
        }
        return envelope

    @staticmethod
    def resign(e):
        e["context_sha256"] = sha(e["context"])
        e["input_fingerprint"] = sha(e["context"])

    def code(self, envelope, code, **kwargs):
        with self.assertRaises(ContractError) as caught:
            validate_context_envelope(envelope, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_real_builder_envelope_accepted_and_deterministic(self):
        envelope = self.fixture()
        self.assertEqual(envelope_validator().schema["$schema"],
                         "https://json-schema.org/draft/2020-12/schema")
        Draft202012Validator.check_schema(envelope_validator().schema)
        self.assertEqual(envelope["context"]["schema_version"], A_CONTEXT_SCHEMA)
        self.assertIs(validate_context_envelope(envelope, expected_project_id=self.pid), envelope)
        self.assertEqual(validate_context_envelope(envelope)["context_sha256"], sha(envelope["context"]))
        self.assertEqual(envelope["context"]["approval_status"], "pending")
        self.assertEqual(envelope["approval"]["decision"], "approved")

    def test_unapproved_not_ready_even_when_payload_is_well_formed(self):
        e = self.fixture(approved=False)
        self.code(e, "context_not_approved")
        self.assertIs(validate_context_envelope(e, require_approved=False), e)

    def test_foreign_project_rejected(self):
        self.code(self.fixture(), "foreign_project", expected_project_id=uuid4().hex)
        e = self.fixture()
        e["context"]["project_id"] = uuid4().hex
        self.resign(e)
        self.code(e, "foreign_project")

    def test_bad_hash_rejected(self):
        e = self.fixture()
        e["context"]["profile"]["fields"]["goal"] = "INJECTED"
        self.code(e, "context_hash_mismatch")

    def test_corrupted_input_fingerprint_rejected(self):
        e = self.fixture()
        e["input_fingerprint"] = "b" * 64
        self.code(e, "fingerprint_mismatch")

    def test_wrong_profile_revision_and_hash_rejected(self):
        e = self.fixture()
        e["profile_revision"] = 2
        self.code(e, "profile_pin_mismatch")
        e = self.fixture()
        e["profile_hash"] = "b" * 64
        self.code(e, "profile_pin_mismatch")

    def test_schema_rejects_missing_required_fields_unknown_versions_and_unknown_props(self):
        for mutate in (
            lambda e: e.pop("context_id"),
            lambda e: e["context"].update(schema_version="project_source_context.v1"),
            lambda e: e.update(arbitrary_source="secret"),
            lambda e: e["context"]["source_manifest"][0].update(content="/tmp/doc"),
            lambda e: e["context"]["evidence_manifest"][0].update(exact_text="hidden"),
            lambda e: e["context"].update(approved_fact_ids=[]),
        ):
            e = self.fixture()
            mutate(e)
            self.code(e, "envelope_schema_invalid")

    def test_reject_mismatched_approval_boolean(self):
        e = self.fixture()
        e["approved"] = False
        self.code(e, "approval_state_mismatch")

    def test_duplicate_source_and_missing_analysis_link_rejected(self):
        e = self.fixture()
        e["context"]["source_manifest"].append(copy.deepcopy(e["context"]["source_manifest"][0]))
        self.resign(e)
        self.code(e, "duplicate_source")
        e = self.fixture()
        e["context"]["source_manifest"][0]["analysis_refs"] = []
        self.resign(e)
        self.code(e, "source_analysis_membership_mismatch")

    def test_evidence_output_mismatch_and_wrong_versions_rejected(self):
        e = self.fixture()
        e["context"]["evidence_manifest"][0]["processing_output_id"] = str(uuid4())
        self.resign(e)
        self.code(e, "evidence_output_pin_mismatch")
        e = self.fixture()
        e["context"]["analysis_refs"][0]["source_version_id"] = str(uuid4())
        self.resign(e)
        self.code(e, "analysis_source_version_mismatch")

    def test_review_count_and_ids_cannot_be_fabricated(self):
        e = self.fixture()
        e["context"]["approved_evidence_ids"] = []
        self.resign(e)
        self.code(e, "approved_evidence_mismatch")
        e = self.fixture()
        e["context"]["coverage"]["evidence_total"] += 1
        self.resign(e)
        self.code(e, "coverage_mismatch")
        e = self.fixture()
        e["context"]["source_manifest"][0]["approved_evidence_count"] = 0
        self.resign(e)
        self.code(e, "source_coverage_mismatch")

    def test_exclusion_requires_explicit_partial_approval(self):
        e = self.fixture(with_video=True)
        self.assertEqual(e["approval"]["decision"], "approved_partial")
        self.assertEqual(len(pinned_video_analyses(e)), 1)
        e["approval"]["decision"] = "approved"
        self.code(e, "approved_with_missing_sources")
        e = self.fixture()
        e["approval"]["decision"] = "approved_partial"
        self.code(e, "partial_without_missing_sources")

    def test_video_refs_pass_through_without_extracted_video_text(self):
        e = self.fixture(with_video=True)
        pinned = pinned_video_analyses(e)
        self.assertEqual(len(pinned), 1)
        actual = next(x for x in e["context"]["analysis_refs"]
                      if x["schema_version"] == A_VIDEO_SCHEMA)
        self.assertEqual(pinned[0].output_sha256, actual["output_sha256"])
        self.assertEqual(pinned[0].output_id, actual["output_id"])
        self.assertFalse(hasattr(pinned[0], "transcript_segments"))
        self.assertFalse(hasattr(pinned[0], "observations"))
        self.assertEqual(pinned_video_analyses(self.fixture()), ())

    def test_source_routing_is_conservative(self):
        self.assertEqual(default_source_role("product_website"), "own_product")
        self.assertEqual(default_source_role("competitor_website"), "competitor")
        self.assertEqual(default_source_role("research_document"), "other")
        self.assertEqual(default_source_role("questionnaire_field"), "other")
        self.assertEqual(default_source_role("unknown_future_kind"), "other")

    def test_legacy_modes_preserved_and_c_input_new(self):
        self.assertNotIn(C_INPUT_MODE, LEGACY_RTB_INPUT_MODES)
        self.assertEqual(C_INPUT_MODE, "knowledge_snapshot_v1")
        self.assertEqual(LEGACY_RTB_INPUT_MODES, (
            "legacy_questionnaire_v1", "approved_project_context_v1",
        ))


if __name__ == "__main__":
    unittest.main()
