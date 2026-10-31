"""Offline tests for Marketing-native B1–B8 (no QDP or paid provider)."""
import copy
import hashlib
import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from intelligence.market_intelligence.extended_b import assemble_b1_b8
from intelligence.market_intelligence.group_b_report import (
    build_group_b_report,verify_group_b_report)
from intelligence.market_intelligence.group_b_taxonomy import resolve_topic
from intelligence.market_intelligence.group_b_subtitle_style import subtitle_style_candidates
from intelligence.market_intelligence.group_a_report import build_group_a_report
from intelligence.market_intelligence._copied.group_b.topic_registry import (
    TopicNode,make_topic_registry)
from intelligence.market_intelligence._copied.group_b.blocks.b1_hook import MECHANISMS
from intelligence.market_intelligence._copied.group_b.blocks.b2_format import VIDEO_FORMATS
from intelligence.market_intelligence._copied.group_b.blocks.b3_story import ROLES
from intelligence.market_intelligence._copied.group_b.blocks.b7_cta import CTA_TYPES

SHA="a"*64
REF="ga-frame:f001-"+SHA
FREF="ga-frame:f002-"+("b"*64)


def _catalog():
    return make_topic_registry(
        categories=[TopicNode(1,"wellness","Wellness",None,None)],
        topics=[TopicNode(2,"face_yoga","Face Yoga",1,None)],
        subtopics=[TopicNode(3,"depuff","Depuff",2,None)]
    )

def _technical():
    m=SimpleNamespace(
        duration_sec=Decimal("5.000"),
        aspect_ratio="4:3",
        scenes=(SimpleNamespace(start_sec=Decimal("0.000"),
                  end_sec=Decimal("5.000"),index=1),),
        method_version="offline-a-tech")
    return SimpleNamespace(measurement=m)

def _visual():
    from intelligence.market_intelligence._copied.group_a.contracts_v1 import canonical_sha256
    record={
        "source_media_sha256":SHA,
        "frames":[{"ref":REF,"png_sha256":SHA,"observed_sec":"0.250"},
                  {"ref":FREF,"png_sha256":"b"*64,"observed_sec":"3.250"}],
        "group_a_ocr":{
           "schema_version":"ga-frame-ocr/v1","source_media_sha256":SHA,
           "frames":[{
              "frame_ref":REF,"observed_sec":"0.250",
              "state":"text_observed","width":320,"height":240,
              "words":[{"text":"Face","bbox":{"x":50,"y":183,"width":48,"height":20}}]
           }]
        },
        "group_a_subtitles":{"has_subtitles":"unknown","proof_kind":"none"},
    }
    record["group_a_ocr"]["observation_sha256"]=canonical_sha256(record["group_a_ocr"])
    return record


def _semantic():
    return {"state":"model_observed_partial",
        "evidence_frame_refs":[REF,FREF],
        "hook_mechanisms":[MECHANISMS[0]],"hook_text":"How to depuff?",
        "hook_visual_description":"Visible hand",
        "format_family":VIDEO_FORMATS[0],
        "story_roles":[ROLES[0]],
        "cta_type":CTA_TYPES[0],"cta_text":"Follow",
        "timeline_windows":[
           {"start_sec":0.25,"end_sec":3.25,
            "state":"model_observed_partial","evidence_frame_refs":[REF],
            "story_roles":[ROLES[0]],"cta_type":CTA_TYPES[0],
            "cta_text":"Follow"},
           {"start_sec":3.25,"end_sec":4.99,
            "state":"model_observed_partial","evidence_frame_refs":[FREF],
            "story_roles":[ROLES[0]],"cta_type":CTA_TYPES[0],
            "cta_text":"Follow"},
        ],"hook_window_observed":True}

def _extras():
    return {"state":"model_observed_partial","evidence_frame_refs":[REF],
        "person_visible":"present","product_visible":"unknown",
        "logo_visible":"unknown",
        "positive_feature_evidence":{
            "person_visible":[REF],"product_visible":[],"logo_visible":[]},
        "topic_hint":"depuff face",
        "subtitle_style_proposals":[{
            "position":"bottom","font_size":"medium",
            "font_style":"bold","text_color":"#FFFFFF",
            "highlight_color":None,"background_style":"shadow",
            "display_mode":"line","evidence_frame_refs":[REF]}],
        "topic_candidates":["depuff face"],
        "topic_label_proposals":[{
           "category_label":"Wellness","topic_label":"Face Yoga",
           "subtopic_label":"Depuff","evidence_frame_refs":[REF],
           "window_start_sec":"0.250"}]}


