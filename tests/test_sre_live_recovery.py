"""M15 pure unit and fake-DB contract tests, no provider, database or money."""
from __future__ import annotations

import subprocess
import sys
import unittest
from unittest.mock import patch

from intelligence.relevance.v1_contracts import SREContractError,fingerprint
from intelligence.relevance.live_recovery import (
    cancel_live_run,pause_live_run,restart_live_run,recover_expired_live_leases,
)

PID="a"*32
RID="11111111-1111-4111-8111-111111111111"
PUB="22222222-2222-4222-8222-222222222222"


class Result:
    def __init__(self,value):self.value=value
    def fetchone(self):return self.value
    def fetchall(self):return self.value


class FakeDB:
    def __init__(self,state="queued",attempts=(),stale=()):
        self.calls=[]
        self.run_state=state
        self.cancel=False
        self.attempts=list(attempts)
        self.stale=list(stale)
        self.item_state="queued"
        self.batch_state="queued"
        self.unsafe=0
        self.unsafe_batches=0
        self.blocked_pairs=0
        self.manifest={
            "schema_version":"sre-live-plan/v1",
            "project_id":PID,"execution_mode":"live",
            "publication_id":PUB,"pipeline_config_id":12,
            "selected_content_versions":[21],
        }
        self.sha=fingerprint(self.manifest)

    def transaction(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):return False

    def execute(self,sql,params=None):
        self.calls.append((sql,params))
        if "SELECT 1 FROM sre_workspaces" in sql:
            return Result((1,) if params[0]==PID else None)
        if "SELECT state,cancel_requested,manifest" in sql:
            return Result((self.run_state,self.cancel,self.manifest,self.sha,
                           PUB,12,"live") if params[0]==PID else None)
        if "SELECT item_id,state FROM sre_live_attempt_fences" in sql:
            return Result(self.attempts)
        if "SELECT id FROM sre_live_items" in sql and "state='unknown'" in sql:
            return Result([])
        if "SELECT count(*) FROM sre_live_items" in sql:
            return Result((self.unsafe,))
        if "SELECT count(*) FROM sre_live_batches" in sql:
            return Result((self.unsafe_batches,))
        if "SELECT count(*) FROM sre_live_expected_pairs" in sql:
            return Result((self.blocked_pairs,))
        if "SELECT id FROM sre_live_items" in sql and "lease_until<=clock_timestamp()" in sql:
            return Result([(x,) for x in self.stale])
        if "UPDATE sre_live_attempt_fences" in sql and "abandoned_before_dispatch" in sql:
            self.attempts=[(item,"abandoned_before_dispatch" if st=="reserved" else st)
                           for item,st in self.attempts]
            return Result(None)
        if "UPDATE sre_live_attempt_fences" in sql and "SET state='unknown'" in sql:
            self.attempts=[(item,"unknown" if st=="dispatch_intent" else st)
                           for item,st in self.attempts]
            return Result(None)
        if "UPDATE sre_live_items" in sql:
            if "state='not_evaluated'" in sql:self.item_state="not_evaluated"
            elif "state='queued'" in sql:self.item_state="queued"
            elif "state=CASE" in sql:
                self.item_state="unknown" if params[0] else params[1]
            return Result(None)
        if "UPDATE sre_live_batches" in sql:
            if "state='queued'" in sql:self.batch_state="queued"
            elif "state=CASE" in sql:self.batch_state="unknown" if params[0] else params[1]
            return Result(None)
        if "UPDATE sre_live_runs" in sql:
            if "state='pending_consent'" in sql:
                self.run_state="pending_consent";self.cancel=False
            else:
                self.run_state=params[0];self.cancel=params[1]
            return Result(None)
        raise AssertionError("unexpected SQL "+sql)


