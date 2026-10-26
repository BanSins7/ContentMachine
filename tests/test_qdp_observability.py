"""FIX-17 bounded observability, local scope and honest metrics semantics."""
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from intelligence.qualified_data.contracts import QDPError
from intelligence.qualified_data.observability import _check_limit,snapshot
from intelligence.qualified_data.worker import stop_worker
from intelligence.qualified_data import http_api as api
from tests.test_qdp_studio import Handler,PID,ROOT,RID


class QDPObservabilityContracts(unittest.TestCase):
    def test_limit_bounds(self):
        for val in (0,51,-1,True,1.4,None,"10"):
            with self.subTest(value=val),self.assertRaises(QDPError):
                _check_limit(val)
        self.assertEqual(_check_limit(50),50)

    def test_wrong_project_fails_before_db(self):
        with self.assertRaises(QDPError):
            snapshot("not-a-project",connect=lambda:None)

    def test_malformed_run_fails_before_db(self):
        with self.assertRaises(QDPError):
            snapshot(PID,run_id="not-a-uuid",connect=lambda:None)

    def test_observability_http_is_read_only_and_scoped(self):
        with patch.object(api,"_reply") as reply,patch(
                "intelligence.qualified_data.observability.snapshot",
                return_value={"ok":True,"analysis_completed":None}) as reader:
            h=Handler()
            api.handle_get(h,urlsplit(ROOT+"/observability?limit=10&run_id="+RID))
            reader.assert_called_once_with(PID,run_id=RID,limit=10)
            reply.assert_called_once()
        for path in ("/observability?limit=0","/observability?limit=51",
                     "/observability?limit=5&limit=6",
                     "/observability?recipient=other"):
            h=Handler()
            api.handle_get(h,urlsplit(ROOT+path))
            self.assertEqual(h.responses[0][0],422)
        h=Handler()
        api.handle_post(h,ROOT+"/observability")
        self.assertEqual(h.responses[0][0],404)

    def test_observability_is_not_a_completed_analysis_api(self):
        source=(Path(__file__).resolve().parents[1]/
                "intelligence/qualified_data/observability.py").read_text()
        self.assertIn('"analysis_completed":None',source)
        self.assertIn('"paid_calls":0',source)
        self.assertNotIn("requests.get",source)
        self.assertNotIn("openrouter",source.lower())

    def test_shutdown_timeout_is_bounded(self):
        for val in (-1,31,True,None,"5"):
            with self.subTest(val=val),self.assertRaises(ValueError):
                stop_worker(timeout_seconds=val)

    def test_benchmark_rejects_production_or_wrong_scale_before_db(self):
        from scripts.qdp_benchmark_pg import run
        with patch.dict(os.environ,{"QDP_DISPOSABLE_TEST_DB":"NO"}):
            with self.assertRaises(RuntimeError):
                run("nonexistent")
        with patch.dict(os.environ,{"QDP_DISPOSABLE_TEST_DB":"YES"}):
            with self.assertRaises(ValueError):
                run("nonexistent",videos=9999)

    def test_048_forward_only_indexes_are_present(self):
        path=Path(__file__).resolve().parents[1]/"intelligence/migrations/048_qdp_operations_indexes.sql"
        ddl=path.read_text()
        for idx in ("idx_qdp_17_project_run_video","idx_qdp_17_run_events_page",
                    "idx_qdp_17_delivery_consumer_due","idx_qdp_17_invalidation_run"):
            self.assertIn(idx,ddl)
        self.assertNotIn("DROP TABLE",ddl.upper())
        self.assertNotIn("ALTER TABLE",ddl.upper())


if __name__=="__main__":
    unittest.main()
