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
