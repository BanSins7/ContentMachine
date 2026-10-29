"""M14: pure DB boundary tests for LIVE claim, lease generation and heartbeat.

No PostgreSQL/provider/paid HTTP needed. Real SKIP LOCKED concurrency and
expiry CAS acceptance is exercised by disposable PostgreSQL17 integration.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import subprocess
import sys
import unittest
from unittest.mock import patch

from intelligence.relevance.live_lease import (
    CLAIM_SQL, HEARTBEAT_SQL, RELEASE_SQL, BLOCK_RIGHTS_SQL,
    WORKSPACE_SHARE_SQL,
    MIN_LEASE_SECONDS, MAX_LEASE_SECONDS, MAX_ATTEMPTS,
    claim_live_item, heartbeat_live_item, release_live_item,
)
from intelligence.relevance.v1_contracts import SREContractError

PID="a"*32
RID="11111111-1111-4111-8111-111111111111"
TOKEN="22222222-2222-4222-8222-222222222222"
EXPIRES=datetime(2026,12,1,tzinfo=timezone.utc)


class Result:
    def __init__(self,value): self.value=value
    def fetchone(self): return self.value


class FakeConn:
    def __init__(self,*,claim=True,renew=True,release=True,block=True):
        self.calls=[]
        self.claim=claim
        self.renew=renew
        self.release=release
        self.block=block
        self.tx_calls=0

    def __enter__(self):return self
    def __exit__(self,*args):return False
    def transaction(self):
        self.tx_calls+=1
        return self

    def execute(self,sql,params):
        self.calls.append((sql,params))
        if sql==WORKSPACE_SHARE_SQL:
            return Result((1,))
        if sql==CLAIM_SQL:
            return Result(
                (55,RID,101,200,"d"*64,1,params[2],EXPIRES,1)
                if self.claim else None
            )
        if sql==HEARTBEAT_SQL:
            return Result((EXPIRES+timedelta(minutes=5),) if self.renew else None)
        if sql==RELEASE_SQL:
            return Result((55,) if self.release else None)
        if sql==BLOCK_RIGHTS_SQL:
            return Result((55,) if self.block else None)
        raise AssertionError("unexpected DB command")


def real_gate(*args,**kwargs):
    return {"content":{"project_id":PID, "video_id":101,
                       "content_version_id":200,"content_sha256":"d"*64}}


class LeaseM14Unit(unittest.TestCase):
    def test_claim_rechecks_rights_and_never_authorizes_paid_dispatch(self):
        db=FakeConn()
        with patch(
            "intelligence.relevance.live_content_gate.read_live_content_gate",
            side_effect=real_gate,
        ) as gate:
            claim=claim_live_item(db,PID,lease_seconds=30)
        self.assertEqual(claim["project_id"],PID)
        self.assertEqual(claim["item_id"],55)
        self.assertEqual(claim["run_id"],RID)
        self.assertEqual(claim["lease_generation"],1)
        self.assertEqual(claim["lease_until"],EXPIRES)
        self.assertTrue(claim["leased"])
        self.assertFalse(claim["dispatch_allowed"])
        self.assertEqual(claim["status"],"running")
        self.assertEqual(db.tx_calls,1)
        self.assertEqual(len(db.calls),2)
        self.assertEqual(db.calls[0],(WORKSPACE_SHARE_SQL,(PID,)))
        self.assertEqual(db.calls[1][0],CLAIM_SQL)
        self.assertEqual(db.calls[1][1][0:2],(PID,100))
        gate.assert_called_once()
        self.assertEqual(gate.call_args.kwargs["phase"],"before_external_send")
        self.assertEqual(gate.call_args.kwargs["pins"],{
            "video_id":101,"content_version_id":200,"content_sha256":"d"*64,
        })

    def test_locked_unavailable_returns_none_no_paid_dispatch(self):
        db=FakeConn(claim=False)
        with patch(
            "intelligence.relevance.live_content_gate.read_live_content_gate",
            side_effect=AssertionError("must not query a missing claim"),
        ):
            self.assertIsNone(claim_live_item(db,PID))
        self.assertEqual(len(db.calls),2)

    def test_revoked_claim_drops_worker_token_and_pauses_rights(self):
        db=FakeConn()
        with patch(
            "intelligence.relevance.live_content_gate.read_live_content_gate",
            side_effect=SREContractError("rights_denied","source","revoked"),
        ):
            result=claim_live_item(db,PID)
        self.assertEqual(result["status"],"paused_rights")
        self.assertEqual(result["reason_code"],"rights_denied")
        self.assertFalse(result["leased"])
        self.assertFalse(result["dispatch_allowed"])
        self.assertNotIn("worker_token",result)
        self.assertEqual([x[0] for x in db.calls],
                         [WORKSPACE_SHARE_SQL,CLAIM_SQL,BLOCK_RIGHTS_SQL])
        self.assertEqual(db.calls[2][1][0:2],(PID,55))
        db=FakeConn(block=False)
        with patch(
            "intelligence.relevance.live_content_gate.read_live_content_gate",
            side_effect=SREContractError("rights_expired","expiry","expired"),
        ),self.assertRaises(SREContractError) as denial:
            claim_live_item(db,PID)
        self.assertEqual(denial.exception.code,"lease_lost")

    def test_heartbeat_and_release_require_matching_db_cas(self):
        db=FakeConn()
        renew=heartbeat_live_item(db,PID,55,TOKEN,2,lease_seconds=120)
        self.assertEqual(renew["lease_generation"],2)
        self.assertFalse(renew["dispatch_allowed"])
        self.assertEqual(renew["lease_until"],EXPIRES+timedelta(minutes=5))
        self.assertEqual(db.calls[0],(WORKSPACE_SHARE_SQL,(PID,)))
        self.assertEqual(db.calls[1],(HEARTBEAT_SQL,(120,PID,55,TOKEN,2)))
        relinquished=release_live_item(db,PID,55,TOKEN,2)
        self.assertTrue(relinquished["released"])
        self.assertFalse(relinquished["dispatch_allowed"])
        self.assertEqual(db.calls[2],(WORKSPACE_SHARE_SQL,(PID,)))
        self.assertEqual(db.calls[3],(RELEASE_SQL,(PID,55,TOKEN,2)))
        for renew_ok,release_ok in ((False,True),(True,False)):
            db=FakeConn(renew=renew_ok,release=release_ok)
            if not renew_ok:
                with self.assertRaises(SREContractError) as e:
                    heartbeat_live_item(db,PID,55,TOKEN,2)
                self.assertEqual(e.exception.code,"lease_lost")
            if not release_ok:
                with self.assertRaises(SREContractError) as e:
                    release_live_item(db,PID,55,TOKEN,2)
                self.assertEqual(e.exception.code,"lease_lost")

    def test_strict_project_duration_lease_uuid_and_generation_types(self):
        for value in (True,False,0,-1,4,301,"40",1.4,None,[],{}):
            with self.subTest(duration=value),self.assertRaises(SREContractError):
                claim_live_item(FakeConn(),PID,lease_seconds=value)
        for value in ("b"*32,"ABC",None,False):
            with self.subTest(project=value),self.assertRaises(SREContractError):
                claim_live_item(FakeConn(),value)
        for value in (True,0,-1,1.5,"55",None):
            with self.subTest(item_id=value),self.assertRaises(SREContractError):
                heartbeat_live_item(FakeConn(),PID,value,TOKEN,1)
        for value in (True,0,-1,1.5,"1",None):
            with self.subTest(generation=value),self.assertRaises(SREContractError):
                release_live_item(FakeConn(),PID,55,TOKEN,value)
        for value in ("bad","A"*36,0,None,True):
            with self.subTest(token=value),self.assertRaises(SREContractError):
                heartbeat_live_item(FakeConn(),PID,55,value,1)

    def test_import_never_loads_external_model_or_postgres(self):
        code=(
            "import sys;import intelligence.relevance.live_lease;"
            "assert 'psycopg' not in sys.modules;"
            "assert 'openai' not in sys.modules;"
            "assert 'openrouter' not in sys.modules;"
        )
        proc=subprocess.run([sys.executable,"-c",code],
                            capture_output=True,text=True,timeout=15)
        self.assertEqual(proc.returncode,0,proc.stderr)

    def test_sql_atomic_skip_locked_and_current_clock_guards(self):
        self.assertIn("FOR UPDATE OF i SKIP LOCKED",CLAIM_SQL)
        self.assertIn("ORDER BY i.run_id,i.video_id,i.id",CLAIM_SQL)
        self.assertIn("lease_generation=i.lease_generation+1",CLAIM_SQL)
        self.assertIn("r.cancel_requested=FALSE",CLAIM_SQL)
        self.assertIn("r.state IN ('queued','running')",CLAIM_SQL)
        self.assertIn("AND b.state='queued'",CLAIM_SQL)
        self.assertIn("p.state='not_evaluated'",CLAIM_SQL)
        self.assertIn("FROM sre_live_attempt_fences a",CLAIM_SQL)
        self.assertIn("a.state='reserved'",CLAIM_SQL)
        self.assertIn("'dispatch_intent','unknown','completed','failed_known'",CLAIM_SQL)
        self.assertIn("SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR SHARE",
                      WORKSPACE_SHARE_SQL)
        self.assertIn("i.lease_until <= clock_timestamp()",CLAIM_SQL)
        for s in (HEARTBEAT_SQL,RELEASE_SQL):
            self.assertIn("i.worker_token=%s::uuid",s)
            self.assertIn("i.lease_generation=%s",s)
            self.assertIn("i.lease_until>clock_timestamp()",s)
            self.assertIn("r.cancel_requested=FALSE",s)
        self.assertEqual((MIN_LEASE_SECONDS,MAX_LEASE_SECONDS,MAX_ATTEMPTS),
                         (5,300,100))


if __name__=="__main__":
    unittest.main()
