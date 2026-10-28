"""VC-MOD-10: HTTP run status counts come only from persisted D1 pages."""
from __future__ import annotations

from datetime import datetime,timezone
from unittest import TestCase
from unittest.mock import patch
from intelligence.collector.repository import get_run

PID="a"*32
STAMP=datetime(2026,10,26,tzinfo=timezone.utc)


class Cursor:
    def __init__(self,one=None,many=None):
        self.one=one
        self.many=many
    def fetchone(self):return self.one
    def fetchall(self):return self.many or []


class DB:
    def __init__(self,state="completed",task="completed",pages=1,outbox=None):
        self.state=state;self.task=task;self.pages=pages
        self.outbox=3*pages if outbox is None else outbox
        self.queries=[]
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,sql,params=None):
        self.queries.append((sql,params))
        if "FROM collector_execution_grants" in sql:
            return Cursor((1,0,1))
        if "FROM collector_provider_tasks t" in sql:
            return Cursor(many=[(31,self.task,"12345678-1234-4234-8234-123456789abc",
                                "c"*64,"completed" if self.task=="completed" else "queued",
                                "succeeded" if self.task=="completed" else "started",61)])
        if "COALESCE(SUM(saved_count),0)" in sql:
            return Cursor((self.pages,3*self.pages,2*self.pages,self.pages))
        if "FROM collector_sre_outbox e" in sql:
            return Cursor((self.outbox,))
        raise AssertionError("Unexpected SQL")


class RunStatusTests(TestCase):
    def read(self,db):
        row=(77,db.state,False,STAMP,STAMP if db.state=="completed" else None,
             "completed" if db.state=="completed" else "released",
             "11111111-1111-4111-8111-111111111111","f"*64,{},11)
        with patch("intelligence.collector.repository._run",return_value=row):
            return get_run(PID,77,connect=lambda:db)

    def test_completed_run_d1_metrics_come_from_real_pages(self):
        db=DB()
        value=self.read(db)
        self.assertEqual(value["d1_persisted_pages"],1)
        self.assertEqual(value["d1_saved_videos"],3)
        self.assertEqual(value["d1_new_videos"],2)
        self.assertEqual(value["d1_duplicate_videos"],1)
        self.assertEqual(value["sre_ready_events"],3)
        self.assertEqual(value["ingest_pending_tasks"],0)
        self.assertTrue(value["d1_completion_verified"])
        self.assertFalse(value["d1_integrity_warning"])
        self.assertTrue(all(PID in params for sql,params in db.queries
                            if "collector_ingest_pages" in sql or
                            "collector_sre_outbox" in sql))

    def test_completed_run_with_missing_sre_outbox_is_integrity_warning(self):
        db=DB(state="completed",task="completed",pages=1,outbox=1)
        value=self.read(db)
        self.assertEqual(value["d1_saved_videos"],3)
        self.assertEqual(value["sre_ready_events"],1)
        self.assertFalse(value["d1_completion_verified"])
        self.assertTrue(value["d1_integrity_warning"])

    def test_provider_submitted_without_d1_receipt_reports_pending_not_complete(self):
        db=DB(state="running",task="submitted",pages=0,outbox=0)
        value=self.read(db)
        self.assertEqual(value["d1_persisted_pages"],0)
        self.assertEqual(value["d1_saved_videos"],0)
        self.assertEqual(value["ingest_pending_tasks"],1)
        self.assertFalse(value["d1_completion_verified"])
        self.assertFalse(value["d1_integrity_warning"])

    def test_legacy_false_completed_state_is_flagged_not_trusted(self):
        db=DB(state="completed",task="completed",pages=0,outbox=0)
        value=self.read(db)
        self.assertTrue(value["d1_integrity_warning"])
        self.assertFalse(value["d1_completion_verified"])
        self.assertEqual(value["d1_saved_videos"],0)

    def test_other_project_cannot_observe_run(self):
        from intelligence.collector.contracts import CollectorError
        with patch("intelligence.collector.repository._run",
                   side_effect=CollectorError("run_not_found","not found",404)):
            with self.assertRaises(CollectorError):
                get_run("b"*32,77,connect=lambda:DB())

if __name__=="__main__":
    import unittest
    unittest.main()
