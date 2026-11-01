"""Offline tests for top100 by popularity, mixed photo/video, consent and SHA.

Never contact TikTok, OpenRouter or Bright Data.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from intelligence.market_intelligence.tiktok_top100 import (
    canonical_tiktok_profile,rank_profile_posts)
from intelligence.market_intelligence import tiktok_top100_register as reg
from scripts import prepare_market_tiktok_top100 as cli
from intelligence.market_intelligence.contracts import MarketError

ACCOUNT="@faceyogilab"
PROFILE="https://www.tiktok.com/@faceyogilab"
SID="sd_mvxdevhz202ah32q5t"


def posts(count=150,photos=True):
    return [{
        "url":f"https://www.tiktok.com/@faceyogilab/{'photo' if photos and i%3==0 else 'video'}/{7600000000000000000+i}",
        "author_username":"faceyogilab","play_count":1000+i*31,
        "digg_count":i*2,"comment_count":i,
        "type":"photo" if photos and i%3==0 else "video",
    } for i in range(count)]


class RankTop100(unittest.TestCase):
    def test_public_username_only(self):
        self.assertEqual(canonical_tiktok_profile(" @FaceYogiLab "),("faceyogilab",PROFILE))
        self.assertEqual(canonical_tiktok_profile(PROFILE),("faceyogilab",PROFILE))
        self.assertEqual(canonical_tiktok_profile("https://www.tiktok.com/@luke.swan89"),
                         ("luke.swan89","https://www.tiktok.com/@luke.swan89"))
        for bad in ("","../bad","@private/123","https://evil.com/@x","name?x=1"):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                canonical_tiktok_profile(bad)

    def test_true_views_descending_top100_mixed_photo_and_video(self):
        result=rank_profile_posts(ACCOUNT,posts(150))
        self.assertTrue(result["ready"])
        self.assertEqual(result["selected"],100)
        self.assertEqual(result["unique_ranked_posts"],150)
        self.assertEqual(result["posts"][0]["views"],1000+149*31)
        self.assertEqual(result["posts"][-1]["views"],1000+50*31)
        self.assertEqual([v["views"] for v in result["posts"]],
                         sorted([v["views"] for v in result["posts"]],reverse=True))
        self.assertIn("photo_carousel",{x["content_kind"] for x in result["posts"]})
        self.assertIn("video",{x["content_kind"] for x in result["posts"]})
        self.assertFalse(result["incomplete_profile_history_possible"] is False)

    def test_missing_views_and_wrong_authors_cannot_fill_top100(self):
        values=posts(98)
        values.extend([
            {"url":"https://www.tiktok.com/@other/video/7600000000000000998",
             "play_count":999999999},
            {"url":"https://www.tiktok.com/@faceyogilab/photo/7600000000000000999"},
        ])
        result=rank_profile_posts(ACCOUNT,values)
        self.assertEqual(result["selected"],98)
        self.assertFalse(result["ready"])
        self.assertEqual(result["rejected"]["different_author"],1)
        self.assertEqual(result["rejected"]["no_views"],1)

    def test_provider_photo_signal_corrects_video_url_variant(self):
        record={"url":"https://www.tiktok.com/@faceyogilab/video/7600000000000000999",
                "is_photo":True,"play_count":"402000"}
        result=rank_profile_posts(ACCOUNT,[record],limit=1)
        self.assertEqual(result["posts"][0]["content_kind"],"photo_carousel")
        self.assertIn("/photo/",result["posts"][0]["url"])

    def test_conflicting_duplicate_never_wins_ranking(self):
        items=posts(101)
        copied=dict(items[-1]);copied["play_count"]=100000000
        result=rank_profile_posts(ACCOUNT,items+[copied])
        self.assertEqual(result["selected"],100)
        self.assertNotIn(items[-1]["url"],[x["url"] for x in result["posts"]])
        self.assertEqual(result["rejected"]["duplicate"],1)


class Top100OneTrigger(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        patcher=patch.object(cli,"REPORTS",self.root)
        patcher.start();self.addCleanup(patcher.stop)
        # Resolve the state path AFTER redirecting REPORTS into this test's
        # temporary root, never against the real user's marketing-qa folder.
        self.path=cli._path(ACCOUNT)
        self.report=self.root/"tiktok-top100-faceyogilab-selection.json"

    def test_one_paid_trigger_writes_durable_preflight_marker(self):
        calls=[]
        def submit(method,path,token,*,query,payload):
            self.assertTrue(self.path.exists())
            self.assertEqual(cli._read(self.path)["state"],
                             "trigger_uncertain_no_auto_retry")
            self.assertEqual(payload,[{"url":PROFILE,"start_date":""}])
            self.assertEqual(query["dataset_id"],cli.PROFILE_DATASET)
            calls.append(1)
            return {"snapshot_id":SID}
        with patch.object(cli.bd,"readiness",return_value={"ready":True}),patch.object(
             cli.bd,"_token",return_value="stub"):
            response=cli.start(ACCOUNT,request=submit)
            with self.assertRaises(FileExistsError):
                cli.start(ACCOUNT,request=submit)
        self.assertEqual(calls,[1])
        self.assertEqual(response["snapshot_id"],SID)
        self.assertEqual(cli._read(self.path)["snapshot_id"],SID)

    def test_provider_http_400_marks_rejected_without_losing_guard(self):
        with patch.object(cli.bd,"readiness",return_value={"ready":True}),patch.object(
             cli.bd,"_token",return_value="stub"):
            def rejected(*args,**kwargs):
                raise MarketError("brightdata_http_400",
                                  "Bright Data отказал в запросе (HTTP 400).")
            with self.assertRaisesRegex(MarketError,"HTTP 400"):
                cli.start(ACCOUNT,request=rejected)
        state=cli._read(self.path)
        self.assertEqual(state["state"],"rejected_http_400")
        self.assertEqual(state["provider_http_status"],400)
        self.assertIsNone(state["snapshot_id"])
        with patch.object(cli.bd,"readiness",return_value={"ready":True}):
            with self.assertRaises(FileExistsError):
                cli.start(ACCOUNT,request=lambda *a,**kw:self.fail("duplicate POST"))

    def test_explicit_http_400_recovery_uses_new_dataset_once(self):
        old="gd_lj71gn6l68bz7y9hc"
        with patch.object(cli.bd,"readiness",return_value={"ready":True}),patch.object(
             cli.bd,"_token",return_value="stub"):
            def legacy_rejection(*a,**kw):
                raise MarketError("brightdata_http_503","provider rejected")
            with self.assertRaises(MarketError):
                cli.start(ACCOUNT,request=legacy_rejection)
            # Simulate the pre-fix local marker left after the user's HTTP 400.
            doc=cli._read(self.path)
            doc["dataset_id"]=old
            self.path.write_text(json.dumps(doc),encoding="utf-8")
            calls=[]
            def recovered(method,path,token,*,query,payload):
                calls.append((method,path,query,payload))
                return {"snapshot_id":SID}
            result=cli.retry_http_400(ACCOUNT,request=recovered)
        self.assertEqual(result["snapshot_id"],SID)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][2]["dataset_id"],cli.PROFILE_DATASET)
        self.assertEqual(calls[0][3],[{"url":PROFILE,"start_date":""}])
        state=cli._read(self.path)
        self.assertEqual(state["previous_dataset_id"],old)
        self.assertEqual(state["previous_http_status_confirmed_by_operator"],400)
        self.assertEqual(state["trigger_count_max"],2)
        with self.assertRaisesRegex(ValueError,"profile_retry_not_allowed_snapshot_exists"):
            cli.retry_http_400(ACCOUNT,request=lambda *a,**k:self.fail("duplicate POST"))

    def test_http_400_recovery_never_retries_the_same_dataset(self):
        with patch.object(cli.bd,"readiness",return_value={"ready":True}),patch.object(
             cli.bd,"_token",return_value="stub"):
            with self.assertRaises(MarketError):
                cli.start(ACCOUNT,request=lambda *a,**kw:(
                    _ for _ in ()).throw(MarketError("brightdata_http_400","HTTP 400")))
            with self.assertRaisesRegex(ValueError,"requires_different_dataset"):
                cli.retry_http_400(ACCOUNT,request=lambda *a,**kw:self.fail("duplicate POST"))

    def test_poll_then_collect_existing_snapshot_zero_new_post(self):
        with patch.object(cli.bd,"readiness",return_value={"ready":True}),patch.object(
             cli.bd,"_token",return_value="stub"):
            cli.start(ACCOUNT,request=lambda *args,**kwargs:{"snapshot_id":SID})
        done=cli.poll(ACCOUNT,progress=lambda snapshot:"ready")
        self.assertEqual(done["paid_requests"],0)
        report=cli.collect(ACCOUNT,fetch=lambda snapshot:posts(150))
        self.assertEqual(report["selected"],100)
        self.assertTrue(report["ready"])
        self.assertEqual(report["paid_requests"],0)
        self.assertTrue(self.report.exists())
        with self.assertRaisesRegex(ValueError,"top100_selection_already_saved"):
            cli.collect(ACCOUNT,fetch=lambda _:posts(150))
        selected,hash_=reg.read_selection(ACCOUNT,root=self.root)
        self.assertEqual(selected["selected"],100)
        self.assertEqual(sha256(self.report.read_bytes()).hexdigest(),hash_)

    def test_selection_mutation_is_rejected_before_d1_write(self):
        with patch.object(cli.bd,"readiness",return_value={"ready":True}),patch.object(
             cli.bd,"_token",return_value="stub"):
            cli.start(ACCOUNT,request=lambda *a,**kw:{"snapshot_id":SID})
        cli.poll(ACCOUNT,progress=lambda _: "ready")
        cli.collect(ACCOUNT,fetch=lambda _:posts(150))
        doc=json.loads(self.report.read_text())
        doc["posts"][0]["views"]=0
        self.report.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError,"top100_selection_sha256_mismatch"):
            reg.read_selection(ACCOUNT,root=self.root)

    def test_check_is_local_and_reports_missing_settings_without_provider_post(self):
        with patch("scripts.run_market_real100._project_id",return_value="a"*32),patch.object(
             cli.bd,"readiness",return_value={"ready":True}),patch(
             "intelligence.market_intelligence.replica_vision._enabled",return_value=False),patch(
             "shutil.which",return_value="binary"),patch(
             "importlib.util.find_spec",
             return_value=object()),patch.object(
             cli.bd,"_api",side_effect=AssertionError("no network")):
            result=cli.check("@luke.swan89","a"*32)
        self.assertEqual(result["username"],"luke.swan89")
        self.assertFalse(result["vision_model_ready"])
        self.assertFalse(result["ready_for_username_top100"])
        self.assertEqual(result["external_requests"],0)
        self.assertEqual(result["billable_requests"],0)
        self.assertTrue(result["new_paid_profile_trigger_possible"])

    def test_all_does_not_trigger_paid_provider_when_preflight_fails(self):
        with patch.object(cli,"check",return_value={
             "ready_for_username_top100":False
             }),patch.object(
             cli,"start",side_effect=AssertionError("MUST NOT charge provider")):
            with self.assertRaisesRegex(ValueError,"top100_local_preflight_not_ready"):
                cli.run_all(ACCOUNT,"a"*32,allow_paid=True)

    def test_one_command_never_starts_paid_collection_without_opt_in(self):
        with patch.object(cli,"start",
                          side_effect=AssertionError("no provider should start")):
            with self.assertRaisesRegex(ValueError,"explicit_provider_cost_opt_in_required"):
                cli.run_all(ACCOUNT,"a"*32,allow_paid=False,paid_metrics=True)

    def test_one_command_reuses_completed_snapshot_without_another_paid_post(self):
        with patch.object(cli.bd,"readiness",return_value={"ready":True}),patch.object(
             cli.bd,"_token",return_value="stub"):
            cli.start(ACCOUNT,request=lambda *args,**kw:{"snapshot_id":SID})
        cli.poll(ACCOUNT,progress=lambda _: "ready")
        cli.collect(ACCOUNT,fetch=lambda _:posts(150))
        with patch.object(cli,"start",
                          side_effect=AssertionError("duplicate trigger")) as trigger,patch.object(
             cli,"launch",return_value={"state":"launched"}) as launched:
            report=cli.run_all(ACCOUNT,"a"*32,allow_paid=True,paid_metrics=True)
        trigger.assert_not_called()
        launched.assert_called_once_with("faceyogilab","a"*32,
                                         paid_metrics=True,interval=20)
        self.assertEqual(report["state"],"launched")

    def test_local_media_roots_include_actual_tiktok_mp4_archive(self):
        import os
        home=self.root/"fake_home"
        home.mkdir()
        with patch.object(cli,"ROOT",self.root),patch(
             "scripts.prepare_market_tiktok_top100.Path.home",
             return_value=home),patch(
             "scripts.prepare_market_tiktok_top100.config.setting",
             return_value=""),patch.dict(os.environ,{},clear=False):
            result=cli.configure_local_media_roots()
            roots=os.environ["CM_MARKET_MEDIA_ROOTS"].split(os.pathsep)
            self.assertIn(str((self.root/"marketing-qa"/"media").resolve()),roots)
            self.assertIn(str((home/"Downloads"/"TikTokArchive").resolve()),roots)
        self.assertEqual(result["roots_configured"],2)

    def test_selected_preflight_pins_exact_100_tiktok_and_ignores_youtube(self):
        with patch.object(cli.bd,"readiness",return_value={"ready":True}),patch.object(
             cli.bd,"_token",return_value="stub"):
            cli.start(ACCOUNT,request=lambda *a,**k:{"snapshot_id":SID})
        cli.poll(ACCOUNT,progress=lambda _:"ready")
        cli.collect(ACCOUNT,fetch=lambda _:posts(150))
        selection,_=reg.read_selection(ACCOUNT,root=self.root)
        source_rows=[(i+2,p["native_id"],p["url"])
                     for i,p in enumerate(selection["posts"])]
        # An unrelated YouTube post exists in the project, but is not included
        # in this query or the pinned scope.
        class Rows:
            def fetchall(self):return source_rows
        class DB:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def execute(self,sql,args):
                self_sql=" ".join(sql.split())
                assert "WHERE platform='tiktok'" in self_sql
                assert "ANY(%s)" in self_sql
                assert args[0]==[p["native_id"] for p in selection["posts"]]
                return Rows()
        with patch("scripts.run_market_real100._project_id",
                   return_value="a"*32),patch(
             "intelligence.market_intelligence.batch.d1.connect",
             return_value=DB()),patch.object(
             cli,"_missing_marketing_schema_tables",return_value=[]),patch(
             "intelligence.market_intelligence.batch._source_for_video",
             return_value=(88,True,True,True,True)) as rights,patch(
             "intelligence.market_intelligence.replica_vision._enabled",
             return_value=True),patch(
             "shutil.which",return_value="binary"),patch(
             "importlib.util.find_spec",return_value=object()),patch.object(
             cli.bd,"readiness",return_value={"ready":True}),patch.object(
             cli.bd,"_api",side_effect=AssertionError("no provider requests")):
            report=cli.preflight_selected(ACCOUNT,"a"*32)
        self.assertTrue(report["can_launch_selected_top100"])
        self.assertEqual(report["selected_exact_tiktok_posts"],100)
        self.assertEqual(report["platform_counts"],{"tiktok":100})
        self.assertEqual(report["selected_rights"]["approved_fetch_store_analyze_and_llm"],100)
        self.assertEqual(report["external_requests"],0)
        rights.assert_called()
        self.assertEqual(rights.call_count,100)

    def test_selected_preflight_requires_rights_on_exact_pinned_posts(self):
        with patch.object(cli.bd,"readiness",return_value={"ready":True}),patch.object(
             cli.bd,"_token",return_value="stub"):
            cli.start(ACCOUNT,request=lambda *a,**k:{"snapshot_id":SID})
        cli.poll(ACCOUNT,progress=lambda _:"ready")
        cli.collect(ACCOUNT,fetch=lambda _:posts(150))
        selection,_=reg.read_selection(ACCOUNT,root=self.root)
        rows=[(i+2,p["native_id"],p["url"])
              for i,p in enumerate(selection["posts"])]
        class Rows:
            def fetchall(self):return rows
        class DB:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def execute(self,sql,args):return Rows()
        def verify(conn,project,video):
            return (88,False,True,True,True) if video["native_id"]==selection["posts"][0]["native_id"] else (88,True,True,True,True)
        with patch("scripts.run_market_real100._project_id",
                   return_value="a"*32),patch(
             "intelligence.market_intelligence.batch.d1.connect",
             return_value=DB()),patch.object(
             cli,"_missing_marketing_schema_tables",return_value=[]),patch(
             "intelligence.market_intelligence.batch._source_for_video",
             side_effect=verify),patch(
             "intelligence.market_intelligence.replica_vision._enabled",
             return_value=True),patch(
             "shutil.which",return_value="binary"),patch(
             "importlib.util.find_spec",return_value=object()),patch.object(
             cli.bd,"readiness",return_value={"ready":True}):
            report=cli.preflight_selected(ACCOUNT,"a"*32)
        self.assertEqual(report["selected_rights"]["fetch_store_analyze_denied"],1)
        self.assertFalse(report["can_launch_selected_top100"])

    def test_missing_carousel_schema_is_reported_without_provider_calls(self):
        class Connection:
            def __init__(self,exists):self.exists=exists
            def execute(self,query,args):
                self.sql=query
                self.args=args
                return self
            def fetchone(self):
                return ("mi_video_analyses",
                        "mi_carousel_analyses" if self.exists else None,
                        "mi_brightdata_numeric_fields")
        ok=Connection(True)
        self.assertEqual(cli._missing_marketing_schema_tables(ok),[])
        self.assertIn("to_regclass",ok.sql)
        self.assertEqual(len(ok.args),3)
        missing=Connection(False)
        self.assertEqual(cli._missing_marketing_schema_tables(missing),
                         ["mi_carousel_analyses"])

    def test_launch_does_not_create_marker_or_batch_if_selected_preflight_fails(self):
        from scripts import run_market_real100
        with patch("scripts.run_market_real100._project_id",return_value="a"*32),patch(
             "intelligence.market_intelligence.replica_vision._enabled",return_value=True),patch(
             "shutil.which",return_value="binary"),patch(
             "importlib.util.find_spec",return_value=object()),patch.object(
             cli,"register",return_value={"video_ids":list(range(2,102))}),patch.object(
             cli,"preflight_selected",return_value={"can_launch_selected_top100":False}),patch.object(
             cli,"configure_local_media_roots",
             side_effect=AssertionError("must not setup directories")),patch(
             "intelligence.market_intelligence.batch.start_batch",
             side_effect=AssertionError("must not start batch")):
            with self.assertRaisesRegex(ValueError,"top100_selected_preflight_failed"):
                cli.launch(ACCOUNT,"a"*32)
        self.assertFalse(list(self.root.glob("*launched.json")))

    def _completed_guard_and_batch(self):
        with patch.object(cli.bd,"readiness",return_value={"ready":True}),patch.object(
             cli.bd,"_token",return_value="stub"):
            cli.start(ACCOUNT,request=lambda *a,**kw:{"snapshot_id":SID})
        cli.poll(ACCOUNT,progress=lambda _:"ready")
        cli.collect(ACCOUNT,fetch=lambda _:posts(150))
        selection,sh=reg.read_selection(ACCOUNT,root=self.root)
        proj="a"*32
        ids=list(range(2,102))
        videos=[{"video_id":ident,"platform":"tiktok","url":post["url"],
                 "state":"awaiting_download","acquisition_reason":None,
                 "carousel_error":None}
                for ident,post in zip(ids,selection["posts"])]
        marker=(self.root/("tiktok-top100-"+selection["username"]+"-"+proj+
                          "-"+sh[:16]+"-launched.json"))
        marker.write_text(json.dumps({
            "schema_version":"marketing-tiktok-top100-launch-guard/v1",
            "project_id":proj,"profile_snapshot_id":SID,
            "selection_sha256":sh,"video_ids":ids,
            "paid_metrics_requested":False,
        }),encoding="utf-8")
        return proj,{"batch_id":2,"videos":videos},marker

    def test_existing_batch_diagnostic_preserves_scope_and_shows_reasons(self):
        project,state,marker=self._completed_guard_and_batch()
        state["videos"][0]["state"]="unavailable"
        state["videos"][0]["acquisition_reason"]="download_blocked"
        state["videos"][1]["state"]="carousel_failed"
        state["videos"][1]["carousel_error"]="carousel_gallery_dl_failed"
        with patch("scripts.run_market_real100._project_id",return_value=project),patch.object(
             cli.bd,"_api",side_effect=AssertionError("no provider POST")):
            result=cli.diagnose_existing(ACCOUNT,project,status_reader=lambda _:state)
        self.assertEqual(result["batch_id"],2)
        self.assertTrue(result["launch_guard_reused"])
        self.assertEqual(result["new_batch_created"],False)
        self.assertEqual(len(result["failure_samples"]),2)
        self.assertEqual(result["provider_requests"],0)
        self.assertEqual(result["model_requests"],0)

    def test_existing_batch_rejects_different_batch_without_launch(self):
        project,state,marker=self._completed_guard_and_batch()
        state["videos"][0]["video_id"]=1
        with patch("scripts.run_market_real100._project_id",return_value=project):
            with self.assertRaisesRegex(ValueError,"existing_batch_scope_mismatch"):
                cli.existing_launched_batch(ACCOUNT,project,status_reader=lambda _:state)

    def test_resume_reuses_guard_and_restores_media_roots_without_paid_metrics(self):
        project,state,marker=self._completed_guard_and_batch()
        class DB:
            def __enter__(self):return self
            def __exit__(self,*args):return False
        with patch("scripts.run_market_real100._project_id",return_value=project),patch(
             "intelligence.market_intelligence.batch.status",return_value=state),patch(
             "intelligence.market_intelligence.batch.d1.connect",
             return_value=DB()),patch.object(
             cli,"_missing_marketing_schema_tables",return_value=[]),patch.object(
             cli,"configure_local_media_roots",return_value={
                 "roots_configured":2}) as roots,patch(
             "scripts.run_market_real100.watch",return_value={"ok":True}) as watch,patch(
             "intelligence.market_intelligence.brightdata_jobs.queue_metrics",
             side_effect=AssertionError("no paid requests")),patch(
             "intelligence.market_intelligence.batch.start_batch",
             side_effect=AssertionError("no new batch")):
            with redirect_stdout(StringIO()) as out:
                result=cli.resume_existing(ACCOUNT,project,interval=20)
        self.assertTrue(result["ok"])
        self.assertIn('"batch_id": 2',out.getvalue())
        roots.assert_called_once_with()
        watch.assert_called_once_with(project,interval=20,resume_paid=False)

    def test_resume_fails_closed_if_media_schema_missing(self):
        project,state,marker=self._completed_guard_and_batch()
        class DB:
            def __enter__(self):return self
            def __exit__(self,*args):return False
        with patch("scripts.run_market_real100._project_id",return_value=project),patch(
             "intelligence.market_intelligence.batch.status",return_value=state),patch(
             "intelligence.market_intelligence.batch.d1.connect",
             return_value=DB()),patch.object(
             cli,"_missing_marketing_schema_tables",
             return_value=["mi_carousel_analyses"]),patch.object(
             cli,"configure_local_media_roots",side_effect=AssertionError("should not resume")):
            with self.assertRaisesRegex(ValueError,"top100_missing_schema"):
                cli.resume_existing(ACCOUNT,project)

    def test_legacy_worker_alias_prevents_uuid_keyerror_offline(self):
        import sys
        from types import ModuleType
        from intelligence.project_sources import video_jobs
        worker=ModuleType("worker")
        live=ModuleType("worker.local_worker")
        stale=ModuleType("local_worker")
        live.jobs={}
        stale.jobs={}
        def update(jid,**fields):
            live.jobs[jid].update(fields)
        def update_stale(jid,**fields):
            stale.jobs[jid].update(fields)
        live.update_job=update
        stale.update_job=update_stale
        worker.local_worker=live
        class Sender:
            def __init__(self):self.messages=[]
            def send(self,msg):self.messages.append(msg)
            def close(self):pass
        class Adapter:
            def resolve_identity(self,url):return url
            def request_download(self,jid,ident):
                import local_worker as legacy
                legacy.update_job(jid,status="complete",progress=100)
        sender=Sender()
        ident="05e782fb-5b6c-43a0-ab83-ebb903ef47d2"
        with patch.dict(sys.modules,{
            "worker":worker,"worker.local_worker":live,"local_worker":stale}),patch.object(
            video_jobs,"adapter_for",return_value=Adapter()):
            video_jobs._download_child(sender,ident,"tiktok","example")
        done=[x for x in sender.messages if x["kind"]=="done"]
        self.assertEqual(done[0]["status"],"complete")
        self.assertEqual(live.jobs[ident]["status"],"complete")

    def test_recover_only_known_local_failure_signatures_without_provider(self):
        from contextlib import nullcontext
        self.assertTrue(cli._uuid_keyerror_reason(
            "'05e782fb-5b6c-43a0-ab83-ebb903ef47d2'"))
        for bad in ("captcha","'123'","'bad-id'","no signal"):
            self.assertFalse(cli._uuid_keyerror_reason(bad))
        project="a"*32
        photo={"video_id":2,"platform":"tiktok","native_id":"7600000000000000002",
               "url":"https://www.tiktok.com/@faceyogilab/photo/7600000000000000002",
               "state":"carousel_failed"}
        video={"video_id":3,"platform":"tiktok","native_id":"7600000000000000003",
               "url":"https://www.tiktok.com/@faceyogilab/video/7600000000000000003",
               "state":"unavailable"}
        other=[{"video_id":k,"platform":"tiktok",
                "native_id":str(7600000000000000000+k),
                "url":f"https://www.tiktok.com/@faceyogilab/video/{7600000000000000000+k}",
                "state":"analysis_partial"} for k in range(4,102)]
        class DB:
            def __init__(self):self.writes=[]
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def transaction(self):return nullcontext()
            def execute(self,sql,args):
                if "FROM jobs WHERE job_type" in sql and "ORDER BY" in sql:
                    self.row=(17,"failed","carousel_storage_root_not_configured",
                              1,1,None,{"url":photo["url"],"video_id":2,
                                        "project_id":project})
                elif "FROM project_video_acquisition_runs r" in sql and "SELECT j.id" in sql:
                    self.row=(18,"unavailable",1,3,"unavailable",
                              "'05e782fb-5b6c-43a0-ab83-ebb903ef47d2'",
                              "'05e782fb-5b6c-43a0-ab83-ebb903ef47d2'",
                              video["url"],video["native_id"]) if args[1]==video["native_id"] else None
                elif sql.lstrip().startswith("UPDATE"):
                    self.writes.append(sql)
                    self.row=(args[0],)
                else:raise AssertionError(sql[:90])
                return self
            def fetchone(self):return self.row
        db=DB()
        with patch.object(cli,"existing_launched_batch",return_value={
             "project_id":project,"batch_id":2,"videos":[photo,video]+other}),patch.object(
             cli,"configure_local_media_roots",return_value={"roots_configured":2}),patch(
             "intelligence.market_intelligence.batch.d1.connect",
             return_value=db),patch.object(
             cli,"_missing_marketing_schema_tables",return_value=[]),patch.object(
             cli.bd,"_api",side_effect=AssertionError("no provider calls")):
            result=cli.recover_local_failures(ACCOUNT,project)
        self.assertEqual(result["carousel_root_failures_requeued"],1)
        self.assertEqual(result["video_worker_keyerrors_requeued"],1)
        self.assertEqual(result["not_requeued"],98)
        self.assertEqual(result["new_provider_requests"],0)
        self.assertEqual(result["new_batches"],0)
        self.assertEqual(len(db.writes),3)


    def test_expired_download_watchdog_only_updates_matching_final_leases(self):
        from contextlib import nullcontext
        from intelligence.market_intelligence import batch
        class DB:
            def __init__(self):self.writes=[]
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def transaction(self):return nullcontext()
            def execute(self,sql,args):
                if "FROM source_extraction_jobs j" in sql:
                    assert "jsonb_array_elements" in sql
                    assert "j.attempts>=j.max_attempts" in sql
                    assert "FOR UPDATE OF j SKIP LOCKED" in sql
                    self.result=[("jid-1","a"*32,"tiktok","7600000000000000001")]
                    self.fetchall=self._all
                elif "pg_try_advisory_xact_lock" in sql:
                    assert args[0]=="psi-video:tiktok:7600000000000000001"
                    self.result=(True,)
                elif "UPDATE source_extraction_jobs" in sql:
                    assert "download_final_lease_expired" in sql
                    self.writes.append(("job",args))
                    self.result=("jid-1",)
                elif "UPDATE project_video_acquisition_runs" in sql:
                    self.writes.append(("run",args))
                    self.result=None
                else:raise AssertionError(sql)
                return self
            def _all(self):return self.result
            def fetchone(self):return self.result
        db=DB()
        with patch.object(batch.d1,"connect",return_value=db),patch.object(
             cli.bd,"_api",side_effect=AssertionError("no paid calls")):
            self.assertEqual(batch.expire_exhausted_downloads(),1)
        self.assertEqual([x[0] for x in db.writes],["job","run"])

    def test_recover_21_exhausted_downloads_only_once_without_calls(self):
        from contextlib import nullcontext
        project="a"*32
        videos=[{"video_id":i,"platform":"tiktok",
                 "native_id":str(7600000000000000000+i),
                 "url":f"https://www.tiktok.com/@faceyogilab/video/{7600000000000000000+i}"}
                 for i in range(1,51)]
        photos=[{"video_id":i,"platform":"tiktok",
                 "native_id":str(7600000000000000000+i),
                 "url":f"https://www.tiktok.com/@faceyogilab/photo/{7600000000000000000+i}"}
                 for i in range(51,101)]
        class DB:
            def __init__(self):self.writes=[];self.keys=[];self.rows={}
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def transaction(self):return nullcontext()
            def execute(self,sql,args):
                if "pg_try_advisory_xact_lock" in sql:
                    self.keys.append(args[0]);self.output=(True,)
                elif "FROM project_video_acquisition_runs r" in sql:
                    n=int(args[1])-7600000000000000000
                    v=videos[n-1]
                    if n<=21:
                        self.output=(str(n),"processing",3,3,True,None,
                                     "not_applicable",0,"prepare",None,False,
                                     v["url"],v["native_id"],"source",900)
                    else:
                        self.output=(str(n),"completed",1,3,None,None,
                                     "not_applicable",0,"complete","asset",False,
                                     v["url"],v["native_id"],"source",None)
                elif "UPDATE source_extraction_jobs" in sql:
                    assert "max_attempts=4" in sql
                    assert "attempts=3 AND max_attempts=3" in sql
                    self.writes.append(("job",args[0]))
                    self.output=(args[0],)
                elif "UPDATE project_video_acquisition_runs" in sql:
                    self.writes.append(("run",args[1]))
                    self.output=None
                else:raise AssertionError(sql[:130])
                return self
            def fetchone(self):return self.output
        db=DB()
        with patch.object(cli,"existing_launched_batch",return_value={
              "project_id":project,"batch_id":2,"videos":videos+photos}),patch(
              "intelligence.market_intelligence.batch.d1.connect",
              return_value=db),patch(
              "intelligence.market_intelligence.batch._source_for_video",
              return_value=("source",True,True,True,True)),patch.object(
              cli.bd,"_api",side_effect=AssertionError("no paid calls")):
            result=cli.recover_exhausted_video_downloads(ACCOUNT,project)
        self.assertEqual(result["requeued"],21)
        self.assertEqual(result["new_brightdata_requests"],0)
        self.assertEqual(result["model_requests"],0)
        self.assertEqual(len(db.writes),42)
        self.assertEqual(len(db.keys),50)
        self.assertEqual(result["skipped"]["not_exact_exhausted_unbilled_3_of_3"],29)


    def test_media_worker_does_not_claim_when_roots_missing(self):
        from intelligence.market_intelligence import media_worker
        with patch("intelligence.market_intelligence.media_roots.ensure_media_roots",
                   side_effect=ValueError("media_roots_unconfigured")) as check,patch.object(
             media_worker.d1,"connect",
             side_effect=AssertionError("must not claim DB job")) as connect:
            with self.assertRaisesRegex(ValueError,"media_roots_unconfigured"):
                media_worker._claim()
        check.assert_called_once()
        connect.assert_not_called()

    def test_verified_old_worker_root_repair_requeues_45_and_never_retries_verified_marker(self):
        from contextlib import nullcontext
        from intelligence.market_intelligence import media_worker
        project="a"*32
        videos=[{"video_id":i,"platform":"tiktok",
                 "native_id":str(7600000000000000000+i),
                 "url":f"https://www.tiktok.com/@faceyogilab/video/{7600000000000000000+i}"}
                for i in range(1,51)]
        photos=[{"video_id":i,"platform":"tiktok",
                 "native_id":str(7600000000000000000+i),
                 "url":f"https://www.tiktok.com/@faceyogilab/photo/{7600000000000000000+i}"}
                for i in range(51,101)]
        class DB:
            def __init__(self):
                self.writes=[]
                self.row=None
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def transaction(self):return nullcontext()
            def execute(self,sql,args):
                if "SELECT id,state,last_error,attempts" in sql:
                    vid=int(args[2])
                    if vid==50:self.row=None
                    else:
                        state="failed" if vid<=45 else "completed" if vid<=48 else "running"
                        prior="media_root_v1" if vid<=19 else "media_root_v2"
                        self.row=(100+vid,state,
                                  "media_roots_unconfigured" if state=="failed" else None,
                                  1,1,{"project_id":project,"video_id":vid,
                                       "sha256":"a"*64,"asset_id":"asset"},
                                  {"marketing_local_recovery":prior})
                elif "UPDATE jobs SET state='queued'" in sql:
                    assert "%s::text" in sql
                    assert "result->>'marketing_local_recovery'=%s" in sql
                    self.writes.append(args)
                    self.row=(args[1],)
                else:raise AssertionError("Unexpected SQL: "+sql[:160])
                return self
            def fetchone(self):return self.row
        db=DB()
        with patch.object(cli,"existing_launched_batch",return_value={
             "project_id":project,"batch_id":2,"videos":videos+photos}),patch(
             "intelligence.market_intelligence.batch.d1.connect",
             return_value=db),patch.object(
             cli,"_missing_marketing_schema_tables",return_value=[]),patch(
             "intelligence.market_intelligence.media_roots.ensure_media_roots",
             return_value=["/trusted1","/trusted2"]),patch.object(
             media_worker,"_read_current",
             return_value=("/trusted1/file.mp4","a"*64,True,None,None)),patch(
             "intelligence.market_intelligence.media_guard.verified_media",
             return_value=Path("/trusted1/file.mp4")) as guard,patch(
             "intelligence.market_intelligence._copied.group_a.media_manifest.verify_file",
             return_value=object()) as copied_a_guard,patch.object(
             cli.bd,"_api",side_effect=AssertionError("no requests")):
            report=cli.recover_media_roots_after_restart(ACCOUNT,project)
        self.assertEqual(report["verified_root_v2_requeued"],19)
        self.assertEqual(report["verified_root_v3_requeued"],26)
        self.assertEqual(report["total_requeued"],45)
        self.assertEqual(len(db.writes),45)
        self.assertEqual(guard.call_count,45)
        self.assertEqual(copied_a_guard.call_count,45)
        self.assertEqual(report["provider_requests"],0)
        self.assertEqual(report["ai_calls"],0)
        self.assertFalse(report["more_retries_allowed"])

    def test_verified_root_repair_refuses_unverified_media_and_previous_verified_repair(self):
        from contextlib import nullcontext
        from intelligence.market_intelligence import media_worker
        project="a"*32
        videos=[{"video_id":i,"platform":"tiktok",
                 "url":f"https://www.tiktok.com/@faceyogilab/video/{7600000000000000000+i}"}
                for i in range(1,51)]
        class DB:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def transaction(self):return nullcontext()
            def execute(self,sql,args):
                if "SELECT id,state,last_error,attempts" not in sql:
                    raise AssertionError("No write permitted: "+sql[:100])
                vid=int(args[2])
                self.row=(vid,"failed","media_roots_unconfigured",1,1,
                          {"project_id":project,"video_id":vid,"sha256":"a"*64},
                          {"marketing_local_recovery":
                           "media_root_verified_v3" if vid<=25 else "media_root_v2"})
                return self
            def fetchone(self):return self.row
        with patch.object(cli,"existing_launched_batch",return_value={
             "project_id":project,"batch_id":2,"videos":videos}),patch(
             "intelligence.market_intelligence.batch.d1.connect",
             return_value=DB()),patch.object(
             cli,"_missing_marketing_schema_tables",return_value=[]),patch(
             "intelligence.market_intelligence.media_roots.ensure_media_roots",
             return_value=["/trusted1","/trusted2"]),patch.object(
             media_worker,"_read_current",
             return_value=("/trusted1/file.mp4","a"*64,True,None,None)),patch(
             "intelligence.market_intelligence.media_guard.verified_media",
             side_effect=ValueError("missing")):
            result=cli.recover_media_roots_after_restart(ACCOUNT,project)
        self.assertEqual(result["total_requeued"],0)
        self.assertEqual(result["not_requeued"]["nonmatching_failure_or_not_allowed_to_retry"],25)
        self.assertEqual(result["not_requeued"]["verified_media_missing_or_invalid"],25)

    def test_media_root_final_repair_v2_and_denies_third(self):
        from contextlib import nullcontext
        from intelligence.market_intelligence import media_worker
        project="a"*32
        videos=[{"video_id":i,"platform":"tiktok",
                 "url":f"https://www.tiktok.com/@faceyogilab/video/{7600000000000000000+i}"}
                 for i in range(1,51)]
        class DB:
            def __init__(self):self.updated=[]
            def __enter__(self):return self
            def __exit__(self,*a):return False
            def transaction(self):return nullcontext()
            def execute(self,sql,args):
                if "SELECT id,state,last_error,attempts" in sql:
                    n=int(args[2])
                    marker="media_root_v1" if n<=25 else "media_root_v2"
                    self.result=(n,"failed","media_roots_unconfigured",1,1,
                      {"project_id":project,"video_id":n,"asset_id":"a"},
                      {"marketing_local_recovery":marker})
                elif "UPDATE jobs SET state='queued'" in sql:
                    assert "jsonb_build_object" in sql and "attempts=0" in sql
                    self.updated.append(args)
                    self.result=(args[-1],)
                else:raise AssertionError(sql[:160])
                return self
            def fetchone(self):return self.result
        db=DB()
        with patch.object(cli,"existing_launched_batch",return_value={
            "project_id":project,"batch_id":2,"videos":videos}),patch(
            "intelligence.market_intelligence.batch.d1.connect",
            return_value=db),patch.object(
            cli,"_missing_marketing_schema_tables",return_value=[]),patch(
            "intelligence.market_intelligence.media_roots.ensure_media_roots",
            return_value=["/approved"]),patch.object(
            media_worker,"_read_current",
            return_value=("/approved/file.mp4","a"*64,True,None,None)),patch(
            "intelligence.market_intelligence.media_guard.verified_media",
            return_value=Path("/approved/file.mp4")):
            result=cli.recover_media_root_analyses(ACCOUNT,project)
        self.assertEqual(result["second_and_final_repairs"],25)
        self.assertEqual(result["media_root_failures_requeued"],25)
        self.assertEqual(result["other_pinned_videos_untouched"],25)
        self.assertTrue(all(a[0]=="media_root_v2" for a in db.updated))



    def test_last_tiktok_http_timeout_retry_exactly_one_unbilled_pinned_video(self):
        from contextlib import nullcontext
        from intelligence.market_intelligence import batch
        project="a"*32
        videos=[{"video_id":i,"platform":"tiktok",
                 "native_id":str(7600000000000000000+i),
                 "url":f"https://www.tiktok.com/@faceyogilab/video/{7600000000000000000+i}",
                 "state":"unavailable" if i==49 else "analysis_partial"}
                for i in range(1,51)]
        photos=[{"video_id":i,"platform":"tiktok",
                 "native_id":str(7600000000000000000+i),
                 "url":f"https://www.tiktok.com/@faceyogilab/photo/{7600000000000000000+i}",
                 "state":"analysis_partial"}
                for i in range(51,101)]
        class DB:
            def __init__(self):self.writes=[];self.ret=None
            def __enter__(self):return self
            def __exit__(self,*a):return False
            def transaction(self):return nullcontext()
            def execute(self,sql,args):
                if "pg_try_advisory_xact_lock" in sql:
                    self.ret=(True,)
                elif "SELECT j.id,j.state,j.attempts,j.max_attempts" in sql:
                    v=videos[48]
                    self.ret=("job-99","unavailable",4,4,"unavailable",
                         "ERROR: [TikTok] "+v["native_id"]+
                         ": HTTPSConnectionPool(host='www.tiktok.com'): Read timed out",
                         "not_applicable",0,"unavailable",None,False,
                         v["url"],v["native_id"],"source",None)
                elif "UPDATE source_extraction_jobs" in sql:
                    assert "max_attempts=5" in sql
                    assert "attempts=4 AND max_attempts=4" in sql
                    assert "state='unavailable'" in sql
                    self.writes.append(("job",args))
                    self.ret=(args[0],)
                elif "UPDATE project_video_acquisition_runs" in sql:
                    self.writes.append(("run",args))
                    self.ret=None
                else:raise AssertionError(sql[:120])
                return self
            def fetchone(self):return self.ret
        db=DB()
        with patch.object(cli,"existing_launched_batch",return_value={
            "project_id":project,"batch_id":2,"videos":videos+photos}),patch.object(
            batch.d1,"connect",return_value=db),patch.object(
            batch,"_source_for_video",
            return_value=("source",True,True,True,False)),patch.object(
            cli.bd,"_api",side_effect=AssertionError("must not call Bright Data")):
            result=cli.recover_pinned_tiktok_timeout(ACCOUNT,project)
        self.assertEqual(result["requeued"],1)
        self.assertEqual(result["requeued_video_ids"],[49])
        self.assertEqual(result["brightdata_requests"],0)
        self.assertEqual(result["model_calls"],0)
        self.assertEqual([x[0] for x in db.writes],["job","run"])

    def test_last_tiktok_timeout_retry_refuses_unrelated_errors_and_exhausted_fifth(self):
        from contextlib import nullcontext
        from intelligence.market_intelligence import batch
        project="a"*32
        videos=[{"video_id":i,"platform":"tiktok",
                 "native_id":str(7600000000000000000+i),
                 "url":f"https://www.tiktok.com/@faceyogilab/video/{7600000000000000000+i}",
                 "state":"unavailable" if i==49 else "analysis_partial"}
                for i in range(1,51)]
        photos=[{"video_id":i,"platform":"tiktok",
                 "native_id":str(7600000000000000000+i),
                 "url":f"https://www.tiktok.com/@faceyogilab/photo/{7600000000000000000+i}"}
                for i in range(51,101)]
        class DB:
            def __enter__(self):return self
            def __exit__(self,*a):return False
            def transaction(self):return nullcontext()
            def execute(self,sql,args):
                if "pg_try_advisory_xact_lock" in sql:
                    self.value=(True,)
                elif "SELECT j.id,j.state,j.attempts,j.max_attempts" in sql:
                    v=videos[48]
                    self.value=("job","unavailable",5,5,"unavailable",
                       "ERROR: [TikTok] "+v["native_id"]+
                       ": HTTPSConnectionPool(host='www.tiktok.com'): Read timed out",
                       "not_applicable",0,"unavailable",None,False,
                       v["url"],v["native_id"],"source",None)
                else:raise AssertionError("unexpected write "+sql[:120])
                return self
            def fetchone(self):return self.value
        with patch.object(cli,"existing_launched_batch",return_value={
             "project_id":project,"batch_id":2,"videos":videos+photos}),patch.object(
             batch.d1,"connect",return_value=DB()),patch.object(
             batch,"_source_for_video",
             return_value=("source",True,True,True,False)):
            result=cli.recover_pinned_tiktok_timeout(ACCOUNT,project)
        self.assertEqual(result["requeued"],0)
        self.assertEqual(result["skipped"]["not_exact_unbilled_tiktok_4_of_4_timeout"],1)

    def test_diagnose_remaining_only_three_exact_pinned_unfinished_videos(self):
        from contextlib import nullcontext
        from intelligence.market_intelligence import batch
        project="a"*32
        videos=[{"video_id":i,"platform":"tiktok",
                 "native_id":str(7600000000000000000+i),
                 "url":f"https://www.tiktok.com/@faceyogilab/video/{7600000000000000000+i}",
                 "state":"analysis_partial" if i<=47 else
                         "media_awaiting_analysis" if i<=49 else "unavailable"}
                for i in range(1,51)]
        photos=[{"video_id":i,"platform":"tiktok",
                 "native_id":str(7600000000000000000+i),
                 "url":f"https://www.tiktok.com/@faceyogilab/photo/{7600000000000000000+i}",
                 "state":"analysis_partial"}
                for i in range(51,101)]
        class DB:
            def __init__(self):self.readonly=False;self.sql_calls=0
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def transaction(self):return nullcontext()
            def execute(self,sql,args=()):
                self.sql_calls+=1
                if sql.strip()=="SET TRANSACTION READ ONLY":
                    self.readonly=True
                    self.value=None
                elif "FROM jobs" in sql:
                    assert self.readonly
                    n=int(args[2])
                    if n==48:
                        self.value=("running",None,1,1,"media_root_v1",3800,1600)
                    elif n==49:
                        self.value=("failed","media_roots_unconfigured",1,1,
                                    "media_root_verified_v3",4000,None)
                    else:self.value=None
                elif "FROM project_video_acquisition_runs r" in sql:
                    assert self.readonly
                    n=int(args[1])-7600000000000000000
                    assert args[2]==videos[n-1]["url"]
                    self.value=(("unavailable","unavailable","not downloaded",
                                 "unavailable","remote unavailable") if n==50
                                else ("completed",None,None,"complete",None))
                else:raise AssertionError(sql[:120])
                return self
            def fetchone(self):return self.value
        db=DB()
        with patch.object(cli,"existing_launched_batch",return_value={
              "project_id":project,"batch_id":2,"videos":videos+photos,
              "state":{"counts":{"analysis_partial":97,
                "media_awaiting_analysis":2,"unavailable":1}}}),patch.object(
              batch.d1,"connect",return_value=db),patch.object(
              cli.bd,"_api",side_effect=AssertionError("no provider calls")):
            result=cli.diagnose_remaining_media(ACCOUNT,project)
        self.assertTrue(db.readonly)
        self.assertEqual(db.sql_calls,7)
        self.assertEqual(result["remaining_video_count"],3)
        self.assertEqual([r["video_id"] for r in result["remaining_videos"]],
                         [48,49,50])
        self.assertEqual(result["remaining_videos"][0]["analysis_job_state"],
                         "running")
        self.assertEqual(result["remaining_videos"][1]["recovery_marker"],
                         "media_root_verified_v3")
        self.assertEqual(result["remaining_videos"][2]["acquisition_state"],
                         "unavailable")
        self.assertEqual(result["database_writes"],0)
        self.assertEqual(result["provider_requests"],0)
        self.assertEqual(result["ai_calls"],0)

    def test_stall_diagnostic_distinguishes_26_waiting_21_downloads_and_no_jobs(self):
        from contextlib import nullcontext
        project="a"*32
        videos=[{"video_id":i,"platform":"tiktok",
                 "native_id":str(7600000000000000000+i),
                 "url":f"https://www.tiktok.com/@faceyogilab/video/{7600000000000000000+i}",
                 "state":"media_awaiting_analysis" if i<=26 else "downloading"}
                for i in range(1,51)]
        photos=[{"video_id":i,"platform":"tiktok",
                 "native_id":str(7600000000000000000+i),
                 "url":f"https://www.tiktok.com/@faceyogilab/photo/{7600000000000000000+i}",
                 "state":"analysis_partial"}
                for i in range(51,101)]
        class DB:
            def __init__(self):self.readonly=False;self.n=0
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def transaction(self):return nullcontext()
            def execute(self,sql,args=()):
                if sql.strip()=="SET TRANSACTION READ ONLY":
                    self.readonly=True
                    self.row=None
                elif "FROM project_video_acquisition_runs r" in sql:
                    assert self.readonly
                    n=int(args[1])-7600000000000000000
                    assert args[2]==videos[n-1]["url"]
                    self.row=("job",("completed" if n<=29 else "queued"),
                              1,3,None,None,"complete" if n<=29 else "queued",
                              None,None,100,None)
                elif "FROM jobs" in sql:
                    assert self.readonly
                    n=int(args[2])
                    self.row=(n,"queued",None,0,1,90,None,-1,None,
                              {"marketing_local_recovery":"media_root_v1"}) if n<=26 else (
                              n,"completed",None,1,1,90,None,-1,None,{}) if n<=29 else None
                else:
                    raise AssertionError("read only SQL unexpected: "+sql[:150])
                self.n+=1
                return self
            def fetchone(self):return self.row
        db=DB()
        with patch.object(cli,"existing_launched_batch",return_value={
             "project_id":project,"batch_id":2,"videos":videos+photos,
             "state":{"counts":{"analysis_partial":53,"media_awaiting_analysis":26,
                                "downloading":21}}}),patch(
             "intelligence.market_intelligence.batch.d1.connect",return_value=db):
            report=cli.diagnose_stalled_batch(ACCOUNT,project)
        self.assertEqual(report["batch_id"],2)
        self.assertEqual(report["video_analysis_jobs"]["states"],
                         {"queued":26,"completed":3,"not_queued":21})
        self.assertEqual(report["video_download_jobs"]["states"],
                         {"completed":29,"queued":21})
        self.assertEqual(report["jobs_requeued"],0)
        self.assertEqual(report["provider_requests"],0)
        self.assertTrue(db.readonly)
        self.assertEqual(db.n,101)

    def test_media_roots_bootstrap_works_in_independent_worker(self):
        import os
        from intelligence.market_intelligence import media_roots
        with TemporaryDirectory() as d:
            root=Path(d)
            home=root/"home"
            home.mkdir()
            with patch.object(media_roots,"PROJECT_ROOT",root),patch(
                 "intelligence.market_intelligence.media_roots.Path.home",
                 return_value=home),patch(
                 "intelligence.market_intelligence.media_roots.config.setting",
                 return_value=""),patch.dict(os.environ,{},clear=False):
                roots=media_roots.ensure_media_roots()
                self.assertEqual(len(roots),2)
                self.assertTrue((root/"marketing-qa"/"media").is_dir())
                self.assertTrue((home/"Downloads"/"TikTokArchive").is_dir())
                self.assertIn(str((root/"marketing-qa"/"media").resolve()),roots)
                self.assertIn(str((home/"Downloads"/"TikTokArchive").resolve()),roots)
                self.assertEqual(os.environ["CM_MARKET_MEDIA_ROOTS"],
                                 os.pathsep.join(roots))

    def test_media_analyzer_always_initializes_roots_before_verification(self):
        from intelligence.market_intelligence import media_worker
        class DB:
            def __enter__(self):return self
            def __exit__(self,*args):return False
        payload={"project_id":"a"*32,"video_id":7}
        with patch.object(media_worker.d1,"connect",return_value=DB()),patch.object(
             media_worker,"_read_current",
             return_value=("/some/verified/file.mp4","a"*64,False,None,None)),patch(
             "intelligence.market_intelligence.media_roots.ensure_media_roots",
             return_value=["/approved"]) as roots,patch.object(
             media_worker,"verified_media",
             side_effect=RuntimeError("verified_media_was_called")) as check:
            with self.assertRaisesRegex(RuntimeError,"verified_media_was_called"):
                media_worker.process((10,payload,1))
        roots.assert_called_once_with()
        self.assertEqual(check.call_args.kwargs["roots"],["/approved"])

    def test_recover_26_exact_sha_verified_video_analyses_only(self):
        from contextlib import nullcontext
        from intelligence.market_intelligence import media_worker
        project="a"*32
        video_ids=list(range(2,52))
        videos=[{"video_id":vid,"platform":"tiktok",
                 "native_id":str(7600000000000000000+vid),
                 "url":f"https://www.tiktok.com/@faceyogilab/video/{7600000000000000000+vid}",
                 "state":"media_awaiting_analysis"} for vid in video_ids]
        videos.extend({"video_id":vid,"platform":"tiktok",
                       "native_id":str(7600000000000000000+vid),
                       "url":f"https://www.tiktok.com/@faceyogilab/photo/{7600000000000000000+vid}",
                       "state":"analysis_partial"} for vid in range(52,102))
        class DB:
            def __init__(self):self.requeued=[]
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def transaction(self):return nullcontext()
            def execute(self,sql,args):
                if "SELECT id,state,last_error,attempts" in sql:
                    vid=int(args[2])
                    self.response=(
                        vid+100,"failed","media_roots_unconfigured",1,1,
                        {"project_id":project,"video_id":vid,"asset_id":"asset",
                         "source_id":"source","sha256":"a"*64},
                        None) if vid<=27 else (
                        vid+100,"completed",None,1,1,
                        {"project_id":project,"video_id":vid},None)
                elif "UPDATE jobs SET state='queued'" in sql:
                    assert "attempts=0" in sql
                    assert "marketing_local_recovery" in sql
                    assert "max_attempts>=1" in sql
                    self.requeued.append(args[0])
                    self.response=(args[0],)
                else:raise AssertionError(sql[:120])
                return self
            def fetchone(self):return self.response
        db=DB()
        with patch.object(cli,"existing_launched_batch",return_value={
             "project_id":project,"batch_id":2,"videos":videos}),patch(
             "intelligence.market_intelligence.batch.d1.connect",
             return_value=db),patch.object(
             cli,"_missing_marketing_schema_tables",return_value=[]),patch(
             "intelligence.market_intelligence.media_roots.ensure_media_roots",
             return_value=["/approved"]),patch.object(
             media_worker,"_read_current",return_value=(
                 "/approved/source.mp4","a"*64,True,None,None)),patch(
             "intelligence.market_intelligence.media_guard.verified_media",
             return_value=Path("/approved/source.mp4")) as verified,patch.object(
             cli.bd,"_api",side_effect=AssertionError("no network")):
            r=cli.recover_media_root_analyses(ACCOUNT,project)
        self.assertEqual(r["media_root_failures_requeued"],26)
        self.assertEqual(r["other_pinned_videos_untouched"],24)
        self.assertEqual(len(db.requeued),26)
        self.assertEqual(verified.call_count,26)
        self.assertEqual(r["new_media_downloads"],0)
        self.assertEqual(r["provider_requests"],0)
        self.assertEqual(r["model_requests"],0)
        self.assertEqual(r["observed_job_states"]["failed:media_roots_unconfigured"],26)
        self.assertEqual(r["observed_job_states"]["completed"],24)

    def test_media_root_recovery_never_requeues_other_errors(self):
        from contextlib import nullcontext
        project="a"*32
        vids=[{"video_id":i,"platform":"tiktok","url":f"https://www.tiktok.com/@fake/video/{7600000000000000000+i}"}
              for i in range(1,51)]
        class DB:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def transaction(self):return nullcontext()
            def execute(self,sql,args):
                if "SELECT id,state,last_error" in sql:
                    self.answer=(int(args[2])+100,"failed","model_error",1,3,
                                 {"project_id":project,"video_id":int(args[2])},None)
                    return self
                raise AssertionError("no writes permitted")
            def fetchone(self):return self.answer
        with patch.object(cli,"existing_launched_batch",return_value={
             "project_id":project,"batch_id":2,"videos":vids}),patch(
             "intelligence.market_intelligence.batch.d1.connect",return_value=DB()),patch.object(
             cli,"_missing_marketing_schema_tables",return_value=[]),patch(
             "intelligence.market_intelligence.media_roots.ensure_media_roots",
             return_value=["/approved"]):
            r=cli.recover_media_root_analyses(ACCOUNT,project)
        self.assertEqual(r["media_root_failures_requeued"],0)
        self.assertEqual(r["other_pinned_videos_untouched"],50)

    def test_registration_transfer_is_separate_deployment_opt_in(self):
        with patch("intelligence.market_intelligence.tiktok_top100_register.config.setting",
                   side_effect=lambda key,default="": "1" if key=="CM_MARKET_VISION_ENABLED" else ""):
            self.assertFalse(reg.transfer_allowed())
        with patch("intelligence.market_intelligence.tiktok_top100_register.config.setting",
                   return_value="1"):
            self.assertTrue(reg.transfer_allowed())


if __name__=="__main__":
    unittest.main()
