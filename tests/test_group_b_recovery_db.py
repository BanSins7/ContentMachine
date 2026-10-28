"""GB-MOD-27 optional disposable PostgreSQL17 failure/recovery QA.

Synthetic QDP/GA source fixture only; no paid model, real provider receipt
or rights claim. Tests never run against DB whose name lacks test/_ci.
"""
from __future__ import annotations

from uuid import uuid4
import os
import unittest

from intelligence.migrate import apply_migrations
from intelligence.group_b.monitoring import (
    MonitoringDenied,ExecutionMetric,record_execution,
    performance_snapshot,ThroughputPolicy,
)
from intelligence.group_b.recovery import read_recovery,recover_expired_run
from intelligence.group_b.orchestrator import (
    claim_local,finish_job,cancel_run,
)
from intelligence.group_b.errors import DependencyUnavailable
import test_group_b_jobs_db as fixture


def disposable_dsn():
    from psycopg.conninfo import conninfo_to_dict
    dsn=os.environ["CM_TEST_PG_DSN"]
    name=conninfo_to_dict(dsn).get("dbname","").lower()
    if "test" not in name and "_ci" not in name:
        raise RuntimeError("Refusing non-disposable PostgreSQL17")
    return dsn


class SyntheticRecoveryAuthority:
    def __init__(self,allow=True):
        self.allow=allow
        self.checked=0
    def verify_recovery(self,project_id,run_id,conn):
        self.checked+=1
        return self.allow is True


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"No disposable PostgreSQL17")
class TestB27RecoveryPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg=psycopg
        cls.dsn=disposable_dsn()
        with psycopg.connect(cls.dsn) as conn:
            apply_migrations(conn)

    def prepare(self):
        base=fixture.TestJobPG17("runTest")
        base.psycopg=self.psycopg
        base.dsn=self.dsn
        base.setUp()
        return base

    def claim(self,base,limit=2):
        with self.psycopg.connect(self.dsn) as conn:
            with conn.transaction():
                return claim_local(
                    conn,project_id=base.project,max_running_local=limit,
                )

    def test_project_capacity_and_queue_separation(self):
        base=self.prepare()
        b=base.new_run()
        base.queue()
        base.queue(b)
        first=self.claim(base,limit=1)
        self.assertIsNotNone(first)
        self.assertEqual(first.kind,"local")
        self.assertIsNone(self.claim(base,limit=1))
        with self.psycopg.connect(self.dsn) as conn:
            count=conn.execute(
                """SELECT count(*) FROM group_b_jobs WHERE project_id=%s
                   AND kind='local' AND state='running'""",
                (base.project,)
            ).fetchone()[0]
            self.assertEqual(count,1)
            cancel_run(conn,project_id=base.project,run_id=first.run_id)
        next_claim=self.claim(base,limit=1)
        self.assertIsNotNone(next_claim)
        self.assertEqual(next_claim.run_id,b)

    def test_immutable_result_metric_same_commit_exact_generation(self):
        base=self.prepare()
        base.queue()
        job=self.claim(base)
        with self.psycopg.connect(self.dsn) as conn:
            with conn.transaction():
                finish_job(
                    conn,job,state="complete",result_sha256="c"*64,
                    elapsed_ms=192
                )
            recorded=conn.execute(
                """SELECT duration_ms,outcome,trace_sha256,charged_usd
                   FROM group_b_execution_metrics
                   WHERE project_id=%s AND job_id=%s::uuid""",
                (base.project,job.id)
            ).fetchone()
            self.assertEqual(recorded[0:2],(192,"complete"))
            self.assertEqual(len(recorded[2]),64)
            self.assertIsNone(recorded[3])
            event=ExecutionMetric(base.project,job.run_id,job.id,
                                  "B5","local",job.generation,
                                  "complete",192,"c"*64)
            _,replayed=record_execution(conn,event)
            self.assertTrue(replayed)
            with self.assertRaises(MonitoringDenied):
                record_execution(conn,ExecutionMetric(
                    base.project,job.run_id,job.id,"B5","local",job.generation,
                    "complete",193,"c"*64
                ))
            with self.assertRaises(Exception):
                with conn.transaction():
                    conn.execute(
                        """DELETE FROM group_b_execution_metrics
                           WHERE project_id=%s AND job_id=%s::uuid""",
                        (base.project,job.id)
                    )
            snap=performance_snapshot(
                conn,project_id=base.project,run_id=base.run
            )
            self.assertEqual(snap["metrics"][0]["elapsed_ms_total"],192)
            self.assertFalse(snap["paid_model_dispatch_activated"])

    def test_stale_lease_cannot_complete_and_crash_recovered_exactly_once(self):
        base=self.prepare()
        base.queue()
        lost=None
        for idx in range(3):
            job=self.claim(base)
            self.assertIsNotNone(job)
            if lost is None:lost=job
            with self.psycopg.connect(self.dsn) as conn:
                conn.execute(
                    """UPDATE group_b_jobs
                       SET lease_until=now()-INTERVAL '1 second'
                       WHERE project_id=%s AND id=%s::uuid""",
                    (base.project,job.id)
                )
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(DependencyUnavailable):
                finish_job(
                    conn,lost,state="complete",
                    result_sha256="c"*64,elapsed_ms=1
                )
            report=read_recovery(
                conn,project_id=base.project,run_id=base.run,
                policy=ThroughputPolicy()
            )
            self.assertEqual(report.expired_exhausted,1)
            with self.assertRaises(MonitoringDenied):
                recover_expired_run(
                    conn,project_id=base.project,run_id=base.run,
                    policy=ThroughputPolicy(),
                    verifier=SyntheticRecoveryAuthority(False),
                )
            done=recover_expired_run(
                conn,project_id=base.project,run_id=base.run,
                policy=ThroughputPolicy(),
                verifier=SyntheticRecoveryAuthority(),
            )
            self.assertEqual(done["terminalized"],1)
            self.assertFalse(done["paid_auto_retry"])
            self.assertEqual(done["new_unknown_duration_metrics"],1)
            after=recover_expired_run(
                conn,project_id=base.project,run_id=base.run,
                policy=ThroughputPolicy(),
                verifier=SyntheticRecoveryAuthority(),
            )
            self.assertEqual(after["terminalized"],0)
            count=conn.execute(
                """SELECT count(*),min(duration_ms)
                   FROM group_b_execution_metrics
                   WHERE project_id=%s AND run_id=%s::uuid
                     AND outcome='lease_expired'""",
                (base.project,base.run)
            ).fetchone()
            self.assertEqual(count[0],1)
            self.assertIsNone(count[1])
            self.assertIsNone(self.claim(base))

    def test_cross_project_no_metric_or_recovery_read(self):
        base=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(MonitoringDenied):
                read_recovery(
                    conn,project_id=uuid4().hex,run_id=base.run,
                    policy=ThroughputPolicy()
                )
            other=performance_snapshot(conn,project_id=uuid4().hex)
            self.assertEqual(other["metrics"],[])
            self.assertEqual(other["paid_ledger"],[])

    def test_1000_job_limit_synthetic_no_paid_dispatch(self):
        base=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            snap=performance_snapshot(conn,project_id=base.project)
            self.assertEqual(snap["running_by_queue"],[])
            self.assertFalse(snap["paid_model_dispatch_activated"])


if __name__=="__main__":
    unittest.main()
