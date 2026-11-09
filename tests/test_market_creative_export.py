"""No-cost Marketing 100-post export contracts: mocks only; zero provider I/O."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from intelligence.market_intelligence.creative_export import make_export,export_sha
from scripts import prepare_market_tiktok_top100 as cli

PROJECT="a"*32
PROFILE="@faceyogilab"
SHA="c"*64
SID="sd_already_collected"
FROZEN="12345678-1234-4123-8123-123456789abc"


def fixture():
    selected=[]
    videos=[]
    evidence=[]
    c_rows=[]
    for i in range(1,101):
        is_carousel=i>50
        kind="photo_carousel" if is_carousel else "video"
        native=str(7600000000000000000+i)
        url=f"https://www.tiktok.com/{PROFILE}/{'photo' if is_carousel else 'video'}/{native}"
        views=i*1000
        p={"native_id":native,"url":url,"content_kind":kind,"views":views}
        selected.append(p)
        videos.append({**p,"video_id":i,"state":"analysis_partial","platform":"tiktok"})
        refs=["slide:abc","slide:def"] if is_carousel else ["ga-frame:001"]
        a={
            "report_sha256":"b"*64,
            "slide_count":2,
            "slide_observations":[
                {"slide_index":n,"ref":ref,"image_png_sha256":"e"*64,
                 "image_width":1080,"image_height":1920,
                 "ocr":{"state":"observed","words":[
                     "Hook text" if n==1 else "Use Face Yoga"]}}
                for n,ref in enumerate(refs,1)]
        } if is_carousel else {"report_sha256":"a"*64,"stages":{"ocr":{"state":"partial"}}}
        b1=({
            "state":"model_observed_partial","hook_text":"Hook text",
            "hook_text_verbatim_ocr_verified":True,
            "hook_visual":"person looking at camera",
            "hook_mechanisms":["curiosity"],
            "evidence_slide_refs":["slide:abc"],
            "hook_slide_index":1
        } if is_carousel else {
            "state":"model_observed_partial",
            "hook_text":"Hello","visual_hook":"person standing",
            "hook_mechanisms":[],
            "evidence_frame_refs":["ga-frame:001"],
            "verbatim_text_evidence":{"state":"verbatim_observed"},
            "hook_window_observed":True
        })
        b7=({
            "state":"model_observed_partial","cta_type":"soft",
            "cta_text":"Use Face Yoga","cta_text_verbatim_ocr_verified":True,
            "evidence_slide_refs":["slide:def"]
        } if is_carousel else {"state":"unavailable","cta_type":None,
                               "cta_text":None,"evidence_frame_refs":[]})
        blocks={f"B{j}":{"state":"unavailable"} for j in range(1,9)}
        blocks.update({
            "B1":b1,
            "B2":{"state":"model_observed_partial",
                  "carousel_format":"storytelling" if is_carousel else None,
                  "format_family":"tutorial" if not is_carousel else None},
            "B3":{"state":"model_observed_partial","story_roles":["hook"]},
            "B4":{"state":"model_observed_partial","visual_features":{"person":"present"}},
            "B6":{"state":"static_ocr_observed","slide_ocr":[
                {"slide_index":1,"words":["Hook text"]}]} if is_carousel else
                {"state":"partial","ocr_style_candidates":{}},
            "B7":b7
        })
        evidence.append({
            "video_id":i,"url":url,"platform":"tiktok","content_kind":kind,
            "mi_analysis_id":i,"creative_state":"slide_analysis_partial",
            "group_a":a,"group_b":{"report_sha256":"f"*64,"blocks":blocks},
            "visual_evidence":{
                "frames":[{"ref":"ga-frame:001","observed_sec":0.3,"png_sha256":"f"*64}],
                "group_a_ocr":{"status":"partial"}
            } if not is_carousel else {"slides":[]},
        })
        c_rows.append({"video_id":i,"platform_native_id":native,
                       "url":url,"views":views,
                       "C1":{"creator_ratio":"6.0"},
                       "C4":{"engagement_rate_fraction":"0.12"},
                       "publication_age":{"published_at":"2026-02-01T00:00:00+00:00"}})
    active={
        "project_id":PROJECT,"batch_id":2,"selection_sha256":SHA,
        "state":{"group_c_snapshot_id":FROZEN},"videos":videos,
    }
    selection={"posts":selected,"profile_snapshot_id":SID}
    frozen={"snapshot_id":FROZEN,"payload_sha256":"0"*64,
            "snapshot":{"video_evidence":evidence}}
    c_data={"project_id":PROJECT,"batch_id":2,
            "pinned_selection_sha256":SHA,"posts":c_rows}
    return active,selection,frozen,c_data


class CreativeEvidenceExport(unittest.TestCase):
    def test_complete_100_rows_ocr_hook_cta_and_c_join(self):
        active,selection,frozen,c_data=fixture()
        result=make_export(active=active,selection=selection,
                           selection_sha=SHA,frozen=frozen,c_data=c_data)
        self.assertEqual(len(result["posts"]),100)
        self.assertEqual(result["counts"]["photo_carousel"],50)
        self.assertEqual(result["counts"]["video"],50)
        self.assertEqual(result["counts"]["carousel_slides"],100)
        self.assertEqual(result["counts"]["carousel_ocr_observed_slides"],100)
        self.assertEqual(result["counts"]["hook_text_ocr_verified"],100)
        self.assertEqual(result["counts"]["cta_text_ocr_verified"],50)
        self.assertEqual(result["counts"]["C1_diagnostic_present"],100)
        self.assertEqual(result["counts"]["C4_diagnostic_present"],100)
        self.assertFalse(result["posts"][0]["model_reconstruction_ready"])
        self.assertEqual(result["posts"][50]["slides"][1]["ocr_words"],
                         ["Use Face Yoga"])
        self.assertEqual(result["posts"][50]["hook"]["text"],"Hook text")
        self.assertEqual(result["posts"][50]["cta"]["candidate_slide_indices"],[2])
        self.assertEqual(result["posts"][50]["cta"]["text"],"Use Face Yoga")
        self.assertEqual(result["posts"][50]["slides"][0]["visual_scene_description_status"],
                         "not_persisted_per_slide")
        self.assertEqual(len(export_sha(result)),64)
        self.assertEqual(result["provider_requests"],0)
        self.assertEqual(result["model_requests"],0)
        self.assertEqual(result["database_writes"],0)

    def test_missing_model_evidence_stays_unknown(self):
        active,selection,frozen,c_data=fixture()
        row=frozen["snapshot"]["video_evidence"][50]
        b=row["group_b"]["blocks"]
        b["B1"]={"state":"unavailable","reason":"model_not_configured",
                 "hook_text":None,"evidence_slide_refs":[]}
        b["B7"]={"state":"unavailable","cta_type":None,"cta_text":None,
                 "evidence_slide_refs":[]}
        result=make_export(active=active,selection=selection,
                           selection_sha=SHA,frozen=frozen)
        item=result["posts"][50]
        self.assertIsNone(item["hook"]["text"])
        self.assertFalse(item["hook"]["text_verbatim_ocr_verified"])
        self.assertIsNone(item["cta"]["text"])
        self.assertEqual(result["counts"]["hook_text_ocr_verified"],99)
        self.assertEqual(result["counts"]["cta_text_ocr_verified"],49)
        self.assertFalse(result["C1_C4_from_saved_local_diagnostic"])
        self.assertIsNone(item["C4_diagnostic"])

    def test_rejects_changed_scope_and_wrong_saved_c(self):
        active,selection,frozen,c_data=fixture()
        active["videos"][60]["url"]="https://www.tiktok.com/@other/photo/7600000000000000061"
        with self.assertRaisesRegex(ValueError,"changed_post_or_media_state"):
            make_export(active=active,selection=selection,
                        selection_sha=SHA,frozen=frozen,c_data=c_data)
        active,selection,frozen,c_data=fixture()
        c_data["posts"][0]["views"]+=1
        with self.assertRaisesRegex(ValueError,"existing_c_report_identity_mismatch"):
            make_export(active=active,selection=selection,
                        selection_sha=SHA,frozen=frozen,c_data=c_data)
        active,selection,frozen,c_data=fixture()
        frozen["snapshot"]["video_evidence"][0]["mi_analysis_id"]=None
        with self.assertRaisesRegex(ValueError,"frozen_analysis_identity_mismatch"):
            make_export(active=active,selection=selection,
                        selection_sha=SHA,frozen=frozen,c_data=c_data)

    def test_cli_writes_one_json_without_provider_calls(self):
        active,selection,frozen,c_data=fixture()
        service=type("S",(),{"get":lambda self,pid,sid:frozen})()
        with TemporaryDirectory() as tmp:
            p=Path(tmp)
            path=p/f"real100-{PROJECT}-single-profile-c-diagnostic.json"
            path.write_text(json.dumps(c_data),encoding="utf-8")
            with patch.object(cli,"existing_launched_batch",return_value=active),patch(
                "intelligence.market_intelligence.tiktok_top100_register.read_selection",
                return_value=(selection,SHA)),patch(
                "intelligence.market_intelligence.service.MarketIntelligenceService",
                return_value=service),patch.object(
                cli.bd,"_api",side_effect=AssertionError("provider forbidden")):
                summary=cli.export_existing_creative_evidence(
                    PROFILE,PROJECT,output_root=p)
            self.assertEqual(summary["state"],
                             "existing_100_creative_evidence_exported")
            self.assertEqual(summary["counts"]["photo_carousel"],50)
            self.assertEqual(summary["counts"]["C1_diagnostic_present"],100)
            self.assertEqual(summary["model_requests"],0)
            self.assertEqual(summary["provider_requests"],0)
            self.assertEqual(summary["database_writes"],0)
            doc=json.loads(Path(summary["report_path"]).read_text("utf-8"))
            self.assertEqual(doc["report_sha256"],
                             export_sha({k:v for k,v in doc.items() if k!="report_sha256"}))
            self.assertEqual(len(doc["posts"]),100)


if __name__=="__main__":
    unittest.main()
