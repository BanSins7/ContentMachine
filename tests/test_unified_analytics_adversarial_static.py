"""UA31 static full adversarial/load/local-QA safety contract checks.

Static assertions are not benchmarks, Python unittest runs, PG17, or CI.
"""
from __future__ import annotations

from pathlib import Path
import unittest

BASE=Path(__file__).resolve().parents[1]


class ScaleAdversarialStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def read(p):
            return (BASE/p).read_text(encoding="utf-8")
        cls.bench=read("intelligence/unified_analytics/scale_benchmark.py")
        cls.qa=read("intelligence/unified_analytics/qa_runner.py")
        cls.core=read("intelligence/migrations/080_unified_analytics_core.sql")
        cls.guards=read("intelligence/migrations/085_unified_analytics_transaction_guards.sql")
        cls.queue=read("intelligence/migrations/088_unified_analytics_qualification_jobs.sql")
        cls.queue_api=read("intelligence/unified_analytics/job_queue.py")
        cls.transport=read("intelligence/unified_analytics/http_api.py")
        cls.service=read("intelligence/unified_analytics/http_service.py")
        cls.adversarial=read("tests/test_unified_analytics_adversarial_http.py")
        cls.workers=read("tests/test_unified_analytics_adversarial_workers.py")
        cls.pg=read("tests/test_unified_analytics_adversarial_pg17.py")
        cls.precision=read("tests/test_unified_analytics_adversarial_precision.py")

    def test_benchmark_explicit_10k_50k_only(self):
        self.assertIn("ROWS_ALLOWED=(10000,50000)",self.bench)
        self.assertIn("args.rows not in ROWS_ALLOWED",self.bench)
        self.assertIn("--confirm-disposable",self.bench)

    def test_benchmark_no_production_dsn_fallback(self):
        self.assertIn('env.get("CM_TEST_PG_DSN")',self.bench)
        for name in ("CM_DB_DSN","CM_UAE_WORKER_DSN","CM_UAE_OPS_DSN"):
            self.assertIn(name,self.bench)
        self.assertIn("ua31_disposable_dsn_required",self.bench)

    def test_benchmark_pg17_version_checked_before_migration(self):
        self.assertIn("SHOW server_version_num",self.bench)
        self.assertIn("170000<=version<180000",self.bench)
        self.assertLess(self.bench.index("170000<=version<180000"),
                        self.bench.index("migrate.apply_migrations(db)"))

    def test_benchmark_only_insert_blocked_not_qualified(self):
        self.assertIn("'blocked'",self.bench)
        self.assertNotIn("'qualified',%s",self.bench)
        self.assertIn('"qualified_e2e_throughput_measured":False',self.bench)
        self.assertIn('"worker_throughput_measured":False',self.bench)
        self.assertIn("no_payload_no_peer_approval",self.bench)

    def test_benchmark_explicit_zero_qualified_guard(self):
        self.assertIn("QUALIFIED_SQL",self.bench)
        self.assertIn('if _count(db,QUALIFIED_SQL,project)!=0:',self.bench)
        self.assertIn("ua31_benchmark_forged_qualified",self.bench)

    def test_benchmark_bounded_batch_and_keyset(self):
        self.assertIn("BATCH_LIMIT=5000",self.bench)
        self.assertIn("PAGE_LIMIT=50",self.bench)
        self.assertIn("LIMIT %s",self.bench)
        self.assertIn("id<%s::bigint",self.bench)

    def test_benchmark_parameterized_sql_scope(self):
        self.assertIn("FROM generate_series(%s::int,%s::int)",self.bench)
        self.assertIn("WHERE project_id=%s",self.bench)
        self.assertNotIn("f\"SELECT",self.bench)
        self.assertNotIn("f\"DELETE",self.bench)

    def test_benchmark_cleanup_is_source_project_video_only(self):
        for target in ("project_registry","sources","videos"):
            self.assertIn("DELETE FROM "+target+" WHERE id=%s",self.bench)
        for forbidden in ("TRUNCATE","DROP TABLE","ALTER ROLE",
                          "DISABLE TRIGGER","session_replication_role"):
            self.assertNotIn(forbidden,self.bench)

    def test_benchmark_no_fake_memory_or_provider_metrics(self):
        self.assertIn('"peak_memory_measured":False',self.bench)
        self.assertIn('"producer_model_latency_measured":False',self.bench)
        self.assertNotIn("compute_c_metrics(",self.bench)

    def test_085_remains_unmodified_effective_veto(self):
        self.assertIn("IF NEW.status = 'qualified' THEN",self.guards)
        self.assertIn("IF NEW.checked IS TRUE OR NEW.is_current IS TRUE",self.guards)

    def test_qa_default_strips_pg17(self):
        self.assertIn('isolated.pop("CM_TEST_PG_DSN",None)',self.qa)
        self.assertIn("validate_pg(pg,confirm,env)",self.qa)

    def test_qa_optin_before_real_pg17(self):
        self.assertIn("if opt and not confirm:",self.qa)
        self.assertIn('"CM_TEST_PG_DSN"',self.qa)
        self.assertIn("--confirm-disposable",self.qa)

    def test_qa_cross_platform_python_not_bash(self):
        self.assertIn("sys.executable",self.qa)
        self.assertIn("sys.platform",self.qa)
        self.assertIn("subprocess.run",self.qa)
        self.assertNotIn("bash -c",self.qa)

    def test_qa_redacts_test_tracebacks(self):
        self.assertIn("capture_output=True,text=True",self.qa)
        self.assertNotIn("response.stderr",self.qa)
        self.assertNotIn("response.stdout",self.qa)
        self.assertIn('"qualified_positive_e2e_verified":False',self.qa)

    def test_uae_http_strict_max_body(self):
        self.assertIn("MAX_BODY=40000",self.transport)
        self.assertIn("duplicate_json_key",self.transport)
        self.assertIn("nonfinite_json",self.transport)
        self.assertIn("invalid_body_size",self.transport)

    def test_http_rejects_crossproject_and_forged_qualified(self):
        self.assertIn("X-UAE-Project-Token",self.transport)
        self.assertIn("aggregate_requires_live_qualified_pins",self.service)
        self.assertIn("status IN ('partial','blocked')",self.service)

    def test_queue_pg17_uses_skip_locked_and_fenced_lease(self):
        self.assertIn("FOR UPDATE OF j SKIP LOCKED",self.queue_api)
        self.assertIn("lease_generation",self.queue)
        self.assertIn("uae_queue_request_ready",self.queue_api)

    def test_adversarial_http_injection_and_credential_rejection(self):
        self.assertIn("test_sql_injection_in_page_limit_rejected_pre_db",
                      self.adversarial)
        self.assertIn("test_wrong_project_token_rejected_before_db",
                      self.adversarial)
        self.assertIn("test_no_token_or_dsns_reflected_in_error_json",
                      self.adversarial)

    def test_concurrent_queue_and_real_pg17_both_present(self):
        self.assertIn("ThreadPoolExecutor(max_workers=8)",self.workers)
        self.assertIn("ThreadPoolExecutor(max_workers=16)",self.pg)
        self.assertIn('CM_TEST_PG_DSN',self.pg)
        self.assertIn("test_lease_revoked_before_resolver_no_side_effects",
                      self.workers)

    def test_decimal_50_values_and_no_normalization(self):
        self.assertIn("range(1,51)",self.precision)
        self.assertIn("test_50_shuffles_identical_sha_stable_decimal_stats",
                      self.precision)
        self.assertIn("test_negative_and_over100_remain_unclipped",
                      self.precision)


if __name__=="__main__":
    unittest.main()
