"""UA-MOD-29 source lifecycle safety, SQL and retention CLI static contracts.

No migration editing and no public endpoint for destructive controls.
"""
from __future__ import annotations

from pathlib import Path
import unittest

from intelligence.unified_analytics import source_lifecycle as lc
from intelligence.unified_analytics import retention_cli as cli

ROOT=Path(__file__).resolve().parents[1]


class RetentionStaticChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src=(ROOT/"intelligence/unified_analytics/source_lifecycle.py").read_text(
            encoding="utf-8"
        )
        cls.cli=(ROOT/"intelligence/unified_analytics/retention_cli.py").read_text(
            encoding="utf-8"
        )
        cls.pg080=(ROOT/"intelligence/migrations/080_unified_analytics_core.sql").read_text(
            encoding="utf-8"
        )
        cls.pg088=(ROOT/"intelligence/migrations/088_unified_analytics_qualification_jobs.sql").read_text(
            encoding="utf-8"
        )
        cls.pg085=(ROOT/"intelligence/migrations/085_unified_analytics_transaction_guards.sql").read_text(
            encoding="utf-8"
        )
        cls.pg086=(ROOT/"intelligence/migrations/086_unified_analytics_payload_delete_integrity.sql").read_text(
            encoding="utf-8"
        )
        cls.pg090=(ROOT/"intelligence/migrations/090_unified_analytics_project_aggregate_runs.sql").read_text(
            encoding="utf-8"
        )
        cls.app=(ROOT/"app.py").read_text(encoding="utf-8")

    def test_no_unapproved_qualified_write_or_status_promote(self):
        self.assertNotIn("SET status='qualified'",self.src)
        self.assertNotIn("SET status = 'qualified'",self.src)
        self.assertNotIn("UPDATE uae_unified_snapshots",self.src)
        self.assertNotIn("compute_c_metrics",self.src)
        self.assertNotIn("INSERT INTO calculated_metrics",self.src)

    def test_revocation_reason_from_current_db_not_user(self):
        self.assertIn("src.status<>'enabled'",lc.REVOCATION_TARGETS_SQL)
        self.assertIn("pol.is_active IS NOT TRUE",lc.REVOCATION_TARGETS_SQL)
        self.assertIn("ms.expires_at<=clock_timestamp()",lc.REVOCATION_TARGETS_SQL)
        self.assertIn("derived_retention_days",lc.REVOCATION_TARGETS_SQL)
        self.assertIn("FOR UPDATE OF sn SKIP LOCKED",lc.REVOCATION_TARGETS_SQL)

    def test_immutable_invalidation_and_outbox_in_same_transaction(self):
        self.assertIn("INSERT INTO uae_snapshot_invalidations",
                      lc.INSERT_INVALIDATION_SQL)
        self.assertIn("OUTBOX_INSERT_SQL",self.src)
        self.assertIn("with self.db.transaction():",self.src)
        self.assertIn("source_access_revoked",self.src)
        self.assertIn("source_retention_expired",self.src)

    def test_revoke_source_project_parameterized(self):
        self.assertIn("sn.project_id=%s AND sn.source_id=%s",
                      lc.REVOCATION_TARGETS_SQL)
        self.assertIn("ON CONFLICT(project_id,snapshot_id,reason_sha256)",
                      lc.INSERT_INVALIDATION_SQL)
        self.assertEqual(lc.MAX_SWEEP,100)

    def test_current_rights_not_trusted_by_old_status_only(self):
        self.assertIn("NOT EXISTS",lc.REVOCATION_TARGETS_SQL)
        self.assertIn("uae_snapshot_invalidations",lc.REVOCATION_TARGETS_SQL)
        self.assertNotIn("WHERE sn.status='qualified'",lc.REVOCATION_TARGETS_SQL)

    def test_retention_plan_uses_exact_sha256_row_fingerprints(self):
        self.assertIn("FINGERPRINT_SQL",self.src)
        self.assertIn("sha256(convert_to",lc.FINGERPRINT_SQL)
        self.assertIn('"identity_sha256s":identities',self.src)
        self.assertIn("lifecycle_plan_stale",self.src)

    def test_digest_scope_project_source_binding(self):
        self.assertIn('"project":project,"source":source',self.src)
        self.assertIn("scope_sha256",self.src)
        self.assertIn("source_present",self.src)

    def test_broad_project_cleanup_requires_extra_consent(self):
        self.assertIn("accept_project_wide_collateral:bool=False",self.src)
        self.assertIn("lifecycle_project_collateral_confirmation_required",
                      self.src)
        self.assertIn("--accept-project-wide-collateral",self.cli)

    def test_source_delete_cascade_from_existing_request_fk(self):
        self.assertIn("DELETE FROM uae_qualification_requests",
                      lc.PURGE_REQUESTS_SQL)
        self.assertIn("WHERE project_id=%s AND source_id=%s",
                      lc.PURGE_REQUESTS_SQL)
        self.assertIn("REFERENCES uae_qualification_requests(project_id, id) ON DELETE CASCADE",
                      self.pg080)
        self.assertIn("REFERENCES uae_unified_snapshots(project_id, id) ON DELETE CASCADE",
                      self.pg080)
        self.assertIn("REFERENCES uae_qualification_jobs(project_id,id) ON DELETE CASCADE",
                      self.pg088)

    def test_retention_targets_do_not_modify_peer_tables(self):
        for sql in (
            lc.PURGE_PROJECT_QDP_SQL,lc.PURGE_PROJECT_RUNS_SQL,
            lc.PURGE_REVISIONS_SQL,lc.PURGE_REQUESTS_SQL,
            lc.PURGE_PROJECT_AUDIT_SQL,
        ):
            for bad in ("DELETE FROM sources","DELETE FROM videos",
                        "DELETE FROM group_a_","DELETE FROM group_b_",
                        "DELETE FROM group_c_","DELETE FROM sre_",
                        "DELETE FROM source_policy_versions"):
                self.assertNotIn(bad,sql)

    def test_deferred_evidence_delete_guard_exists(self):
        self.assertIn("DEFERRABLE INITIALLY DEFERRED",self.pg086)
        self.assertIn("existing_payload",self.pg086)
        self.assertIn("ON DELETE CASCADE",self.pg090)

    def test_cleans_unscopable_project_only_records_conservatively(self):
        for name in ("PURGE_PROJECT_QDP_QUARANTINE_SQL",
                     "PURGE_PROJECT_QDP_SQL","PURGE_PROJECT_AUDIT_SQL",
                     "PURGE_PROJECT_RUNS_SQL"):
            self.assertIn(name,self.src)
        self.assertIn("project_wide_collateral",self.src)

    def test_always_verify_zero_counts_before_commit(self):
        self.assertIn("after=self._plan(self.db,project,source)",self.src)
        self.assertIn("if any(after.counts.values()):",self.src)
        self.assertIn("lifecycle_purge_incomplete",self.src)

    def test_ops_token_required_before_any_connection(self):
        self.assertIn("if not authorize(a.project,env):",self.cli)
        self.assertLess(self.cli.index("if not authorize(a.project,env):"),
                        self.cli.index("conn=connect()"))

    def test_preview_never_accepts_apply(self):
        self.assertIn('ops.add_argument("--preview",action="store_true")',self.cli)
        self.assertIn("retention_readonly_flags_invalid",self.cli)

    def test_invalidation_and_purge_require_exact_project_source_confirm(self):
        self.assertIn("a.confirm_project!=a.project",self.cli)
        self.assertIn("a.confirm_source!=a.source",self.cli)
        self.assertIn('p.add_argument("--plan-sha256")',self.cli)

    def test_cli_has_no_http_or_worker_side_effects(self):
        self.assertNotIn("QualificationWorker(",self.cli)
        self.assertNotIn("app.Handler",self.cli)
        self.assertNotIn("requests.post(",self.cli)
        self.assertNotIn("CM_DB_DSN",self.cli)
        self.assertNotIn("while True:",self.cli)

    def test_no_public_retention_route_in_app(self):
        self.assertNotIn("source_lifecycle",self.app)
        self.assertNotIn("retention_cli",self.app)
        self.assertNotIn("/api/retention/purge",self.app)

    def test_cli_never_prints_ids_or_secret_value(self):
        self.assertIn('"external_source_data_deleted":False',self.cli)
        self.assertIn('new_trace_id()',self.cli)
        self.assertNotIn('"job_id":',self.cli)
        self.assertNotIn("env[OPS_TOKEN_ENV]",self.cli)

    def test_project_fresh_lock_for_preview_purge_and_revoke(self):
        self.assertIn("SELECT id FROM project_registry WHERE id=%s FOR UPDATE",
                      lc.PROJECT_LOCK_SQL)
        self.assertIn("lifecycle_fresh_transaction_required",self.src)
        self.assertIn("SET TRANSACTION ISOLATION LEVEL",self.src)


if __name__=="__main__":
    unittest.main()
