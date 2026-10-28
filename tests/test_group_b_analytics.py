"""GB-MOD-25 pure, synthetic UAE reader negotiation and qualification gates.

No synthetic test 'candidate' is evidence of real current media, Group C or
an authenticated UAE consumer. A pure candidate is NEVER UAE qualified.
"""
from __future__ import annotations
from dataclasses import replace
from copy import deepcopy
import unittest

from intelligence.group_b.integration.analytics_contract import (
    HandoffError,HandoffState,make_compatibility,validate_snapshot,
    blocked,UAE_PORT,UAE_PEER_WIRE,
)


def capability(*,carousel=True):
    return make_compatibility(
        understands_b25_version=True,
        understands_stale_invalidation=True,
        understands_immutable_review=True,
        understands_qdp_receipt=True,
        understands_carousel_na=carousel,
    )


def input_fixture(*,carousel=False):
    parts={
        "hook":{"classification_status":"inferred","mechanisms":["problem"]},
        "format":{"classification_status":"inferred",
                  "video_primary":None if carousel else "talking_head",
                  "carousel_format":"explainer" if carousel else None},
        "story":{"frames":[] if carousel else [
                    {"frame_index":1,"role":"hook","needs_review":False}],
                 "intervals":[],"slides":[{
                    "slide_index":1,"role":"hook","needs_review":False}
                 ] if carousel else []},
        "visual":{"person_visible":True,"product_visible":False,
                  "logo_visible":False,"ai_generated":None},
        "cta_summary":{"analysis_status":"complete","has_cta":False,
                       "detected_occurrences":0},
        "topic":{"category_id":1,"topic_id":3,"subtopic_id":None,
                 "needs_review":False},
    }
    if not carousel:
        parts["editing"]={"editing_type":"single_take","cut_count":0}
        parts["subtitles"]={"subtitle_type":"none"}
    blocks={f"B{i}":{
        "status":"complete","evidence_sha256":"a"*64,
        "error_code":None,
    } for i in range(1,9)}
    if carousel:
        for b in ("B5","B6"):
            blocks[b]={"status":"not_run","evidence_sha256":None,
                       "error_code":None}
    return dict(
        project_id="f"*32,run_id="00000000-0000-0000-0000-000000000001",
        analysis_id=99,analysis_version="group-b-atomic/v1",
        content_kind="photo_carousel" if carousel else "video",
        block_rows=blocks,parts=parts,qualified_freshness=True,
        signed_source=True,quality_passed=True,
        compatibility=capability(),expected_a_id=55,linked_a_id=55,
        expected_policy_id=7,linked_policy_id=7,
        expected_qdp_sha="b"*64,received_qdp_sha="b"*64,
        has_correction=False,has_invalidation=False,
    )


