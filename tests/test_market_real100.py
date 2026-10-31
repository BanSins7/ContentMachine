"""Real Marketing 100 runner contracts. All data providers/network calls mocked.

Not a fake "100 live videos" certification: tests enforce that actual batch
launch is blocked before any effects if 100 project-sourced videos or rights
are absent; paid metric queue is strictly scoped and explicitly requested.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from intelligence.market_intelligence import batch,brightdata_jobs
from intelligence.market_intelligence.contracts import MarketError
from scripts import run_market_real100 as real

PROJECT="a"*32
URL="https://www.tiktok.com/@luke.swan89/video/7701927285129055520"


def rows(count):
    return [(idx+1,URL[:-19]+str(idx+1000000000000000000),
             "tiktok",str(idx+1000000000000000000),"author"+str(idx//10))
            for idx in range(count)]


class FakeDB:
    def __init__(self,videos):
        self.videos=videos
        self.queries=[]
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def execute(self,sql,args=()):
        self.queries.append((sql,args))
        if "FROM videos v" in sql and "project_sources ps" in sql:
            return self
        raise AssertionError(sql[:60])
    def fetchall(self): return self.videos


def base_patches():
    return [
        patch("scripts.run_market_real100.batch._project"),
        patch("scripts.run_market_real100.batch._source_for_video",
              return_value=("source-uuid",True,True,True,True)),
        patch("scripts.run_market_real100.vision_enabled",return_value=True),
        patch("scripts.run_market_real100.shutil.which",return_value="exists"),
        patch("scripts.run_market_real100.config.setting",
              return_value="C:\\ContentMachine\\videos"),
        patch("scripts.run_market_real100.brightdata_provider.readiness",
              return_value={"configured":True,"enabled":True,
                            "rights_verified":True,"ready":True}),
    ]


class RealHundredAcceptance(unittest.TestCase):
    def test_preflight_reports_exact_100_registered_source_rights(self):
        db=FakeDB(rows(100))
        with base_patches()[0] as root,base_patches()[1] as source,\
             base_patches()[2],base_patches()[3],base_patches()[4],base_patches()[5]:
            report=real.preflight(PROJECT,connection=db)
        self.assertEqual(report["selected_real_video_ids"],100)
        self.assertEqual(report["missing_to_100"],0)
        self.assertEqual(report["rights"]["approved_fetch_store_analyze"],100)
        self.assertTrue(report["can_start_real_media"])
        self.assertEqual(report["paid_triggers"],0)
        self.assertEqual(len(report["video_ids"]),100)
        self.assertEqual(len(db.queries),1)

    def test_99_posts_are_not_reported_as_100(self):
        db=FakeDB(rows(99))
        with base_patches()[0],base_patches()[1],base_patches()[2],\
             base_patches()[3],base_patches()[4],base_patches()[5]:
            r=real.preflight(PROJECT,connection=db)
        self.assertEqual(r["missing_to_100"],1)
        self.assertFalse(r["can_start_real_media"])

    def test_missing_rights_blocks_before_any_batch_or_provider(self):
        db=FakeDB(rows(100))
        with base_patches()[0],patch(
            "scripts.run_market_real100.batch._source_for_video",
            return_value=None),base_patches()[2],base_patches()[3],\
            base_patches()[4],base_patches()[5],\
            patch("scripts.run_market_real100.batch.start_batch") as start,\
            patch("scripts.run_market_real100.brightdata_jobs.queue_metrics") as metrics:
            with self.assertRaisesRegex(ValueError,"real100_preflight_failed"):
                real.run(PROJECT,paid_metrics=True,connection=db)
            start.assert_not_called()
            metrics.assert_not_called()

    def test_real_batch_rejects_shortage_before_inserting(self):
        class C(FakeDB):
            def execute(self,sql,args=()):
                if "INSERT INTO jobs" in sql:
                    self.fail_insert=True
                    raise AssertionError("insert must never run")
                return super().execute(sql,args)
        db=C(rows(99))
        with patch("intelligence.market_intelligence.batch._project"),patch(
             "intelligence.market_intelligence.batch.d1.connect",
             return_value=db),patch(
             "intelligence.market_intelligence.batch.start_worker") as worker:
            with self.assertRaises(MarketError) as raised:
                batch.start_batch(PROJECT,100)
            self.assertEqual(raised.exception.code,"insufficient_real_videos")
            worker.assert_not_called()

    def test_paid_queue_uses_only_explicit_100_video_ids(self):
        ids=list(range(1,101))
        eligible={
            "can_start_real_media":True,"video_ids":ids,
            "brightdata_readiness":{"ready":True},
        }
        with TemporaryDirectory() as tmp,patch(
            "scripts.run_market_real100.REPORTS",Path(tmp)),patch(
            "scripts.run_market_real100.preflight",return_value=eligible),patch(
            "scripts.run_market_real100.batch.start_batch",
            return_value={"selected":100,"batch_id":17}) as start,patch(
            "scripts.run_market_real100.brightdata_jobs.queue_metrics",
            return_value={"enabled":True,"scoped_to_video_ids":True,
                          "queued":2}) as metric:
            real.run(PROJECT,paid_metrics=True)
            start.assert_called_once_with(PROJECT,100)
            metric.assert_called_once_with(PROJECT,ids)
            with self.assertRaises(FileExistsError):
                real.run(PROJECT,paid_metrics=True)
            self.assertEqual(metric.call_count,1)

    def test_unpaid_run_never_queues_brightdata(self):
        ids=list(range(1,101))
        with TemporaryDirectory() as tmp,patch(
            "scripts.run_market_real100.REPORTS",Path(tmp)),patch(
            "scripts.run_market_real100.preflight",
            return_value={"can_start_real_media":True,"video_ids":ids,
                          "brightdata_readiness":{"ready":False}}),patch(
            "scripts.run_market_real100.batch.start_batch",
            return_value={"selected":100,"batch_id":17}),patch(
            "scripts.run_market_real100.brightdata_jobs.queue_metrics") as metrics:
            real.run(PROJECT,paid_metrics=False)
            metrics.assert_not_called()

    def test_status_once_never_starts_workers(self):
        fake={"batch_id":17,"selected":100,
              "download_and_analysis_states":{"analysis_partial":17},
              "media_analyses_persisted":17,"metric_jobs":{},
              "group_c_state":"awaiting_exact_metrics_or_media",
              "group_c_coverage":{}}
        with patch("scripts.run_market_real100.status_report",return_value=fake),\
             patch("scripts.run_market_real100._write_report",
                   return_value=Path("report.json")),\
             patch("scripts.run_market_real100.batch.start_worker") as a,\
             patch("scripts.run_market_real100.media_worker.start_worker") as b,\
             patch("scripts.run_market_real100.brightdata_jobs.start_worker") as c:
            self.assertEqual(real.watch(PROJECT,once=True),fake)
            a.assert_not_called(); b.assert_not_called(); c.assert_not_called()

    def test_status_never_labels_partial_abc_as_certified(self):
        fake={"batch_id":19,"requested":100,"selected":100,
              "counts":{"analysis_partial":100},
              "videos":[{"video_id":n,"state":"analysis_partial"}
                        for n in range(1,101)],
              "group_c_state":"awaiting_exact_metrics_or_media",
              "group_c_coverage":{},"group_c_snapshot_id":None}
        with patch("scripts.run_market_real100.batch.status",
                   return_value=fake),patch(
             "scripts.run_market_real100.brightdata_jobs.status",
             return_value={"jobs":[]}):
            result=real.status_report(PROJECT)
        self.assertEqual(result["media_analyses_persisted"],100)
        self.assertFalse(result["full_100_mp4_a_b_c_certified"])
        self.assertIsNone(result["group_c_snapshot_id"])

    def test_profile_discovery_no_paid_metric_implicit_dispatch(self):
        from intelligence.market_intelligence import profile_jobs
        import inspect
        source=inspect.getsource(profile_jobs.run_once)
        self.assertNotIn("queue_metrics(",source)
        self.assertNotIn("bd.trigger(",source)

    def test_discover_uses_actual_registered_accounts(self):
        profiles=[{"url":"https://www.youtube.com/@realaccount",
                   "platform":"youtube","native_id":None}]
        jobs=[{"profile_url":profiles[0]["url"],"state":"completed"}]
        with patch("intelligence.market_intelligence.batch._project"),patch(
            "intelligence.market_intelligence.service._seeds_from_registry",
            return_value={"authors":profiles}),patch(
            "intelligence.market_intelligence.profile_jobs.queue_profiles",
            return_value=jobs) as queued,patch(
            "intelligence.market_intelligence.profile_jobs.start_worker"),patch(
            "intelligence.market_intelligence.profile_jobs.list_jobs",
            return_value={"jobs":jobs,"total_discovered_video_links":100}),patch(
            "intelligence.market_intelligence.brightdata_jobs.queue_metrics") as paid:
            summary=real.discover(PROJECT,interval=5)
            queued.assert_called_once_with(PROJECT,profiles)
            paid.assert_not_called()
        self.assertEqual(summary["discovered_video_links"],100)

    def test_exact_c_audit_reports_each_stage_and_missing_100_not_certified(self):
        found=[{"video_id":n,"state":"analysis_partial"} for n in range(1,101)]
        mock_status={"batch_id":9,"videos":found}
        evid=[{"video_id":n,"platform":"tiktok",
               "mi_analysis_id":n,
               "metric_snapshot_id":n,
               "group_a":{"status":"partial"},
               "group_b":{"blocks":{"B1":{}}},
               "group_c":{
                   "c0":"unavailable",
                   "c0_quality_gate":{"reason":"observation_time_assumed"},
                   "c1_creator_ratio":None,
                   "c2_niche_outlier":None,
                   "c3_views_growth_per_hour":None,
                   "c4":None,"c5":None,"c6_freshness":None,
                   "c7_breakout_preview":None}}
              for n in range(1,101)]
        class Preview:
            def preview(self,project):
                return {"snapshot":{"video_evidence":evid}}
        with patch("scripts.run_market_real100.batch.status",return_value=mock_status):
            audit=real.audit_c(PROJECT,service=Preview())
        self.assertEqual(audit["actual_d1_video_evidence"],100)
        self.assertEqual(audit["stage_counts_out_of_100"]["A_report"],100)
        self.assertEqual(audit["stage_counts_out_of_100"]["B_report"],100)
        self.assertEqual(audit["stage_counts_out_of_100"]["C0"],0)
        self.assertEqual(audit["stage_counts_out_of_100"]["C7"],0)
        self.assertEqual(audit["c0_failure_reasons"]["C0:observation_time_assumed"],100)
        self.assertFalse(audit["full_100_abc_certified"])

    def test_c_audit_fails_if_batch_has_99_even_if_project_has_100(self):
        mock_status={"batch_id":9,"videos":[
            {"video_id":n} for n in range(1,100)]}
        with patch("scripts.run_market_real100.batch.status",return_value=mock_status):
            with self.assertRaisesRegex(ValueError,"audit_requires_pinned_exactly_100"):
                real.audit_c(PROJECT)

    def test_provider_claim_filters_paid_jobs_by_scope_before_claim(self):
        class R:
            def fetchone(self):return None
        class DB:
            def __init__(self):self.statements=[]
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def transaction(self):return nullcontext()
            def execute(self,sql,args):
                self.statements.append((sql,args))
                return R()
        db=DB()
        scope="a"*32
        with patch("intelligence.market_intelligence.brightdata_jobs.d1.connect",
                   return_value=db):
            self.assertIsNone(brightdata_jobs._claim(
                project_id=PROJECT,scope_id=scope))
        self.assertEqual(len(db.statements),3)
        for sql,args in db.statements:
            self.assertIn("real100_scope_id",sql)
            self.assertIn("project_id",sql)
            self.assertEqual(args,(brightdata_jobs.JOB_TYPE,
                                   PROJECT,PROJECT,scope,scope))

    def test_paid_queue_has_scope_marker_before_any_trigger(self):
        class R:
            def __init__(self,rows=None,one=None):
                self.rows=rows or [];self.one=one
            def fetchall(self):return self.rows
            def fetchone(self):return self.one
        class DB:
            def __init__(self):self.payload=None
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def transaction(self):return nullcontext()
            def execute(self,sql,args):
                if "SELECT payload->'urls'" in sql:return R([])
                if "FROM videos v" in sql:
                    return R([(1,"tiktok",URL,None,None)]
                             if args[3]=="tiktok" else [])
                if "INSERT INTO jobs" in sql:
                    import json
                    self.payload=json.loads(args[2])
                    return R(one=(17,))
                raise AssertionError(sql[:65])
        db=DB()
        with patch("intelligence.market_intelligence.brightdata_jobs.bd.readiness",
                   return_value={"ready":True}),patch(
             "intelligence.market_intelligence.brightdata_jobs.d1.connect",
             return_value=db),patch(
             "intelligence.market_intelligence.brightdata_jobs.start_worker") as worker:
            result=brightdata_jobs.queue_metrics(PROJECT,[1])
        self.assertEqual(result["queued"],1)
        self.assertEqual(db.payload["urls"],[URL])
        self.assertEqual(db.payload["project_id"],PROJECT)
        self.assertEqual(len(db.payload["real100_scope_id"]),32)
        worker.assert_called_once_with(
            project_id=PROJECT,scope_id=db.payload["real100_scope_id"])

    def test_paid_watch_restarts_only_scoped_metric_worker(self):
        videos=[{"video_id":i} for i in range(1,101)]
        fake={"batch_id":7,"selected":100,
              "download_and_analysis_states":{},"metric_jobs":{},
              "media_analyses_persisted":0,
              "group_c_state":"awaiting_exact_metrics_or_media",
              "group_c_coverage":{}}
        with patch("scripts.run_market_real100.batch.start_worker"),patch(
            "scripts.run_market_real100.media_worker.start_worker"),patch(
            "scripts.run_market_real100.batch.status",
            return_value={"videos":videos}),patch(
            "scripts.run_market_real100.status_report",
            return_value=fake),patch(
            "scripts.run_market_real100._write_report",
            return_value=Path("progress.json")),patch(
            "scripts.run_market_real100.time.sleep",
            side_effect=KeyboardInterrupt),patch(
            "scripts.run_market_real100.brightdata_jobs.start_worker") as worker:
            with self.assertRaises(KeyboardInterrupt):
                real.watch(PROJECT,resume_paid=True)
            worker.assert_called_once()
            self.assertEqual(worker.call_args.kwargs["project_id"],PROJECT)
            self.assertEqual(len(worker.call_args.kwargs["scope_id"]),32)

    def test_paid_scope_must_be_unique_and_bounded(self):
        with patch("intelligence.market_intelligence.brightdata_jobs.bd.readiness",
                   return_value={"ready":True}),patch(
            "intelligence.market_intelligence.brightdata_jobs.d1.connect",
            side_effect=AssertionError("DB must not be contacted")):
            for invalid in ([],[1,1],list(range(101)),[True],["1"]):
                with self.subTest(invalid=str(invalid)[:40]),self.assertRaises(MarketError):
                    brightdata_jobs.queue_metrics(PROJECT,invalid)

    def test_real_sql_provider_queue_is_strictly_scoped_to_ids(self):
        class R:
            def __init__(self,rows=None,one=None):
                self.rows=rows or [];self.one=one
            def fetchall(self):return self.rows
            def fetchone(self):return self.one
        class DB:
            def __init__(self):self.checked=[];self.queries=[]
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def transaction(self):return nullcontext()
            def execute(self,sql,args):
                if "SELECT payload->'urls'" in sql: return R([])
                if "FROM videos v" in sql:
                    self.queries.append((sql,args))
                    if args[3]=="tiktok":
                        return R([(1,"tiktok",URL,None,None)])
                    return R([])
                if "INSERT INTO jobs" in sql: return R(one=(17,))
                raise AssertionError(sql[:75])
        db=DB()
        with patch("intelligence.market_intelligence.brightdata_jobs.bd.readiness",
                   return_value={"ready":True}),patch(
             "intelligence.market_intelligence.brightdata_jobs.d1.connect",
             return_value=db),patch(
             "intelligence.market_intelligence.brightdata_jobs.start_worker") as worker:
            result=brightdata_jobs.queue_metrics(PROJECT,[1])
        self.assertTrue(result["scoped_to_video_ids"])
        self.assertEqual(result["queued"],1)
        self.assertEqual(result["scope_size"],1)
        self.assertEqual(len(db.queries),3)
        for sql,args in db.queries:
            self.assertIn("v.id=ANY(%s::bigint[])",sql)
            self.assertEqual(args[-2:],([1],[1]))
        worker.assert_called_once()


if __name__=="__main__":
    unittest.main()
