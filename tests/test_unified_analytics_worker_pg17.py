"""UA-MOD-18 real PostgreSQL 17 atomic-fence regression.

Only runs with disposable CM_TEST_PG_DSN. Reuses synthetic UA17 queue
fixture; does not certify LIVE A/B/C/SRE or actual source approvals.
A temporary probe row stands in for the writer to prove PostgreSQL rolls
back it if the SAME transaction cannot acknowledge the lease.
"""
from __future__ import annotations

from contextlib import ExitStack
import os
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics import worker as worker_mod
from intelligence.unified_analytics.job_queue import QualificationJobQueue
from test_unified_analytics_job_queue_pg17 import QueueRealPG17


class FakePins:
    def __init__(self,scope): self.scope=scope


class Resolver:
    def resolve(self,db,scope): return (FakePins(scope),)


class Assembler:
    def __init__(self,db,scope,candidates,*,as_of):
        self._conn,self._scope,self._as_of=db,scope,as_of


class DraftMaker:
    def __init__(self,assembler): pass
    def assemble(self):
        return SimpleNamespace(decision=SimpleNamespace(
            qualified=False,status=SimpleNamespace(value="blocked"),
        ))


class ProbeWriter:
    def __init__(self,assembler,request_id):
        self.db=assembler._conn
    def _write(self,db,draft):
        db.execute("INSERT INTO ua18_atomic_probe(marker) VALUES(17)")
        # No forged UAE qualified snapshot; completion must reject
        # an unknown immutable snapshot and roll this test marker back.
        return SimpleNamespace(snapshot_id=987654321)


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "CM_TEST_PG_DSN disposable PostgreSQL 17 required")
class WorkerAtomicPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        QueueRealPG17.setUpClass()

    def setUp(self):
        self.fixture=QueueRealPG17(
            methodName="test_real_pg17_duplicate_batch_and_claim_race"
        )
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_real_pg17_failed_ack_rolls_back_probe_row(self):
        fixture=self.fixture
        with fixture.connect() as db:
            q=QualificationJobQueue(db)
            q.enqueue(fixture.spec())
            lease=q.claim(fixture.project,"ua18.worker",lease_seconds=60)[0]
            db.execute(
                "CREATE TEMP TABLE ua18_atomic_probe(marker integer)"
                " ON COMMIT PRESERVE ROWS"
            )
            limits=worker_mod.WorkerLimits(
                fixture.project,"ua18.worker",lease_seconds=60,
            )
            worker=worker_mod.QualificationWorker(
                lambda:db,limits,resolver=Resolver(),
            )
            with ExitStack() as stack:
                stack.enter_context(patch.object(
                    worker_mod,"JoinedCandidatePins",FakePins
                ))
                stack.enter_context(patch.object(
                    worker_mod,"JoinedContextAssembler",Assembler
                ))
                stack.enter_context(patch.object(
                    worker_mod,"ImmutableSnapshotAssembler",DraftMaker
                ))
                stack.enter_context(patch.object(
                    worker_mod,"ImmutableSnapshotRepository",ProbeWriter
                ))
                with self.assertRaises(AnalyticsBoundaryError) as ctx:
                    worker._atomic_snapshot_and_ack(db,lease)
            self.assertEqual(ctx.exception.code,"worker_lease_lost")
            remaining=db.execute(
                "SELECT count(*) AS n FROM ua18_atomic_probe"
            ).fetchone()["n"]
            self.assertEqual(remaining,0)
            state=db.execute(
                "SELECT state FROM uae_qualification_jobs WHERE id=%s::uuid",
                (lease.job_id,),
            ).fetchone()["state"]
            self.assertEqual(state,"leased")
            self.assertTrue(q.cancel(fixture.project,lease.job_id))

    def test_pg17_cancelled_lease_prevents_any_resolver_read(self):
        fixture=self.fixture
        with fixture.connect() as db:
            q=QualificationJobQueue(db)
            q.enqueue(fixture.spec())
            old=q.claim(fixture.project,"ua18.worker")[0]
            self.assertTrue(q.cancel(fixture.project,old.job_id))
            resolver=Resolver()
            worker=worker_mod.QualificationWorker(
                lambda:db,worker_mod.WorkerLimits(
                    fixture.project,"ua18.worker"
                ),resolver=resolver,
            )
            with self.assertRaises(AnalyticsBoundaryError) as ctx:
                worker._atomic_snapshot_and_ack(db,old)
            self.assertEqual(ctx.exception.code,"worker_lease_lost")
            self.assertFalse(q.complete(old,snapshot_id=1))


if __name__=="__main__":
    unittest.main()
