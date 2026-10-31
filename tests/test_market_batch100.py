"""Marketing hundred-video selection and API smoke tests (offline)."""
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from intelligence.market_intelligence.batch import balanced_selection, MAX_BATCH
from intelligence.market_intelligence.contracts import MarketError
from intelligence.market_intelligence.http_api import handle_get, handle_post

PROJECT = "a" * 32


class FakeHandler:
    def __init__(self, body=None):
        self.responses=[]
        self.headers={"Content-Type":"application/json"}
        if body is not None:
            raw=json.dumps(body).encode("utf-8")
            self.headers["Content-Length"]=str(len(raw))
            self.rfile=io.BytesIO(raw)
    def json_response(self,code,body):
        self.responses.append((code,body))


class Batch100Tests(unittest.TestCase):
    def test_deterministic_balanced_selection_exact_100(self):
        records=[]
        for n in range(1,205):
            platform="tiktok" if n<170 else "youtube"
            creator="viral_one" if n<170 else "other"
            records.append((n,"https://example.test/"+str(n),platform,str(n),creator))
        selected=balanced_selection(records)
        self.assertEqual(len(selected),100)
        self.assertEqual(len({r[0] for r in selected}),100)
        self.assertEqual(sum(r[2]=="youtube" for r in selected),35)
        self.assertEqual(selected,balanced_selection(list(reversed(records))))

    def test_reject_out_of_bounds_without_io(self):
        for amount in (0,101,-1,True,"100",None):
            with self.subTest(amount=amount),self.assertRaises(MarketError):
                balanced_selection([],amount)

    def test_deduplicates_video_ids(self):
        rows=[(1,"url-1","tiktok","id-1","a"),
              (1,"url-1","tiktok","id-1","a"),
              (2,"url-2","youtube","id-2","b")]
        self.assertEqual(len(balanced_selection(rows)),2)

    def test_real_batch_start_returns_persisted_result_and_is_idempotent(self):
        # Exercise the *real* start_batch, not the HTTP service mock.
        # The previous production path inserted a job then raised NameError
        # because 'computed' was never initialized.
        from contextlib import nullcontext
        from intelligence.market_intelligence.batch import start_batch
        class Rows:
            def __init__(self, allrows=None, one=None):
                self.allrows=allrows or []
                self.one=one
            def fetchall(self): return self.allrows
            def fetchone(self): return self.one
        class DB:
            def __init__(self):
                self.job_id=17
                self.existing=False
                self.saved={"market_snapshot_id":"snapshot-17",
                    "market_snapshot_sha256":"a"*64,
                    "group_c_coverage":{"c0_eligible_exact_videos":1}}
                self.inserts=0
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def transaction(self): return nullcontext()
            def execute(self,sql,args):
                if "FROM videos v" in sql: return Rows(allrows=[
                    (1,"https://example.test/1","youtube","id1","author1")])
                if "INSERT INTO jobs" in sql:
                    self.inserts+=1
                    if not self.existing:
                        self.existing=True
                        return Rows(one=(self.job_id,None))
                    return Rows(one=None)
                if "SELECT id,result FROM jobs" in sql:
                    return Rows(one=(self.job_id,self.saved))
                raise AssertionError(sql)
        db=DB()
        with patch("intelligence.market_intelligence.batch._project"), patch(
            "intelligence.market_intelligence.batch.d1.connect",return_value=db), patch(
            "intelligence.market_intelligence.batch.start_worker") as worker:
            first=start_batch(PROJECT,limit=1)
            second=start_batch(PROJECT,limit=1)
        self.assertEqual(first["batch_id"],17)
        self.assertIsNone(first["group_c_snapshot_id"])
        self.assertEqual(first["group_c_state"],"awaiting_exact_metrics_or_media")
        self.assertEqual(second["batch_id"],17)
        self.assertEqual(second["group_c_snapshot_id"],"snapshot-17")
        self.assertEqual(second["group_c_snapshot_sha256"],"a"*64)
        self.assertEqual(second["group_c_coverage"]["c0_eligible_exact_videos"],1)
        self.assertEqual(second["group_c_state"],"frozen_diagnostic")
        self.assertEqual(db.inserts,2)
        self.assertEqual(worker.call_count,2)

    def test_batch_status_exposes_persisted_group_c_snapshot(self):
        from intelligence.market_intelligence.batch import status
        class Rows:
            def __init__(self,one): self.one=one
            def fetchone(self): return self.one
        class DB:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def execute(self,sql,args):
                if "SELECT id,payload,result FROM jobs" in sql:
                    return Rows((17,{"requested":1,"videos":[{
                        "video_id":1,"url":"https://example.test/1",
                        "platform":"youtube","native_id":"id1"}]},{
                        "market_snapshot_id":"snapshot-17",
                        "market_snapshot_sha256":"b"*64,
                        "group_c_coverage":{"c0_eligible_exact_videos":1}}))
                if "FROM mi_video_analyses a" in sql: return Rows(None)
                raise AssertionError(sql)
        with patch("intelligence.market_intelligence.batch._project"),patch(
            "intelligence.market_intelligence.batch.d1.connect",return_value=DB()),patch(
            "intelligence.market_intelligence.batch._source_for_video",return_value=None):
            result=status(PROJECT)
        self.assertEqual(result["batch_id"],17)
        self.assertEqual(result["group_c_snapshot_id"],"snapshot-17")
        self.assertEqual(result["group_c_snapshot_sha256"],"b"*64)
        self.assertEqual(result["group_c_state"],"frozen_diagnostic")
        self.assertEqual(result["group_c_coverage"]["c0_eligible_exact_videos"],1)
        self.assertEqual(result["counts"],{"rights_required":1})

    @patch("intelligence.market_intelligence.http_api.MarketIntelligenceService")
    def test_batch_http_start_strict_fields(self,service):
        service.return_value.start_100_video_batch.return_value={
            "ok":True,"batch_id":123,"selected":100}
        handler=FakeHandler({"limit":100})
        self.assertTrue(handle_post(handler,
            f"/api/projects/{PROJECT}/market-intelligence/batch"))
        self.assertEqual(handler.responses[0][0],200)
        service.return_value.start_100_video_batch.assert_called_once_with(PROJECT,100)
        bad=FakeHandler({"approve_everything":True})
        self.assertTrue(handle_post(bad,
            f"/api/projects/{PROJECT}/market-intelligence/batch"))
        self.assertEqual(bad.responses[0][0],422)

    @patch("intelligence.market_intelligence.http_api.MarketIntelligenceService")
    def test_batch_status_readonly(self,service):
        service.return_value.batch_status.return_value={"ok":True,"selected":3}
        handler=FakeHandler()
        url=SimpleNamespace(
            path=f"/api/projects/{PROJECT}/market-intelligence/batch",query="")
        self.assertTrue(handle_get(handler,url))
        self.assertEqual(handler.responses[0],(200,{"ok":True,"selected":3}))


if __name__=="__main__":
    unittest.main()
