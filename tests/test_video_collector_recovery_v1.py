"""VC-MOD-13: crash, cancellation, quota and retention boundaries.

Offline deterministic DB transaction fakes. PG17 locking/privacy tests remain
unverified; no provider calls, no arbitrary deletes or reassignment of quota.
"""
from __future__ import annotations

from datetime import datetime,timedelta,timezone
from unittest import TestCase
from unittest.mock import patch

from intelligence.collector.contracts import CollectorError
from intelligence.collector import recovery,worker,execution
from intelligence.collector.ingest import _retention_deadline
from test_video_collector_ingest_v1 import FakeDB,worker_context,inspect

PID="a"*32
OTHER="b"*32
TS=datetime(2026,10,26,12,0,tzinfo=timezone.utc)


class Cursor:
    def __init__(self,row=None,rows=None):
        self.row=row;self.rows=rows if rows is not None else []
    def fetchone(self):return self.row
    def fetchall(self):return list(self.rows)


class Store:
    def __init__(self):
        self.queries=[]
        self.cancelled=True
        self.stage="cancelling"
        self.active_lease=False
        self.task_state="submitted"
        self.job_state="queued"
        self.provider_task_id="12345678-1234-4234-8234-123456789abc"
        self.reconciled=0
        self.fail=None
        self.metadata={100:{"expires_at":TS-timedelta(days=1),
                            "deleted_at":None},
                       101:{"expires_at":None,"deleted_at":None},
                       102:{"expires_at":TS+timedelta(days=1),"deleted_at":None}}
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def transaction(self):
        class T:
            def __init__(self,store):self.db=store
            def __enter__(self):
                self.before=(self.db.reconciled,self.db.task_state,self.db.job_state)
                return self.db
            def __exit__(self,typ,*args):
                if typ:
                    self.db.reconciled,self.db.task_state,self.db.job_state=self.before
                return False
        return T(self)
    def execute(self,sql,params=None):
        self.queries.append((sql,params))
        if self.fail and self.fail in sql:
            raise RuntimeError("injected SQL failure")
        if "SELECT t.id,t.run_id,t.job_id,t.source_request_id" in sql:
            if params[0]!=PID or not self.cancelled or self.task_state=="unknown":
                return Cursor(rows=[])
            return Cursor(rows=[(31,77,91,61)])
        if "SELECT cancel_requested FROM search_runs" in sql:
            return Cursor((self.cancelled,))
        if "SELECT project_id,stage FROM collector_runs" in sql:
            return Cursor((PID,self.stage))
        if "SELECT t.state,t.provider_task_id,j.state,j.lease_until" in sql:
            return Cursor((self.task_state,self.provider_task_id,self.job_state,TS))
        if "SELECT 1 FROM jobs WHERE" in sql:
            return Cursor((1,) if self.active_lease else None)
        if "UPDATE collector_provider_tasks SET state='unknown'" in sql:
            self.task_state="unknown";self.reconciled+=1;return Cursor((31,))
        if "UPDATE source_requests SET state='unknown'" in sql:return Cursor((61,))
        if "UPDATE jobs SET state='failed'" in sql:
            self.job_state="failed";return Cursor((91,))
        if "SELECT DISTINCT m.id,m.video_id,m.expires_at,m.deleted_at" in sql:
            if params[0]!=PID:return Cursor(rows=[])
            return Cursor(rows=[(100,777,self.metadata[100]["expires_at"],
                                 self.metadata[100]["deleted_at"]),
                                (101,778,None,None)][:params[1]])
        if "SELECT m.id FROM source_video_metadata m" in sql:
            if params[0]!=PID:return Cursor(rows=[])
            return Cursor(rows=[(100,)][:params[1]]
                          if self.metadata[100]["deleted_at"] is None else [])
        if "UPDATE source_video_metadata" in sql:
            if params[0]!=100 or self.metadata[100]["deleted_at"] is not None:
                return Cursor()
            self.metadata[100]["deleted_at"]=TS;return Cursor((100,))
        raise AssertionError("Unexpected SQL "+sql[:150])


