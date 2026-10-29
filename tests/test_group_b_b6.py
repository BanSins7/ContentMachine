"""GB-MOD-18 synthetic B6 detector contract tests (NO real OCR/video/model).

Fake verifier in this suite is NOT an authenticated production detector.
"""
from __future__ import annotations

import copy
from dataclasses import replace
from hashlib import sha256
import unittest

from intelligence.group_b.blocks.b6_subtitles import (
    B6SubtitleError, TYPES, STYLE_FIELDS, validate_b6_subtitles, unknown_b6,
)
from intelligence.group_b.context.evidence import VerifiedObservation
from intelligence.group_b.context.builder import ContextPacket, _entry
from intelligence.group_b.contracts import canonical_hash
from test_group_b_b2 import fixture


def scene(*, kind="video", with_ocr=True):
    m = fixture(content_kind=kind)
    if with_ocr and kind in ("video", "slideshow_video"):
        original = m.observations
        captions = tuple(VerifiedObservation(
            "ocr_span", f"{50+i:064x}", sha256((f"subtitle {i}").encode()).hexdigest(),
            frame.asset_sha256, frame.media_asset_id, "video_absolute",
            frame.start_ms, frame.start_ms + 500, None, frame.ref_sha256,
        ) for i, frame in enumerate(original[:2]))
        m = replace(m, observations=m.observations + captions)
        m = replace(m, manifest_sha256=canonical_hash(m.payload()))
    entries = []
    for o in m.observations:
        entries.append(_entry(o, text_data=(
            "subtitle " + str(int(o.ref_sha256, 16)-50)
            if o.kind == "ocr_span" else None
        )))
    relevant = [o for o in m.observations
                if o.kind in ("ocr_span", "sampled_frame")]
    selected_images = sum(o.kind == "sampled_frame" for o in relevant)
    p = {
        "schema_version":"group-b-context/v1", "block":"B6",
        "project_id":m.project_id, "b_run_id":m.b_run_id,
        "video_id":m.video_id, "qdp_run_id":m.qdp_run_id,
        "ga_result_id":m.ga_result_id,
        "ga_result_sha256":m.ga_result_sha256,
        "source_policy_version_id":m.source_policy_version_id,
        "source_evidence_manifest_sha256":m.manifest_sha256,
        "input_content_kind":m.content_kind,
        "delivery_state":"not_sent", "media_delivery_state":"opaque_refs_not_attached",
        "entries":entries, "omitted":[],
        "coverage":{
            "source_observations":len(m.observations),
            "relevant_observations":len(relevant),
            "selected_observations":len(relevant),
            "omitted_relevant":0,
            "selected_image_refs":selected_images,
            "available_frame_refs":selected_images,
            "available_slide_refs":0,
            "coverage_complete":True,
        },
    }
    return m, ContextPacket(p,canonical_hash(p),1024,3000)


def scan(m, *, subtitle_type="standard", confidence="high",
         inspection_scope="full_original_video"):
    frames = [o.ref_sha256 for o in m.observations if o.kind == "sampled_frame"]
    captions = [o.ref_sha256 for o in m.observations if o.kind == "ocr_span"]
    positive = subtitle_type not in ("none","unknown")
    styles = {
        "position":"bottom","font_size":"medium","font_style":"bold",
        "text_color":"#FFFFFF","highlight_color":None,
        "background_style":"shadow","display_mode":"phrase",
    } if positive else {field:None for field in STYLE_FIELDS}
    return {
        "schema_version":"group-b-b6-subtitles/v1",
        "project_id":m.project_id, "b_run_id":m.b_run_id,
        "video_id":m.video_id, "qdp_run_id":m.qdp_run_id,
        "ga_result_id":m.ga_result_id,
        "ga_result_sha256":m.ga_result_sha256,
        "source_policy_version_id":m.source_policy_version_id,
        "source_evidence_manifest_sha256":m.manifest_sha256,
        "content_kind":m.content_kind,
        "inspection_scope":inspection_scope,"subtitle_type":subtitle_type,
        "confidence":confidence,
        "subtitle_ocr_refs":captions if positive else [],
        "subtitle_frame_refs":frames[:2] if positive else [],
        "dynamic_frame_refs":frames[:2] if subtitle_type == "dynamic" else [],
        "style_evidence_refs":frames[:2] if positive else [],
        "style":styles,
        "inspector_evidence_sha256":"d"*64,
    }


class Detector:
    def __init__(self, decision=True):
        self.decision = decision
        self.calls = 0
    def verify_b6_scan(self, manifest, packet, candidate):
        self.calls += 1
        return self.decision is True and manifest.verify_digest() and packet.verify_digest()


