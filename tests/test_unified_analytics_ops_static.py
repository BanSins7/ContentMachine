"""UA28 source/SQL/CLI invariant guard (no real DB or network)."""
from __future__ import annotations

from pathlib import Path
import unittest

from intelligence.unified_analytics import ops_observability as o
from intelligence.unified_analytics import ops_cli as cli

ROOT=Path(__file__).resolve().parents[1]


class OpsInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ops=(ROOT/"intelligence/unified_analytics/ops_observability.py").read_text(
            encoding="utf-8")
        cls.cli=(ROOT/"intelligence/unified_analytics/ops_cli.py").read_text(
            encoding="utf-8")
        cls.worker=(ROOT/"intelligence/unified_analytics/worker.py").read_text(
            encoding="utf-8")
        cls.migrations=(ROOT/"intelligence/migrations/088_unified_analytics_qualification_jobs.sql").read_text(
            encoding="utf-8")

    def test_no_new_migration_needed_queue_events_are_source_truth(self):
        self.assertIn("CREATE TABLE uae_qualification_job_events",self.migrations)
        self.assertIn("CREATE TRIGGER uae_job_audit_trg",self.migrations)
        self.assertIn("uae_snapshot_evidence",o.EVIDENCE_SQL)

    def test_all_ops_queries_project_scoped(self):
        for query in (o.QUEUE_SQL,o.SNAPSHOT_SQL,o.EVIDENCE_SQL,
                      o.INVALIDATIONS_SQL,o.OUTBOX_SQL,o.EVENTS_SQL):
            self.assertIn("project_id=%s",query)

    def test_no_select_all_raw_payload_joins(self):
        for query in (o.QUEUE_SQL,o.SNAPSHOT_SQL,o.EVIDENCE_SQL,
                      o.INVALIDATIONS_SQL,o.OUTBOX_SQL,o.EVENTS_SQL):
            for bad in ("SELECT *","creator_id","media_path",
                        "token","payload_sha256","calculated_metrics"):
                self.assertNotIn(bad,query)

    def test_qualified_not_counted_as_current_eligible(self):
        self.assertIn("status IN ('partial','blocked')",o.SNAPSHOT_SQL)
        self.assertIn("status IN ('partial','blocked')",o.EVIDENCE_SQL)
        self.assertNotIn("status='qualified'",o.SNAPSHOT_SQL)
        self.assertFalse(o.OpsReport.__dataclass_fields__[
            "qualified_live_readable"].default)

    def test_repeatable_read_not_unbounded_stream(self):
        self.assertIn("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ",
                      self.ops)
        self.assertLessEqual(o.MAX_SAMPLE_ROWS,140)
        self.assertIn("len(evidences)>MAX_SAMPLE_ROWS",self.ops)

    def test_no_dynamic_sql_event_reason_labels(self):
        self.assertNotIn("reason_code",o.EVIDENCE_SQL)
        self.assertNotIn("last_error_code",o.QUEUE_SQL)
        self.assertIn("EVENT_CODES=(",self.ops)
        self.assertIn("operation_denied",o.EVENT_CODES)

    def test_worker_metrics_opt_in_no_auto_logger(self):
        self.assertIn("telemetry: SafeEventCounters | None = None",self.worker)
        self.assertIn("if self.telemetry is not None:",self.worker)
        self.assertIn("self.telemetry.record(code)",self.worker)
        self.assertNotIn("print(lease.token",self.worker)

    def test_worker_uses_existing_pg_fenced_lease_invariants(self):
        self.assertIn("self._atomic_snapshot_and_ack(db,lease)",self.worker)
        self.assertIn("QualificationJobQueue(db).recover_expired(",self.worker)
        self.assertIn("self._observe",self.worker)

    def test_cli_has_separate_operator_env_no_http_project_token_reuse(self):
        self.assertEqual(cli.OPS_TOKEN_ENV,"CM_UAE_OPS_PROJECT_TOKEN")
        self.assertEqual(cli.OPS_MAP_ENV,"CM_UAE_OPS_TOKENS_SHA256")
        self.assertEqual(cli.OPS_DSN_ENV,"CM_UAE_OPS_DSN")
        self.assertNotIn("CM_DB_DSN",self.cli)
        self.assertNotIn("CM_UAE_HTTP_TOKENS_SHA256",self.cli)

    def test_cli_no_secret_in_commandline_args(self):
        self.assertIn('parser.add_argument("--project",required=True)',self.cli)
        self.assertNotIn('parser.add_argument("--token"',self.cli)
        self.assertNotIn('parser.add_argument("--dsn"',self.cli)
        self.assertNotIn('parser.add_argument("--password"',self.cli)

    def test_manual_recovery_requires_double_confirm(self):
        self.assertIn('action.add_argument("--recover-expired"',self.cli)
        self.assertIn('parser.add_argument("--apply"',self.cli)
        self.assertIn('parser.add_argument("--confirm-project")',self.cli)
        self.assertIn("args.confirm_project!=args.project",self.cli)

    def test_readonly_mode_no_implicit_recover(self):
        self.assertIn('action.add_argument("--inspect"',self.cli)
        self.assertIn("inspect(args.project,db) if args.inspect",self.cli)
        self.assertNotIn("args.inspect or args.recover_expired",self.cli)

    def test_no_worker_start_or_raw_job_ids_output(self):
        self.assertIn('"producer_run_started":False',self.cli)
        self.assertIn('"qualified":False',self.cli)
        self.assertNotIn('"job_id":',self.cli)
        self.assertNotIn("QualificationWorker(",self.cli)

    def test_output_only_fixed_fields(self):
        self.assertIn("safe_json(self)->str:",self.ops)
        self.assertIn("cli_schema_version",self.cli)
        self.assertIn("new_trace_id()",self.cli)
        self.assertNotIn("traceback.print_exc",self.cli)
        self.assertNotIn("logging.basicConfig",self.cli)

    def test_no_background_loop_or_unbounded_retries(self):
        self.assertNotIn("while True",self.cli)
        self.assertNotIn("setInterval",self.cli)
        self.assertNotIn("ThreadPoolExecutor",self.cli)
        self.assertNotIn("run(watch=True",self.cli)

    def test_db_timeout_config_bounded(self):
        self.assertIn("connect_timeout=5",self.cli)
        self.assertIn("statement_timeout=10000",self.cli)
        self.assertIn("lock_timeout=3000",self.cli)

    def test_no_public_http_route_for_ops(self):
        app=(ROOT/"app.py").read_text(encoding="utf-8")
        self.assertNotIn("ops_observability",app)
        self.assertNotIn("ops_cli",app)
        self.assertNotIn("uae/metrics",app)


if __name__=="__main__":
    unittest.main()
