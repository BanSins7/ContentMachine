"""Marketing carousel: real image bytes, ordered slide evidence, copied B enums.

Offline deterministic tests; no fake claim of live Instagram/TikTok acquisition.
A gallery-dl CLI runner is mocked while IMAGE decode/OCR, hashes, and A/B
contracts execute the real Marketing implementation.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest
from PIL import Image

from intelligence.project_sources.normalize import normalize_url
from intelligence.discovery.normalize import canonical_video_url
from intelligence.market_intelligence.carousel_media import (
    CarouselError,carousel_candidate,download_carousel,scan_images,
    analyze_carousel,build_carousel_b)
from intelligence.market_intelligence.carousel_vision import _validate_model
from intelligence.market_intelligence._copied.group_b.blocks.b2_format import CAROUSEL_FORMATS
from intelligence.market_intelligence import batch

TT="https://www.tiktok.com/@faceyogi/photo/7701927285129055520"
IG="https://www.instagram.com/p/AbCdEfGhIJK/"
MP4="https://www.tiktok.com/@faceyogi/video/7701927285129055520"


def sample_image(path,shade):
    im=Image.new("RGB",(96,128),(shade,shade,shade))
    im.save(path,format="JPEG")


def make_folder(root):
    folder=root/"verified"
    folder.mkdir()
    for i,v in enumerate((60,120,180),1):
        sample_image(folder/(f"{i:03d}.jpg"),v)
    return folder


class CarouselContracts(unittest.TestCase):
    def test_photo_post_identity_survives_two_normalizers(self):
        a=normalize_url(TT,"user_video_reference")
        self.assertEqual(a.canonical_url,TT)
        self.assertEqual(a.native_id,"7701927285129055520")
        self.assertEqual(canonical_video_url(a.canonical_url)[2],TT)
        self.assertTrue(carousel_candidate(TT))
        self.assertFalse(carousel_candidate(MP4))

    def test_tiktok_profile_photo_discovery_does_not_invent_video_url(self):
        from intelligence.market_intelligence.profile_discovery import _from_entry
        account="https://www.tiktok.com/@faceyogi"
        code="7701927285129055520"
        self.assertEqual(_from_entry("tiktok",account,{"id":code,"type":"photo"}),TT)
        self.assertEqual(_from_entry("tiktok",account,{"id":code}),None)
        self.assertEqual(_from_entry("tiktok",account,{
            "id":code,"webpage_url":TT}),TT)

    def test_instagram_p_keeps_identity_not_fabricated_reel(self):
        got=normalize_url(IG,"user_video_reference")
        self.assertEqual(got.canonical_url,IG.rstrip("/"))
        self.assertTrue(carousel_candidate(got.canonical_url))
        self.assertEqual(canonical_video_url(IG)[2],IG)

    def test_real_jpeg_slide_manifest_binds_exact_original_and_rendered_hash(self):
        with TemporaryDirectory() as d:
            folder=make_folder(Path(d))
            manifest=scan_images(folder,expected_url=TT)
            self.assertEqual(manifest["content_kind"],"photo_carousel")
            self.assertEqual(manifest["post_native_id"],"7701927285129055520")
            self.assertEqual(len(manifest["slides"]),3)
            self.assertEqual([s["slide_index"] for s in manifest["slides"]],[1,2,3])
            self.assertTrue(all(len(s["source_image_sha256"])==64 for s in manifest["slides"]))
            self.assertTrue(all(len(s["image_png_sha256"])==64 for s in manifest["slides"]))
            self.assertIsNone(manifest["video_duration_sec"])
            self.assertIsNone(manifest["retention"])
            self.assertIsNone(manifest["slide_view_durations"])

    def test_swapped_or_missing_source_slide_blocks_analysis(self):
        with TemporaryDirectory() as d:
            folder=make_folder(Path(d))
            manifest=scan_images(folder,expected_url=TT)
            sample_image(folder/"002.jpg",12)
            with patch("intelligence.market_intelligence.carousel_media._ocr_backend",
                       return_value=None):
                with self.assertRaisesRegex(CarouselError,"carousel_source_changed"):
                    analyze_carousel(manifest,folder)
            (folder/"002.jpg").unlink()
            with self.assertRaisesRegex(CarouselError,"slide_missing_or_unsafe"):
                analyze_carousel(manifest,folder)

    def test_slide_order_gap_and_extra_images_rejected(self):
        with TemporaryDirectory() as d:
            folder=make_folder(Path(d))
            (folder/"002.jpg").rename(folder/"004.jpg")
            with self.assertRaisesRegex(CarouselError,"slide_order_unverified"):
                scan_images(folder,expected_url=TT)

    def test_mixed_album_is_not_silently_labeled_image_only(self):
        with TemporaryDirectory() as d:
            folder=make_folder(Path(d))
            (folder/"004.mp4").write_bytes(b"not-a-verified-video")
            with self.assertRaisesRegex(CarouselError,"mixed_carousel_not_yet_supported"):
                scan_images(folder,expected_url=IG)

    def test_extra_25th_slide_refuses_full_coverage_claim(self):
        with TemporaryDirectory() as d:
            folder=make_folder(Path(d))
            for n in range(4,26):
                sample_image(folder/(f"{n:03d}.jpg"),n)
            with self.assertRaisesRegex(CarouselError,"carousel_slide_count_unavailable"):
                scan_images(folder,expected_url=TT)

    def test_actual_a_and_all_eight_b_from_source_slides(self):
        with TemporaryDirectory() as d:
            folder=make_folder(Path(d))
            manifest=scan_images(folder,expected_url=TT)
            with patch("intelligence.market_intelligence.carousel_media._ocr_backend",
                       return_value=None):
                report=analyze_carousel(manifest,folder,allow_llm=False)
            self.assertEqual(report["content_kind"],"photo_carousel")
            a=report["group_a"]
            b=report["group_b"]
            self.assertEqual(len(a["slide_observations"]),3)
            self.assertEqual(a["video_technical"],"not_applicable")
            self.assertEqual(a["audio_asr"],"unavailable_no_verified_carousel_soundtrack")
            self.assertEqual(a["slide_dwell_time"],"unavailable")
            self.assertEqual(set(b["blocks"]),{"B"+str(i) for i in range(1,9)})
            self.assertEqual(b["blocks"]["B5"]["state"],"not_applicable")
            self.assertEqual(b["blocks"]["B1"]["state"],"unavailable")
            self.assertEqual(b["blocks"]["B2"]["state"],"unavailable")
            self.assertTrue(b["no_fabricated_timestamps"])
            self.assertFalse(b["whole_video_B_certified"])
            self.assertFalse(report["original_full_abc_certified"])

    def test_model_cannot_cite_unseen_or_nonfirst_slide_hook(self):
        slides=[{"ref":"s1-a"},{"ref":"s2-b"}]
        candidate={
            "hook_mechanisms":["problem"],"hook_text":"face yoga",
            "hook_visual":"a routine","carousel_format":"step_by_step",
            "story_roles":[],"visual_features":{},
            "cta_type":None,"cta_text":None,"topic_hint":"facial massage",
            "category_label":None,"topic_label":None,"subtopic_label":None,
            "evidence_slide_refs":["s2-b"]
        }
        with self.assertRaisesRegex(ValueError,"carousel_model_hook_not_first_slide"):
            _validate_model(candidate,slides)
        candidate["evidence_slide_refs"]=["s1-a","invented"]
        with self.assertRaisesRegex(ValueError,"carousel_model_untrusted_slide_refs"):
            _validate_model(candidate,slides)

    def test_copied_b2_carousel_taxonomy_is_used_not_video_format(self):
        slides=[{"slide_index":1,"ref":"s1-a","ocr":{"words":[]}},
                {"slide_index":2,"ref":"s2-b","ocr":{"words":[]}}]
        model={"state":"model_observed_partial",
               "evidence_slide_refs":["s1-a"],
               "hook_mechanisms":["problem"],
               "carousel_format":"step_by_step",
               "story_roles":["problem"],"visual_features":{},
               "cta_type":None,"topic_hint":"face yoga"}
        self.assertIn("step_by_step",CAROUSEL_FORMATS)
        report=build_carousel_b(slides,model,"a"*64,"b"*64)
        self.assertEqual(report["blocks"]["B2"]["carousel_format"],"step_by_step")
        self.assertEqual(report["blocks"]["B1"]["hook_slide_index"],1)
        self.assertEqual(report["blocks"]["B5"]["state"],"not_applicable")

    def test_b_rejects_missing_or_reordered_slide_indices(self):
        inferred={"state":"unavailable"}
        missing=[{"ref":"s1-a","ocr":{"words":[]}},
                 {"ref":"s2-b","ocr":{"words":[]}}]
        with self.assertRaisesRegex(CarouselError,"carousel_b_slide_evidence_invalid"):
            build_carousel_b(missing,inferred,"a"*64,"b"*64)
        reversed_slides=[{"slide_index":2,"ref":"s2-b","ocr":{"words":[]}},
                         {"slide_index":1,"ref":"s1-a","ocr":{"words":[]}}]
        with self.assertRaisesRegex(CarouselError,"carousel_b_slide_evidence_invalid"):
            build_carousel_b(reversed_slides,inferred,"a"*64,"b"*64)

    def test_model_text_is_not_verbatim_without_same_slide_ocr_evidence(self):
        slides=[{"slide_index":1,"ref":"s1-a","ocr":{"words":["actual","face","routine"]}},
                {"slide_index":2,"ref":"s2-b","ocr":{"words":["save","this"]}}]
        proposal={"state":"model_observed_partial",
                  "evidence_slide_refs":["s1-a","s2-b"],
                  "hook_mechanisms":["problem"],"hook_text":"invented claim",
                  "cta_type":"none","cta_text":"invented CTA",
                  "carousel_format":"step_by_step","story_roles":[],
                  "visual_features":{},"topic_hint":None}
        b=build_carousel_b(slides,proposal,"a"*64,"b"*64)
        self.assertIsNone(b["blocks"]["B1"]["hook_text"])
        self.assertFalse(b["blocks"]["B1"]["hook_text_verbatim_ocr_verified"])
        self.assertIsNone(b["blocks"]["B7"]["cta_text"])
        proposal["hook_text"]="actual face"
        b2=build_carousel_b(slides,proposal,"a"*64,"b"*64)
        self.assertEqual(b2["blocks"]["B1"]["hook_text"],"actual face")

    def test_gallery_dl_command_only_canonical_image_post_no_billing(self):
        class Result:
            returncode=0
        def fake_runner(args,**kwargs):
            self.assert_in(args)
            folder=Path(args[args.index("--directory")+1])
            for i in range(1,4):
                sample_image(folder/(f"{i:03d}.jpg"),i*50)
            return Result()
        def assert_in(args):
            self.assertEqual(args[-1],TT)
            self.assertIn("--config-ignore",args)
            self.assertIn("--no-input",args)
            self.assertIn("--range",args)
            self.assertNotIn("--cookies-from-browser",args)
            self.assertNotIn("POST",args)
        self.assert_in=assert_in
        with TemporaryDirectory() as d:
            result=download_carousel(TT,Path(d),runner=fake_runner)
            self.assertEqual(len(result["slides"]),3)
            with self.assertRaisesRegex(CarouselError,"carousel_target_exists"):
                download_carousel(TT,Path(d),runner=fake_runner)

    def test_batch_routes_photo_to_carousel_not_to_video(self):
        video={"video_id":1,"platform":"tiktok",
               "native_id":"7701927285129055520","url":TT}
        with patch("intelligence.market_intelligence.batch.d1.connect") as con,\
             patch("intelligence.market_intelligence.batch._source_for_video",
                   return_value=("uuid",True,True,True,True)),\
             patch("intelligence.market_intelligence.carousel_jobs.queue_carousel",
                   return_value={"queued":True}) as carousel,\
             patch("intelligence.market_intelligence.batch.enqueue_video") as mp4:
            status=batch._drive_video("a"*32,video)
        self.assertEqual(status,"carousel_queued")
        carousel.assert_called_once()
        mp4.assert_not_called()

    def test_sql_reader_is_rights_scoped_and_includes_real_carousel_source(self):
        from intelligence.market_intelligence.service import SQL_VIDEO_EVIDENCE
        self.assertIn("mi_carousel_analyses",SQL_VIDEO_EVIDENCE)
        self.assertIn("ca.source_id",SQL_VIDEO_EVIDENCE)
        self.assertIn("ps.can_analyze",SQL_VIDEO_EVIDENCE)
        self.assertIn("'photo_carousel'",SQL_VIDEO_EVIDENCE)

    def test_real_c1_c5_are_separated_by_content_kind(self):
        from datetime import datetime,timedelta,timezone
        from intelligence.market_intelligence.performance import enrich
        moment=datetime(2026,10,30,12,tzinfo=timezone.utc)
        rows=[]; histories={}
        for i in range(1,13):
            rows.append({"video_id":i,"platform":"tiktok","creator_id":"same",
                         "content_kind":"photo_carousel" if i<=6 else "video",
                         "published_at":(moment-timedelta(hours=24)).isoformat()})
            histories[i]=[{"views":100*i,"id":i,"observed_at":moment.isoformat(),
                           "source_id":"one","metric_provider":"one",
                           "source_metric_definition":"platform_post_views",
                           "source_metric_version":"v1"}]
        result=enrich(rows,histories)
        car=result[0]
        clip=result[6]
        self.assertEqual(car["creator_baseline_post_count"],5)
        self.assertEqual(clip["creator_baseline_post_count"],5)
        self.assertEqual(car["creator_baseline_views"],400)
        self.assertEqual(clip["creator_baseline_views"],1000)


if __name__=="__main__":
    unittest.main()
