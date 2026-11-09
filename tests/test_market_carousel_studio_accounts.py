"""Offline end-to-end checks for cumulative per-profile Carousel Studio JSON.

No Bright Data/Gemini requests and no real TikTok credentials. Existing
per-run results must remain byte-for-byte intact.
"""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

from intelligence.market_intelligence import carousel_studio as studio
from intelligence.market_intelligence import carousel_studio_accounts as accounts
from intelligence.market_intelligence import carousel_studio_http as http

PROFILE="https://www.tiktok.com/@example"
PROFILE_TWO="https://www.tiktok.com/@another"
RUN1="1"*32
RUN2="2"*32
RUN3="3"*32
FIRST="7000000000000000001"
SECOND="7000000000000000002"
THIRD="7000000000000000003"
VIDEO="7999999999999999999"


class Handler:
    def __init__(self):
        self.results=[]
        self.response_code=None
        self.headers=[]
        self.wfile=BytesIO()
    def json_response(self,code,body):
        self.results.append((code,body))
    def send_response(self,code):
        self.response_code=code
    def send_header(self,key,value):
        self.headers.append((key,value))
    def end_headers(self):
        pass


class AccountMasterTest(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        p=patch.object(studio,"ROOT",self.root)
        p.start()
        self.addCleanup(p.stop)

    def _insert_run(self,ident,profile,when):
        folder=self.root/ident
        folder.mkdir()
        with studio.db() as c:
            c.execute("""INSERT INTO studio_runs(
                id,source_kind,source_input,state,phase,progress,
                total,created_at,updated_at,output_dir)
                VALUES(?, 'profile', ?, 'completed', 'Готово', 100, 2, ?, ?, ?)""",
                (ident,profile,when,when,str(folder)))

    def _insert_item(self,run,native,state="completed"):
        url="https://www.tiktok.com/@example/photo/"+native
        manifest={"slides":[{"slide_index":1},{"slide_index":2},{"slide_index":3}]}
        narrative={"overall_story_summary":"Сохранённый результат "+native,
                   "global_hook":{"present":True,"supported_by_slides":[1]},
                   "global_cta":{"present":False},
                   "product_integration":{"present":False}}
        metrics={"observed_counters":{
            "views":123,"likes":7,"comments":1,"shares":1,"saves":2}}
        with studio.db() as c:
            c.execute("""INSERT INTO studio_items(
                run_id,native_id,canonical_url,state,
                manifest_json,metrics_json,analysis_json,asset_count)
                VALUES(?,?,?,?,?,?,?,3)""",
                (run,native,url,state,json.dumps(manifest),
                 json.dumps(metrics),json.dumps(narrative) if state=="completed" else None))
            if state=="completed":
                c.execute("UPDATE studio_runs SET completed=completed+1 WHERE id=?",(run,))
            else:
                c.execute("UPDATE studio_runs SET failed=failed+1 WHERE id=?",(run,))
        if state=="completed":
            dest=self.root/run/"carousels"/native
            dest.mkdir(parents=True)
            (dest/"analysis.json").write_text(json.dumps({
                "model":studio.STUDIO_MODEL,"source_url":url,
                "narrative":narrative,"usage":{"prompt_tokens":50,"completion_tokens":50}
            }),encoding="utf-8")
        return url

    def _snapshot(self,run,counts):
        with studio.db() as c:
            for native,kind,views in counts:
                url="https://www.tiktok.com/@example/"+(
                    "photo/" if kind=="photo_carousel" else "video/")+native
                c.execute("""INSERT INTO studio_channel_posts(
                    run_id,native_id,kind,canonical_url,counts_json)
                    VALUES(?,?,?,?,?)""",(run,native,kind,url,json.dumps({
                        "observed_counters":{
                            "views":views,"likes":10,"comments":1,"shares":1,"saves":1
                        }})))

    def test_cumulative_profile_master_unifies_runs_deduplicates_and_reuses_latest_snapshot(self):
        self._insert_run(RUN1,PROFILE,"2026-11-01T09:00:00+00:00")
        self._insert_item(RUN1,FIRST)
        self._insert_item(RUN1,SECOND)
        self._snapshot(RUN1,[
            (FIRST,"photo_carousel",10),(SECOND,"photo_carousel",20),
            (VIDEO,"video",300)])
        studio._package(RUN1)
        aid=accounts.identity(PROFILE)
        before=accounts.master(aid)
        self.assertEqual(before["cumulative"]["completed_carousels"],2)
        original=(self.root/RUN1/"full-analysis.json").read_bytes()
        original_gemini=(self.root/RUN1/"carousels"/FIRST/"analysis.json").read_bytes()

        # A later run sees much newer public counters; we preserve ONE new
        # snapshot rather than aggregating obsolete counters from RUN1.
        self._insert_run(RUN2,PROFILE,"2026-11-02T09:00:00+00:00")
        self._insert_item(RUN2,FIRST,state="failed")
        self._insert_item(RUN2,THIRD)
        self._snapshot(RUN2,[
            (FIRST,"photo_carousel",100),(SECOND,"photo_carousel",200),
            (THIRD,"photo_carousel",300),(VIDEO,"video",400)])
        with patch.object(studio,"_analyze",side_effect=AssertionError("no LLM")),patch.object(
                studio,"_trigger_metrics",side_effect=AssertionError("no Bright Data")):
            studio._package(RUN2)
            catalog=accounts.catalog()
            merged=accounts.master(aid)
        self.assertEqual(len(catalog),1)
        self.assertEqual(catalog[0]["username"],"@example")
        self.assertEqual(catalog[0]["completed_carousels"],3)
        self.assertEqual(merged["cumulative"]["run_count"],2)
        self.assertEqual(merged["cumulative"]["completed_carousels"],3)
        self.assertEqual(merged["cumulative"]["unique_attempted_carousels"],3)
        self.assertEqual(len(merged["items"]),3)
        self.assertEqual(merged["latest_profile_snapshot"]["run_id"],RUN2)
        self.assertEqual(merged["latest_profile_snapshot"]["post_count"],4)
        self.assertEqual(len(merged["profile_snapshot_history"]),2)
        self.assertEqual(merged["profile_snapshot_history"][0]["run_id"],RUN1)
        self.assertEqual(merged["profile_snapshot_history"][1]["run_id"],RUN2)
        self.assertEqual(
            merged["profile_snapshot_history"][0]["posts"][0]["observed_counters"]["views"],10)
        self.assertEqual(
            merged["group_analytics"]["channel"]["all_posts"]["observed"]["views"],1000)
        self.assertEqual(
            merged["group_analytics"]["carousel_classification"]["with_verified_slide_count"],3)
        first=next(p for p in merged["items"] if p["native_id"]==FIRST)
        self.assertEqual(first["source_run_id"],RUN1)
        self.assertEqual(first["state"],"completed")
        self.assertEqual(first["llm_analysis_artifact"]["usage"]["prompt_tokens"],50)
        self.assertIn("overall_story_summary",first["gemini"])
        self.assertEqual(
            first["artifact_paths_relative_to_studio_root"]["analysis"],
            RUN1+"/carousels/"+FIRST+"/analysis.json")
        self.assertEqual((self.root/RUN1/"full-analysis.json").read_bytes(),original)
        self.assertEqual((self.root/RUN1/"carousels"/FIRST/"analysis.json").read_bytes(),
                         original_gemini)
        self.assertTrue((self.root/"accounts"/aid/"account-master-analysis.json").is_file())

        # This old result remains accessible through the new account endpoints.
        lookup=Handler()
        self.assertTrue(http.handle_get(lookup,urlparse("/api/studio/accounts")))
        self.assertEqual(lookup.results[0][1]["accounts"][0]["id"],aid)
        detail=Handler()
        self.assertTrue(http.handle_get(detail,urlparse("/api/studio/accounts/"+aid)))
        self.assertEqual(detail.results[0][1]["cumulative"]["completed_carousels"],3)
        self.assertNotIn("items",detail.results[0][1])
        download=Handler()
        self.assertTrue(http.handle_get(download,urlparse(
            "/api/studio/accounts/"+aid+"/download/json")))
        self.assertEqual(download.response_code,200)
        payload=json.loads(download.wfile.getvalue())
        self.assertEqual(len(payload["items"]),3)

    def test_discover_old_runs_without_master_and_keep_accounts_isolated(self):
        self._insert_run(RUN1,PROFILE,"2026-11-01T09:00:00+00:00")
        self._insert_item(RUN1,FIRST)
        self._insert_run(RUN3,PROFILE_TWO,"2026-11-02T09:00:00+00:00")
        with studio.db() as c:
            c.execute("""INSERT INTO studio_items(
                run_id,native_id,canonical_url,state,metrics_json,analysis_json)
                VALUES(?,?,?,'completed',?,?)""",(
                    RUN3,THIRD,"https://www.tiktok.com/@another/photo/"+THIRD,
                    json.dumps({"observed_counters":{"views":50}}),
                    json.dumps({"overall_story_summary":"Other account"})))
        # No _package was ever called, modeling a pre-feature local database.
        with patch.object(studio,"_analyze",side_effect=AssertionError("no model")),patch.object(
            studio,"_discover",side_effect=AssertionError("no provider")):
            saved=accounts.catalog()
        self.assertEqual(len(saved),2)
        example=accounts.master(accounts.identity(PROFILE))
        other=accounts.master(accounts.identity(PROFILE_TWO))
        self.assertEqual([x["native_id"] for x in example["items"]],[FIRST])
        self.assertEqual([x["native_id"] for x in other["items"]],[THIRD])
        self.assertEqual(example["cumulative"]["run_count"],1)
        self.assertEqual(other["cumulative"]["run_count"],1)
        self.assertNotEqual(accounts.identity(PROFILE),accounts.identity(PROFILE_TWO))
        self.assertIsNone(accounts.master("a"*32))
        self.assertIsNone(accounts.file_path("a"*32,"json"))


if __name__=="__main__":
    unittest.main()
