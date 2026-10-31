"""Automated source-scoped 100-video Group C finalization without any QDP."""
import unittest
from contextlib import nullcontext
from unittest.mock import patch
from intelligence.market_intelligence.batch import finalize_once
from intelligence.market_intelligence.contracts import MarketError

class _Result:
    def __init__(self,allrows=None,one=None):
        self.rows=allrows
        self.one=one
    def fetchall(self):
        return self.rows or []
    def fetchone(self):
        return self.one

class _Conn:
    def __init__(self,active=False):
        self.active=active
        self.updates=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def transaction(self):return nullcontext()
    def execute(self,sql,args):
        if "SELECT id,payload FROM jobs" in sql:
            return _Result(allrows=[(5,{"project_id":"a"*32,
                "videos":[{"video_id":1},{"video_id":2}]})])
        if "SELECT 1 FROM jobs" in sql:
            return _Result(one=(1,) if self.active else None)
        if "UPDATE jobs SET" in sql:
            self.updates.append((sql,args))
            return _Result(one=(5,))
        raise AssertionError(sql)

class AutoCFinalizeTests(unittest.TestCase):
    def test_atomic_no_provider_or_model_required_for_native_c(self):
        c=_Conn()
        done={"videos":[{"state":"analysis_partial"},{"state":"analysis_partial"}]}
        snapshot={"snapshot_id":"batch-snapshot-uuid",
                  "payload_sha256":"f"*64,
                  "coverage":{"c0_eligible_exact_videos":2}}
        with patch("intelligence.market_intelligence.batch.d1.connect",
                   return_value=c),patch(
                       "intelligence.market_intelligence.batch.status",
                       return_value=done),patch(
                       "intelligence.market_intelligence.service.MarketIntelligenceService.freeze",
                       return_value=snapshot) as freeze:
            self.assertEqual(finalize_once(),1)
        self.assertEqual(freeze.call_args.kwargs["video_ids"],[1,2])
        self.assertIs(freeze.call_args.kwargs["require_all_exact"],True)
        self.assertEqual(len(c.updates),1)
        self.assertIn("batch-snapshot-uuid",c.updates[0][1][0])

    def test_no_finalization_while_c0_incomplete(self):
        c=_Conn()
        done={"videos":[{"state":"analysis_partial"},{"state":"analysis_partial"}]}
        with patch("intelligence.market_intelligence.batch.d1.connect",
                   return_value=c),patch(
                       "intelligence.market_intelligence.batch.status",
                       return_value=done),patch(
                       "intelligence.market_intelligence.service.MarketIntelligenceService.freeze",
                       side_effect=MarketError("c0_metrics_pending","Missing",409)):
            self.assertEqual(finalize_once(),0)
        self.assertFalse(c.updates)

    def test_no_finalization_if_metric_worker_active(self):
        c=_Conn(active=True)
        done={"videos":[{"state":"analysis_partial"},{"state":"analysis_partial"}]}
        with patch("intelligence.market_intelligence.batch.d1.connect",
                   return_value=c),patch(
                       "intelligence.market_intelligence.batch.status",
                       return_value=done),patch(
                       "intelligence.market_intelligence.service.MarketIntelligenceService.freeze") as freeze:
            self.assertEqual(finalize_once(),0)
            freeze.assert_not_called()

    def test_no_finalization_if_a_or_b_missing(self):
        c=_Conn()
        data={"videos":[{"state":"analysis_partial"},{"state":"media_awaiting_analysis"}]}
        with patch("intelligence.market_intelligence.batch.d1.connect",
                   return_value=c),patch(
                       "intelligence.market_intelligence.batch.status",
                       return_value=data),patch(
                       "intelligence.market_intelligence.service.MarketIntelligenceService.freeze") as freeze:
            self.assertEqual(finalize_once(),0)
            freeze.assert_not_called()

if __name__=="__main__":
    unittest.main()
