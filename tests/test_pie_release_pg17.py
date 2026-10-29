"""PIE-MOD-35 optional *real* disposable PostgreSQL17 release matrix.

Never connects unless CM_TEST_PG_DSN is explicitly set to a database whose
name contains 'test'. Fresh/upgrade require separate dedicated DSNs, and
two-worker tests require an intentionally seeded isolated PIE project and
DRAFT run (CM_TEST_PIE_PROJECT_ID + CM_TEST_PIE_RUN_ID).
The stub identity below is TEST ONLY, never a real authorization grant.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4
import os
import time
import unittest

from intelligence import migrate
from intelligence.patterns_insights.contracts import PIEBoundaryError
from intelligence.patterns_insights.research.orchestrator import (
    JobIntent,enqueue_job,claim_deterministic_job,complete_job,
    cancel_job,recover_expired_jobs,
)

DSN=os.getenv('CM_TEST_PG_DSN','')
FRESH=os.getenv('CM_TEST_PG_FRESH_DSN','')
UPGRADE=os.getenv('CM_TEST_PG_UPGRADE_DSN','')
PROJECT=os.getenv('CM_TEST_PIE_PROJECT_ID','')
RUN=os.getenv('CM_TEST_PIE_RUN_ID','')


class TestIdentityOnly:
    """No actual authentication: cannot count as a source rights E2E PASS."""
    def require_current_job_access(self,project,actor,operation):
        return operation in {'enqueue','claim','cancel','complete','recover',
                             'execute:insight_deterministic'}


def open_test_database(value):
    import psycopg
    if not value:raise unittest.SkipTest('Requires explicit disposable PG17 DSN')
    conn=psycopg.connect(value,autocommit=True,connect_timeout=6)
    name,version=conn.execute(
        'SELECT current_database(), current_setting(%s)::integer',
        ('server_version_num',)).fetchone()
    if ('test' not in name.lower() or
        name.lower() in ('postgres','template0','template1','contentmachine')
        or version<170000 or version>=180000):
        conn.close()
        raise AssertionError('Refuse non-disposable/non-PostgreSQL17 database')
    return conn


def require_fixture(conn):
    if not (len(PROJECT)==32 and all(c in '0123456789abcdef' for c in PROJECT)):
        raise unittest.SkipTest('Seed CM_TEST_PIE_PROJECT_ID in disposable DB')
    import uuid
    try:
        if str(uuid.UUID(RUN))!=RUN:raise ValueError('noncanonical run')
    except ValueError:
        raise unittest.SkipTest('Seed CM_TEST_PIE_RUN_ID (canonical UUID)')
    found=conn.execute(
        """SELECT status FROM pie_research_runs
            WHERE project_id=%s AND id=%s""",(PROJECT,RUN)).fetchone()
    if found!=('draft',):
        raise unittest.SkipTest('Seed dedicated project+draft run in disposable DB')
    other=conn.execute(
        'SELECT count(*) FROM pie_jobs WHERE project_id=%s',(PROJECT,)).fetchone()[0]
    if other:
        raise unittest.SkipTest('Test project must have no other queued jobs')


@unittest.skipUnless(DSN,'No explicit CM_TEST_PG_DSN')
class PostgreSQL17ReleaseSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn=open_test_database(DSN)
        migrate.apply_migrations(cls.conn)
    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_exact_pinned_migration_versions(self):
        path=Path(migrate.MIGRATIONS)
        expected=[p.name for p in sorted(path.glob('[0-9]*.sql'))]
        installed={r[0] for r in self.conn.execute(
            'SELECT version,sha256 FROM schema_migrations').fetchall()}
        self.assertTrue(set(expected)<=installed)
        self.assertIn('207_pie_orchestrator_queue.sql',installed)

    def test_no_modified_migration_checksum(self):
        import hashlib
        stored=dict(self.conn.execute(
            'SELECT version,sha256 FROM schema_migrations').fetchall())
        for name,sha in stored.items():
            file=Path(migrate.MIGRATIONS)/name
            self.assertTrue(file.is_file())
            self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(),sha,
                             'Deployed migration differs: '+name)

    def test_stage33_orchestrator_source_constraints(self):
        columns={row[0] for row in self.conn.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_name='pie_jobs'""").fetchall()}
        self.assertTrue({'project_id','fence_token','lease_until',
                         'leased_by','attempts','available_at'}<=columns)
        self.assertEqual(self.conn.execute(
            'SELECT to_regclass(%s)',('pie_job_execution_specs',)).fetchone()[0],
            'pie_job_execution_specs')

    def test_private_stage31_32_34_ledger_present(self):
        for table in ('pie_candidate_insight_generations',
                      'pie_candidate_insight_verification_events',
                      'pie_candidate_insight_review_events',
                      'pie_pattern_registry_invalidations'):
            self.assertIsNotNone(self.conn.execute(
                'SELECT to_regclass(%s)',(table,)).fetchone()[0])

    def test_outbox_constraints_and_triggers(self):
        triggers={r[0] for r in self.conn.execute(
            """SELECT tgname FROM pg_trigger
                WHERE tgrelid='pie_outbox'::regclass AND NOT tgisinternal"""
        ).fetchall()}
        self.assertIn('pie_outbox_immutable',triggers)

    def test_migration_idempotent_replay(self):
        self.assertEqual(migrate.apply_migrations(self.conn),[])


