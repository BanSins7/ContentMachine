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


    def test_profile_limit_defaults_to_30_and_validates_before_billing(self):
        for bad in (0,5001,True,"30",None,1.5):
            with self.assertRaisesRegex(ValueError,"от 1 до 5000"):
                st.start("profile","@example",max_carousels=bad)
        with patch.object(st.bd,"readiness",return_value={"ready":True}), patch.object(
                st.config,"setting",return_value="mock-local-key"), patch.object(
                st,"_thread") as spawn:
            response=st.start("profile","@example")
            row=st.get_run(response["id"])
            self.assertEqual(row["max_carousels"],30)
            self.assertEqual(row["source_kind"],"profile")
            spawn.assert_called_once_with(response["id"])

    def test_select_30_newest_dated_then_unknown_provider_order(self):
        posts=[]
        for i in range(40):
            date=1700000000+i if i<35 else None
            posts.append((f"https://www.tiktok.com/@example/photo/{7000000000000000000+i}",
                         {"create_time":date} if date is not None else {}))
        selected=st._select_profile_carousels(posts,30)
        self.assertEqual(len(selected),30)
        self.assertEqual([x[0] for x in selected],
                         [posts[i][0] for i in range(34,4,-1)])
        self.assertEqual(st._select_profile_carousels(posts,50)[-5:],posts[-5:])
        for bad in (0,5001,True,None):
            with self.assertRaises(ValueError):
                st._select_profile_carousels(posts,bad)

    def test_no_timestamp_preserves_provider_order(self):
        posts=[(f"post-{i}",{}) for i in range(40)]
        self.assertEqual(st._select_profile_carousels(posts,30),posts[:30])

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


    def test_profile_cap_limits_paid_actions_but_keeps_whole_account_denominator(self):
        run="e"*32
        folder=self.root/run
        folder.mkdir()
        with st.db() as con:
            con.execute("""INSERT INTO studio_runs
                (id,source_kind,source_input,max_carousels,state,phase,progress,
                 created_at,updated_at,output_dir)
                VALUES(?,'profile',?,30,'queued','',0,?,?,?)""",
                (run,"https://www.tiktok.com/@example",st.utc(),st.utc(),str(folder)))
        posts=[]
        for i in range(34):
            native=str(7000000000000000000+i)
            url=f"https://www.tiktok.com/@example/photo/{native}"
            posts.append((url,{"url":url,"play_count":1000+i,
                               "digg_count":100,"comment_count":5,
                               "share_count":2,"collect_count":1,
                               "create_time":1700000000+i}))
        def fake_discover(ident,kind,source):
            with st.db() as con:
                for url,raw in posts:
                    native=url.rsplit("/",1)[-1]
                    con.execute("""INSERT INTO studio_channel_posts
                       (run_id,native_id,kind,canonical_url,counts_json)
                       VALUES(?,?,?,?,?)""",(
                           ident,native,"photo_carousel",url,
                           json.dumps({"observed_counters":{
                               "views":raw["play_count"],"likes":100,
                               "comments":5,"shares":2,"saves":1}})))
                con.execute("""INSERT INTO studio_channel_posts
                    (run_id,native_id,kind,canonical_url,counts_json)
                    VALUES(?,?,?,?,?)""",(ident,"7999999999999999999","video",
                    "https://www.tiktok.com/@example/video/7999999999999999999",
                    json.dumps({"observed_counters":{
                        "views":500,"likes":10,"comments":1,"shares":1,"saves":1}})))
            return posts,"returned_profile_snapshot_only"
        def fake_download(url,directory):
            native=url.rsplit("/",1)[-1]
            photo_dir=directory/("carousel-tiktok-"+native)
            photo_dir.mkdir()
            slides=[]
            for number in (1,2):
                filepath=photo_dir/f"{number:03d}.jpg"
                Image.new("RGB",(16,24),(number*15,24,32)).save(filepath)
                data=filepath.read_bytes()
                slides.append({"slide_index":number,"file_name":filepath.name,
                               "source_image_sha256":sha256(data).hexdigest()})
            return {"post_native_id":native,"slides":slides}
        with patch.object(st,"_discover",side_effect=fake_discover),patch.object(
             st,"_trigger_metrics",return_value={}) as metrics,patch.object(
             st,"download_carousel",side_effect=fake_download) as downloads,patch.object(
             st,"_analyze",return_value=({"global_hook":{"present":False}},
                                        {"reported_cost_usd":0.001})) as gemini:
            st._process(run)
        self.assertEqual(len(metrics.call_args.args[1]),30)
        self.assertEqual(downloads.call_count,30)
        self.assertEqual(gemini.call_count,30)
        detail=st.get_run(run)
        self.assertEqual(detail["total"],30)
        self.assertEqual(detail["completed"],30)
        self.assertEqual(detail["failed"],0)
        self.assertEqual(detail["max_carousels"],30)
        self.assertEqual(len(detail["items"]),30)
        self.assertEqual(detail["state"],"completed")
        summary=json.loads(st.download_path(run,"json").read_text("utf-8"))
        self.assertEqual(len(summary["channel_snapshot_public_metrics"]["posts"]),35)
        self.assertEqual(summary["group_analytics"]["channel"]["all_posts"]["post_count"],35)
        self.assertEqual(summary["gemini_cost_summary"]["provider_cost_coverage"],30)
        self.assertEqual(summary["gemini_cost_summary"]["sum_known_provider_cost_usd"],0.03)
        self.assertEqual(summary["group_analytics"]["carousel_classification"][
            "coverage_of_profile_photo_posts_pct"],88.235)


    def test_pause_resume_and_repeated_account_skip_old_analysis(self):
        import urllib.error

        def records(count):
            result=[]
            for i in range(count):
                native=str(7200000000000000000+i)
                url=f"https://www.tiktok.com/@example/photo/{native}"
                result.append((url,{"url":url,"play_count":100+i,
                                    "digg_count":10,"comment_count":2,
                                    "share_count":1,"collect_count":1,
                                    "create_time":1700000000+i}))
            return result

        def discover(ident,kind,source):
            original=records(3) if len(runs)<=1 else records(4)
            with st.db() as conn:
                for url,data in original:
                    conn.execute("""INSERT OR REPLACE INTO studio_channel_posts
                        (run_id,native_id,kind,canonical_url,counts_json)
                        VALUES(?,?,?,?,?)""",(
                        ident,url.rsplit("/",1)[-1],"photo_carousel",url,
                        json.dumps({"observed_counters":{
                            "views":data["play_count"],"likes":10,
                            "comments":2,"shares":1,"saves":1}})))
            return original,"returned_profile_snapshot_only"

        def photo(url,item_dir):
            native=url.rsplit("/",1)[-1]
            folder=item_dir/("carousel-tiktok-"+native)
            folder.mkdir(exist_ok=True)
            slides=[]
            for index in (1,2):
                path=folder/f"{index:03d}.jpg"
                Image.new("RGB",(24,30),(20*index,30,40)).save(path)
                content=path.read_bytes()
                slides.append({"slide_index":index,"file_name":path.name,
                               "source_image_sha256":sha256(content).hexdigest()})
            return {"post_native_id":native,"slides":slides}

        ready=patch.object(st.bd,"readiness",return_value={"ready":True})
        creds=patch.object(st.config,"setting",return_value="mock-key")
        runs=[]
        with ready,creds,patch.object(st,"_thread") as spawn:
            created=st.start("profile","@example",max_carousels=3)
            runs.append(created["id"])
            spawn.assert_called_once()
        run=created["id"]
        one={"overall_story_summary":"Сохранено до паузы"}
        later={"overall_story_summary":"Продолжено после паузы"}
        insufficient=urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",402,
            "Payment Required",None,None)
        with patch.object(st,"_discover",side_effect=discover),patch.object(
             st,"_trigger_metrics",return_value={}) as metric_calls,patch.object(
             st,"download_carousel",side_effect=photo) as downloads,patch.object(
             st,"_analyze",side_effect=[(one,{"reported_cost_usd":0.002}),insufficient]) as model:
            st._process(run)
        paused=st.get_run(run)
        self.assertEqual(paused["state"],"paused")
        self.assertEqual(paused["completed"],1)
        self.assertEqual(paused["failed"],0)
        self.assertEqual(paused["total"],3)
        self.assertEqual(downloads.call_count,2)
        self.assertEqual(model.call_count,2)
        self.assertEqual(len(metric_calls.call_args.args[1]),3)
        statuses={x["native_id"]:x["state"] for x in paused["items"]}
        self.assertEqual(sorted(statuses.values()),
                         ["awaiting_credits","completed","queued"])
        saved_id=next(x["native_id"] for x in paused["items"]
                      if x["state"]=="completed")
        waiting_id=next(x["native_id"] for x in paused["items"]
                        if x["state"]=="awaiting_credits")
        self.assertTrue((self.root/run/"carousels"/waiting_id/"manifest.json").is_file())
        self.assertEqual(json.loads((self.root/run/"carousels"/waiting_id/
            "gemini-request.json").read_text())["state"],
            "rejected_insufficient_credits")
        saved=(self.root/run/"carousels"/saved_id/"analysis.json").read_bytes()
        self.assertIsNotNone(st.download_path(run,"zip"))
        self.assertIsNotNone(st.download_path(run,"json"))

        # Re-entering an unfinished username must NOT create another paid run.
        with ready,creds,patch.object(st,"_thread") as spawn:
            existing=st.start("profile","@example",max_carousels=3)
        self.assertEqual(existing["id"],run)
        self.assertTrue(existing["existing_run"])
        self.assertTrue(existing["resume_available"])
        spawn.assert_not_called()

        with patch.object(st,"_thread") as spawn:
            resumed=st.resume(run)
        self.assertEqual(resumed["id"],run)
        self.assertTrue(resumed["resumed"])
        spawn.assert_called_once_with(run,resume=True)
        with patch.object(st,"_discover",side_effect=AssertionError(
                "Paid profile snapshot must NOT be repeated")),patch.object(
             st,"_trigger_metrics",side_effect=AssertionError(
                "Paid per-post metrics must NOT be repeated")),patch.object(
             st,"download_carousel",side_effect=photo) as downloads,patch.object(
             st,"_analyze",return_value=(later,{"reported_cost_usd":0.001})) as model:
            st._process(run,resume=True)
        finished=st.get_run(run)
        self.assertEqual(finished["state"],"completed")
        self.assertEqual(finished["completed"],3)
        self.assertEqual(finished["failed"],0)
        self.assertEqual(model.call_count,2)
        self.assertEqual(downloads.call_count,1)
        self.assertEqual((self.root/run/"carousels"/saved_id/"analysis.json").read_bytes(),saved)

        # A completely new run can refresh the public profile snapshot, but
        # MUST exclude all previously completed IDs before limiting/charging.
        with ready,creds,patch.object(st,"_thread"):
            new_job=st.start("profile","@example",max_carousels=3)
        self.assertNotEqual(new_job["id"],run)
        runs.append(new_job["id"])
        with patch.object(st,"_discover",side_effect=discover),patch.object(
             st,"_trigger_metrics",return_value={}) as metrics,patch.object(
             st,"download_carousel",side_effect=photo) as downloads,patch.object(
             st,"_analyze",return_value=(later,{"reported_cost_usd":0.001})) as model:
            st._process(new_job["id"])
        new_result=st.get_run(new_job["id"])
        self.assertEqual(new_result["skipped_existing"],3)
        self.assertEqual(new_result["total"],1)
        self.assertEqual(new_result["completed"],1)
        self.assertEqual(downloads.call_count,1)
        self.assertEqual(model.call_count,1)
        self.assertEqual(len(metrics.call_args.args[1]),1)
        self.assertEqual(len(json.loads(st.download_path(new_job["id"],"json")
            .read_text())["channel_snapshot_public_metrics"]["posts"]),4)

    def test_ambiguous_post_request_interruption_cannot_auto_retry(self):
        run=self._create_run()
        with st.db() as conn:
            conn.execute("""INSERT INTO studio_targets
                (run_id,native_id,ordinal,canonical_url) VALUES(?,?,?,?)""",
                (run,NATIVE,0,URL))
            conn.execute("""INSERT INTO studio_items
                (run_id,native_id,canonical_url,state)
                VALUES(?,?,?,'analyzing')""",(run,NATIVE,URL))
            conn.execute("UPDATE studio_runs SET state='interrupted' WHERE id=?",(run,))
        marker=self.root/run/"carousels"/NATIVE/"gemini-request.json"
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({"state":"started_no_automatic_retry",
                                      "model":st.STUDIO_MODEL}),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"неопределённый платный вызов"):
            st.resume(run)
        self.assertEqual(st.get_run(run)["state"],"interrupted")

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
