"""GC29 performance-contract QA; never invent throughput without PG17.

Destructive fixtures are gated by 4 explicit local environment variables.
64-row smoke is intentionally NOT a 10k benchmark; 10k run is separate opt-in.
"""
from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from intelligence.group_c.contracts import ContractError
from intelligence.group_c.performance import (
    BENCHMARK_VERSION,DB_PREFIXES,MIN_POSTS,MAX_POSTS,SMOKE_POSTS,
    BenchmarkPolicy,BenchmarkReport,PlanMeasure,EXPECTED_INDEXES,
    SQL,_require_disposable,_explain,_json_report,parse_plan,seed,profile,
)

MIGRATIONS=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"


def _plan(*,ms=10.0,nodes=None):
    return {
        "Planning Time":0.5,"Execution Time":ms,
        "Plan":nodes or {
            "Node Type":"Limit","Actual Rows":501,
            "Shared Hit Blocks":50,"Shared Read Blocks":2,
            "Plans":[{
                "Node Type":"Index Scan",
                "Index Name":"idx_gc29_raw_source_video_latest",
                "Actual Rows":501,"Shared Hit Blocks":40,
                "Shared Read Blocks":1,
            }]
        }
    }


class TestPerformancePure(unittest.TestCase):
    def test_policy_explicit_at_least_10000_canonical_int(self):
        p=BenchmarkPolicy()
        self.assertEqual(p.post_count,10000)
        self.assertEqual(p.queued_jobs,1000)
        self.assertEqual(p.warm_repeats,5)
        for key,val in (
            ("post_count",9999),("post_count",True),
            ("post_count",100001),("post_count","10000"),
            ("queued_jobs",0),("queued_jobs",10001),
            ("warm_repeats",0),("warm_repeats",True),
            ("warm_repeats",31),
        ):
            with self.subTest(key=key,val=val),self.assertRaises(ContractError):
                BenchmarkPolicy(**{key:val})

    def test_static_sql_only_explicit_cohort_latest_queue_and_metadata(self):
        self.assertEqual(set(SQL),{
            "cohort_candidates","latest_project_observation",
            "scoped_ready_queue","latest_metadata",
        })
        self.assertIn("DISTINCT ON (s.video_id)",SQL["cohort_candidates"])
        self.assertIn("ORDER BY s.video_id,s.observed_at DESC,s.id DESC",
                      SQL["cohort_candidates"])
        self.assertIn("LEFT JOIN LATERAL",SQL["cohort_candidates"])
        self.assertIn("NOT EXISTS",SQL["scoped_ready_queue"])
        self.assertIn("s.source_id=%s",SQL["cohort_candidates"])
        self.assertIn("pv.project_id=%s",SQL["cohort_candidates"])
        self.assertIn("clock_timestamp()",SQL["scoped_ready_queue"])

    def test_explain_plan_is_measured_not_an_estimated_explain(self):
        self.assertEqual(len(EXPECTED_INDEXES),6)
        self.assertEqual(BENCHMARK_VERSION,"group-c-performance/v1")
        src=Path(__file__).resolve().parents[1]/"intelligence"/"group_c"/"performance.py"
        text=src.read_text(encoding="utf-8")
        self.assertIn("EXPLAIN (ANALYZE TRUE, BUFFERS TRUE, FORMAT JSON",
                      text)
        self.assertNotIn("DROP DATABASE",text)

    def test_parse_real_plan_first_plus_warm_measured_values(self):
        p=parse_plan("cohort_candidates",_plan(ms=17.2),
                     [_plan(ms=12.1),_plan(ms=8.5),_plan(ms=10.0)])
        self.assertEqual(p.name,"cohort_candidates")
        self.assertEqual(p.first_pass_ms,17.2)
        self.assertEqual(p.median_repeated_ms,10.0)
        self.assertEqual(p.min_repeated_ms,8.5)
        self.assertEqual(p.max_repeated_ms,12.1)
        self.assertEqual(p.root_actual_rows,501)
        self.assertEqual(p.chosen_indexes,("idx_gc29_raw_source_video_latest",))
        self.assertEqual(p.seq_scan_nodes,0)
        self.assertEqual(p.total_shared_hit_blocks,90)
        self.assertEqual(p.total_shared_read_blocks,3)
        self.assertFalse(p.os_cache_cold_verified)

    def test_reject_missing_or_estimated_only_results(self):
        for plan,warm in (
            ({},[_plan()]),(_plan(),[]),
            ({"Plan":{},"Planning Time":-1,"Execution Time":0},[_plan()]),
            ({"Plan":{},"Planning Time":0},[_plan()]),
        ):
            with self.subTest(plan=plan),self.assertRaises(ContractError):
                parse_plan("cohort_candidates",plan,warm)
        with self.assertRaises(ContractError):
            parse_plan("unreviewed_sql",_plan(),[_plan()])

    def test_disposable_guard_blocks_missing_both_env_consents(self):
        class Conn:
            autocommit=True
            def execute(self,*args):
                raise AssertionError("No database inspection before explicit consent")
        for env in ({},{"GC_TEST_DISPOSABLE_DB":"YES"},
                    {"GC_PERF_ALLOW_WRITE":"YES"}):
            with patch.dict(os.environ,env,clear=True):
                with self.assertRaises(ContractError):
                    _require_disposable(Conn())

    def test_disposable_guard_blocks_prod_like_database(self):
        class Cur:
            def __init__(self,db):self.db=db
            def fetchone(self):return (self.db,17)
        class Conn:
            autocommit=True
            def __init__(self,db):self.db=db
            def execute(self,*_):return Cur(self.db)
        env={"GC_TEST_DISPOSABLE_DB":"YES","GC_PERF_ALLOW_WRITE":"YES"}
        with patch.dict(os.environ,env,clear=True):
            for db in ("production","contentmachine","myproject","contentmachine_gc",
                       "contentmachine_prod"):
                with self.subTest(db=db),self.assertRaises(ContractError):
                    _require_disposable(Conn(db))
            _require_disposable(Conn("contentmachine_gc_performance"))

    def test_guard_rejects_wrong_pg_version_and_transaction_context(self):
        class Cur:
            def fetchone(self):return ("contentmachine_gc_perf",16)
        class Conn:
            autocommit=True
            def execute(self,*_):return Cur()
        with patch.dict(os.environ,{"GC_TEST_DISPOSABLE_DB":"YES",
                                    "GC_PERF_ALLOW_WRITE":"YES"},clear=True):
            with self.assertRaises(ContractError):
                _require_disposable(Conn())
        class NotAuto:autocommit=False
        with self.assertRaises(ContractError):
            _require_disposable(NotAuto())

    def test_performance_schema_is_strictly_additive(self):
        sql=(MIGRATIONS/"064_group_c_performance_indexes.sql").read_text(encoding="utf-8")
        self.assertEqual(sql.count("CREATE INDEX "),6)
        for name in EXPECTED_INDEXES:
            self.assertIn(name,sql)
        self.assertNotIn("DROP ",sql.upper())
        self.assertNotIn("ALTER TABLE ",sql.upper())
        self.assertNotIn("CREATE INDEX CONCURRENTLY",sql.upper())

    def test_reports_can_only_be_emitted_from_typed_real_measurements(self):
        with self.assertRaises(ContractError):
            _json_report({"qualifies_10k":True})
        p=PlanMeasure(
            "cohort_candidates",10.0,7.0,6.0,9.0,0.4,10,0,
            501.0,("Limit","Index Scan"),("idx_gc29_raw_source_video_latest",),0,
        )
        with self.assertRaises(FrozenInstanceError):
            p.first_pass_ms=0

    def test_small_smoke_is_not_allowed_to_claim_10k(self):
        self.assertEqual(SMOKE_POSTS,64)
        self.assertGreaterEqual(MIN_POSTS,10000)
        self.assertLessEqual(MAX_POSTS,100000)
        with self.assertRaises(ContractError):
            BenchmarkPolicy(post_count=SMOKE_POSTS)

    def test_explain_never_allows_unsanctioned_sql(self):
        class Conn:
            def execute(self,*_):
                raise AssertionError("Unexpected database execution")
        with self.assertRaises(ContractError):
            _explain(Conn(),"SELECT password FROM secrets",())


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES"
    and os.getenv("GC_PERF_ALLOW_WRITE")=="YES"
    and os.getenv("GC_PERF_RUN_SMALL")=="YES",
    "64-post smoke: disposable PG17 and explicit perf/seed opt-in required",
)
class TestPerformanceSmokePG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.conn=psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True)
        _require_disposable(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_real_64_post_observations_real_explain_and_backpressure(self):
        pop=seed(self.conn,post_count=SMOKE_POSTS,queued_jobs=32)
        report=profile(self.conn,pop,warm_repeats=2)
        self.assertEqual(report.dataset_posts,64)
        self.assertEqual(report.dataset_observations,128)
        self.assertEqual(report.dataset_queued_jobs,32)
        self.assertFalse(report.qualifies_10k)
        self.assertFalse(report.cohort_overflow_confirmed)
        self.assertEqual(len(report.query_measures),4)
        self.assertEqual(len(report.installed_indexes),6)
        self.assertTrue(all(m.first_pass_ms>=0 for m in report.query_measures))
        self.assertTrue(all(not m.os_cache_cold_verified for m in report.query_measures))
        self.assertEqual(report.backpressure.claims_granted,2)
        self.assertEqual(report.backpressure.claims_denied_by_budget,2)
        self.assertEqual(report.backpressure.final_running,2)
        self.assertEqual(report.backpressure.final_queued,30)
        self.assertIn('"qualifies_10k": false',_json_report(report))


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES"
    and os.getenv("GC_PERF_ALLOW_WRITE")=="YES"
    and os.getenv("GC_PERF_RUN_10K")=="YES",
    "10k benchmark: explicit dedicated disposable PG17 manual opt-in only",
)
class TestPerformance10kPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.conn=psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True)
        _require_disposable(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_genuine_10k_source_posts_safe_cohort_overflow_and_queue_pressure(self):
        pop=seed(self.conn,post_count=10000,queued_jobs=1000)
        report=profile(self.conn,pop,warm_repeats=3)
        self.assertEqual(report.dataset_posts,10000)
        self.assertEqual(report.dataset_observations,20000)
        self.assertTrue(report.qualifies_10k)
        self.assertTrue(report.cohort_overflow_confirmed)
        self.assertEqual(report.backpressure.final_running,2)
        self.assertEqual(report.backpressure.final_queued,998)
        self.assertTrue(all(measure.source=="real_pg17_explain_analyze_buffers"
                            for measure in report.query_measures))
        self.assertTrue(all(not measure.os_cache_cold_verified
                            for measure in report.query_measures))


if __name__=="__main__":
    unittest.main()
