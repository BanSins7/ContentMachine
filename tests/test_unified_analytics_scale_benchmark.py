"""UA31 benchmark safety & bounded virtual load unit tests.

Virtual DB does NOT measure PostgreSQL throughput. These tests only check
opt-in refusal, exact project-scoped insert/page counts and cleanup/error
semantics of the separate real disposable PG17 benchmark.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
import io
import unittest

from intelligence.unified_analytics import scale_benchmark as bench

TEST_DSN="postgresql://local/test_ua31"
ENV={"CM_TEST_PG_DSN":TEST_DSN,"CM_DB_DSN":"postgresql://local/prod"}


class Cursor:
    def __init__(self,rows):self.rows=rows
    def fetchone(self):return self.rows[0] if self.rows else None
    def fetchall(self):return self.rows


class FakeBenchDB:
    def __init__(self,fail_at=None):
        self.project=None
        self.source=None
        self.video=None
        self.n=0
        self.qualified=0
        self.deleted=[]
        self.calls=[]
        self.fail_at=fail_at
        self.inserts=0
    @contextmanager
    def transaction(self):
        yield
    def execute(self,sql,args=()):
        self.calls.append(sql)
        if sql==bench.PROJECT_SQL:
            self.project=args[0]
            return Cursor([])
        if sql==bench.SOURCE_SQL:
            self.source=args[0]
            return Cursor([])
        if sql==bench.POLICY_SQL:
            return Cursor([{"id":7}])
        if sql==bench.VIDEO_SQL:
            self.video=17
            return Cursor([{"id":17}])
        if sql.startswith("INSERT INTO sre_workspaces") or sql.startswith(
                "INSERT INTO sre_project_videos"):
            return Cursor([])
        if sql==bench.BATCH_SQL:
            self.inserts+=1
            if self.fail_at==self.inserts:
                raise RuntimeError("postgresql://secret:do-not-leak@example")
            a,b=args[7:9]
            self.n+=b-a+1
            return Cursor([])
        if sql==bench.COUNT_SQL:
            return Cursor([{"n":self.n}])
        if sql==bench.QUALIFIED_SQL:
            return Cursor([{"n":self.qualified}])
        if sql==bench.PAGE_SQL:
            project,b1,b2,limit=args
            assert b1==b2 and project==self.project
            high=self.n if b1 is None else min(b1-1,self.n)
            low=max(1,high-limit+1)
            return Cursor([{"id":x} for x in range(high,low-1,-1)])
        if sql.startswith("DELETE FROM"):
            self.deleted.append((sql,args))
            return Cursor([])
        raise AssertionError("unexpected DB operation")


class BenchmarkSafetyTests(unittest.TestCase):
    def options(self,**change):
        params=dict(rows=10000,batch=2000,confirm_disposable=True)
        params.update(change)
        return SimpleNamespace(**params)

    def test_default_denies_without_disposable_confirmation(self):
        with self.assertRaises(ValueError):
            bench.validate_args(self.options(confirm_disposable=False),ENV)

    def test_missing_test_dsn_denied(self):
        with self.assertRaises(ValueError):
            bench.validate_args(self.options(),{})

    def test_no_fallback_to_production_dsn(self):
        with self.assertRaises(ValueError):
            bench.validate_args(self.options(),{
                "CM_TEST_PG_DSN":TEST_DSN,"CM_DB_DSN":TEST_DSN,
            })

    def test_no_reuse_worker_dsn(self):
        with self.assertRaises(ValueError):
            bench.validate_args(self.options(),{
                "CM_TEST_PG_DSN":TEST_DSN,"CM_UAE_WORKER_DSN":TEST_DSN,
            })

    def test_no_reuse_ops_dsn(self):
        with self.assertRaises(ValueError):
            bench.validate_args(self.options(),{
                "CM_TEST_PG_DSN":TEST_DSN,"CM_UAE_OPS_DSN":TEST_DSN,
            })

    def test_unsupported_sizes_disallowed(self):
        for size in (0,50,9999,10001,50001,10**8,True,"10000"):
            with self.subTest(size=size):
                with self.assertRaises(ValueError):
                    bench.validate_args(self.options(rows=size),ENV)

    def test_invalid_batches_rejected_pre_db(self):
        for size in (0,-1,5001,10000,True,"2000"):
            with self.subTest(size=size):
                with self.assertRaises(ValueError):
                    bench.validate_args(self.options(batch=size),ENV)

    def test_dsn_required_exact_optin(self):
        self.assertEqual(
            bench.validate_args(self.options(),ENV),
            TEST_DSN,
        )

    def test_virtual_10k_rows_replay_scans_all_without_fake_qualified(self):
        db=FakeBenchDB()
        fake_times=iter((0.0,2.0,3.0,5.0))
        out=bench.run(db,rows=10000,batch=2000,clock=lambda:next(fake_times))
        self.assertEqual(out["rows"],10000)
        self.assertEqual(out["scanned"],10000)
        self.assertEqual(out["qualified_records"],0)
        self.assertEqual(out["fixture"],
                         "unqualified_blocked_no_payload_no_peer_approval")
        self.assertEqual(db.inserts,5)
        self.assertEqual(out["insert_rows_per_second"],5000.0)
        self.assertEqual(len(db.deleted),3)

    def test_virtual_50k_pages_bounded_and_cleanup(self):
        db=FakeBenchDB()
        clock=iter((0.,5.,6.,16.))
        out=bench.run(db,rows=50000,batch=5000,clock=lambda:next(clock))
        self.assertEqual(out["scanned"],50000)
        self.assertEqual(db.inserts,10)
        self.assertEqual(out["keyset_page_size"],50)
        self.assertEqual(out["keyset_rows_per_second"],5000.0)
        self.assertFalse(out["qualified_e2e_throughput_measured"])

    def test_preinsertion_fault_tries_project_scoped_cleanup(self):
        db=FakeBenchDB(fail_at=1)
        with self.assertRaises(RuntimeError):
            bench.run(db,rows=10000,batch=2000)
        self.assertEqual(len(db.deleted),3)
        self.assertEqual(db.n,0)
        self.assertTrue(all("WHERE id=%s" in sql for sql,_ in db.deleted))

    def test_midload_fault_tries_project_scoped_cleanup(self):
        db=FakeBenchDB(fail_at=2)
        with self.assertRaises(RuntimeError):
            bench.run(db,rows=10000,batch=2000)
        self.assertEqual(db.inserts,2)
        self.assertEqual(len(db.deleted),3)

    def test_qualified_row_intrusion_aborts(self):
        db=FakeBenchDB()
        db.qualified=1
        with self.assertRaisesRegex(RuntimeError,"forged_qualified"):
            bench.run(db,rows=10000,batch=2000)
        self.assertEqual(len(db.deleted),3)

    def test_disposable_db_count_mismatch_aborts(self):
        class BadCount(FakeBenchDB):
            def execute(self,sql,args=()):
                if sql==bench.COUNT_SQL:return Cursor([{"n":99}])
                return super().execute(sql,args)
        db=BadCount()
        with self.assertRaisesRegex(RuntimeError,"incorrect_insert_count"):
            bench.run(db,rows=10000,batch=2000)
        self.assertEqual(len(db.deleted),3)

    def test_bad_keyset_order_is_rejected_and_cleanup(self):
        class BrokenPages(FakeBenchDB):
            def execute(self,sql,args=()):
                if sql==bench.PAGE_SQL:
                    return Cursor([{"id":4},{"id":5}])
                return super().execute(sql,args)
        db=BrokenPages()
        with self.assertRaisesRegex(RuntimeError,"nonmonotonic_keyset"):
            bench.run(db,rows=10000,batch=2000)
        self.assertEqual(len(db.deleted),3)

    def test_report_does_not_return_project_source_dsn_or_raw_facts(self):
        db=FakeBenchDB()
        fake_times=iter((0.,1.,2.,3.))
        result=bench.run(db,rows=10000,batch=2000,clock=lambda:next(fake_times))
        serialized=str(result)
        self.assertNotIn(db.project,serialized)
        self.assertNotIn(db.source,serialized)
        self.assertNotIn(TEST_DSN,serialized)
        self.assertNotIn("breakout_score",serialized)
        self.assertNotIn("media_path",serialized)

    def test_max_page_is_fifty(self):
        self.assertEqual(bench.PAGE_LIMIT,50)
        self.assertIn("LIMIT %s",bench.PAGE_SQL)
        self.assertIn("id<%s::bigint",bench.PAGE_SQL)

    def test_scale_rows_all_synthetic_blocked(self):
        self.assertIn("'blocked'",bench.BATCH_SQL)
        self.assertNotIn("'qualified'",bench.BATCH_SQL)

    def test_closes_without_real_pg_connection_when_declined(self):
        # No psycopg is imported until validation passes.
        self.assertEqual(bench.main(
            ["--rows","10000"],environ=ENV),2)


if __name__=="__main__":
    unittest.main()
