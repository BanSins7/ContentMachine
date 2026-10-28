"""GB-MOD-23 deterministic review/gold contracts, no paid or external data."""
from __future__ import annotations

from dataclasses import replace
import json
import unittest

from intelligence.group_b.review import (
    ReviewError,make_decision,verify_decision,validate_effective_row,
    GoldSample,QualityPolicy,gold_sample_key,gold_split,select_gold_candidates,
    make_gold_set,evaluate_quality,
)
from intelligence.group_b.topic_registry import make_topic_registry,TopicNode

SHA="a"*64
KEY="group-b-atomic/v1:"+"b"*64


def correction(block="B8",target="topic",field="subtopic_id",value=None,
               *,index=None,previous_revision=0,previous_sha256=None,
               kind="correction",sample_key=None,gold_version=None):
    if kind=="gold_annotation" and sample_key is None:
        sample_key=gold_sample_key(analysis_id=11,block=block,
                                   target=target,index=index,field=field)
    return make_decision(
        kind=kind,analysis_id=11,analysis_key=KEY,
        manifest_sha256=SHA,previous_revision=previous_revision,
        previous_sha256=previous_sha256,block=block,target=target,
        index=index,field=field,value=value,
        evidence_refs=["e"*64],
        reason="Verified original-media classification correction",
        gold_version=gold_version,sample_key=sample_key,
    )


