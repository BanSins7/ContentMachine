"""VC-MOD-12: project-scoped read-only Collector runs/videos/trace + HTTP/UI QA.

Offline SQL facsimile. No paid provider, cursor-driven provider paging, DTK GET,
SRE send, or PostgreSQL writes; PG17 race/real seeded QA is a later gate.
"""
from __future__ import annotations

from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from unittest import TestCase
from unittest.mock import patch

from intelligence.collector.contracts import CollectorError
from intelligence.collector import http_api
from intelligence.collector.results_api import (
    list_runs,list_run_results,get_run_trace,
)

PROJECT="a"*32
FOREIGN="b"*32
NOW=datetime(2026,10,26,12,30,tzinfo=timezone.utc)
TASK_UUID="11111111-1111-4111-8111-111111111111"
GEN="22222222-2222-4222-8222-222222222222"
PUB="33333333-3333-4333-8333-333333333333"
QUERY="44444444-4444-4444-8444-444444444444"
MANIFEST="55555555-5555-4555-8555-555555555555"
VIDEOID="7250000000000001000"


class Cursor:
    def __init__(self,rows):
        self.rows=rows
    def fetchone(self):
        return self.rows[0] if self.rows else None
    def fetchall(self):
        return list(self.rows)


class DB:
    def __init__(self):
        self.queries=[]
        self.runs=[
            (202,"completed","completed",NOW,NOW,GEN,QUERY,
             "dtk_tiktok",MANIFEST,"published",PUB,1,1,2,2),
            (201,"running","released",NOW,None,GEN,QUERY,
             "dtk_tiktok",MANIFEST,"published",PUB,1,0,0,0),
        ]
        self.videos=[
            (700,0,NOW,"f"*64,61,TASK_UUID,100,"valid",VIDEOID,
             "https://www.tiktok.com/@demo/video/"+VIDEOID,
             "Example workout","video",NOW,Decimal("12.500"),
             800,"collector.keyword",900,NOW,
             "66666666-6666-4666-8666-666666666666","pending",2,None,None,
             [QUERY],["hyp_demo"]),
            (701,0,NOW,"e"*64,62,TASK_UUID,101,"partial",
             "7250000000000001001","https://www.tiktok.com/@demo/video/7250000000000001001",
             None,"unknown",None,None,
             801,"collector.keyword",901,NOW,
             "77777777-7777-4777-8777-777777777777","delivered",1,None,NOW,
             [QUERY],["hyp_demo"]),
        ]
        self.ancestry=(QUERY,GEN,PUB,2,"b"*64,"dtk_tiktok","c"*64,
                       MANIFEST,PUB,"published",2,0,2,"d"*64)
        self.tasks=[
            (50,"completed",TASK_UUID,None,61,"succeeded","completed",
             1,2,0,2,0,2,1,0,1,0),
        ]
        self.qv=[(50,QUERY,"hyp_demo","dir_demo")]
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def execute(self,sql,params=None):
        self.queries.append((sql,params))
        if "FROM collector_runs c" in sql and "ORDER BY c.run_id DESC" in sql:
            self._project_assert(sql,params)
            before=params[1]
            rows=[x for x in self.runs if before is None or x[0]<before]
            return Cursor(rows[:params[-1]])
        if "FROM collector_ingest_pages ip" in sql and "JOIN collector_ingest_page_videos pv" in sql:
            self._project_assert(sql,params)
            page,video=params[2],params[4]
            rows=[x for x in self.videos if (x[0],x[6])>(page,video)]
            return Cursor(rows[:params[-1]])
        if "FROM collector_runs c" in sql and "JOIN collector_query_sets q" in sql:
            self._project_assert(sql,params)
            return Cursor([self.ancestry])
        if "FROM collector_provider_tasks t" in sql and "SELECT t.id,t.state" in sql:
            self._project_assert(sql,params)
            return Cursor(self.tasks)
        if "FROM collector_sp_intake_receipts r" in sql:
            # This legacy C9 fixture has no SP/Deep V2 handoff receipt.
            return Cursor([])
        if "JOIN collector_task_queries l" in sql:
            return Cursor(self.qv)
        raise AssertionError("Unexpected SQL: "+sql[:115])
    @staticmethod
    def _project_assert(sql,params):
        assert PROJECT in params and sql.count("project_id")>0


class Handler:
    def __init__(self):
        self.response=None
    def json_response(self,code,data):
        self.response=code,data


