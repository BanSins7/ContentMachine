"""Marketing B hook enum resilience with strict SHA-pinned frame evidence.

No provider requests or API keys are used. These tests cover the real failure
where gpt-4.1-mini returned invalid_hook_mechanisms on window 4 of 01.mp4.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import unittest
from unittest.mock import patch

from intelligence.market_intelligence._copied.group_b.blocks.b1_hook import MECHANISMS
from intelligence.market_intelligence._copied.group_b.blocks.b2_format import VIDEO_FORMATS
from intelligence.market_intelligence._copied.group_b.blocks.b3_story import ROLES
from intelligence.market_intelligence.replica_vision import _validate, classify_grounded


SHA="a"*64
REF="ga-frame:f001-"+SHA


def evidence():
    return {
        "format_family":VIDEO_FORMATS[0],
        "hook_mechanisms":[],
        "hook_text":None,
        "hook_visual_description":"A visible smartphone app screen",
        "story_roles":[ROLES[0]],
        "cta_type":None,
        "cta_text":None,
        "evidence_frame_refs":[REF],
        "visual_subjects":["smartphone"],
    }


class HookTaxonomyRecovery(unittest.TestCase):
    def test_unmapped_hook_does_not_invalidate_other_cited_semantics(self):
        payload=evidence()
        payload["hook_mechanisms"]=["arbitrary model synonym"]
        result=_validate(payload,[REF])
        self.assertEqual(result["hook_mechanisms"],[])
        self.assertEqual(result["unmapped_hook_mechanisms_count"],1)
        self.assertEqual(result["hook_mechanisms_status"],"partial_unmapped")
        self.assertEqual(result["format_family"],VIDEO_FORMATS[0])
        self.assertEqual(result["story_roles"],[ROLES[0]])
        self.assertNotIn("arbitrary model synonym",result["hook_mechanisms"])

    def test_mixed_approved_and_unmapped_retains_only_exact_taxonomy(self):
        payload=evidence()
        payload["hook_mechanisms"]=[MECHANISMS[0],"some new hook",MECHANISMS[1]]
        result=_validate(payload,[REF])
        self.assertEqual(result["hook_mechanisms"],[MECHANISMS[0],MECHANISMS[1]])
        self.assertEqual(result["unmapped_hook_mechanisms_count"],1)
        self.assertEqual(result["hook_mechanisms_status"],"partial_unmapped")

    def test_approved_and_unknown_are_distinguishable(self):
        good=evidence()
        good["hook_mechanisms"]=[MECHANISMS[0]]
        self.assertEqual(_validate(good,[REF])["hook_mechanisms_status"],"model_observed")
        none=evidence()
        self.assertEqual(_validate(none,[REF])["hook_mechanisms_status"],"unknown")

    def test_bad_hook_field_structure_is_unknown_not_validated_hook(self):
        for invalid in (None,"surprise",{"value":"surprise"},[123],["x"*121],
                        [MECHANISMS[0],MECHANISMS[0]],["a"]*11):
            with self.subTest(invalid=invalid):
                payload=evidence()
                payload["hook_mechanisms"]=invalid
                result=_validate(payload,[REF])
                self.assertEqual(result["hook_mechanisms"],[])
                self.assertEqual(result["hook_mechanisms_status"],"invalid_structure")
                self.assertEqual(result["format_family"],VIDEO_FORMATS[0])
                self.assertNotIn("surprise",result["hook_mechanisms"])

    def test_forged_evidence_and_uncited_claims_still_fail_closed(self):
        fake=evidence()
        fake["hook_mechanisms"]=["arbitrary"]
        fake["evidence_frame_refs"]=["invented-frame"]
        with self.assertRaisesRegex(ValueError,"invalid_evidence_frame_refs"):
            _validate(fake,[REF])
        uncited=evidence()
        uncited["hook_mechanisms"]=["arbitrary"]
        uncited["evidence_frame_refs"]=[]
        with self.assertRaisesRegex(ValueError,"uncited_semantic_claim"):
            _validate(uncited,[REF])

    def test_other_taxonomy_enums_still_strict(self):
        for name,value,error in (
            ("format_family","made_up","unsupported_format_family"),
            ("story_roles",["invented"],"invalid_story_roles"),
            ("cta_type","invented_cta","unsupported_cta"),
        ):
            with self.subTest(name=name):
                payload=evidence()
                payload[name]=value
                payload["hook_mechanisms"]=["arbitrary"]
                with self.assertRaisesRegex(ValueError,error):
                    _validate(payload,[REF])

    def test_live_requester_mock_preserves_window_after_unknown_hook(self):
        frame=b"not-a-real-png-mocked-bytes"
        sha=hashlib.sha256(frame).hexdigest()
        ref="ga-frame:f001-"+sha
        one={"frame_ref":ref,"png_sha256":sha,
             "sample_index":1,"observed_sec":"14.633"}
        payload=evidence()
        payload["evidence_frame_refs"]=[ref]
        payload["hook_mechanisms"]=["unknown_creative_device"]
        count=[]
        def requester(data):
            count.append(1)
            return {"id":"local-mock-response","choices":[{
                "message":{"content":json.dumps(payload)}}]}
        env={"CM_MARKET_VISION_ENABLED":"1",
             "CM_MARKET_MEDIA_TO_PROVIDER_ALLOWED":"1",
             "CM_MARKET_OPENROUTER_API_KEY":"local-fake-not-network",
             "CM_MARKET_VISION_MODEL":"test/mock"}
        with patch.dict(os.environ,env):
            observed=classify_grounded(
                frames=[one],png_blobs={"f001-"+sha+".png":frame},
                transcript=[],duration_sec=20.9,requester=requester)
        self.assertEqual(len(count),1)
        self.assertEqual(observed["state"],"model_observed_partial")
        self.assertEqual(observed["hook_mechanisms"],[])
        self.assertEqual(observed["hook_mechanisms_status"],"partial_unmapped")
        self.assertEqual(observed["unmapped_hook_mechanisms_count"],1)
        self.assertEqual(observed["evidence_frame_refs"],[ref])


if __name__=="__main__":
    unittest.main()