class TestReviewPure(unittest.TestCase):
    def deny(self,*args,**kwargs):
        with self.assertRaises(ReviewError):
            correction(*args,**kwargs)

    def test_immutable_version_sha_and_stale_chain(self):
        d=correction()
        self.assertTrue(d.verify_digest())
        self.assertTrue(verify_decision(d))
        self.assertEqual(d.payload["previous_revision"],0)
        next_=correction(previous_revision=1,previous_sha256=d.decision_sha256)
        self.assertTrue(verify_decision(next_))
        damaged=dict(d.payload,previous_sha256="f"*64)
        self.assertFalse(verify_decision(replace(d,payload=damaged)))

    def test_bad_sha_invalid_original_index_and_cross_block_field(self):
        self.deny("B7","occurrence","cta_type","follow",index=None)
        self.deny("B3","slide","role","hook",index=0)
        self.deny("B8","topic","is_question",True)
        self.deny("B8","topic","category_id",0)
        self.deny("B8","topic","topic_id",True)

    def test_all_eight_blocks_supported_but_never_fake_none(self):
        samples=[
            ("B1","hook","mechanisms",["problem"],None),
            ("B2","format","video_primary","talking_head",None),
            ("B3","interval","role","hook",1),
            ("B4","visual","ai_generated",None,None),
            ("B5","editing","cuts_per_minute","3.0000",None),
            ("B6","subtitle","subtitle_type","standard",None),
            ("B7","occurrence","cta_type","follow",1),
            ("B8","topic","category_id",1,None),
        ]
        for block,target,field,val,index in samples:
            with self.subTest(block=block):
                d=correction(block,target,field,val,index=index)
                self.assertTrue(verify_decision(d))
        self.deny("B6","subtitle","subtitle_type","made_up")
        self.deny("B7","occurrence","cta_type","none",index=1)

    def test_bad_decimals_url_color_and_boolean_fail_closed(self):
        for numeric in ("NaN","Infinity","-1","1.00001","abc"):
            with self.subTest(numeric=numeric):
                self.deny("B5","editing","cuts_per_minute",numeric)
        self.deny("B7","occurrence","destination_url",
                  "http://unverified.example",index=1)
        self.deny("B6","subtitle","text_color","#NOGOOD")
        self.deny("B4","visual","person_visible","false")
        self.deny("B1","hook","mechanisms",["problem","problem"])

    def test_correction_cannot_forge_gold_and_gold_must_pin_sample_identity(self):
        self.deny("B8","topic","topic_id",10,
                  gold_version="gold-v2")
        self.deny("B8","topic","topic_id",10,
                  kind="gold_annotation",gold_version="gold-v2",
                  sample_key="sample-fabricated")
        valid=correction("B8","topic","topic_id",10,
                         kind="gold_annotation",gold_version="gold-v2")
        self.assertTrue(verify_decision(valid))
        self.assertEqual(valid.payload["sample_key"],
                         gold_sample_key(analysis_id=11,block="B8",
                                         target="topic",index=None,field="topic_id"))

    def test_b5_measured_cross_field_consistency(self):
        row={"editing_type":"single_take","cut_count":0,
             "cuts_first_5_sec":0,"cuts_per_minute":"0",
             "avg_shot_duration":"12.00"}
        validate_effective_row("B5",row)
        row["cut_count"]=5
        with self.assertRaises(ReviewError):
            validate_effective_row("B5",row)
        row["editing_type"]="fast_cuts"
        row["cuts_per_minute"]="35.0000"
        validate_effective_row("B5",row)

    def test_b2_correction_cannot_change_original_media_kind(self):
        row={"video_primary":None,"video_secondary":None,
             "carousel_format":"explainer","_content_type":"video"}
        with self.assertRaises(ReviewError):
            validate_effective_row("B2",row)
        row["_content_type"]="carousel"
        validate_effective_row("B2",row)
        row["carousel_format"]=None
        row["video_primary"]="talking_head"
        with self.assertRaises(ReviewError):
            validate_effective_row("B2",row)

    def test_b6_none_does_not_imply_missing_full_source(self):
        row={"subtitle_type":"standard","position":"bottom",
             "font_size":"medium","font_style":"bold","text_color":"#FFFFFF",
             "highlight_color":None,"background_style":"shadow","display_mode":"line"}
        validate_effective_row("B6",row)
        row["subtitle_type"]="none"
        with self.assertRaises(ReviewError):
            validate_effective_row("B6",row)
        for field in ("position","font_size","font_style","text_color",
                      "background_style","display_mode"):
            row[field]=None
        validate_effective_row("B6",row)

    def test_b8_correction_must_revalidate_approved_hierarchy(self):
        reg=make_topic_registry(
            categories=[TopicNode(1,"fitness","Fitness",None,None)],
            topics=[TopicNode(10,"strength","Strength",1,None)],
            subtopics=[TopicNode(100,"squats","Squats",10,None)],
        )
        row={"category_id":1,"topic_id":10,"subtopic_id":100}
        validate_effective_row("B8",row,reg)
        row["subtopic_id"]=None
        validate_effective_row("B8",row,reg)
        row["topic_id"]=999
        with self.assertRaises(ReviewError):
            validate_effective_row("B8",row,reg)

    def test_stratified_sampling_deterministic_and_split_disjoint(self):
        candidates=[{"analysis_id":i,"block":block,"target":"topic",
                     "index":None,"field":"topic_id"}
                    for block in ("B7","B8") for i in range(1,35)]
        # B7 target is not a topic; to avoid an invalid synthetic sample,
        # use a real original occurrence per B7 sample instead.
        for c in candidates:
            if c["block"]=="B7":
                c.update(target="occurrence",index=1,field="cta_type")
        a=select_gold_candidates(candidates,gold_version="gold-v1",per_block=7)
        b=select_gold_candidates(list(reversed(candidates)),
                                 gold_version="gold-v1",per_block=7)
        self.assertEqual(a,b)
        self.assertEqual(len(a),14)
        self.assertEqual({v["block"] for v in a},{"B7","B8"})
        self.assertTrue(all(v["split"] in ("calibration","evaluation") for v in a))

    def test_gold_quality_pass_only_independent_consensus_and_support(self):
        samples=[]
        for i in range(100,300):
            key=gold_sample_key(analysis_id=i,block="B8",
                                target="topic",index=None,field="topic_id")
            if gold_split("quality-2026-v1",key)!="evaluation":
                continue
            samples.append(GoldSample(key,"B8","topic_id","topic",None,i,10,
                                      (("reviewer_A",10),("reviewer_B",10)),
                                      "evaluation"))
            if len(samples)==5:break
        gold=make_gold_set("quality-2026-v1",samples)
        policy=QualityPolicy("thresholds-v1",min_evaluation_samples=2,
                             min_class_support=2,min_precision=.95,min_recall=.95)
        score=evaluate_quality(gold,policy)
        self.assertEqual(score["quality_gate"],"pass")
        self.assertEqual(score["inter_annotator_agreement"],1.0)
        self.assertEqual(score["per_class"]["B8.topic_id:10"]["support"],5)
        self.assertEqual(score["macro_recall"],1.0)
        self.assertEqual(score["gold_sha256"],gold.gold_sha256)
        too_small=QualityPolicy("strict-v1",min_evaluation_samples=10)
        self.assertEqual(evaluate_quality(gold,too_small)["quality_gate"],"blocked")

    def test_disputed_gold_never_assumed_true_negative(self):
        result=[]
        for i in range(500,550):
            key=gold_sample_key(analysis_id=i,block="B8",
                                target="topic",index=None,field="topic_id")
            if gold_split("quality-2026-v1",key)=="evaluation":
                result.append(GoldSample(
                    key,"B8","topic_id","topic",None,i,10,
                    (("reviewer_A",10),("reviewer_B",20)),"evaluation"))
            if len(result)==3:break
        gold=make_gold_set("quality-2026-v1",result)
        score=evaluate_quality(gold,QualityPolicy("gate-v1",min_evaluation_samples=2,
                                                  min_class_support=1))
        self.assertEqual(score["quality_gate"],"blocked")
        self.assertEqual(score["disputed_or_under_review_count"],3)
        self.assertIsNone(score["macro_recall"])

    def test_no_predicted_class_without_support_can_pass(self):
        result=[]
        for i in range(600,650):
            key=gold_sample_key(analysis_id=i,block="B8",
                                target="topic",index=None,field="topic_id")
            if gold_split("quality-2026-v1",key)=="evaluation":
                result.append(GoldSample(
                    key,"B8","topic_id","topic",None,i,20,
                    (("reviewer_A",10),("reviewer_B",10)),"evaluation"))
            if len(result)==3:break
        gold=make_gold_set("quality-2026-v1",result)
        score=evaluate_quality(gold,QualityPolicy("gate-v1",min_evaluation_samples=2,
                                                  min_class_support=1))
        self.assertEqual(score["quality_gate"],"blocked")
        self.assertEqual(score["per_class"]["B8.topic_id:20"]["support"],0)
        self.assertEqual(score["per_class"]["B8.topic_id:10"]["recall"],0.0)


if __name__=="__main__":
    unittest.main()
