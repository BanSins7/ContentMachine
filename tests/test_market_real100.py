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
