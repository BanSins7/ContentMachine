"""GB-MOD-16: 26 B3 semantic role, intervals, repeated loops, review cases.

All tests use synthetic GA refs and fake trusted verifiers, never paid HTTP.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import unittest

from intelligence.group_b.blocks.b3_sampling import build_b3_sampling_plan
from intelligence.group_b.blocks.b3_story import (
    ROLES, B3StoryError, validate_b3_story, unknown_b3,
)
from intelligence.group_b.blocks.b3_schema import B3_STORY_SCHEMA
from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.providers.protocols import VerifiedChatResponse
from intelligence.group_b.providers.openrouter import _validate_schema,_validate_json_value
from test_group_b_story_sampling import (
    video_manifest,manifest,image_slide,
)

REQ="e"*64


def reply(raw):
    return VerifiedChatResponse("fixture/story",REQ,"receipt-fixture",
                                deepcopy(raw),{"prompt_tokens":500,"completion_tokens":100},"stop")


class FakeReceipt:
    def __init__(self,refs):
        self.refs=refs
        self.calls=0
    def verify_b3_response(self,response,plan,delivered):
        self.calls+=1
        return (response.request_sha256==REQ and
                response.response_id=="receipt-fixture" and
                delivered==self.refs and plan.verify_digest())


class FakeBoundary:
    def __init__(self,truth=True):
        self.truth=truth
        self.calls=0
    def verify_semantic_boundary(self,manifest,plan,start,end,role,refs):
        self.calls+=1
        return self.truth and start<end and role in ROLES and len(refs)>=2


def make_video():
    m=video_manifest(duration=2300,times=(0,500,1000,1500,2000))
    plan=build_b3_sampling_plan(m)
    refs=tuple(plan.matched_refs)
    data={
        "frames":[{
            "slot_index":i+1,
            "role":"before_after" if i in (0,1) else "result",
            "before_after_phase":"before" if i in (0,1) else "",
            "confidence":"high",
            "evidence_refs":[ref],
        } for i,ref in enumerate(refs)],
        "intervals":[
            {"start_ms":0,"end_ms":1000,
             "role":"before_after","before_after_phase":"before",
             "boundary_quality":"known","confidence":"high",
             "evidence_refs":[refs[0],refs[2]]},
            {"start_ms":1000,"end_ms":2000,
             "role":"before_after","before_after_phase":"after",
             "boundary_quality":"known","confidence":"high",
             "evidence_refs":[refs[2],refs[4]]},
        ],
        "slides":[],
    }
    return m,plan,refs,data


def make_carousel():
    pairs=(image_slide(1),image_slide(2),image_slide(3))
    m=manifest(assets=tuple(p[0] for p in pairs),
               observations=tuple(p[1] for p in pairs),
               kind="photo_carousel")
    plan=build_b3_sampling_plan(m)
    refs=tuple(plan.matched_refs)
    data={
        "frames":[],"intervals":[],
        "slides":[{
            "slide_index":i+1,"role":"context" if i<2 else "cta",
            "before_after_phase":"","confidence":"high",
            "evidence_refs":[ref],"screen_text":"",
            "visual_description":"Displayed visual",
        } for i,ref in enumerate(refs)],
    }
    return m,plan,refs,data


class TestB3Story(unittest.TestCase):
    def setUp(self):
        self.m,self.p,self.refs,self.raw=make_video()
        self.receipt=FakeReceipt(self.refs)
        self.boundary=FakeBoundary()

    def check(self,*,m=None,p=None,refs=None,raw=None,receipt=None,boundary=None):
        return validate_b3_story(
            manifest=self.m if m is None else m,
            plan=self.p if p is None else p,
            response=reply(self.raw if raw is None else raw),
            receipt_verifier=self.receipt if receipt is None else receipt,
            delivered_visual_refs=self.refs if refs is None else refs,
            boundary_verifier=self.boundary if boundary is None else boundary,
        )

    def denied(self,**kwargs):
        with self.assertRaises(B3StoryError):
            self.check(**kwargs)

    def test_exact_original_26_roles_and_schema(self):
        self.assertEqual(len(ROLES),26)
        self.assertEqual(len(set(ROLES)),26)
        self.assertEqual(ROLES[-1],"other")
        _validate_schema(B3_STORY_SCHEMA)
        _validate_json_value(B3_STORY_SCHEMA,self.raw)

    def test_verified_video_repeated_roles_preserved_across_semantic_intervals(self):
        story=self.check()
        self.assertTrue(story.authenticated_inference)
        self.assertTrue(story.verify_digest())
        self.assertEqual(story.payload["classification_status"],"inferred")
        self.assertEqual(story.story_pattern,("before_after","before_after"))
        frames=story.frame_db_rows()
        self.assertEqual(len(frames),5)
        self.assertEqual(frames[0]["time_sec"],Decimal("0"))
        self.assertEqual(frames[1]["time_sec"],Decimal("0.5"))
        self.assertEqual(frames[0]["sampled_frame_ref"],self.refs[0])
        rows=story.interval_db_rows()
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]["boundary_quality"],"known")
        self.assertEqual(rows[0]["before_after_phase"],"before")
        self.assertEqual(rows[1]["before_after_phase"],"after")
        self.assertEqual(rows[1]["start_sec"],Decimal("1"))
        self.assertEqual(self.boundary.calls,2)
        self.assertEqual(self.receipt.calls,1)
        self.assertFalse(story.needs_review)

    def test_repeated_carousel_roles_are_not_collapsed(self):
        m,p,refs,raw=make_carousel()
        story=self.check(m=m,p=p,refs=refs,raw=raw,
                         receipt=FakeReceipt(refs))
        self.assertEqual(story.story_pattern,("context","context","cta"))
        self.assertEqual(story.payload["content_type"],"carousel")
        self.assertEqual(story.payload["frames"],[])
        self.assertEqual(story.payload["intervals"],[])
        rows=story.slide_db_rows()
        self.assertEqual([x["slide_index"] for x in rows],[1,2,3])
        self.assertTrue(all(x["screen_text"] is None for x in rows))
        self.assertFalse(story.needs_review)

    def test_before_after_unknown_phase_and_other_force_review(self):
        raw=deepcopy(self.raw)
        raw["intervals"][0]["before_after_phase"]=""
        story=self.check(raw=raw)
        self.assertTrue(story.needs_review)
        self.assertTrue(story.interval_db_rows()[0]["needs_review"])
        raw=deepcopy(self.raw)
        raw["frames"][0]["role"]="other"
        raw["frames"][0]["before_after_phase"]=""
        story=self.check(raw=raw)
        self.assertTrue(story.needs_review)
        self.assertTrue(story.frame_db_rows()[0]["needs_review"])

    def test_unknown_does_not_fabricate_other_or_interval_or_false(self):
        story=unknown_b3(plan=self.p,reason="paid_provider_disabled")
        self.assertFalse(story.authenticated_inference)
        self.assertTrue(story.verify_digest())
        self.assertEqual(story.story_pattern,())
        self.assertEqual(story.frame_db_rows(),())
        self.assertEqual(story.interval_db_rows(),())
        self.assertEqual(story.payload["classification_status"],"unknown")

    def test_estimated_or_unknown_semantic_boundary_requires_review(self):
        for quality in ("estimated","unknown"):
            raw=deepcopy(self.raw)
            raw["intervals"][0]["boundary_quality"]=quality
            result=self.check(raw=raw)
            self.assertTrue(result.needs_review)
            self.assertTrue(result.interval_db_rows()[0]["needs_review"])
            self.assertIn("semantic_boundary_not_verified",result.payload["reason_codes"])

    def test_known_boundary_without_independent_attestation_denied(self):
        self.denied(boundary=object())
        self.denied(boundary=FakeBoundary(False))

    def test_technical_ga_scene_boundary_is_not_semantic_proof(self):
        # A model cannot simply assert 'known' based on a scene/cut.
        self.denied(boundary=object())

    def test_gaps_mark_review_and_not_invented(self):
        raw=deepcopy(self.raw)
        raw["intervals"]=[raw["intervals"][0]]
        result=self.check(raw=raw)
        self.assertEqual(result.story_pattern,("before_after",))
        self.assertIn("story_closing_gap",result.payload["reason_codes"])
        self.assertTrue(result.needs_review)

        raw=deepcopy(self.raw)
        raw["intervals"]=[]
        result=self.check(raw=raw)
        self.assertIn("semantic_intervals_not_measured",result.payload["reason_codes"])
        self.assertEqual(result.story_pattern,())

    def test_overlapping_or_nonobserved_interval_times_denied(self):
        for mutation in ("overlap","not_observed","reverse","out_of_video","duplicate"):
            raw=deepcopy(self.raw)
            if mutation=="overlap":
                raw["intervals"][1]["start_ms"]=500
            elif mutation=="not_observed":
                raw["intervals"][0]["end_ms"]=801
            elif mutation=="reverse":
                raw["intervals"][0]["end_ms"]=0
            elif mutation=="out_of_video":
                raw["intervals"][1]["end_ms"]=3000
            else:
                raw["intervals"][1]["start_ms"]=0
            with self.subTest(mutation=mutation),self.assertRaises(B3StoryError):
                self.check(raw=raw)

    def test_only_own_real_sampled_frame_can_receive_role(self):
        for mutation in ("missing_slot","duplicate","bad_evidence","wrong_type"):
            raw=deepcopy(self.raw)
            if mutation=="missing_slot":
                raw["frames"][0]["slot_index"]=300
            elif mutation=="duplicate":
                raw["frames"][1]["slot_index"]=1
            elif mutation=="bad_evidence":
                raw["frames"][0]["evidence_refs"]=[self.refs[1]]
            else:
                raw["frames"][0]["slot_index"]=True
            with self.subTest(mutation=mutation),self.assertRaises(B3StoryError):
                self.check(raw=raw)

    def test_partial_sampling_cannot_claim_inferred(self):
        m=video_manifest(duration=2300,times=(0,1000,2000))
        plan=build_b3_sampling_plan(m)
        refs=tuple(plan.matched_refs)
        raw={"frames":[{
            "slot_index":s["slot_index"],"role":"hook",
            "before_after_phase":"","confidence":"high",
            "evidence_refs":[s["matched_ref_sha256"]]
        } for s in plan.payload["slots"] if s["status"]=="matched"],
        "intervals":[],"slides":[]}
        result=self.check(m=m,p=plan,refs=refs,raw=raw,receipt=FakeReceipt(refs))
        self.assertTrue(result.needs_review)
        self.assertIn("sampling_incomplete",result.payload["reason_codes"])
        self.assertEqual(len(result.frame_db_rows()),3)

    def test_provider_or_delivery_or_self_hash_is_not_authorization(self):
        self.denied(refs=self.refs[:-1])
        self.denied(receipt=object())
        self.denied(receipt=FakeReceipt(("fraud",)))
        p=replace(self.p,plan_sha256="0"*64)
        self.denied(p=p)
        fake=deepcopy(self.p.payload)
        fake["slots"][0]["observed_ms"]=123
        from intelligence.group_b.blocks.b3_sampling import B3SamplingPlan
        self.denied(p=B3SamplingPlan(fake,canonical_hash(fake)))

    def test_unverified_slide_ocr_and_forged_slide_index_denied(self):
        m,p,refs,raw=make_carousel()
        bad=deepcopy(raw)
        bad["slides"][0]["screen_text"]="unverified sign"
        with self.assertRaises(B3StoryError):
            self.check(m=m,p=p,refs=refs,raw=bad,receipt=FakeReceipt(refs))
        bad=deepcopy(raw)
        bad["slides"][0]["slide_index"]=99
        with self.assertRaises(B3StoryError):
            self.check(m=m,p=p,refs=refs,raw=bad,receipt=FakeReceipt(refs))

    def test_low_confidence_repeated_frame_keeps_own_review_state(self):
        raw=deepcopy(self.raw)
        raw["frames"][0]["confidence"]="low"
        x=self.check(raw=raw)
        self.assertTrue(x.needs_review)
        self.assertTrue(x.frame_db_rows()[0]["needs_review"])
        self.assertFalse(x.frame_db_rows()[1]["needs_review"])

    def test_no_paid_http_or_database_write_in_semantic_validator(self):
        import inspect
        import intelligence.group_b.blocks.b3_story as impl
        source=inspect.getsource(impl)
        self.assertNotIn("post_json(",source)
        self.assertNotIn("INSERT INTO",source)


if __name__=="__main__":
    unittest.main()
