"""UA31 deterministic worker/queue adversarial concurrency on mock DB.

These are synthetic lease/fencing/crash/rollback checks, not real PG17
throughput. Real PG17 concurrency remains the separately gated QA suite.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics.job_queue import (
    QualificationJobQueue,INSERT_SQL,CLAIM_SQL,SUCCEED_SQL,QueueSpec,
)
from intelligence.unified_analytics import worker as worker_mod
from test_unified_analytics_job_queue import (
    FakeQueueDB,spec,PROJECT,REQUEST,SHA,
)
from test_unified_analytics_worker import (
    FakeDB as WorkerDB,Resolver,FakePins,Assembler,DraftMaker,Repo,
    limits,lease,
)


class QueueScaleFaults(unittest.TestCase):
    def setUp(self):
        self.db=FakeQueueDB()
        self.queue=QualificationJobQueue(self.db)

    def test_eight_concurrent_idempotent_enqueue_one_job(self):
        inputs=spec(key="ua31.duplicate.job",batch="ua31.same")
        def attempt(_):
            return QualificationJobQueue(self.db).enqueue(inputs)
        with ThreadPoolExecutor(max_workers=8) as ex:
            receipts=list(ex.map(attempt,range(8)))
        self.assertEqual(len(self.db.jobs),1)
        self.assertEqual(sorted(r.state for r in receipts),
                         ["created"]+["replayed"]*7)
        self.assertEqual(len({r.job_id for r in receipts}),1)

    def test_eight_concurrent_claimers_one_lease(self):
        self.queue.enqueue(spec(key="ua31.single.claim",batch="ua31.one"))
        def claim(i):
            return QualificationJobQueue(self.db).claim(
                PROJECT,"ua31.worker."+str(i),limit=1)
        with ThreadPoolExecutor(max_workers=8) as ex:
            all_leases=list(ex.map(claim,range(8)))
        self.assertEqual(sum(map(len,all_leases)),1)
        self.assertEqual(len(self.db.jobs),1)

    def test_concurrent_claimers_at_most_number_of_jobs(self):
        self.queue.enqueue_batch(tuple(
            spec(key="ua31.work."+str(i),batch="ua31.batch."+str(i))
            for i in range(20)
        ))
        def claim(i):
            return QualificationJobQueue(self.db).claim(
                PROJECT,"ua31.worker."+str(i),limit=4)
        with ThreadPoolExecutor(max_workers=8) as ex:
            groups=list(ex.map(claim,range(8)))
        all_leases=[l for grp in groups for l in grp]
        self.assertEqual(len(all_leases),20)
        self.assertEqual(len({x.job_id for x in all_leases}),20)
        self.assertTrue(all(len(group)<=4 for group in groups))

    def test_enqueue_sql_fault_rolls_back_and_does_not_log_secret(self):
        self.db.fault=INSERT_SQL
        with self.assertRaises(AnalyticsBoundaryError) as err:
            self.queue.enqueue(spec(key="ua31.failed",batch="ua31.failed"))
        self.assertNotIn("DO_NOT_PRINT",str(err.exception))
        self.assertEqual(self.db.jobs,{})

    def test_lease_sql_fault_rolls_back_pending_state(self):
        self.queue.enqueue(spec(key="ua31.claimfault",batch="ua31.claimfault"))
        self.db.fault=CLAIM_SQL
        with self.assertRaises(AnalyticsBoundaryError):
            self.queue.claim(PROJECT,"ua31.worker",limit=1)
        self.assertEqual(tuple(j["state"] for j in self.db.jobs.values()),
                         ("pending",))

    def test_stale_lease_never_acknowledges_new_generation(self):
        self.queue.enqueue(spec(key="ua31.stale",batch="ua31.stale"))
        first=self.queue.claim(PROJECT,"ua31.worker.1",limit=1)[0]
        self.db.clock=first.leased_until+timedelta(seconds=1)
        recovered=self.queue.recover_expired(PROJECT)
        self.assertEqual(len(recovered),1)
        second=self.queue.claim(PROJECT,"ua31.worker.2",limit=1)[0]
        self.assertNotEqual(first.token,second.token)
        self.assertNotEqual(first.generation,second.generation)
        self.assertFalse(self.queue.complete(first,snapshot_id=31))

    def test_source_revoked_mid_claim_no_new_lease(self):
        self.queue.enqueue(spec(key="ua31.revoked",batch="ua31.revoked"))
        self.db.ready=False
        self.assertEqual(self.queue.claim(PROJECT,"ua31.worker"),())
        self.assertEqual(tuple(j["state"] for j in self.db.jobs.values()),
                         ("pending",))

    def test_snapshot_invalidated_prevents_success(self):
        self.queue.enqueue(spec(key="ua31.invalidated",batch="ua31.invalidated"))
        l=self.queue.claim(PROJECT,"ua31.worker")[0]
        self.db.snapshot_valid=False
        self.assertFalse(self.queue.complete(l,snapshot_id=42))
        self.assertEqual(tuple(j["state"] for j in self.db.jobs.values()),
                         ("leased",))

    def test_cancelled_lease_cannot_resurrect_from_late_worker(self):
        self.queue.enqueue(spec(key="ua31.cancel",batch="ua31.cancel"))
        l=self.queue.claim(PROJECT,"ua31.worker")[0]
        self.assertTrue(self.queue.cancel(PROJECT,l.job_id))
        self.assertFalse(self.queue.complete(l,snapshot_id=42))
        self.assertEqual(tuple(j["state"] for j in self.db.jobs.values()),
                         ("cancelled",))

    def test_max_retries_dead_letter_not_reprocessed(self):
        self.queue.enqueue(spec(key="ua31.dead",batch="ua31.dead",
                                max_attempts=1))
        l=self.queue.claim(PROJECT,"ua31.worker")[0]
        self.assertEqual(self.queue.fail(l,error_code="load_injected"),"dead")
        self.assertEqual(self.queue.claim(PROJECT,"ua31.worker"),())
        self.assertEqual(len(self.queue.dead_letters(PROJECT)),1)


class WorkerAdversarial(unittest.TestCase):
    def setUp(self):
        self.db=WorkerDB()
        self.resolver=Resolver()
        self.worker=worker_mod.QualificationWorker(
            lambda:self.db,limits(),resolver=self.resolver,
        )

    def test_write_fails_before_ack_and_no_persistent_result(self):
        self.db.reject_ack=True
        with patch.object(worker_mod,"JoinedCandidatePins",FakePins),\
             patch.object(worker_mod,"JoinedContextAssembler",Assembler),\
             patch.object(worker_mod,"ImmutableSnapshotAssembler",DraftMaker),\
             patch.object(worker_mod,"ImmutableSnapshotRepository",Repo):
            with self.assertRaises(AnalyticsBoundaryError):
                self.worker._atomic_snapshot_and_ack(self.db,lease())
        self.assertEqual(self.db.saved,[])
        self.assertFalse(self.db.acknowledged)

    def test_lease_revoked_before_resolver_no_side_effects(self):
        self.db.reject_lease=True
        with self.assertRaises(AnalyticsBoundaryError):
            self.worker._atomic_snapshot_and_ack(self.db,lease())
        self.assertEqual(self.resolver.calls,0)
        self.assertEqual(self.db.saved,[])

    def test_no_resolver_fails_before_transaction(self):
        empty=worker_mod.QualificationWorker(
            lambda:self.db,limits(),resolver=None)
        with self.assertRaises(AnalyticsBoundaryError):
            empty._atomic_snapshot_and_ack(self.db,lease())
        self.assertEqual(self.db.queries,[])

    def test_fault_message_does_not_expose_raw_db_secret(self):
        self.db.fail_query=True
        outcome=self.worker.process(lease())
        self.assertNotIn("postgresql://",str(outcome))
        self.assertNotIn("secret",str(outcome).lower())
        self.assertNotEqual(outcome.state,"succeeded")


if __name__=="__main__":
    unittest.main()
