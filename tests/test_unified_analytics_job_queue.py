"""UA-MOD-17 queue: deterministic mock transitions + static PG17 contract.

Fakes prove facade routing and negative fences, NOT PostgreSQL concurrency
or real QDP/SRE/ABC approvals. Optional disposable PG17 tests follow.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import threading
from types import SimpleNamespace
from uuid import uuid4
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics.job_queue import (
    QueueSpec,QualificationJobQueue,QUEUE_WIRE,
    INSERT_SQL,EXISTING_SQL,CLAIM_SQL,RENEW_SQL,SUCCEED_SQL,FAIL_SQL,
    CANCEL_SQL,RECOVER_SQL,DEAD_SQL,
)

PROJECT="a"*32
REQUEST="00112233-4455-4677-8899-aabbccddeeff"
SHA="b"*64
NOW=datetime(2026,10,27,18,0,tzinfo=timezone.utc)


class Cursor:
    def __init__(self,rows): self.value=rows
    def fetchall(self): return self.value


class FakeQueueDB:
    def __init__(self):
        self.info=SimpleNamespace(transaction_status=0)
        self.lock=threading.RLock()
        self.jobs={}
        self.clock=NOW
        self.ready=True
        self.snapshot_valid=True
        self.fault=None

    @contextmanager
    def transaction(self):
        with self.lock:
            prior=copy.deepcopy(self.jobs)
            try: yield
            except Exception:
                self.jobs=prior
                raise

    def execute(self,sql,args=()):
        if sql==self.fault:
            raise RuntimeError("password=DO_NOT_PRINT")
        if sql=="SET TRANSACTION ISOLATION LEVEL READ COMMITTED":
            return Cursor([])
        if sql==INSERT_SQL:
            key,batch,digest,priority,max_a,project,rid,sha=args
            if not self.ready or project!=PROJECT or rid!=REQUEST or sha!=SHA:
                return Cursor([])
            if any(x["key"]==key or (
                x["request_id"]==rid and x["batch"]==batch
            ) for x in self.jobs.values()):
                return Cursor([])
            job_id=str(uuid4())
            self.jobs[job_id]=dict(
                id=job_id,project_id=project,request_id=rid,job_sha256=digest,
                key=key,batch=batch,priority=priority,max_attempts=max_a,
                state="pending",attempts=0,lease_generation=0,
                lease_token=None,lease_owner=None,leased_until=None,
                last_error_code=None,available_at=self.clock,
                finished_at=None,snapshot_id=None,
            )
            return Cursor([dict(id=job_id,job_sha256=digest,state="pending")])
        if sql==EXISTING_SQL:
            project,key,rid,batch=args
            return Cursor([dict(id=x["id"],job_sha256=x["job_sha256"],
                                state=x["state"])
                for x in self.jobs.values() if x["project_id"]==project
                and (x["key"]==key or (
                    x["request_id"]==rid and x["batch"]==batch))])
        if sql==CLAIM_SQL:
            project,limit,worker,seconds=args
            results=[]
            for j in sorted(self.jobs.values(),key=lambda x:(-x["priority"],x["id"])):
                if (len(results)>=limit or j["project_id"]!=project or
                    j["state"] not in ("pending","retry") or
                    j["available_at"]>self.clock or not self.ready):
                    continue
                j["state"]="leased"
                j["attempts"]+=1
                j["lease_generation"]+=1
                j["lease_token"]=str(uuid4())
                j["lease_owner"]=worker
                j["leased_until"]=self.clock+timedelta(seconds=seconds)
                results.append(self._lease_row(j))
            return Cursor(results)
        if sql==RENEW_SQL:
            seconds,project,jid,token,generation,worker=args
            j=self._fenced(project,jid,token,generation,worker)
            if j and self.ready:
                j["leased_until"]=self.clock+timedelta(seconds=seconds)
                return Cursor([self._lease_row(j)])
            return Cursor([])
        if sql==SUCCEED_SQL:
            sid,project,jid,token,generation,worker,check=args
            j=self._fenced(project,jid,token,generation,worker)
            if j and sid==check and self.ready and self.snapshot_valid:
                j.update(state="succeeded",snapshot_id=sid,
                    lease_token=None,lease_owner=None,leased_until=None,
                    finished_at=self.clock)
                return Cursor([dict(id=jid)])
            return Cursor([])
        if sql==FAIL_SQL:
            backoff,reason,project,jid,token,generation,worker=args
            j=self._fenced(project,jid,token,generation,worker)
            if j is None: return Cursor([])
            state="dead" if j["attempts"]>=j["max_attempts"] else "retry"
            j.update(state=state,lease_token=None,lease_owner=None,
                leased_until=None,last_error_code=reason,
                available_at=self.clock+timedelta(seconds=backoff),
                finished_at=self.clock if state=="dead" else None)
            return Cursor([dict(state=state)])
        if sql==CANCEL_SQL:
            project,jid=args
            j=self.jobs.get(jid)
            if not j or j["project_id"]!=project or j["state"] not in (
                "pending","retry","leased"
            ): return Cursor([])
            j.update(state="cancelled",lease_token=None,lease_owner=None,
                leased_until=None,finished_at=self.clock)
            return Cursor([dict(id=jid)])
        if sql==RECOVER_SQL:
            project,limit=args
            rows=[]
            for j in self.jobs.values():
                if (len(rows)>=limit or j["project_id"]!=project or
                    j["state"]!="leased" or j["leased_until"]>self.clock):
                    continue
                state="dead" if j["attempts"]>=j["max_attempts"] else "retry"
                j.update(state=state,lease_token=None,lease_owner=None,
                    leased_until=None,last_error_code="lease_expired",
                    available_at=self.clock,
                    finished_at=self.clock if state=="dead" else None)
                rows.append(dict(id=j["id"],state=state))
            return Cursor(rows)
        if sql==DEAD_SQL:
            project,limit=args
            return Cursor([dict(
                id=j["id"],request_id=j["request_id"],
                batch_key=j["batch"],attempts=j["attempts"],
                max_attempts=j["max_attempts"],last_error_code=j["last_error_code"],
                finished_at=j["finished_at"],
            ) for j in self.jobs.values()
                if j["project_id"]==project and j["state"]=="dead"][:limit])
        raise AssertionError("unknown SQL")

    def _fenced(self,project,jid,token,generation,worker):
        j=self.jobs.get(jid)
        if (j is not None and j["project_id"]==project and
            j["state"]=="leased" and j["lease_token"]==token and
            j["lease_generation"]==generation and
            j["lease_owner"]==worker and j["leased_until"]>self.clock):
            return j
        return None

    @staticmethod
    def _lease_row(j):
        return dict(
            id=j["id"],project_id=j["project_id"],request_id=j["request_id"],
            job_sha256=j["job_sha256"],lease_token=j["lease_token"],
            lease_generation=j["lease_generation"],
            lease_owner=j["lease_owner"],leased_until=j["leased_until"],
            attempts=j["attempts"],max_attempts=j["max_attempts"],
        )


def spec(batch="part.1",key="job.default",**changes):
    values=dict(project_id=PROJECT,request_id=REQUEST,request_sha256=SHA,
                idempotency_key=key,batch_key=batch,max_attempts=2)
    values.update(changes)
    return QueueSpec(**values)


class QueueFacadeTests(unittest.TestCase):
    def setUp(self):
        self.db=FakeQueueDB()
        self.queue=QualificationJobQueue(self.db)

    def test_duplicate_key_and_batch_do_not_create_second_job(self):
        original=spec()
        a=self.queue.enqueue(original)
        b=self.queue.enqueue(original)
        self.assertEqual((a.state,b.state),("created","replayed"))
        self.assertEqual(a.job_id,b.job_id)
        self.assertEqual(len(self.db.jobs),1)
        self.assertEqual(QUEUE_WIRE,"uae-qualification-job/v1")

    def test_conflicting_duplicate_is_rejected_without_replacing_job(self):
        self.queue.enqueue(spec())
        for conflict in (spec(priority=9),spec(key="another.key")):
            with self.assertRaises(AnalyticsBoundaryError) as ctx:
                self.queue.enqueue(conflict)
            self.assertEqual(ctx.exception.code,"queue_identity_collision")
        self.assertEqual(len(self.db.jobs),1)

    def test_multi_batch_atomic_when_source_readiness_disappears(self):
        specs=(spec(),spec(batch="part.2",key="job.second"))
        self.assertEqual(len(self.queue.enqueue_batch(specs)),2)
        self.assertEqual(self.queue.enqueue_batch(specs)[0].state,"replayed")
        self.db.ready=False
        with self.assertRaises(AnalyticsBoundaryError):
            self.queue.enqueue(spec(batch="part.3",key="job.third"))
        self.assertEqual(len(self.db.jobs),2)

    def test_batch_rollback_on_second_item_error(self):
        with self.assertRaises(AnalyticsBoundaryError):
            self.queue.enqueue_batch((
                spec(),spec(batch="part.1",key="collision.key")
            ))
        self.assertFalse(self.db.jobs)

    def test_two_workers_cannot_claim_same_job(self):
        self.queue.enqueue(spec())
        def take(worker):
            return self.queue.claim(PROJECT,worker,limit=1)
        with ThreadPoolExecutor(max_workers=2) as ex:
            a,b=list(ex.map(take,("worker.a","worker.b")))
        self.assertEqual(sorted((len(a),len(b))),[0,1])
        self.assertEqual(next(iter(a or b)).attempts,1)

    def test_crash_expiry_recovery_and_stale_lease_fence(self):
        self.queue.enqueue(spec())
        old=self.queue.claim(PROJECT,"worker.a")[0]
        self.db.clock+=timedelta(seconds=61)
        self.assertIsNone(self.queue.renew(old))
        self.assertEqual(self.queue.recover_expired(PROJECT)[0][1],"retry")
        new=self.queue.claim(PROJECT,"worker.b")[0]
        self.assertNotEqual(old.token,new.token)
        self.assertEqual(new.generation,old.generation+1)
        self.assertFalse(self.queue.complete(old,snapshot_id=7))
        self.assertIsNone(self.queue.fail(old,error_code="late_worker"))
        self.assertTrue(self.queue.complete(new,snapshot_id=7))
        self.assertEqual(self.db.jobs[old.job_id]["state"],"succeeded")

    def test_retry_then_dead_letter_after_bounded_attempts(self):
        self.queue.enqueue(spec())
        one=self.queue.claim(PROJECT,"worker.a")[0]
        self.assertEqual(self.queue.fail(one,error_code="peer_unavailable",
                                         backoff_seconds=10),"retry")
        self.assertEqual(self.queue.claim(PROJECT,"worker.b"),())
        self.db.clock+=timedelta(seconds=11)
        two=self.queue.claim(PROJECT,"worker.b")[0]
        self.assertEqual(self.queue.fail(two,error_code="peer_unavailable"),"dead")
        self.assertFalse(self.queue.complete(two,snapshot_id=7))
        dlq=self.queue.dead_letters(PROJECT)
        self.assertEqual(len(dlq),1)
        self.assertEqual(dlq[0]["attempts"],2)

    def test_expired_final_attempt_goes_to_dead_letter(self):
        self.queue.enqueue(spec(max_attempts=1))
        lease=self.queue.claim(PROJECT,"worker.a")[0]
        self.db.clock+=timedelta(minutes=2)
        self.assertEqual(self.queue.recover_expired(PROJECT)[0][1],"dead")
        self.assertIsNone(self.queue.fail(lease,error_code="late_failure"))

    def test_cancel_revokes_active_token(self):
        self.queue.enqueue(spec())
        lease=self.queue.claim(PROJECT,"worker.a")[0]
        self.assertTrue(self.queue.cancel(PROJECT,lease.job_id))
        self.assertFalse(self.queue.cancel(PROJECT,lease.job_id))
        self.assertFalse(self.queue.complete(lease,snapshot_id=7))
        self.assertEqual(self.queue.claim(PROJECT,"worker.b"),())

    def test_current_rights_or_snapshot_invalidation_fails_closed(self):
        self.queue.enqueue(spec())
        old=self.queue.claim(PROJECT,"worker.a")[0]
        self.db.ready=False
        self.assertIsNone(self.queue.renew(old))
        self.assertFalse(self.queue.complete(old,snapshot_id=5))
        self.db.ready=True
        self.db.snapshot_valid=False
        self.assertFalse(self.queue.complete(old,snapshot_id=5))
        self.db.snapshot_valid=True
        self.assertTrue(self.queue.complete(old,snapshot_id=5))

    def test_db_failure_redacted_and_batch_rolled_back(self):
        self.db.fault=INSERT_SQL
        with self.assertRaises(AnalyticsBoundaryError) as ctx:
            self.queue.enqueue(spec())
        self.assertEqual(ctx.exception.code,"queue_storage_unavailable")
        self.assertNotIn("password",str(ctx.exception))
        self.assertFalse(self.db.jobs)

    def test_type_and_auth_boundary_fail_closed(self):
        for bad in (
            dict(project_id="wrong"),dict(max_attempts=True),
            dict(request_id="not-a-uuid"),dict(priority=100),
            dict(idempotency_key="oops"),dict(request_sha256="x"*64),
        ):
            with self.assertRaises(AnalyticsBoundaryError):
                spec(**bad)
        with self.assertRaises(AnalyticsBoundaryError):
            self.queue.claim(PROJECT,"w",limit=1000)
        with self.assertRaises(AnalyticsBoundaryError):
            self.queue.enqueue_batch(())
        self.db.info.transaction_status=2
        with self.assertRaises(AnalyticsBoundaryError) as ctx:
            self.queue.claim(PROJECT,"worker.a")
        self.assertEqual(ctx.exception.code,"queue_fresh_transaction_required")


class MigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).resolve().parents[1]
        cls.sql=(root/"intelligence"/"migrations"/
                 "088_unified_analytics_qualification_jobs.sql"
                 ).read_text(encoding="utf-8").lower()

    def test_pg17_atomic_skip_locked_and_fenced_state(self):
        for marker in (
            "create table uae_qualification_jobs",
            "unique(project_id,idempotency_key)",
            "unique(project_id,request_id,batch_key)",
            "lease_generation","lease_token uuid",
            "uae_job_lifecycle_guard","uae_job_audit_transition",
            "create table uae_qualification_job_events",
            "uae_job_event_update_guard","state='dead'",
            "uae_queue_request_ready","uae_qdp_handoff_inbox",
        ):
            self.assertIn(marker,self.sql)
        self.assertIn("for update of j skip locked",CLAIM_SQL.lower())
        self.assertIn("for update of j skip locked",RECOVER_SQL.lower())
        for statement in (RENEW_SQL,SUCCEED_SQL,FAIL_SQL):
            for fence in ("lease_generation=%s","lease_token=%s::uuid",
                          "leased_until>clock_timestamp()"):
                self.assertIn(fence,statement.lower())

    def test_no_shadow_owner_or_public_activation(self):
        for banned in (
            "create table calculated_metrics","create table qdp_runs",
            "create table ga_results","create table group_b_runs",
            "create table group_c_runs","grant select","cron.schedule(",
            "create extension","drop table","truncate table",
            "compute_c_metrics(",
        ):
            self.assertNotIn(banned,self.sql)
        self.assertIn("s.status='enabled'",self.sql)
        self.assertIn("p.is_active is true",self.sql)
        self.assertIn("p.reviewed_at is not null",self.sql)
        self.assertIn("count(distinct h.analysis_group)",self.sql)
        self.assertIn("status in ('partial','blocked')",SUCCEED_SQL.lower())


if __name__=="__main__":
    unittest.main()
