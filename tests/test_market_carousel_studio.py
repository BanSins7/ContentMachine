"""Offline real SQLite + local image + Gemini mock QA for TikTok Carousel Studio.

No Bright Data HTTP or LLM calls. No GitHub test data contains paid credentials.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image

from intelligence.market_intelligence import carousel_studio as st

URL="https://www.tiktok.com/@example/photo/7660142485791968544"
NATIVE="7660142485791968544"


class TestStudio(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        patcher=patch.object(st,"ROOT",self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _create_run(self):
        run="d"*32
        folder=self.root/run
        folder.mkdir()
        with st.db() as c:
            c.execute("""INSERT INTO studio_runs
                (id,source_kind,source_input,state,phase,progress,created_at,
                 updated_at,output_dir)
                VALUES(?, 'post',?, 'queued','',0,?,?,?)""",
                (run,URL,st.utc(),st.utc(),str(folder)))
        return run

    def test_source_validation(self):
        self.assertEqual(st._input("post",URL),("post",URL))
        self.assertEqual(st._input("profile","@example"),
                         ("profile","https://www.tiktok.com/@example"))
        for bad in ("https://www.tiktok.com/@example/video/7660142485791968544",
                    "http://127.0.0.1/secrets",
                    "https://www.tiktok.com/@other/photo/abc",
                    "ftp://www.tiktok.com/@example/photo/7660142485791968544"):
            with self.assertRaises(ValueError,msg=bad):
                st._input("post",bad)

    def test_metrics_are_observed_not_invented(self):
        values=st._metrics({
            "url":URL,"play_count":200,"digg_count":20,
            "share_count":10,"comment_count":4,"collect_count":6,
            "create_time":1700000000,"number_of_private_users":5
        })
        self.assertEqual(values["observed_counters"]["views"],200)
        self.assertEqual(values["derived_public_ratios"]["engagement_per_view_pct"],20)
        self.assertEqual(values["derived_public_ratios"]["likes_per_view_pct"],10)
        self.assertIsNone(values["unavailable_not_zero"]["retention"])
        self.assertNotIn("create_time",values["observed_numeric_fields"])
        missing=st._metrics(None)
        self.assertIsNone(missing["derived_public_ratios"]["engagement_per_view_pct"])
        self.assertIsNone(missing["observed_counters"]["views"])


    def test_new_model_and_standard_quote_are_studio_only(self):
        self.assertEqual(st.STUDIO_MODEL,"google/gemini-3.8-flash")
        # The benchmark remains immutable and keeps its original model.
        self.assertEqual(st.story.MODEL,"google/gemini-3-flash-preview")
        with patch.object(st.story,"_request",return_value={
            "analysis":{"overall_story_summary":"Тест"},
            "usage":{"prompt_tokens":1000,"completion_tokens":2000,
                     "reported_cost_usd":0.009}}) as request:
            analysis,usage=st._analyze(self.root,[
                {"slide_index":1,"relative_image":"unused",
                 "input_jpeg_sha256":"0"*64}])
        self.assertEqual(request.call_args.kwargs["model"],st.STUDIO_MODEL)
        self.assertEqual(analysis["overall_story_summary"],"Тест")
        self.assertEqual(usage["estimated_standard_cost_usd"],0.00825)
        self.assertEqual(usage["reported_cost_usd"],0.009)
        self.assertEqual(usage["standard_reference_pricing"][
            "input_usd_per_million_tokens"],0.75)
        self.assertEqual(usage["standard_reference_pricing"][
            "output_usd_per_million_tokens"],3.75)
        self.assertTrue(usage["not_a_spend_cap"])
        missing=st._priced_usage({"prompt_tokens":1000,"completion_tokens":None})
        self.assertIsNone(missing["estimated_standard_cost_usd"])

    def test_gemini_cost_rollup_never_calls_missing_receipts_zero(self):
        run=self._create_run()
        folder=self.root/run
        output=folder/"carousels"/NATIVE
        output.mkdir(parents=True)
        (output/"analysis.json").write_text(json.dumps({
            "model":st.STUDIO_MODEL,
            "usage":{"reported_cost_usd":0.02,
                     "estimated_standard_cost_usd":0.017}
        }),encoding="utf-8")
        items=[{"state":"completed","native_id":NATIVE},
               {"state":"completed","native_id":"7660142485791968545"}]
        summary=st._gemini_cost_summary(folder,items)
        self.assertEqual(summary["completed_carousels"],2)
        self.assertEqual(summary["usage_receipts"],1)
        self.assertEqual(summary["provider_cost_coverage"],1)
        self.assertEqual(summary["sum_known_provider_cost_usd"],0.02)
        self.assertEqual(summary["standard_quote_estimate_coverage"],1)
        self.assertEqual(summary["sum_known_standard_quote_estimate_usd"],0.017)
        self.assertEqual(summary["models_in_results"],[st.STUDIO_MODEL])
        self.assertIsNone(st._gemini_cost_summary(folder,[])[
            "sum_known_provider_cost_usd"])

    def test_real_db_and_zip_gemini_progress_logs(self):
        run=self._create_run()
        def fake_download(url,folder):
            target=folder/("carousel-tiktok-"+NATIVE)
            target.mkdir()
            slides=[]
            for i in (1,2):
                image=Image.new("RGB",(96,128),(i*35,70,100))
                path=target/f"{i:03d}.jpg"
                image.save(path)
                data=path.read_bytes()
                slides.append({
                    "slide_index":i,"file_name":path.name,
                    "source_image_sha256":sha256(data).hexdigest(),
                    "image_width":96,"image_height":128})
            return {"schema_version":"mi-photo-carousel-media/v1",
                    "post_native_id":NATIVE,
                    "canonical_post_url":URL,"slides":slides,
                    "content_kind":"photo_carousel"}
        with patch.object(st,"_discover",return_value=([(URL,{
                "url":URL,"play_count":100,"digg_count":10,
                "share_count":5,"comment_count":3,"collect_count":2})],"exact_post")),patch.object(
                st,"_trigger_metrics",return_value={}),patch.object(
                st,"download_carousel",side_effect=fake_download),patch.object(
                st,"_analyze",return_value=(
                    {"global_hook":{"present":False},
                     "slide_roles":[{"slide_index":1,"primary_role":"other"},
                                    {"slide_index":2,"primary_role":"other"}]},
                    {"reported_cost_usd":0.002})):
            st._process(run)
        detail=st.get_run(run)
        self.assertEqual(detail["completed"],1)
        self.assertEqual(detail["failed"],0)
        self.assertEqual(detail["total"],1)
        self.assertEqual(detail["state"],"completed")
        self.assertEqual(detail["progress"],100)
        self.assertEqual(detail["items"][0]["asset_count"],2)
        self.assertGreater(len(detail["events"]),4)
        self.assertTrue(all(detail["downloads"].values()))
        grouped=st.read_group_analytics(run)
        self.assertEqual(grouped["carousel_classification"]["with_verified_slide_count"],1)
        self.assertEqual(grouped["groups"][0]["metrics"]["post_count"],1)
        self.assertIsNone(grouped["channel"]["all_posts"])
        with st.db() as conn:
            stored_groups=conn.execute("SELECT payload_json FROM studio_group_analytics WHERE run_id=?",(run,)).fetchone()
        self.assertIsNotNone(stored_groups)
        metrics=st.item_download_path(run,NATIVE,"metrics")
        analysis=st.item_download_path(run,NATIVE,"analysis")
        manifest=st.item_download_path(run,NATIVE,"manifest")
        self.assertTrue(all([metrics,analysis,manifest]))
        first=st.item_slide_path(run,NATIVE,1)
        self.assertTrue(first and first.is_file())
        self.assertIsNone(st.item_slide_path(run,NATIVE,3))
        self.assertIsNone(st.item_slide_path(run,NATIVE,-1))
        self.assertIsNone(st.item_slide_path(run,NATIVE,"01"))
        self.assertEqual(json.loads(metrics.read_text("utf-8"))[
            "derived_public_ratios"]["engagement_per_view_pct"],20)
        with ZipFile(st.download_path(run,"zip")) as z:
            names=z.namelist()
            self.assertIn("full-analysis.json",names)
            self.assertIn("group-analytics.json",names)
            self.assertIn("channel-snapshot-public-metrics.json",names)
            self.assertIn("events.jsonl",names)
            self.assertIn(f"carousels/{NATIVE}/analysis.json",names)
            self.assertIn(f"carousels/{NATIVE}/metrics.json",names)
            self.assertIn(f"carousels/{NATIVE}/manifest.json",names)
            self.assertIn(f"carousels/{NATIVE}/carousel-tiktok-{NATIVE}/001.jpg",names)
            self.assertIn(f"carousels/{NATIVE}/carousel-tiktok-{NATIVE}/002.jpg",names)
        aggregate=json.loads(st.download_path(run,"json").read_text("utf-8"))["aggregate_public_metrics"]
        self.assertEqual(aggregate["number_of_carousels"],1)
        self.assertEqual(aggregate["total_observed"]["views"],100)
        self.assertEqual(aggregate["median_views"],100)
        pricing=json.loads(st.download_path(run,"json").read_text("utf-8"))[
            "gemini_cost_summary"]
        self.assertEqual(pricing["completed_carousels"],1)
        self.assertEqual(pricing["provider_cost_coverage"],1)
        self.assertEqual(pricing["sum_known_provider_cost_usd"],0.002)
        self.assertIsNone(pricing["sum_known_standard_quote_estimate_usd"])
        self.assertEqual(aggregate["top_10_by_public_views"][0]["native_id"],NATIVE)
        with st.db() as c:
            stored=c.execute("SELECT analysis_json,metrics_json,manifest_json FROM studio_items").fetchone()
        self.assertIsNotNone(stored["analysis_json"])
        self.assertIsNotNone(stored["metrics_json"])
        self.assertIsNotNone(stored["manifest_json"])
        self.assertEqual(len(json.loads(stored["manifest_json"])["slides"]),2)

    def test_no_retry_on_restart_and_safe_paths(self):
        run=self._create_run()
        self.assertEqual(st.recover_interrupted(),1)
        self.assertEqual(st.get_run(run)["state"],"interrupted")
        self.assertEqual(st.recover_interrupted(),0)
        self.assertIsNone(st.download_path("../"+run,"json"))
        self.assertIsNone(st.item_download_path(run,"../secrets","analysis"))
        self.assertIsNone(st.item_download_path(run,NATIVE,"../../settings"))

    def test_no_post_count_cap_in_profile_discovery(self):
        run=self._create_run()
        records=[
            {"url":f"https://www.tiktok.com/@example/photo/{7000000000000000000+i}",
             "author_username":"example","play_count":i}
            for i in range(5100)]
        records+= [
            {"url":"https://www.tiktok.com/@example/video/7890000000000000000",
             "play_count":99},
            {"url":"https://www.tiktok.com/@example/video/7890000000000000001",
             "is_photo":True,"play_count":99},
            {"url":"https://www.tiktok.com/@other/photo/7880000000000000000",
             "play_count":99}
        ]
        with patch.object(st,"_trigger_profile",return_value="s_123456789abc"),patch.object(
                st,"_bd_stream",return_value=iter(records)):
            found,scope=st._discover(run,"profile","https://www.tiktok.com/@example")
        self.assertEqual(len(found),5101)
        self.assertEqual(scope,"returned_profile_snapshot_only")
        self.assertTrue(all("/photo/" in url for url,_ in found))
        with st.db() as conn:
            channel=conn.execute("""SELECT kind,COUNT(*) FROM studio_channel_posts
                                    WHERE run_id=? GROUP BY kind""",(run,)).fetchall()
        self.assertEqual(dict((kind,count) for kind,count in channel),
                         {"photo_carousel":5101,"video":1})


if __name__=="__main__":
    unittest.main()