class ReadOnlyResultsTests(TestCase):
    def test_latest_project_runs_list_with_real_d1_counters(self):
        db=DB()
        result=list_runs(PROJECT,limit=1,connect=lambda:db)
        self.assertEqual(result["schema_version"],"collector.run_list/v1")
        self.assertEqual(len(result["runs"]),1)
        self.assertEqual(result["runs"][0]["run_id"],202)
        self.assertEqual(result["runs"][0]["d1_saved_videos"],2)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["next_before_run_id"],202)
        self.assertEqual(result["external_calls"],0)

    def test_runs_keyset_next_page_no_duplicate_run(self):
        db=DB()
        data=list_runs(PROJECT,limit=1,before_run_id=202,connect=lambda:db)
        self.assertEqual([r["run_id"] for r in data["runs"]],[201])
        self.assertFalse(data["has_more"])
        self.assertIsNone(data["next_before_run_id"])

    def test_results_page_only_joined_real_d1_video_and_metadata(self):
        db=DB()
        with patch("intelligence.collector.results_api._run",return_value=(202,)):
            data=list_run_results(PROJECT,202,limit=1,connect=lambda:db)
        self.assertTrue(data["d1_only"])
        self.assertEqual(data["videos"][0]["video_id"],100)
        self.assertEqual(data["videos"][0]["duration_seconds"],"12.500")
        self.assertEqual(data["videos"][0]["candidate_ids"],[QUERY])
        self.assertEqual(data["videos"][0]["hypothesis_ids"],["hyp_demo"])
        self.assertEqual(data["videos"][0]["sre"]["delivery_state"],"pending")
        self.assertEqual(data["videos"][0]["sre"]["relevance_state"],"not_evaluated")
        self.assertFalse(data["videos"][0]["sre"]["classification_verified"])
        self.assertTrue(data["has_more"])
        self.assertEqual(data["next_after"],"700.100")

    def test_result_keyset_continues_exact_video_not_provider_page_two(self):
        db=DB()
        with patch("intelligence.collector.results_api._run",return_value=(202,)):
            data=list_run_results(PROJECT,202,limit=1,after="700.100",
                                  connect=lambda:db)
        self.assertEqual(len(data["videos"]),1)
        self.assertEqual(data["videos"][0]["video_id"],101)
        self.assertFalse(data["has_more"])
        self.assertIsNone(data["next_after"])
        self.assertEqual(data["videos"][0]["sre"]["delivery_state"],"delivered")
        self.assertFalse(data["videos"][0]["sre"]["classification_verified"])

    def test_empty_page_is_truthful_without_fabricated_results(self):
        db=DB();db.videos=[]
        with patch("intelligence.collector.results_api._run",return_value=(202,)):
            data=list_run_results(PROJECT,202,connect=lambda:db)
        self.assertEqual(data["videos"],[])
        self.assertFalse(data["has_more"])
        self.assertEqual(data["external_calls"],0)

    def test_reject_invalid_project_run_cursor_and_limit_before_query(self):
        cases=[
            lambda: list_runs("INVALID",connect=lambda:DB()),
            lambda: list_runs(PROJECT,limit=51,connect=lambda:DB()),
            lambda: list_runs(PROJECT,before_run_id=-4,connect=lambda:DB()),
            lambda: list_run_results(PROJECT,0,connect=lambda:DB()),
            lambda: list_run_results(PROJECT,202,after="0.1",connect=lambda:DB()),
            lambda: list_run_results(PROJECT,202,after="1.x",connect=lambda:DB()),
            lambda: list_run_results(PROJECT,202,after="1.2.3",connect=lambda:DB()),
        ]
        for case in cases:
            with self.subTest(case=str(case)),self.assertRaises((CollectorError,ValueError)):
                case()

    def test_other_project_run_access_is_not_globally_searchable(self):
        db=DB()
        with patch("intelligence.collector.results_api._run",
                   side_effect=CollectorError("run_not_found","not found",404)):
            for func in (list_run_results,get_run_trace):
                with self.subTest(name=func.__name__),self.assertRaises(CollectorError):
                    func(FOREIGN,202,connect=lambda:db)
        self.assertEqual(db.queries,[])

    def test_trace_uses_separate_sql_sums_no_multiplicative_event_join(self):
        db=DB()
        with patch("intelligence.collector.results_api._run",return_value=(202,)):
            result=get_run_trace(PROJECT,202,connect=lambda:db)
        self.assertEqual(result["lineage"]["manifest_id"],MANIFEST)
        self.assertEqual(result["lineage"]["qv_status"],"published")
        self.assertEqual(result["lineage"]["generation_id"],GEN)
        self.assertEqual(result["summary"]["d1_saved_videos"],2)
        self.assertEqual(result["summary"]["sre_confirmed_delivered"],1)
        self.assertFalse(result["sre_relevance_verified"])
        self.assertFalse(result["lineage"]["c9_sp_snapshot_verified_here"])
        self.assertEqual(result["tasks"][0]["candidate_ids"],[QUERY])
        self.assertEqual(result["tasks"][0]["hypothesis_ids"],["hyp_demo"])
        queries=str([sql for sql,_ in db.queries])
        self.assertIn("COALESCE(SUM(ip.saved_count),0)",queries)
        self.assertNotIn("LEFT JOIN collector_sre_outbox e",queries)

    def test_trace_without_tasks_shows_empty_not_fake_success(self):
        db=DB();db.tasks=[];db.qv=[]
        with patch("intelligence.collector.results_api._run",return_value=(202,)):
            result=get_run_trace(PROJECT,202,connect=lambda:db)
        self.assertEqual(result["tasks"],[])
        self.assertEqual(result["summary"]["d1_persisted_pages"],0)
        self.assertEqual(result["summary"]["sre_confirmed_delivered"],0)

    def test_http_reads_are_get_only_and_route_scoped(self):
        with patch("intelligence.collector.results_api.list_runs",
                   return_value={"runs":[],"has_more":False}) as f:
            h=Handler()
            self.assertTrue(http_api.handle_get(h,urlsplit(
                "/api/intelligence/collector/runs?project_id="+PROJECT)))
            self.assertEqual(h.response[0],200)
            f.assert_called_once_with(PROJECT,limit=20,before_run_id=None)
        with patch("intelligence.collector.results_api.list_run_results",
                   return_value={"videos":[]}) as f:
            h=Handler()
            self.assertTrue(http_api.handle_get(h,urlsplit(
                "/api/intelligence/collector/runs/202/results?project_id="+PROJECT+
                "&limit=5&after=700.100")))
            self.assertEqual(h.response[0],200)
            f.assert_called_once_with(PROJECT,202,limit=5,after="700.100")
        with patch("intelligence.collector.results_api.get_run_trace",
                   return_value={"tasks":[]}) as f:
            h=Handler()
            self.assertTrue(http_api.handle_get(h,urlsplit(
                "/api/intelligence/collector/runs/202/trace?project_id="+PROJECT)))
            self.assertEqual(h.response[0],200)
            f.assert_called_once_with(PROJECT,202)

    def test_http_rejects_unknown_params_repeated_project_and_wrong_method(self):
        urls=[
            "/api/intelligence/collector/runs?project_id="+PROJECT+"&debug=1",
            "/api/intelligence/collector/runs?project_id="+PROJECT+
            "&project_id="+FOREIGN,
            "/api/intelligence/collector/runs/202/results?project_id="+PROJECT+
            "&limit=999",
            "/api/intelligence/collector/runs/202/trace?project_id="+PROJECT+
            "&provider_key=secret",
        ]
        for uri in urls:
            with self.subTest(uri=uri):
                h=Handler()
                self.assertTrue(http_api.handle_get(h,urlsplit(uri)))
                self.assertIn(h.response[0],(400,404,422))
        with patch("intelligence.collector.results_api.list_run_results",
                   side_effect=CollectorError("run_not_found","not found",404)):
            h=Handler()
            self.assertTrue(http_api.handle_get(h,urlsplit(
                "/api/intelligence/collector/runs/202/results?project_id="+FOREIGN)))
            self.assertEqual(h.response[0],404)
        # The host already denies nonauthorized origin before handle_get.

    def test_ui_viewing_does_not_expose_provider_or_write_raw_json(self):
        root=Path(__file__).resolve().parents[1]
        script=(root/"site/collector.js").read_text(encoding="utf-8")
        html=(root/"site/index.html").read_text(encoding="utf-8")
        for tag in ("collectorV1LoadRuns","collectorV1Runs","collectorV1LoadTrace",
                    "collectorV1Trace","collectorV1Videos","collectorV1NextResults",
                    "collectorV1MoreRuns"):
            self.assertEqual(html.count('id="'+tag+'"'),1)
            self.assertIn(tag,script)
        self.assertIn("textContent",script)
        self.assertIn("noopener noreferrer",script)
        self.assertIn("collectorV1LoadRuns').addEventListener('click'",script)
        self.assertNotIn("innerHTML",script)
        self.assertNotIn("setInterval(",script)
        self.assertNotIn("setTimeout(",script)
        self.assertNotIn("/api/tools/dtk/run",script)

if __name__=="__main__":
    import unittest
    unittest.main()
