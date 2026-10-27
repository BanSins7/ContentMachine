"""GB-MOD-20 B8 registry and topic proposal pure regression suite.

Fixtures, receipts and image attachments are synthetic, NOT real model usage,
source-rights authorization or semantic truth.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import copy
import unittest

from intelligence.group_b.topic_registry import (
    TopicNode,TopicRegistryError,make_topic_registry,normalize_label,
)
from intelligence.group_b.blocks.b8_topic import (
    B8TopicError,validate_b8_topic,unknown_b8,prepare_b8_request,
)
from intelligence.group_b.blocks.b8_schema import B8_OUTPUT_SCHEMA
from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.context.builder import ContextPacket,_entry
from intelligence.group_b.context.evidence import VerifiedObservation
from intelligence.group_b.providers.protocols import (
    VerifiedChatResponse,ModelDescriptor,ModelCatalogSnapshot,
)
from intelligence.group_b.providers.openrouter import (
    VerifiedImage,ProviderFailure,_validate_schema,_validate_json_value,
)
from test_group_b_b2 import fixture,IMAGE_BYTES

QUOTE="Strength training: squats and deadlifts."
REQ="a"*64


def taxonomy(*,duplicate=False,merges=True,category_merged=False):
    categories=[
        TopicNode(1,"fitness","Fitness",None,None),
        TopicNode(2,"cooking","Cooking",None,None),
    ]
    if category_merged:
        categories.append(TopicNode(3,"sport","Sport",None,1))
    topics=[
        TopicNode(10,"strength_training","Strength Training",1,None),
        TopicNode(11,"cardio","Cardio",1,None),
        TopicNode(20,"recipes","Recipes",2,None),
    ]
    if merges:
        topics.append(TopicNode(12,"powerlifting","Powerlifting",1,10))
    if duplicate:
        topics.append(TopicNode(13,"strength_training_duplicate",
                                "Strength Training",1,None))
    subs=[
        TopicNode(100,"squats","Squats",10,None),
        TopicNode(101,"deadlift","Deadlift",10,None),
        TopicNode(200,"pasta","Pasta",20,None),
    ]
    return make_topic_registry(categories=categories,topics=topics,subtopics=subs)


def source(kind="video",*,quote=True):
    m=fixture(content_kind=kind)
    if quote and kind in ("video","slideshow_video"):
        f=m.observations[0]
        transcript=VerifiedObservation(
            "transcript_segment","b"*64,sha256(QUOTE.encode("utf-8")).hexdigest(),
            f.asset_sha256,f.media_asset_id,"video_absolute",0,1500,
        )
        m=replace(m,observations=m.observations+(transcript,))
        m=replace(m,manifest_sha256=canonical_hash(m.payload()))
    return m


def context(m,*,excluded=()):
    removed=set(excluded)
    relevant=[o for o in m.observations if o.kind in (
        "transcript_segment","ocr_span","sampled_frame","slide_ref")]
    entries=[_entry(o,text_data=QUOTE if o.kind=="transcript_segment" else None)
             for o in relevant if o.ref_sha256 not in removed]
    missing=[{"kind":o.kind,"ref_sha256":o.ref_sha256,"reason":"observation_cap"}
             for o in relevant if o.ref_sha256 in removed]
    p={
        "schema_version":"group-b-context/v1","block":"B8",
        "project_id":m.project_id,"b_run_id":m.b_run_id,
        "video_id":m.video_id,"qdp_run_id":m.qdp_run_id,
        "ga_result_id":m.ga_result_id,
        "ga_result_sha256":m.ga_result_sha256,
        "source_policy_version_id":m.source_policy_version_id,
        "source_evidence_manifest_sha256":m.manifest_sha256,
        "input_content_kind":m.content_kind,
        "delivery_state":"not_sent","media_delivery_state":"opaque_refs_not_attached",
        "entries":entries,"omitted":missing,
        "coverage":{
            "source_observations":len(m.observations),
            "relevant_observations":len(relevant),
            "selected_observations":len(entries),
            "omitted_relevant":len(missing),
            "selected_image_refs":sum(x["kind"] in ("sampled_frame","slide_ref")
                                      for x in entries),
            "available_frame_refs":sum(x.kind=="sampled_frame" for x in relevant),
            "available_slide_refs":sum(x.kind=="slide_ref" for x in relevant),
            "coverage_complete":not missing,
        }
    }
    return ContextPacket(p,canonical_hash(p),1200,2200)


def output(m,*,category="Fitness",topic="Strength Training",subtopic="Squats",
           confidence="high"):
    refs=[o.ref_sha256 for o in m.observations
          if o.kind in ("sampled_frame","slide_ref")]
    if any(o.kind=="transcript_segment" for o in m.observations):
        refs.append("b"*64)
    return {
        "category_label":category,"topic_label":topic,
        "subtopic_label":subtopic,
        "confidence":confidence,"evidence_refs":refs,
    }


def response(candidate):
    return VerifiedChatResponse(
        "fixture/topic",REQ,"fixture-response-b8",copy.deepcopy(candidate),
        {"prompt_tokens":120,"completion_tokens":50},"stop"
    )


class Receipt:
    def __init__(self,accepted=True):
        self.accepted=accepted
        self.calls=0
    def verify_b8_response(self,reply,packet,images):
        self.calls+=1
        return (self.accepted is True and reply.request_sha256==REQ and
                reply.response_id=="fixture-response-b8" and
                packet.verify_digest() and type(images) is tuple)


class TestB8(unittest.TestCase):
    def setUp(self):
        self.m=source()
        self.p=context(self.m)
        self.reg=taxonomy()
        self.raw=output(self.m)

    def check(self,*,m=None,p=None,reg=None,raw=None,receipt=None,images=None):
        m=self.m if m is None else m
        p=self.p if p is None else p
        raw=self.raw if raw is None else raw
        if images is None:
            selected={e["ref_sha256"] for e in p.payload["entries"]}
            images=tuple(o.ref_sha256 for o in m.observations
                         if o.ref_sha256 in selected and
                         o.kind in ("sampled_frame","slide_ref"))
        return validate_b8_topic(
            manifest=m,packet=p,registry=self.reg if reg is None else reg,
            response=response(raw),
            receipt_verifier=Receipt() if receipt is None else receipt,
            delivered_image_refs=images,
        )

    def deny(self,**kwargs):
        with self.assertRaises(B8TopicError):
            self.check(**kwargs)

    def test_label_only_schema_and_sql005_canonical_ids(self):
        _validate_schema(B8_OUTPUT_SCHEMA)
        _validate_json_value(B8_OUTPUT_SCHEMA,self.raw)
        self.assertEqual(len(B8_OUTPUT_SCHEMA["properties"]),5)
        self.assertNotIn("topic_id",B8_OUTPUT_SCHEMA["properties"])
        p=self.check()
        self.assertTrue(p.verify_digest())
        self.assertTrue(p.can_persist)
        self.assertEqual(p.db_fields(),{
            "category_id":1,"topic_id":10,"subtopic_id":100,
            "taxonomy_version":"1.0","needs_review":False,
        })
        self.assertEqual(p.payload["registry_snapshot_sha256"],
                         self.reg.snapshot_sha256)

    def test_alias_merged_to_same_parent_canonical_target(self):
        raw=output(self.m,topic="Powerlifting",subtopic="")
        p=self.check(raw=raw)
        self.assertTrue(p.can_persist)
        self.assertEqual(p.db_fields()["topic_id"],10)
        self.assertEqual(p.payload["alias_levels"],["topic"])
        self.assertEqual(p.db_fields()["subtopic_id"],None)
        cat=taxonomy(category_merged=True)
        raw=output(self.m,category="Sport",topic="Strength Training")
        p=self.check(reg=cat,raw=raw)
        self.assertFalse(p.can_persist)
        self.assertIn("category_merge_requires_topic_review",p.payload["review_reasons"])

    def test_cross_parent_and_new_topic_never_auto_created(self):
        for updates,expected in (
            ({"topic":"Recipes"},"new_or_cross_category_topic_requires_review"),
            ({"topic":"Kettlebell Mastery"},"new_or_cross_category_topic_requires_review"),
            ({"category":"Nutrition"},"new_category_requires_review"),
            ({"subtopic":"Burpees"},"new_or_cross_topic_subtopic_requires_review"),
        ):
            raw=output(self.m,**updates)
            with self.subTest(updates=updates):
                p=self.check(raw=raw)
                self.assertFalse(p.can_persist)
                self.assertTrue(p.payload["needs_review"])
                self.assertIn(expected,p.payload["review_reasons"])
                with self.assertRaises(B8TopicError):
                    p.db_fields()

    def test_duplicate_semantic_names_cannot_pick_random_existing_id(self):
        reg=taxonomy(duplicate=True)
        p=self.check(reg=reg)
        self.assertFalse(p.can_persist)
        self.assertIn("ambiguous_semantic_registry_match",p.payload["review_reasons"])

    def test_wrong_parent_merge_and_cycle_rejected_by_registry_builder(self):
        cats=[TopicNode(1,"fitness","Fitness",None,None)]
        for records in (
            [TopicNode(10,"a","A",1,20),
             TopicNode(20,"b","B",1,10)],
            [TopicNode(10,"a","A",1,20),
             TopicNode(20,"b","B",2,None)],
        ):
            with self.assertRaises(TopicRegistryError):
                make_topic_registry(categories=cats,
                    topics=records,subtopics=[])

    def test_normalization_is_conservative_not_fuzzy_synonym_invention(self):
        self.assertEqual(normalize_label("  STRENGTH  Training "),
                         "strength training")
        raw=output(self.m,category=" fitness ",
                   topic="STRENGTH  Training",subtopic="SQUATS")
        self.assertTrue(self.check(raw=raw).can_persist)
        raw=output(self.m,topic="Weightlifting")
        self.assertFalse(self.check(raw=raw).can_persist)
        with self.assertRaises(TopicRegistryError):
            normalize_label("")

    def test_new_or_unknown_without_authenticated_response_no_sql(self):
        p=unknown_b8(manifest=self.m,reason="provider_unavailable")
        self.assertTrue(p.verify_digest())
        self.assertFalse(p.can_persist)
        self.assertEqual(p.payload["classification_status"],"unknown")
        with self.assertRaises(B8TopicError):
            p.db_fields()
        raw=output(self.m,category="",topic="")
        q=self.check(raw=raw)
        self.assertFalse(q.can_persist)
        self.assertIn("b8_model_topic_unknown",q.payload["review_reasons"])

    def test_sparse_selected_refs_or_low_confidence_requires_review(self):
        m=source()
        subset=m.observations[0].ref_sha256
        p=context(m,excluded=(subset,))
        raw=output(m)
        raw["evidence_refs"]=[r for r in raw["evidence_refs"] if r!=subset]
        result=self.check(m=m,p=p,raw=raw)
        self.assertFalse(result.can_persist)
        self.assertIn("b8_selected_context_incomplete",result.payload["review_reasons"])
        raw=output(self.m,confidence="low")
        self.assertFalse(self.check(raw=raw).can_persist)

    def test_visual_only_carousel_and_wrong_content_type(self):
        m=source(kind="photo_carousel")
        p=self.check(m=m,p=context(m),raw=output(m))
        self.assertTrue(p.can_persist)
        unsupported=source(kind="image_post")
        self.deny(m=unsupported,p=context(unsupported),
                  raw=output(unsupported))

    def test_mixed_carousel_video_slide_source_anchored_topic(self):
        m=source(kind="mixed_carousel")
        assets=list(m.assets)
        original=list(m.observations)
        assets[0]=replace(assets[0],media_kind="video",duration_ms=2500)
        first=original[0]
        original[0]=replace(first,kind="sampled_frame",timebase="video_absolute",
                            start_ms=0,end_ms=0)
        original.append(VerifiedObservation(
            "transcript_segment","b"*64,sha256(QUOTE.encode()).hexdigest(),
            assets[0].source_sha256,assets[0].asset_id,
            "video_absolute",0,1500,1,
        ))
        m=replace(m,assets=tuple(assets),observations=tuple(original))
        m=replace(m,manifest_sha256=canonical_hash(m.payload()))
        p=self.check(m=m,p=context(m),raw=output(m))
        self.assertTrue(p.can_persist)
        self.assertEqual(p.db_fields()["topic_id"],10)
        damaged=list(m.observations)
        damaged[-1]=replace(damaged[-1],slide_index=2)
        forged=replace(m,observations=tuple(damaged))
        forged=replace(forged,manifest_sha256=canonical_hash(forged.payload()))
        self.deny(m=forged,p=context(forged),raw=output(forged))

    def test_forged_source_sha_ocr_or_unselected_image_denied(self):
        p=copy.deepcopy(self.p)
        p.payload["entries"][-1]["untrusted_text"]="changed by prompt injection"
        p=replace(p,packet_sha256=canonical_hash(p.payload))
        self.deny(p=p)
        p=copy.deepcopy(self.p)
        p.payload["coverage"]["relevant_observations"]+=1
        p=replace(p,packet_sha256=canonical_hash(p.payload))
        self.deny(p=p)
        raw=output(self.m)
        raw["evidence_refs"]=["c"*64]
        self.deny(raw=raw)
        self.deny(images=())
        self.deny(receipt=Receipt(False))
        self.deny(receipt=object())
        wrong=replace(self.m,manifest_sha256="f"*64)
        self.deny(m=wrong)

    def test_registry_tamper_and_invalid_enum_response_denied(self):
        reg=replace(self.reg,snapshot_sha256="f"*64)
        self.deny(reg=reg)
        raw=output(self.m,confidence="superhigh")
        self.deny(raw=raw)
        raw=output(self.m)
        raw["category_id"]=1
        self.deny(raw=raw)

    def test_strict_unsent_multimodal_request_with_original_bytes(self):
        model=ModelDescriptor(
            "test/vision","e"*64,8000,1024,
            frozenset(("text","image")),frozenset(("text",)),
            frozenset(("structured_outputs",)),
            Decimal("0.000001"),Decimal("0.000002"),
            Decimal("0.0002"),Decimal("0.00001"),
        )
        cat=ModelCatalogSnapshot((model,),"e"*64,"fixture",True)
        with self.assertRaises(ProviderFailure):
            prepare_b8_request(catalog=cat,model=model.slug,packet=self.p,
                               max_output_tokens=500)
        attach=tuple(VerifiedImage(o.ref_sha256,"image/png",IMAGE_BYTES)
                     for o in self.m.observations if o.kind=="sampled_frame")
        q=prepare_b8_request(catalog=cat,model=model.slug,packet=self.p,
                             max_output_tokens=500,attachments=attach)
        self.assertTrue(q.verify_digest())
        self.assertFalse(q.body["stream"])
        self.assertIn("Search Planner",q.body["messages"][0]["content"])
        self.assertEqual(q.request_sha256,prepare_b8_request(
            catalog=cat,model=model.slug,packet=self.p,
            max_output_tokens=500,attachments=attach).request_sha256)
        bad=attach[:-1]+(
            VerifiedImage(attach[-1].ref_sha256,"image/png",b"bad"),)
        with self.assertRaises(ProviderFailure):
            prepare_b8_request(catalog=cat,model=model.slug,packet=self.p,
                               max_output_tokens=500,attachments=bad)


if __name__=="__main__":
    unittest.main()