class M15Recovery(unittest.TestCase):
    def test_cancel_pre_dispatch_terminal_and_idempotent(self):
        db=FakeDB(attempts=[(7,"reserved")])
        result=cancel_live_run(db,PID,RID)
        self.assertEqual((result["status"],result["unknown_items"]),
                         ("cancelled",0))
        self.assertFalse(result["automatic_retry_allowed"])
        self.assertFalse(result["dispatch_allowed"])
        self.assertEqual(db.run_state,"cancelled")
        self.assertEqual(db.item_state,"cancelled")
        self.assertEqual(db.batch_state,"cancelled")
        self.assertEqual(db.attempts,[(7,"abandoned_before_dispatch")])
        self.assertTrue(cancel_live_run(db,PID,RID)["idempotent"])
        with self.assertRaises(SREContractError):
            restart_live_run(db,PID,RID)
        self.assertEqual(db.calls[0][1],(PID,))

    def test_cancel_after_intent_is_unknown_not_safe_cancelled(self):
        db=FakeDB(state="running",attempts=[(7,"dispatch_intent")])
        r=cancel_live_run(db,PID,RID)
        self.assertEqual(r["status"],"unknown")
        self.assertEqual(r["unknown_items"],1)
        self.assertTrue(db.cancel)
        self.assertEqual(db.item_state,"unknown")
        self.assertEqual(db.batch_state,"unknown")
        self.assertEqual(db.attempts,[(7,"unknown")])
        self.assertFalse(r["automatic_retry_allowed"])
        self.assertTrue(cancel_live_run(db,PID,RID)["idempotent"])
        with self.assertRaises(SREContractError):
            restart_live_run(db,PID,RID)

    def test_pause_pre_send_and_restart_requires_snapshot_and_new_consent(self):
        db=FakeDB(state="running",attempts=[(7,"reserved")])
        paused=pause_live_run(db,PID,RID,reason="paused_rights")
        self.assertEqual(paused["status"],"paused_rights")
        self.assertEqual(db.item_state,"paused_rights")
        self.assertEqual(db.attempts,[(7,"abandoned_before_dispatch")])
        with patch("intelligence.relevance.live_recovery._verified_inputs",
                   return_value=(db.manifest,db.sha)) as verified:
            resumed=restart_live_run(db,PID,RID)
        verified.assert_called_once_with(db,PID,PUB,[21],12)
        self.assertEqual(resumed["status"],"pending_consent")
        self.assertEqual(db.item_state,"not_evaluated")
        self.assertEqual(db.batch_state,"queued")
        self.assertFalse(resumed["dispatch_allowed"])
        self.assertFalse(resumed["automatic_retry_allowed"])

    def test_restart_rejects_unknown_paid_attempt_or_changed_snapshot(self):
        db=FakeDB(state="paused_rights",attempts=[(7,"unknown")])
        with self.assertRaises(SREContractError) as e:
            restart_live_run(db,PID,RID)
        self.assertEqual(e.exception.code,"ambiguous_charge")
        db=FakeDB(state="paused_budget")
        with patch("intelligence.relevance.live_recovery._verified_inputs",
                   return_value=(None,"d"*64)):
            with self.assertRaises(SREContractError) as e:
                restart_live_run(db,PID,RID)
            self.assertEqual(e.exception.code,"snapshot_mismatch")
        self.assertEqual(db.run_state,"paused_budget")
        for unsafe,blocked in ((1,0),(0,1)):
            db=FakeDB(state="paused_rights")
            db.unsafe=unsafe
            db.blocked_pairs=blocked
            with patch("intelligence.relevance.live_recovery._verified_inputs",
                       return_value=(db.manifest,db.sha)):
                with self.assertRaises(SREContractError):
                    restart_live_run(db,PID,RID)

    def test_crash_safe_reserved_requeues_and_intent_fails_stop(self):
        db=FakeDB(state="running",attempts=[(9,"reserved")],stale=[9])
        r=recover_expired_live_leases(db,PID,RID)
        self.assertEqual((r["recovered_items"],r["unknown_items"]),(1,0))
        self.assertEqual(db.item_state,"queued")
        self.assertEqual(db.attempts,[(9,"abandoned_before_dispatch")])
        self.assertFalse(r["automatic_retry_allowed"])  # no financial permission
        db=FakeDB(state="running",attempts=[(9,"dispatch_intent")],stale=[9])
        r=recover_expired_live_leases(db,PID,RID)
        self.assertEqual(r["status"],"unknown")
        self.assertEqual(r["recovered_items"],0)
        self.assertTrue(db.cancel)
        self.assertEqual(db.attempts,[(9,"unknown")])
        self.assertFalse(r["automatic_retry_allowed"])
        db=FakeDB(state="running",stale=[])
        self.assertTrue(recover_expired_live_leases(db,PID,RID)["idempotent"])

    def test_reject_wrong_project_run_terminal_pause_reason(self):
        for method in (cancel_live_run,pause_live_run,restart_live_run,
                       recover_expired_live_leases):
            with self.subTest(method=method.__name__):
                with self.assertRaises(SREContractError):
                    method(FakeDB(),"b"*32,RID)
                with self.assertRaises(SREContractError):
                    method(FakeDB(),PID,"not-uuid")
        for reason in ("cancelled","completed",True,0,{},None):
            with self.subTest(reason=str(reason)),self.assertRaises(SREContractError):
                pause_live_run(FakeDB(),PID,RID,reason=reason)
        for state in ("cancelled","completed","partial","failed","unknown"):
            with self.subTest(state=state),self.assertRaises(SREContractError):
                pause_live_run(FakeDB(state=state),PID,RID)
        with self.assertRaises(SREContractError):
            restart_live_run(FakeDB(state="running"),PID,RID)

    def test_no_provider_db_import_and_consistent_order(self):
        snippet=(
            "import sys;import intelligence.relevance.live_recovery;"
            "assert 'psycopg' not in sys.modules;"
            "assert 'openai' not in sys.modules;"
            "assert 'openrouter' not in sys.modules;"
        )
        p=subprocess.run([sys.executable,"-c",snippet],
                         capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stderr)
        db=FakeDB()
        cancel_live_run(db,PID,RID)
        texts=[s for s,_ in db.calls]
        a=next(i for i,s in enumerate(texts) if "FOR UPDATE" in s and "sre_workspaces" in s)
        b=next(i for i,s in enumerate(texts) if "sre_live_runs" in s and "FOR UPDATE" in s)
        c=next(i for i,s in enumerate(texts) if "sre_live_attempt_fences" in s
               and "FOR UPDATE" in s)
        self.assertTrue(a<b<c)


if __name__=="__main__":
    unittest.main()
