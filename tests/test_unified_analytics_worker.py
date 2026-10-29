"""UA-MOD-18 worker: lease atomicity, graceful stop, limits, no activation.

Unit fakes validate orchestration only. They are NEVER authoritative SRE,
QDP, A/B/C or PostgreSQL transaction substitutes.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import threading
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics import worker as module
from intelligence.unified_analytics.job_queue import QueueLease

PROJECT="a"*32
REQUEST="00112233-4455-4677-8899-aabbccddeeff"
JOB="11223344-5566-4788-8999-aabbccddeeff"
TOKEN="22223333-4444-4555-8666-777788889999"
SHA="b"*64
NOW=datetime(2026,10,27,18,0,tzinfo=timezone.utc)


def lease():
    return QueueLease(
        PROJECT,JOB,REQUEST,SHA,TOKEN,1,"uae.worker.1",
        NOW+timedelta(minutes=1),1,4,
    )


def limits(**changes):
    args=dict(project_id=PROJECT,worker_id="uae.worker.1",max_jobs=3,
              lease_seconds=60,statement_timeout_ms=20000)
    args.update(changes)
    return module.WorkerLimits(**args)


class Cursor:
    def __init__(self,rows): self.data=rows
    def fetchall(self): return self.data


class FakeDB:
    def __init__(self):
        self.info=SimpleNamespace(transaction_status=0)
        self.lease=lease()
        self.requests=0
        self.saved=[]
        self.acknowledged=False
        self.reject_lease=False
        self.reject_ack=False
        self.fail_query=False
        self.queries=[]

    def __enter__(self): return self
    def __exit__(self,*args): return False

    @contextmanager
    def transaction(self):
        previous=copy.deepcopy((self.saved,self.acknowledged))
        try: yield
        except BaseException:
            self.saved,self.acknowledged=previous
            raise

    def execute(self,sql,args=()):
        self.queries.append(sql)
        if self.fail_query:
            raise RuntimeError("postgresql://username:secret@example")
        if sql.startswith("SET TRANSACTION ISOLATION"):
            return Cursor([])
        if sql==module.TIMEOUT_SQL:
            return Cursor([])
        if sql==module.WORKER_CLOCK_SQL:
            return Cursor([dict(db_now=NOW)])
        if sql==module.WORKER_LEASE_SQL:
            self.requests+=1
            if self.reject_lease: return Cursor([])
            l=self.lease
            if args!=(PROJECT,JOB,TOKEN,l.generation,l.worker,SHA):
                return Cursor([])
            return Cursor([dict(
                job_id=JOB,project_id=PROJECT,request_id=REQUEST,
                job_sha256=SHA,lease_token=TOKEN,
                lease_owner=l.worker,lease_generation=l.generation,
                leased_until=l.leased_until,request_sha256=SHA,
                video_id=17,source_id="test.src",
                source_policy_version_id=5,content_kind="video",
                qdp_run_id=REQUEST,qdp_input_sha256=SHA,
            )])
        if sql==module.SUCCEED_SQL:
            if self.reject_ack: return Cursor([])
            self.acknowledged=True
            return Cursor([dict(id=JOB)])
        raise AssertionError("Unexpected SQL")


class FakePins:
    def __init__(self,scope): self.scope=scope


class Resolver:
    def __init__(self, *, absent=False):
        self.absent=absent
        self.calls=0
    def resolve(self,db,scope):
        self.calls+=1
        return () if self.absent else (FakePins(scope),)


class Assembler:
    def __init__(self,db,scope,candidates,*,as_of):
        self._conn=db
        self._scope=scope
        self._as_of=as_of
        self._candidates=candidates


class DraftMaker:
    def __init__(self,assembler): self.assembler=assembler
    def assemble(self):
        return SimpleNamespace(
            decision=SimpleNamespace(
                qualified=False,status=SimpleNamespace(value="blocked")
            ),
        )


class Repo:
    def __init__(self,assembler,request_id):
        assert request_id==REQUEST
        self.db=assembler._conn
    def _write(self,db,draft):
        assert db is self.db
        db.saved.append(17)
        return SimpleNamespace(snapshot_id=17)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.db=FakeDB()
        self.resolver=Resolver()
        self.worker=module.QualificationWorker(
            lambda:self.db,limits(),resolver=self.resolver,
        )

    @contextmanager
    def patched_pipeline(self):
        with patch.object(module,"JoinedCandidatePins",FakePins),\
             patch.object(module,"JoinedContextAssembler",Assembler),\
             patch.object(module,"ImmutableSnapshotAssembler",DraftMaker),\
             patch.object(module,"ImmutableSnapshotRepository",Repo):
            yield

    def test_success_stores_nonqualified_snapshot_and_ack_atomically(self):
        with self.patched_pipeline():
            outcome=self.worker._atomic_snapshot_and_ack(self.db,lease())
        self.assertEqual(outcome.snapshot_id,17)
        self.assertEqual(self.db.saved,[17])
        self.assertTrue(self.db.acknowledged)
        self.assertIn(module.WORKER_LEASE_SQL,self.db.queries)
        self.assertIn(module.SUCCEED_SQL,self.db.queries)
        self.assertEqual(self.resolver.calls,1)

    def test_stale_lease_prevents_reads_and_any_snapshot(self):
        self.db.reject_lease=True
        with self.patched_pipeline():
            with self.assertRaises(AnalyticsBoundaryError) as ctx:
                self.worker._atomic_snapshot_and_ack(self.db,lease())
        self.assertEqual(ctx.exception.code,"worker_lease_lost")
        self.assertEqual(self.resolver.calls,0)
        self.assertFalse(self.db.saved)

    def test_cancel_after_evaluation_rolls_back_snapshot_and_ack(self):
        self.db.reject_ack=True
        with self.patched_pipeline():
            with self.assertRaises(AnalyticsBoundaryError) as ctx:
                self.worker._atomic_snapshot_and_ack(self.db,lease())
        self.assertEqual(ctx.exception.code,"worker_lease_lost")
        self.assertEqual(self.db.saved,[])
        self.assertFalse(self.db.acknowledged)
        self.assertEqual(self.resolver.calls,1)

    def test_missing_candidates_cannot_fabricate_blocked_snapshot(self):
        self.resolver.absent=True
        with self.patched_pipeline():
            with self.assertRaises(AnalyticsBoundaryError) as ctx:
                self.worker._atomic_snapshot_and_ack(self.db,lease())
        self.assertEqual(ctx.exception.code,"worker_candidate_discovery_unavailable")
        self.assertFalse(self.db.saved)

    def test_db_errors_are_redacted_and_no_snapshot_remains(self):
        self.db.fail_query=True
        with self.patched_pipeline():
            with self.assertRaises(RuntimeError):
                self.worker._atomic_snapshot_and_ack(self.db,lease())
        self.assertEqual(self.db.saved,[])

    def test_no_resolver_rejects_run_before_claiming_any_job(self):
        worker=module.QualificationWorker(lambda:self.db,limits())
        with self.assertRaises(AnalyticsBoundaryError) as ctx:
            worker.run()
        self.assertEqual(ctx.exception.code,"worker_candidate_discovery_unavailable")
        self.assertFalse(self.db.queries)

    def test_limits_do_not_allow_unbounded_activity_or_timeout_after_lease(self):
        for bad in (
            dict(max_jobs=0),dict(max_jobs=10001),
            dict(batch_size=26),dict(poll_seconds=0),
            dict(statement_timeout_ms=60000),
            dict(lock_timeout_ms=-1),dict(lease_seconds=0),
            dict(project_id="wrong"),dict(worker_id="ab"),
        ):
            with self.assertRaises(AnalyticsBoundaryError):
                limits(**bad)

    def test_validity_of_request_is_loaded_under_same_fenced_scope(self):
        self.assertIn("FOR UPDATE OF j",module.WORKER_LEASE_SQL)
        self.assertIn("uae_queue_request_ready",module.WORKER_LEASE_SQL)
        self.assertIn("j.request_sha256=r.request_sha256",
                      module.WORKER_LEASE_SQL)
        self.assertIn("j.leased_until>clock_timestamp()",
                      module.WORKER_LEASE_SQL)
        self.assertNotIn("status='qualified'",module.WORKER_LEASE_SQL)

    def test_recover_only_without_discovery_is_explicit_safe_mode(self):
        w=module.QualificationWorker(lambda:self.db,limits())
        with patch.object(module,"QualificationJobQueue") as q:
            q.return_value.recover_expired.return_value=((JOB,"retry"),)
            self.assertEqual(w.run(recover_only=True),())
            q.return_value.recover_expired.assert_called_once()

    def test_stop_before_poll_does_not_open_db(self):
        stop=threading.Event()
        stop.set()
        w=module.QualificationWorker(
            lambda:self.db,limits(),resolver=self.resolver,stop_event=stop
        )
        self.assertEqual(w.run(),())
        self.assertFalse(self.db.queries)

    def test_once_claims_only_one_bounded_batch(self):
        many=(lease(),lease(),lease())
        with patch.object(module,"QualificationJobQueue") as q:
            q.return_value.recover_expired.return_value=()
            q.return_value.claim.return_value=many[:2]
            with patch.object(self.worker,"process",
                side_effect=lambda l:module.WorkOutcome(l.job_id,"lost")
            ) as processed:
                self.worker.limits=limits(batch_size=2,max_jobs=3)
                out=self.worker.run()
        self.assertEqual(len(out),2)
        self.assertEqual(processed.call_count,2)
        self.assertEqual(q.return_value.claim.call_count,1)

    def test_graceful_stop_mid_batch_does_not_process_rest(self):
        stop=threading.Event()
        self.worker.stop=stop
        with patch.object(module,"QualificationJobQueue") as q:
            q.return_value.recover_expired.return_value=()
            q.return_value.claim.return_value=(lease(),lease())
            def process(l):
                stop.set()
                return module.WorkOutcome(l.job_id,"lost")
            with patch.object(self.worker,"process",side_effect=process) as run:
                self.worker.limits=limits(batch_size=2)
                out=self.worker.run(watch=True)
        self.assertEqual(len(out),1)
        self.assertEqual(run.call_count,1)

    def test_explicit_cli_rejects_unwired_once_and_watch(self):
        from unittest.mock import patch as mock_patch
        with mock_patch.dict("os.environ",{"CM_UAE_WORKER_DSN":"postgresql://local"}):
            for mode in ("--once","--watch"):
                with self.assertRaises(SystemExit) as ctx:
                    module.main([mode,"--project",PROJECT,"--worker","uae.worker.1"])
                self.assertNotEqual(ctx.exception.code,0)


if __name__=="__main__":
    unittest.main()