class NativeGroupB(unittest.TestCase):
    def _build(self):
        visual=_visual()
        ga=build_group_a_report(_technical(),visual,SHA)
        visual["group_a_report"]=ga
        with patch("intelligence.market_intelligence.group_b_taxonomy._catalog",
                   return_value=(_catalog(),None)):
            b=assemble_b1_b8(visual=visual,inferred=_semantic(),
                measurements=_technical(),transcript=[],extras=_extras())
        report=build_group_b_report(source_sha256=SHA,
            group_a=ga,visual=visual,group_b=b,
            duration_sec=Decimal("5.000"))
        return visual,ga,b,report

    def test_eight_blocks_and_sha_pinned_report(self):
        visual,ga,b,report=self._build()
        self.assertEqual(set(b["blocks"]),{"B"+str(i) for i in range(1,9)})
        self.assertTrue(verify_group_b_report(
            report,SHA,ga["report_sha256"]))
        self.assertFalse(report["original_full_group_b_certified"])
        self.assertEqual(b["blocks"]["B4"]["positive_feature_evidence"]["person_visible"],[REF])
        self.assertEqual(b["blocks"]["B6"]["ocr_style_candidates"]["observations"][0]["position"],"bottom")
        self.assertEqual(b["blocks"]["B6"]["model_style_candidates"][0]["font_style"],"bold")
        self.assertEqual(b["blocks"]["B8"]["approved_topic_id"],2)
        self.assertEqual(b["blocks"]["B8"]["taxonomy_match"]["subtopic_id"],3)

    def test_b1_must_be_early_window(self):
        bad=_semantic()
        bad["timeline_windows"][0]["state"]="unavailable"
        bad["hook_window_observed"]=False
        with patch("intelligence.market_intelligence.group_b_taxonomy._catalog",
                   return_value=(_catalog(),None)):
            b=assemble_b1_b8(visual=_visual(),inferred=bad,
                measurements=_technical(),transcript=[],extras=_extras())
        self.assertEqual(b["blocks"]["B1"]["state"],"unavailable")
        self.assertIsNone(b["blocks"]["B1"]["hook_text"])

    def test_b3_and_b7_unobserved_intervals_rejected(self):
        visual,ga,b,report=self._build()
        for block,key in (("B3","narrative_segments"),
                          ("B7","cta_occurrences")):
            tamper=copy.deepcopy(b)
            tamper["blocks"][block][key][0]["start_sec"]=4.0
            with self.assertRaises(ValueError):
                build_group_b_report(source_sha256=SHA,group_a=ga,
                    visual=visual,group_b=tamper,duration_sec="5.000")

    def test_b4_negative_and_forged_evidence_rejected(self):
        visual,ga,b,report=self._build()
        tamper=copy.deepcopy(b)
        tamper["blocks"]["B4"]["visual_features"]["logo_visible"]="absent"
        with self.assertRaises(ValueError):
            build_group_b_report(source_sha256=SHA,group_a=ga,
                visual=visual,group_b=tamper,duration_sec="5.000")
        tamper=copy.deepcopy(b)
        tamper["blocks"]["B4"]["evidence_frame_refs"]=["not-a-real-frame"]
        with self.assertRaises(ValueError):
            build_group_b_report(source_sha256=SHA,group_a=ga,
                visual=visual,group_b=tamper,duration_sec="5.000")

    def test_b8_and_hook_text_tamper_breaks_entire_payload(self):
        from intelligence.market_intelligence.group_b_report import verify_group_b_payload
        visual,ga,b,report=self._build()
        self.assertTrue(verify_group_b_payload(report,b))
        for field in ("hook_text","hook_mechanisms"):
            bad=copy.deepcopy(b)
            if field=="hook_text":
                bad["blocks"]["B1"]["hook_text"]="injected unproven claim"
            else:
                bad["blocks"]["B1"]["hook_mechanisms"]=[]
            self.assertFalse(verify_group_b_payload(report,bad))
        bad=copy.deepcopy(b)
        bad["blocks"]["B8"]["approved_topic_id"]=99999
        self.assertFalse(verify_group_b_payload(report,bad))

    def test_source_a_immutable_digest_enforced(self):
        visual,ga,b,report=self._build()
        ga["duration_sec"]="12.000"
        with self.assertRaises(ValueError):
            build_group_b_report(source_sha256=SHA,group_a=ga,
                visual=visual,group_b=b,duration_sec="5.000")
        modified=copy.deepcopy(report)
        modified["blocks"]["B1"]["state"]="completed"
        self.assertFalse(verify_group_b_report(modified,SHA,report["group_a_report_sha256"]))

    def test_hook_and_cta_text_need_verbatim_ga_observation(self):
        from intelligence.market_intelligence.group_b_text_evidence import match_text
        visual=_visual()
        actual=match_text("Face",visual,start_sec=0,end_sec=1,
                          allowed_refs=[REF])
        self.assertEqual(actual["state"],"verbatim_observed")
        self.assertEqual(actual["evidence_frame_refs"],[REF])
        model_only=match_text("Buy the product",visual,start_sec=0,end_sec=1,
                              allowed_refs=[REF])
        self.assertEqual(model_only["state"],"model_proposal_only")
        no_refs=match_text("Face",visual,start_sec=0,end_sec=1,
                           allowed_refs=[])
        self.assertNotEqual(no_refs["state"],"verbatim_observed")
        visual["asr_segments"]=[{"start_sec":0.25,"end_sec":0.9,
                                 "text":"Buy the product"}]
        visual["speech"]={"video_time_verified":False}
        self.assertNotEqual(match_text("Buy the product",visual,
             start_sec=0,end_sec=1)["state"],"verbatim_observed")
        visual["speech"]["video_time_verified"]=True
        audible=match_text("Buy the product",visual,start_sec=0,end_sec=1)
        self.assertEqual(audible["state"],"verbatim_observed")
        self.assertEqual(audible["matched_sources"][0]["kind"],"verified_ga11_speech")

    def test_b8_unconfigured_and_bad_hierarchy_unknown(self):
        proposed={"category_label":"Wellness","topic_label":"Face Yoga",
                  "subtopic_label":"Depuff"}
        good=resolve_topic(proposed,[REF],catalog=_catalog())
        self.assertTrue(good["mi_approved_topic_match"])
        self.assertEqual(good["topic_id"],2)
        none=resolve_topic({"category_label":"Made Up",
                            "topic_label":"Face Yoga","subtopic_label":None},
                           [REF],catalog=_catalog())
        self.assertFalse(none["mi_approved_topic_match"])
        with patch("intelligence.market_intelligence.group_b_taxonomy._catalog",
                   return_value=(None,"taxonomy_not_configured")):
            missing=resolve_topic(proposed,[REF])
        self.assertEqual(missing["state"],"observed_unmapped")

    def test_pinned_taxonomy_rejects_modified_and_missing_catalog(self):
        from intelligence.market_intelligence.group_b_taxonomy import _catalog
        payload={"schema_version":"mi-b8-taxonomy/v1",
                 "categories":[{"id":1,"slug":"wellness","name":"Wellness",
                                "parent_id":None,"merged_into_id":None}],
                 "topics":[{"id":2,"slug":"face_yoga","name":"Face Yoga",
                            "parent_id":1,"merged_into_id":None}],
                 "subtopics":[{"id":3,"slug":"depuff","name":"Depuff",
                               "parent_id":2,"merged_into_id":None}]}
        raw=json.dumps(payload,separators=(",",":"),ensure_ascii=False)
        pin=hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with patch("intelligence.market_intelligence.group_b_taxonomy.config.setting",
                   side_effect=lambda name,default="":{
                       "CM_MARKET_B_TAXONOMY_JSON":raw,
                       "CM_MARKET_B_TAXONOMY_SHA256":pin}.get(name,default)):
            verified,reason=_catalog()
            self.assertIsNone(reason)
            self.assertTrue(verified.verify_digest())
        with patch("intelligence.market_intelligence.group_b_taxonomy.config.setting",
                   side_effect=lambda name,default="":{
                       "CM_MARKET_B_TAXONOMY_JSON":raw+" ",
                       "CM_MARKET_B_TAXONOMY_SHA256":pin}.get(name,default)):
            invalid,reason=_catalog()
            self.assertIsNone(invalid)
            self.assertEqual(reason,"taxonomy_sha256_mismatch")

    def test_b6_style_cannot_cite_unobserved_frame(self):
        visual,ga,b,report=self._build()
        tampered=copy.deepcopy(b)
        tampered["blocks"]["B6"]["model_style_candidates"][0]["evidence_frame_refs"]=["invented"]
        with self.assertRaises(ValueError):
            build_group_b_report(source_sha256=SHA,group_a=ga,
                visual=visual,group_b=tampered,duration_sec="5.000")

    def test_b7_model_cannot_invent_destination_url(self):
        visual,ga,b,report=self._build()
        tampered=copy.deepcopy(b)
        tampered["blocks"]["B7"]["cta_occurrences"][0]["destination_url"]="https://example.org/invalid"
        with self.assertRaises(ValueError):
            build_group_b_report(source_sha256=SHA,
                group_a=ga,visual=visual,group_b=tampered,
                duration_sec="5.000")

    def test_ocr_geometry_cannot_invent_subtitle_presence(self):
        result=subtitle_style_candidates(_visual())
        self.assertEqual(result["state"],"sampled_geometry_observed")
        self.assertFalse(result["subtitle_type_certified"])
        self.assertIsNone(result["font_style"])
        without=_visual()
        without["group_a_ocr"]["source_media_sha256"]="c"*64
        self.assertEqual(subtitle_style_candidates(without)["state"],"unavailable")


if __name__=="__main__":
    unittest.main()