class RecoveryTests(TestCase):
    def test_cancelled_recovery_dry_run_no_db_writes_or_provider_calls(self):
        db=Store()
        result=recovery.reconcile_cancelled(PID,connect=lambda:db)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["candidates"],1)
        self.assertEqual(result["reconciled"],0)
        self.assertEqual(result["provider_calls"],0)
        self.assertEqual(result["refunded_consumed_requests"],0)
        self.assertFalse(any("UPDATE " in sql for sql,_ in db.queries))

    def test_cancelled_submitted_moved_unknown_without_provider_poll(self):
        db=Store()
        with patch("intelligence.collector.worker._finalize") as final,patch(
             "intelligence.connectors.dtk.submit",side_effect=AssertionError),patch(
             "intelligence.connectors.dtk.task",side_effect=AssertionError):
            result=recovery.reconcile_cancelled(PID,dry_run=False,connect=lambda:db)
        self.assertEqual(result["reconciled"],1)
        self.assertEqual(db.task_state,"unknown")
        self.assertEqual(db.job_state,"failed")
        self.assertEqual(result["refunded_consumed_requests"],0)
        final.assert_called_once_with(db,77)
        self.assertEqual(recovery.reconcile_cancelled(
            PID,dry_run=False,connect=lambda:db)["reconciled"],0)

    def test_cancelled_alive_lease_never_stolen(self):
        db=Store();db.active_lease=True
        with patch("intelligence.collector.worker._finalize") as final:
            result=recovery.reconcile_cancelled(PID,dry_run=False,connect=lambda:db)
        self.assertEqual(result["reconciled"],0)
        self.assertEqual(db.task_state,"submitted")
        final.assert_not_called()

    def test_cancelled_false_positive_after_lock_is_skipped(self):
        db=Store();db.cancelled=False
        result=recovery.reconcile_cancelled(PID,dry_run=False,connect=lambda:db)
        self.assertEqual(result["reconciled"],0)
        self.assertEqual(result["candidates"],0)

    def test_cancelled_project_isolation(self):
        db=Store()
        result=recovery.reconcile_cancelled(OTHER,dry_run=False,connect=lambda:db)
        self.assertEqual(result["reconciled"],0)
        self.assertFalse(any("UPDATE " in q for q,_ in db.queries))

    def test_cancelled_crash_rolls_back_task_and_job(self):
        db=Store();db.fail="UPDATE source_requests SET state='unknown'"
        with self.assertRaises(RuntimeError):
            recovery.reconcile_cancelled(PID,dry_run=False,connect=lambda:db)
        self.assertEqual(db.task_state,"submitted")
        self.assertEqual(db.job_state,"queued")
        self.assertEqual(db.reconciled,0)

    def test_retention_effective_earliest_reviewed_source_policy(self):
        db=FakeDB();db.retention_days=30;db.retention_policy_days=14
        self.assertEqual(_retention_deadline(db,12,TS),TS+timedelta(days=14))
        db.retention_days=7
        self.assertEqual(_retention_deadline(db,12,TS),TS+timedelta(days=7))

    def test_expired_retention_blocks_old_provider_page_before_d1_insert(self):
        db=FakeDB()
        db.database_now=TS+timedelta(days=16)
        with self.assertRaises(CollectorError) as e:
            _retention_deadline(db,12,TS)
        self.assertEqual(e.exception.code,"ingest_retention_expired")

    def test_retention_missing_invalid_or_excessive_blocks_ingest(self):
        for policy in (0,-1,True,None,"30",36501):
            with self.subTest(policy=policy):
                db=FakeDB();db.retention_policy_days=policy
                with self.assertRaises(CollectorError):
                    _retention_deadline(db,12,TS)

    def test_retention_audit_finds_due_and_legacy_without_deleting(self):
        db=Store()
        result=recovery.retention_audit(PID,limit=1,connect=lambda:db)
        self.assertTrue(result["read_only"])
        self.assertEqual(result["metadata"][0]["metadata_id"],100)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["deleted_audit_rows"],0)
        all_rows=recovery.retention_audit(PID,limit=5,connect=lambda:db)
        self.assertEqual(all_rows["metadata"][1]["action_required"],
                         "verify_legacy_retention_policy")
        self.assertFalse(any("DELETE" in sql for sql,_ in db.queries))

    def test_scrub_must_be_explicit_and_preserve_audit_ids(self):
        db=Store()
        with self.assertRaises(CollectorError):
            recovery.scrub_expired_metadata(PID,connect=lambda:db)
        result=recovery.scrub_expired_metadata(
            PID,confirm=True,connect=lambda:db)
        self.assertEqual(result["metadata_ids"],[100])
        self.assertEqual(result["scrubbed_count"],1)
        self.assertEqual(result["deleted_audit_rows"],0)
        self.assertEqual(result["provider_calls"],0)
        self.assertIsNotNone(db.metadata[100]["deleted_at"])
        self.assertIsNone(db.metadata[101]["deleted_at"])
        self.assertIsNone(db.metadata[102]["deleted_at"])
        self.assertEqual(recovery.scrub_expired_metadata(
            PID,confirm=True,connect=lambda:db)["scrubbed_count"],0)
        self.assertFalse(any("DELETE FROM" in q for q,_ in db.queries))

    def test_project_and_limit_guards_fail_before_connection(self):
        for method in (recovery.reconcile_cancelled,recovery.retention_audit,
                       recovery.scrub_expired_metadata):
            with self.subTest(method=method.__name__):
                with patch("intelligence.collector.recovery._connection",
                           side_effect=AssertionError("unexpected DB")):
                    with self.assertRaises((CollectorError,ValueError)):
                        method("bad-project")
                    with self.assertRaises((CollectorError,ValueError)):
                        method(PID,limit=101)

    def test_worker_never_claims_cancelling_submitted_or_automatically_resubmits(self):
        source=__import__("inspect").getsource(worker._claim)
        self.assertIn("t.state='submitted' AND c.stage='released'",source)
        self.assertIn("r.cancel_requested=FALSE",source)
        self.assertIn("state='not_submitted'",source)
        self.assertIn("pg_advisory_xact_lock",source)
        self.assertIn("reserved_remaining>0",source)
        self.assertIn("attempts=attempts+CASE WHEN %s THEN 1 ELSE 0 END",source)

    def test_quota_release_requires_both_atomic_returning_rows(self):
        source=__import__("inspect").getsource(execution.cancel_released)
        self.assertIn("RETURNING source_id",source)
        self.assertIn("RETURNING run_id",source)
        self.assertIn("quota_release_race",source)
        self.assertIn("unsubmitted>grant[2]",source)

    def test_recovery_never_invents_provider_or_second_page(self):
        source=__import__("inspect").getsource(recovery)
        self.assertNotIn("api.submit(",source)
        self.assertNotIn("api.task(",source)
        self.assertNotIn("INSERT INTO collector_ingest_pages",source)
        self.assertNotIn("DELETE FROM collector_ingest_pages",source)
        self.assertIn("cancelled_submit_uncertain",source)
        self.assertIn("retention_consent_required",source)

if __name__=="__main__":
    import unittest
    unittest.main()
