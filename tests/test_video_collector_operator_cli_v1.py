"""VC-MOD-14: standalone CLI smoke, fail-closed flags and operator confirmation.

All tests offline: no real DTK, PostgreSQL, publisher, SRE inbox or network.
"""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from unittest import TestCase
from unittest.mock import patch

from intelligence.collector import cli
from intelligence.collector.contracts import CollectorError

PID="a"*32
BAD="b"*32

class Cursor:
    def __init__(self,one=None,rows=None):
        self.one=one
        self.rows=rows if rows is not None else []
    def fetchone(self):return self.one
    def fetchall(self):return list(self.rows)

class FakeDB:
    def __init__(self,rows=None):
        self.rows=rows or []
        self.sql=[]
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,sql,params=None):
        self.sql.append((sql,params))
        if sql=="SELECT 1":return Cursor(one=(1,))
        if "FROM schema_migrations" in sql:return Cursor(rows=self.rows)
        if "SELECT COUNT(*) FROM collector_sre_outbox" in sql:
            return Cursor(one=(2,))
        raise AssertionError("Unknown SQL "+sql[:110])

class CLI(TestCase):
    def run_cli(self,*args,connect=None):
        out,err=StringIO(),StringIO()
        with redirect_stdout(out),redirect_stderr(err):
            code=cli.main(list(args),connect=connect)
        return code,out.getvalue(),err.getvalue()

    def test_package_is_importable_and_entrypoint_has_no_live_work_on_import(self):
        from intelligence.collector import __main__ as entry
        self.assertIs(entry.main,cli.main)
        self.assertFalse(hasattr(entry,"submit"))
        self.assertFalse(hasattr(entry,"deliver"))
        self.assertEqual(cli.SCHEMA_VERSION,"collector.operator_cli/v1")

    def test_offline_doctor_is_default_and_safely_reports_disabled_flags(self):
        with patch("intelligence.collector.cli.config.setting",return_value="0"),patch(
             "intelligence.collector.repository._connection",
             side_effect=AssertionError("unexpected DB")):
            code,out,err=self.run_cli("doctor")
        self.assertEqual(code,0,err)
        self.assertIn('"checked": false',out)
        self.assertIn('"live_execution_authorized": false',out)
        self.assertIn('"sre_receiver_installed": false',out)
        self.assertIn('"external_provider_calls": 0',out)
        self.assertIn("057_collector_discovery_outbox.sql",out)

    def test_diagnostics_never_print_real_passwords_or_DSN(self):
        def setting(name,default=""):
            if name=="CM_DB_DSN":return "postgres://username:PASSWORD-secret@10.0.1.8/db"
            if name=="CM_DTK_API_KEY":return "SENSITIVE-API-KEY"
            return "1"
        with patch("intelligence.collector.cli.config.setting",side_effect=setting):
            code,out,err=self.run_cli("doctor")
        self.assertEqual(code,0)
        self.assertNotIn("PASSWORD-secret",out+err)
        self.assertNotIn("SENSITIVE-API-KEY",out+err)
        self.assertNotIn("CM_DB_DSN",out+err)
        self.assertNotIn("CM_DTK_API_KEY",out+err)
        self.assertIn('"live_execution_authorized": false',out)

    def test_diagnostics_db_optin_reads_only_migration_checksums(self):
        files=cli._migration_files()
        db=FakeDB(rows=files)
        with patch("intelligence.collector.cli.config.enabled",return_value=True):
            code,out,err=self.run_cli("doctor","--db-read",connect=lambda:db)
        self.assertEqual(code,0,err)
        self.assertIn('"healthy": true',out)
        self.assertIn('"vc_migrations_applied": 4',out)
        self.assertTrue(all("UPDATE" not in sql and "INSERT" not in sql
                            and "DELETE" not in sql for sql,_ in db.sql))
        self.assertEqual(db.sql[0][0],"SELECT 1")

    def test_migration_checksum_mismatch_blocks_readiness(self):
        rows=cli._migration_files()
        forged=[(name,"0"*64 if i==0 else sha) for i,(name,sha) in enumerate(rows)]
        db=FakeDB(rows=forged)
        code,out,err=self.run_cli("doctor","--db-read",connect=lambda:db)
        self.assertEqual(code,2,err)
        self.assertIn('"healthy": false',out)
        self.assertIn('"checksum_mismatch_versions"',out)
        self.assertIn("054_collector_planning.sql",out)

    def test_missing_migration_blocks_readiness_without_auto_applying(self):
        db=FakeDB(rows=cli._migration_files()[:-1])
        code,out,err=self.run_cli("doctor","--db-read",connect=lambda:db)
        self.assertEqual(code,2,err)
        self.assertIn('"missing_versions"',out)
        self.assertIn("057_collector_discovery_outbox.sql",out)
        self.assertFalse(any("CREATE" in sql for sql,_ in db.sql))

    def test_explicit_operator_confirmation_is_project_exact(self):
        for operation in ("cancel-reconcile","retention-scrub","sre-recover"):
            with self.subTest(operation=operation):
                with patch("intelligence.collector.recovery.reconcile_cancelled",
                           side_effect=AssertionError("unexpected mutation")),patch(
                     "intelligence.collector.recovery.scrub_expired_metadata",
                     side_effect=AssertionError("unexpected mutation")),patch(
                     "intelligence.collector.sre_delivery.recover_expired",
                     side_effect=AssertionError("unexpected mutation")):
                    for confirm in (None,BAD,"untrusted"):
                        args=[operation,"--project-id",PID,"--apply"]
                        if confirm is not None:args+=["--confirm-project",confirm]
                        code,out,err=self.run_cli(*args)
                        self.assertEqual(code,2)
                        self.assertIn("operator_confirmation_required",err)

    def test_cancel_reconcile_is_dry_run_unless_apply(self):
        with patch("intelligence.collector.recovery.reconcile_cancelled",
                   return_value={"dry_run":True,"provider_calls":0}) as f:
            code,out,err=self.run_cli("cancel-reconcile","--project-id",PID)
        self.assertEqual(code,0,err)
        self.assertIn('"operator_apply": false',out)
        f.assert_called_once_with(PID,limit=50,dry_run=True,connect=None)

    def test_cancel_reconcile_confirmed_apply_calls_exact_scope(self):
        with patch("intelligence.collector.recovery.reconcile_cancelled",
                   return_value={"dry_run":False,"reconciled":1}) as f:
            code,out,err=self.run_cli("cancel-reconcile","--project-id",PID,
                                      "--confirm-project",PID,"--apply",
                                      "--limit","7")
        self.assertEqual(code,0,err)
        self.assertIn('"operator_apply": true',out)
        f.assert_called_once_with(PID,limit=7,dry_run=False,connect=None)

    def test_retention_scrub_previews_without_mutation(self):
        with patch("intelligence.collector.recovery.retention_audit",
                   return_value={"metadata":[{"action_required":"scrub_expired"},
                                              {"action_required":"verify_legacy_retention_policy"}],
                                 "has_more":False}) as preview,patch(
             "intelligence.collector.recovery.scrub_expired_metadata",
             side_effect=AssertionError("No scrub in preview")):
            code,out,err=self.run_cli("retention-scrub","--project-id",PID)
        self.assertEqual(code,0,err)
        self.assertIn('"due_metadata": 1',out)
        self.assertIn('"dry_run": true',out)
        preview.assert_called_once()

    def test_confirmed_scrub_passes_confirm_boolean_to_repository(self):
        with patch("intelligence.collector.recovery.scrub_expired_metadata",
                   return_value={"scrubbed_count":1}) as f:
            code,out,err=self.run_cli("retention-scrub","--project-id",PID,
                                      "--apply","--confirm-project",PID)
        self.assertEqual(code,0,err)
        self.assertIn('"scrubbed_count": 1',out)
        f.assert_called_once_with(PID,limit=50,confirm=True,connect=None)

    def test_sre_recover_readonly_default_then_confirmed_only(self):
        db=FakeDB()
        code,out,err=self.run_cli("sre-recover","--project-id",PID,connect=lambda:db)
        self.assertEqual(code,0,err)
        self.assertIn('"expired_leases": 2',out)
        self.assertFalse(any("UPDATE" in sql for sql,_ in db.sql))
        with patch("intelligence.collector.sre_delivery.recover_expired",
                   return_value={"recovered":1}) as f:
            code,out,err=self.run_cli("sre-recover","--project-id",PID,
                                      "--apply","--confirm-project",PID)
        self.assertEqual(code,0,err)
        f.assert_called_once_with(PID,connect=None)

    def test_worker_runs_only_existing_gated_worker_not_shadow_dispatch(self):
        with patch("intelligence.collector.worker.main",return_value=2) as f:
            code,out,err=self.run_cli("worker","--once","--interval","10")
        self.assertEqual(code,2)
        f.assert_called_once_with(["--interval","10.0","--once"])

    def test_publisher_help_does_not_load_QV_or_consume_outbox(self):
        with patch("intelligence.collector.publisher_cli.publish_qv",
                   side_effect=AssertionError("No publisher IO")):
            code,out,err=self.run_cli("publisher-help")
        self.assertEqual(code,0,err)
        self.assertIn("python -m intelligence.collector.publisher_cli",out)
        self.assertIn('"publish_requires_explicit_flag": true',out)

    def test_no_public_mutation_routes_or_auto_sre_delivery_in_cli(self):
        source=__import__("inspect").getsource(cli)
        self.assertNotIn("dispatch_one(",source)
        self.assertNotIn("app.route(",source)
        self.assertNotIn("api.submit(",source)
        self.assertIn("confirm_project!=pid",source)
        self.assertIn("opt_in_db_read_required",source)

    def test_invalid_operator_limit_fails_without_running_recovery(self):
        with patch("intelligence.collector.recovery.reconcile_cancelled",
                   side_effect=CollectorError("invalid_limit","Bad limit",400)):
            code,out,err=self.run_cli("cancel-reconcile","--project-id",PID,
                                      "--limit","999")
        self.assertEqual(code,2)
        self.assertIn("invalid_limit",err)

if __name__=="__main__":
    import unittest
    unittest.main()