@unittest.skipUnless(FRESH,'No separate CM_TEST_PG_FRESH_DSN')
class PostgreSQL17FreshSchema(unittest.TestCase):
    def test_fresh_schema_all_migrations_apply_and_replay(self):
        with open_test_database(FRESH) as conn:
            self.assertIsNone(conn.execute(
                "SELECT to_regclass('schema_migrations')").fetchone()[0],
                'Fresh database must have no migrations previously installed')
            applied=migrate.apply_migrations(conn)
            self.assertIn('207_pie_orchestrator_queue.sql',applied)
            self.assertEqual(migrate.apply_migrations(conn),[])


@unittest.skipUnless(UPGRADE,'No separate CM_TEST_PG_UPGRADE_DSN')
class PostgreSQL17UpgradeSchema(unittest.TestCase):
    def test_real_upgrade_applies_only_new_pending_migrations(self):
        with open_test_database(UPGRADE) as conn:
            if conn.execute(
                "SELECT to_regclass('schema_migrations')").fetchone()[0] is None:
                raise unittest.SkipTest('Upgrade fixture needs previously deployed baseline')
            baseline={r[0] for r in conn.execute(
                'SELECT version FROM schema_migrations').fetchall()}
            self.assertTrue(baseline)
            applied=migrate.apply_migrations(conn)
            self.assertFalse(set(applied)&baseline)
            self.assertEqual(migrate.apply_migrations(conn),[])
            current={r[0] for r in conn.execute(
                'SELECT version FROM schema_migrations').fetchall()}
            self.assertTrue(baseline<=current)


@unittest.skipUnless(DSN and PROJECT and RUN,'No seeded disposable PG17 PIE project/run')
class PostgreSQL17TwoWorkerE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn=open_test_database(DSN)
        migrate.apply_migrations(cls.conn)
        require_fixture(cls.conn)
        cls.auth=TestIdentityOnly()
    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.created=[]

    def tearDown(self):
        for job in self.created:
            self.conn.execute(
                'DELETE FROM pie_outbox WHERE project_id=%s AND event_key LIKE %s',
                (PROJECT,'pie-job:'+job+':%'))
            self.conn.execute(
                'DELETE FROM pie_jobs WHERE project_id=%s AND id=%s',
                (PROJECT,job))

    def enqueue(self,max_attempts=3):
        intent=JobIntent(PROJECT,RUN,str(uuid4()),
                         'insight_deterministic','c'*64,max_attempts)
        receipt=enqueue_job(self.conn,intent,'qa-controller',self.auth)
        self.created.append(receipt.job_id)
        return receipt

    def test_true_two_connections_single_claim(self):
        job=self.enqueue()
        barrier=Barrier(2)
        def worker(name):
            with open_test_database(DSN) as conn:
                barrier.wait(timeout=8)
                return claim_deterministic_job(conn,PROJECT,name,
                                               self.auth,lease_seconds=10)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(worker,name) for name in ('qa-worker-a','qa-worker-b')]
            output=[f.result(timeout=15) for f in futures]
        winners=[x for x in output if x is not None]
        self.assertEqual(len(winners),1)
        self.assertEqual(winners[0].job_id,job.job_id)
        self.assertEqual(winners[0].fence_token,1)
        loser='qa-worker-a' if winners[0].leased_by=='qa-worker-b' else 'qa-worker-b'
        with self.assertRaises(PIEBoundaryError):
            complete_job(self.conn,winners[0],loser,self.auth,'d'*64)
        finished=complete_job(self.conn,winners[0],winners[0].leased_by,self.auth,'d'*64)
        self.assertEqual(finished.state,'completed')
        self.assertEqual(
            self.conn.execute(
                'SELECT state FROM pie_jobs WHERE project_id=%s AND id=%s',
                (PROJECT,job.job_id)).fetchone()[0],'completed')
        self.assertEqual(self.conn.execute(
            'SELECT count(*) FROM pie_outbox WHERE project_id=%s AND event_key LIKE %s',
            (PROJECT,'pie-job:'+job.job_id+':%')).fetchone()[0],3)

    def test_tenant_scope_prevents_claiming_unrelated_job(self):
        self.enqueue()
        self.assertIsNone(claim_deterministic_job(
            self.conn,'e'*32,'qa-other-tenant',self.auth,lease_seconds=10))

    def test_real_immutable_ledger_update_rejected(self):
        job=self.enqueue()
        row=self.conn.execute(
            'SELECT event_sha256 FROM pie_job_orchestration_events WHERE project_id=%s AND job_id=%s',
            (PROJECT,job.job_id)).fetchone()[0]
        with self.assertRaises(Exception):
            self.conn.execute(
                """UPDATE pie_job_orchestration_events SET event_code='completed'
                   WHERE project_id=%s AND job_id=%s""",(PROJECT,job.job_id))
        self.assertEqual(self.conn.execute(
            'SELECT event_sha256 FROM pie_job_orchestration_events WHERE project_id=%s AND job_id=%s',
            (PROJECT,job.job_id)).fetchone()[0],row)

    def test_real_expired_lease_cannot_complete_after_recovery(self):
        job=self.enqueue(max_attempts=2)
        leased=claim_deterministic_job(self.conn,PROJECT,'qa-expiry-worker',
                                       self.auth,lease_seconds=10)
        self.assertEqual(leased.job_id,job.job_id)
        # Deliberately allow the database clock (not local) to expire lease.
        time.sleep(10.25)
        recovered=recover_expired_jobs(
            self.conn,PROJECT,'qa-recovery-worker',self.auth,limit=1)
        self.assertEqual(len(recovered),1)
        self.assertEqual(recovered[0].state,'queued')
        with self.assertRaises(PIEBoundaryError):
            complete_job(self.conn,leased,'qa-expiry-worker',self.auth,'d'*64)
        cancel_job(self.conn,PROJECT,job.job_id,'qa-controller',self.auth)


if __name__=='__main__':
    unittest.main()
