"""UA31 optional disposable PostgreSQL17 16-way lease stress and fault gates.

No QDP/SRE/A/B/C genuine producer run: UA17 fixture inserts synthetic
A/B/C receipts for *queue admission only*. Test counts unique claimed jobs
and lease fences; no worker calls models or writes qualified snapshots.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import os
import threading
import unittest

from intelligence.unified_analytics.job_queue import QualificationJobQueue
from test_unified_analytics_job_queue_pg17 import QueueRealPG17


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "Disposable PG17 CM_TEST_PG_DSN required")
class ConcurrentPG17QA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        QueueRealPG17.setUpClass()
    def setUp(self):
        self.f=QueueRealPG17(
            methodName="test_real_pg17_duplicate_batch_and_claim_race"
        )
        self.f.setUp()
    def tearDown(self):
        self.f.tearDown()

    def test_100_pending_jobs_claimed_uniquely_by_16_workers(self):
        f=self.f
        with f.connect() as db:
            queue=QualificationJobQueue(db)
            jobs=queue.enqueue_batch(tuple(
                f.spec("ua31."+str(i)) for i in range(100)
            ))
            self.assertEqual(len(jobs),100)
            self.assertTrue(all(x.state=="created" for x in jobs))
        gate=threading.Barrier(16)
        def claim(i):
            with f.connect() as db:
                gate.wait(timeout=30)
                return QualificationJobQueue(db).claim(
                    f.project,"ua31.worker."+str(i),limit=10,
                    lease_seconds=90,
                )
        with ThreadPoolExecutor(max_workers=16) as pool:
            groups=list(pool.map(claim,range(16)))
        claimed=[x for group in groups for x in group]
        self.assertEqual(len({x.job_id for x in claimed}),len(claimed))
        self.assertLessEqual(len(claimed),100)
        # SKIP LOCKED can return fewer under contention; no made-up guarantee
        # all rows are leased in one wave. Drain in a safe sequential wave.
        with f.connect() as db:
            queue=QualificationJobQueue(db)
            for i in range(15):
                more=queue.claim(f.project,"ua31.drain."+str(i),
                                 limit=10,lease_seconds=90)
                claimed.extend(more)
                if not more:break
            self.assertEqual(len(claimed),100)
            self.assertEqual(len({x.job_id for x in claimed}),100)
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_qualification_jobs "
                "WHERE project_id=%s AND state='leased'",(f.project,)
            ).fetchone()["n"],100)
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_unified_snapshots "
                "WHERE project_id=%s",(f.project,)
            ).fetchone()["n"],0)

    def test_idempotent_replay_under_8_separate_connections(self):
        f=self.f
        def enqueue(_):
            with f.connect() as db:
                return QualificationJobQueue(db).enqueue(f.spec("ua31.replay"))
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes=list(pool.map(enqueue,range(8)))
        self.assertEqual(sorted(x.state for x in outcomes),
                         ["created"]+["replayed"]*7)
        self.assertEqual(len({x.job_id for x in outcomes}),1)

    def test_revocation_after_claim_fences_renewal_and_success(self):
        f=self.f
        with f.connect() as db:
            queue=QualificationJobQueue(db)
            queue.enqueue(f.spec("ua31.revoke"))
            lease=queue.claim(f.project,"ua31.worker",limit=1)[0]
            with db.transaction():
                db.execute(
                    "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
                    (f.source,),
                )
            self.assertIsNone(queue.renew(lease,lease_seconds=60))
            self.assertFalse(queue.complete(lease,snapshot_id=12345))
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_unified_snapshots "
                "WHERE project_id=%s",(f.project,)
            ).fetchone()["n"],0)

    def test_cancelled_claim_lease_generation_not_reusable(self):
        f=self.f
        with f.connect() as db:
            q=QualificationJobQueue(db)
            q.enqueue(f.spec("ua31.cancel"))
            lease=q.claim(f.project,"ua31.worker")[0]
            self.assertTrue(q.cancel(f.project,lease.job_id))
            self.assertFalse(q.complete(lease,snapshot_id=1))
            self.assertIsNone(q.fail(lease,error_code="late_worker"))
            self.assertEqual(q.claim(f.project,"ua31.other"),())

    def test_duplicate_unqualified_request_cannot_claim_qualified(self):
        f=self.f
        with f.connect() as db:
            q=QualificationJobQueue(db)
            q.enqueue(f.spec("ua31.fence"))
            l=q.claim(f.project,"ua31.worker")[0]
            self.assertFalse(q.complete(l,snapshot_id=999999999))
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_unified_snapshots "
                "WHERE project_id=%s AND status='qualified'",(f.project,)
            ).fetchone()["n"],0)


if __name__=="__main__":
    unittest.main()