class TestB6(unittest.TestCase):
    def setUp(self):
        self.m, self.p = scene()
        self.s = scan(self.m)

    def check(self, m=None, p=None, s=None, detector=None):
        return validate_b6_subtitles(
            manifest=self.m if m is None else m,
            packet=self.p if p is None else p,
            scan=self.s if s is None else s,
            detector_verifier=Detector() if detector is None else detector,
        )

    def deny(self, **kw):
        with self.assertRaises(B6SubtitleError):
            self.check(**kw)

    def test_exact_three_types_and_seven_fields(self):
        self.assertEqual(TYPES, ("none", "standard", "dynamic"))
        self.assertEqual(len(STYLE_FIELDS), 7)
        result = self.check()
        self.assertTrue(result.verify_digest())
        self.assertTrue(result.can_persist)
        self.assertEqual(result.db_fields(), {
            "subtitle_type":"standard",**self.s["style"],
        })
        self.assertEqual(result.payload["review_reasons"], [])

    def test_dynamic_moving_captions_and_word_by_word(self):
        s=scan(self.m,subtitle_type="dynamic")
        output=self.check(s=s)
        self.assertTrue(output.can_persist)
        self.assertEqual(output.db_fields()["subtitle_type"],"dynamic")
        s["dynamic_frame_refs"]=[]
        s["style"]["display_mode"]="word_by_word"
        self.assertTrue(self.check(s=s).can_persist)
        s["style"]["display_mode"]="line"
        self.deny(s=s)

    def test_standard_cannot_hide_dynamic_word_or_motion(self):
        s=copy.deepcopy(self.s)
        s["style"]["display_mode"]="word_by_word"
        self.deny(s=s)
        s=copy.deepcopy(self.s)
        s["dynamic_frame_refs"]=s["subtitle_frame_refs"]
        self.deny(s=s)

    def test_attested_none_only_on_full_original_video(self):
        m,p=scene(with_ocr=False)
        none=scan(m,subtitle_type="none")
        output=self.check(m=m,p=p,s=none)
        self.assertTrue(output.can_persist)
        self.assertEqual(output.db_fields(),{"subtitle_type":"none",
            **{field:None for field in STYLE_FIELDS}})
        none["inspection_scope"]="partial"
        partial=self.check(m=m,p=p,s=none)
        self.assertFalse(partial.can_persist)
        with self.assertRaises(B6SubtitleError):
            partial.db_fields()
        none["subtitle_frame_refs"]=[m.observations[0].ref_sha256]
        self.deny(m=m,p=p,s=none)

    def test_no_ocr_and_no_scan_never_implies_no_subtitles(self):
        m,p=scene(with_ocr=False)
        s=scan(m)
        self.deny(m=m,p=p,s=s)
        absent=unknown_b6(manifest=m,reason="ocr_unavailable")
        self.assertTrue(absent.verify_digest())
        self.assertFalse(absent.can_persist)
        self.assertIsNone(absent.payload["subtitle_type"])
        with self.assertRaises(B6SubtitleError):
            absent.db_fields()

    def test_slide_text_never_becomes_subtitles(self):
        for kind in ("photo_carousel","mixed_carousel","image_post"):
            m,p=scene(kind=kind,with_ocr=False)
            with self.subTest(kind=kind), self.assertRaises(B6SubtitleError):
                validate_b6_subtitles(
                    manifest=m,packet=p,scan=scan(m,subtitle_type="none"),
                    detector_verifier=Detector(),
                )
        m,p=scene(kind="slideshow_video")
        self.assertTrue(self.check(m=m,p=p,s=scan(m)).can_persist)

    def test_forged_ocr_frame_link_hidden_evidence_and_bad_counters(self):
        for edit in ("ocr_link","dropped","fake_count","bad_text","bad_time"):
            m,p=scene()
            p=copy.deepcopy(p)
            if edit == "ocr_link":
                p.payload["entries"][-1]["frame_ref_sha256"]="e"*64
            elif edit == "dropped":
                p.payload["entries"].pop()
            elif edit == "fake_count":
                p.payload["coverage"]["source_observations"]=0
            elif edit == "bad_text":
                p.payload["entries"][-1]["untrusted_text"]="injected captions"
            else:
                original=list(m.observations)
                original[0]=replace(original[0],end_ms=500)
                m=replace(m,observations=tuple(original))
                m=replace(m,manifest_sha256=canonical_hash(m.payload()))
            p=replace(p,packet_sha256=canonical_hash(p.payload))
            with self.subTest(edit=edit),self.assertRaises(B6SubtitleError):
                self.check(m=m,p=p,s=scan(m))

    def test_style_sql005_enums_colors_and_none_null_contract(self):
        for field,value in (
            ("position","left"),("font_size","huge"),
            ("font_style","italic"),("text_color","red"),
            ("highlight_color","#12345G"),
            ("background_style","gradient"),("display_mode","typewriter"),
        ):
            s=copy.deepcopy(self.s)
            s["style"][field]=value
            with self.subTest(field=field),self.assertRaises(B6SubtitleError):
                self.check(s=s)
        m,p=scene(with_ocr=False)
        n=scan(m,subtitle_type="none")
        n["style"]["position"]="bottom"
        self.deny(m=m,p=p,s=n)

    def test_detector_required_and_low_confidence_never_sql(self):
        self.deny(detector=object())
        self.deny(detector=Detector(False))
        s=scan(self.m,confidence="low")
        p=self.check(s=s)
        self.assertFalse(p.can_persist)
        self.assertIn("subtitle_low_confidence",p.payload["review_reasons"])
        s=scan(self.m,inspection_scope="partial")
        self.assertFalse(self.check(s=s).can_persist)
        s=scan(self.m,subtitle_type="unknown")
        self.assertFalse(self.check(s=s).can_persist)

    def test_wrong_source_and_unselected_ocr_references_denied(self):
        s=copy.deepcopy(self.s)
        s["ga_result_sha256"]="f"*64
        self.deny(s=s)
        s=copy.deepcopy(self.s)
        s["subtitle_ocr_refs"]=["e"*64]
        self.deny(s=s)
        s=copy.deepcopy(self.s)
        s["subtitle_frame_refs"]=s["subtitle_frame_refs"][:1]
        self.deny(s=s)
        s=copy.deepcopy(self.s)
        s["subtitle_ocr_refs"]+=s["subtitle_ocr_refs"][:1]
        self.deny(s=s)


if __name__=="__main__":
    unittest.main()