class TestUAEHandoffPure(unittest.TestCase):
    def test_reader_wire_names_match_unified_analytics_contract(self):
        self.assertEqual(UAE_PORT,"uae-group-b-reader/v1")
        self.assertEqual(UAE_PEER_WIRE,"uae-group-b-exact/v1")
        cap=capability()
        self.assertTrue(cap.verify())
        self.assertTrue(cap.compatible)
        self.assertEqual(cap.capability_sha256,capability().capability_sha256)

    def test_synthetic_video_candidate_is_never_qualified(self):
        verdict=validate_snapshot(**input_fixture())
        self.assertEqual(verdict.state,HandoffState.CANDIDATE)
        self.assertTrue(verdict.can_offer)
        self.assertFalse(verdict.uae_qualified)
        self.assertIsNone(verdict.payload_sha256)  # real SQL read constructs payload
        self.assertFalse(verdict.verify_digest())

    def test_unknown_b6_subtitles_quarantines_video(self):
        a=input_fixture()
        a["block_rows"]["B6"]["status"]="partial"
        result=validate_snapshot(**a)
        self.assertEqual(result.state,HandoffState.BLOCKED)
        self.assertIn("block_unverified_b6",result.reasons)

    def test_b8_needs_review_is_never_canonical_topic(self):
        a=input_fixture()
        a["parts"]["topic"]["needs_review"]=True
        self.assertIn("classification_unknown_or_no_whole_scan",
                      validate_snapshot(**a).reasons)

    def test_exact_group_a_policy_and_qdp_receipt_must_match(self):
        for changes,reason in [
            ({"linked_a_id":88},"exact_group_a_fk_mismatch"),
            ({"linked_policy_id":11},"source_policy_version_mismatch"),
            ({"received_qdp_sha":"d"*64},"qdp_preparation_receipt_mismatch"),
            ({"signed_source":False},"fresh_signed_a_qdp_media_unverified"),
            ({"quality_passed":False},"expert_quality_gate_unverified"),
            ({"qualified_freshness":False},"fresh_signed_a_qdp_media_unverified"),
        ]:
            a=input_fixture()
            a.update(changes)
            with self.subTest(changes=changes):
                self.assertIn(reason,validate_snapshot(**a).reasons)

    def test_review_or_invalidation_prevents_reuse(self):
        for k in ("has_correction","has_invalidation"):
            a=input_fixture()
            a[k]=True
            self.assertFalse(validate_snapshot(**a).can_offer)

    def test_unknown_b1_b2_or_false_unscreened_cta_cannot_qualify(self):
        a=input_fixture()
        a["parts"]["hook"]["classification_status"]="unknown"
        self.assertFalse(validate_snapshot(**a).can_offer)
        a=input_fixture()
        a["parts"]["format"]["classification_status"]="needs_review"
        self.assertFalse(validate_snapshot(**a).can_offer)
        a=input_fixture()
        a["parts"]["cta_summary"]["has_cta"]=None
        self.assertFalse(validate_snapshot(**a).can_offer)

    def test_missing_component_or_evidence_fails_closed(self):
        a=input_fixture()
        del a["parts"]["editing"]
        self.assertIn("component_payload_incomplete_or_fabricated",
                      validate_snapshot(**a).reasons)
        a=input_fixture()
        a["block_rows"]["B1"]["evidence_sha256"]=None
        self.assertIn("block_unverified_b1",validate_snapshot(**a).reasons)
        a=input_fixture()
        a["block_rows"].pop("B7")
        self.assertIn("eight_block_matrix_incomplete",
                      validate_snapshot(**a).reasons)

    def test_carousel_not_applicable_allowed_only_explicit_consumer_contract(self):
        a=input_fixture(carousel=True)
        self.assertTrue(validate_snapshot(**a).can_offer)
        a["compatibility"]=capability(carousel=False)
        self.assertFalse(validate_snapshot(**a).can_offer)
        a=input_fixture(carousel=True)
        a["block_rows"]["B5"]={
            "status":"complete","evidence_sha256":"a"*64,"error_code":None}
        self.assertIn("carousel_video_only_block_fabricated",
                      validate_snapshot(**a).reasons)

    def test_mixed_carousel_video_story_cannot_fabricate_editing(self):
        a=input_fixture(carousel=True)
        a["content_kind"]="mixed_carousel"
        a["parts"]["story"]["frames"]=[
            {"frame_index":1,"role":"hook","needs_review":False}
        ]
        self.assertIn("story_coverage_or_unreviewed_roles_missing",
                      validate_snapshot(**a).reasons)

    def test_no_group_c_fact_or_uae_qualification_is_fabricated(self):
        a=input_fixture()
        a["parts"]["unexpected_group_c_breakout_score"]=100
        self.assertFalse(validate_snapshot(**a).can_offer)
        denied=blocked(a["project_id"],a["run_id"],"upstream_unavailable")
        self.assertFalse(denied.can_offer)
        self.assertFalse(denied.uae_qualified)

    def test_bad_consumer_checksum_or_contract_denied(self):
        a=input_fixture()
        cap=a["compatibility"]
        a["compatibility"]=replace(cap,capability_sha256="0"*64)
        self.assertIn("uae_contract_not_negotiated",
                      validate_snapshot(**a).reasons)
        a["compatibility"]=make_compatibility(
            adapter_schema="uae-group-b-reader/v999",
            understands_b25_version=True,
            understands_stale_invalidation=True,
            understands_immutable_review=True,
            understands_qdp_receipt=True,
            understands_carousel_na=True,
        )
        self.assertFalse(validate_snapshot(**a).can_offer)


if __name__=="__main__":
    unittest.main()
