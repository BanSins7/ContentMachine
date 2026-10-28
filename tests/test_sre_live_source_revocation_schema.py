"""M34 permanent source tombstone, cache purge, SQL fencing and outbox checks."""
from pathlib import Path
import unittest
import re
from intelligence.relevance.migration_guard import build_manifest,assess_applied

SQL=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"/"068_sre_live_source_revocation.sql"

class M34RevocationSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=SQL.read_text(encoding="utf-8")

    def test_no_duplicate_ddl_and_no_dollar_replacement_corruption(self):
        # The M34 SQL has literal regexp '
        s=self.sql
        self.assertIn("CREATE TABLE sre_live_source_tombstones",s)
        self.assertIn("PRIMARY KEY(project_id,content_version_id)",s)
        self.assertIn("source_policy_version_id BIGINT NOT NULL",s)
        self.assertIn("M34 revocation does not match original tenant content",s)
        self.assertIn("M34 revocation tombstone is permanent",s)
        self.assertIn("BEFORE UPDATE OR DELETE ON sre_live_source_tombstones",s)
        self.assertIn("REVOKE ALL ON sre_live_source_tombstones FROM PUBLIC",s)

    def test_cache_and_offline_contexts_physically_purged_but_finance_kept(self):
        s=self.sql
        self.assertIn("DELETE FROM sre_local_embedding_cache",s)
        self.assertIn("DELETE FROM sre_offline_items",s)
        self.assertIn("DELETE FROM sre_classification_results result",s)
        self.assertIn("DELETE FROM sre_stage_executions stage",s)
        self.assertIn("CREATE TABLE sre_offline_revocation_fences",s)
        self.assertIn("PRIMARY KEY(project_id,run_id,content_version_id)",s)
        self.assertIn("BEFORE UPDATE OR DELETE ON sre_offline_revocation_fences",s)
        self.assertIn("M34 revoked offline run cannot recreate stage evidence",s)
        self.assertIn("BEFORE INSERT OR UPDATE ON sre_stage_executions",s)
        self.assertIn("BEFORE INSERT OR UPDATE ON sre_classification_results",s)
        self.assertIn("REVOKE ALL ON sre_offline_revocation_fences FROM PUBLIC",s)
        self.assertIn("run.execution_mode='offline_fixture'",s)
        self.assertIn("M34 revoked source cannot be recached",s)
        self.assertIn("BEFORE INSERT ON sre_local_embedding_cache",s)
        self.assertNotIn("DELETE FROM sre_live_opus_receipts",s)
        self.assertNotIn("run.execution_mode='live'\n      AND result",s)
        self.assertNotIn("DELETE FROM sre_live_budget_reservations",s)
        self.assertNotIn("DELETE FROM sre_live_human_reviews",s)

    def test_pre_send_financial_holds_released_only_without_paid_intent(self):
        s=self.sql
        self.assertIn("UPDATE sre_live_budget_reservations res",s)
        self.assertIn("release_reason='rights_revoked'",s)
        self.assertIn("res.state='reserved'",s)
        self.assertIn("'dispatch_intent','unknown','completed','failed_known'",s)
        self.assertIn("SELECT 1 FROM sre_live_opus_dispatch_intents d",s)
        self.assertIn("AND d.reservation_id=res.reservation_id",s)
        self.assertIn("UPDATE sre_live_opus_consents consent",s)
        self.assertIn("revoke_reason='security_revoked'",s)
        self.assertIn("consent.revoked_at IS NULL",s)
        self.assertIn("d.consent_id=consent.consent_id",s)
        self.assertIn("BEFORE INSERT ON sre_live_opus_consents",s)
        self.assertIn("BEFORE INSERT ON sre_live_budget_reservations",s)
        self.assertNotIn("DELETE FROM sre_live_budget_events",s)
        self.assertNotIn("UPDATE sre_live_opus_receipts",s)

    def test_separately_authorized_primary_s3_payload_purge_certificate(self):
        s=self.sql
        self.assertIn("CREATE TABLE sre_source_payload_purge_certificates",s)
        self.assertIn("PRIMARY KEY(project_id,content_version_id)",s)
        self.assertIn("REFERENCES sre_live_source_tombstones",s)
        self.assertIn("M34 cannot attest S3 purge without a safe absent primary row",s)
        self.assertIn("pin.reason_code NOT IN",s)
        self.assertIn("'privacy_delete','source_deleted','retention_expired'",s)
        self.assertIn("FROM sre_live_items i",s)
        self.assertIn("BEFORE UPDATE OR DELETE ON sre_source_payload_purge_certificates",s)
        self.assertIn("REVOKE ALL ON sre_source_payload_purge_certificates FROM PUBLIC",s)
        self.assertNotIn("DELETE FROM sre_live_items",s)
        self.assertNotIn("DELETE FROM sre_live_opus_dispatch_intents",s)

    def test_durable_outbox_and_bounded_ack_require_cancel_or_terminal(self):
        s=self.sql
        self.assertIn("CREATE TABLE sre_live_revocation_outbox",s)
        self.assertIn("UNIQUE(project_id,content_version_id,run_id)",s)
        self.assertIn("FOREIGN KEY(run_id,project_id)",s)
        self.assertIn("INSERT INTO sre_live_revocation_outbox",s)
        self.assertIn("M34 outbox cannot acknowledge an active run",s)
        self.assertIn("r.state='unknown' AND r.cancel_requested=TRUE",s)
        self.assertIn("r.state IN ('cancelled','completed','partial','failed')",s)
        self.assertNotIn("r.cancel_requested=TRUE OR",s)
        self.assertIn("WHERE state='pending'",s)
        self.assertIn("REVOKE ALL ON sre_live_revocation_outbox FROM PUBLIC",s)

    def test_immediate_atomic_run_fail_stop_and_no_reset(self):
        s=self.sql
        self.assertIn("UPDATE sre_live_runs r",s)
        self.assertIn("SET cancel_requested=TRUE,updated_at=clock_timestamp()",s)
        self.assertIn("UPDATE sre_offline_runs r",s)
        self.assertIn("UPDATE sre_classification_runs legacy",s)
        self.assertIn("M34 revoked LIVE source cannot restart",s)
        self.assertIn("M34 revoked S4 run cannot restart",s)
        self.assertIn("BEFORE UPDATE ON sre_live_runs",s)
        self.assertIn("BEFORE UPDATE ON sre_offline_runs",s)
        self.assertIn("r.state='unknown' AND r.cancel_requested=TRUE",s)
        self.assertIn("M34 outbox cannot acknowledge an active run",s)

    def test_read_and_all_new_derived_writes_blocked(self):
        s=self.sql
        self.assertIn("CREATE OR REPLACE VIEW sre_live_classification_results",s)
        self.assertIn("NOT EXISTS (\n    SELECT 1 FROM sre_live_source_tombstones revoked",s)
        for table in ("sre_live_items","sre_offline_items",
                      "sre_live_opus_quotes","sre_live_opus_dispatch_intents",
                      "sre_live_verified_pair_results","sre_live_human_reviews",
                      "sre_live_gold_benchmark_snapshots"):
            self.assertIn("BEFORE INSERT ON "+table,s)
        self.assertIn("M34 revoked source cannot create derived result",s)
        self.assertIn("M34 revoked source cannot enter gold snapshot",s)

    def test_no_raw_content_and_next_migration_prefix(self):
        table=self.sql[self.sql.index("CREATE TABLE sre_live_source_tombstones"):
                       self.sql.index("CREATE FUNCTION sre_m34_guard_tombstone")]
        for raw in ("raw_json","prompt_text","transcript_text",
                    "api_key","model_output","video_url"):
            self.assertNotIn(raw,table)
        self.assertIn("068_sre_live_source_revocation.sql",build_manifest())
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"069")


if __name__=="__main__":
    unittest.main()
 and quoted PL/pgSQL dollars;
        # string-replacement code must never interpret JS '$\'' as a suffix.
        s=self.sql
        names=re.findall(
            r"^CREATE (?:TABLE|FUNCTION|TRIGGER) ([a-z0-9_]+)",
            s,re.M,
        )
        self.assertGreaterEqual(len(names),30)
        self.assertEqual(len(names),len(set(names)))
        self.assertEqual(s.count("CREATE FUNCTION sre_m34_revoke_dependents"),1)
        self.assertEqual(s.count("CREATE FUNCTION sre_m34_guard_tombstone"),1)
        self.assertEqual(s.count("CREATE FUNCTION sre_m34_guard_cancel_reset"),1)
        self.assertEqual(s.count("CREATE TABLE sre_source_payload_purge_certificates"),1)
        self.assertEqual(s.count("REVOKE ALL ON sre_live_source_tombstones FROM PUBLIC"),1)
        self.assertLess(len(s),33000)
        self.assertIn("content_sha256 ~ '^[0-9a-f]{64}
        s=self.sql
        self.assertIn("CREATE TABLE sre_live_source_tombstones",s)
        self.assertIn("PRIMARY KEY(project_id,content_version_id)",s)
        self.assertIn("source_policy_version_id BIGINT NOT NULL",s)
        self.assertIn("M34 revocation does not match original tenant content",s)
        self.assertIn("M34 revocation tombstone is permanent",s)
        self.assertIn("BEFORE UPDATE OR DELETE ON sre_live_source_tombstones",s)
        self.assertIn("REVOKE ALL ON sre_live_source_tombstones FROM PUBLIC",s)

    def test_cache_and_offline_contexts_physically_purged_but_finance_kept(self):
        s=self.sql
        self.assertIn("DELETE FROM sre_local_embedding_cache",s)
        self.assertIn("DELETE FROM sre_offline_items",s)
        self.assertIn("DELETE FROM sre_classification_results result",s)
        self.assertIn("DELETE FROM sre_stage_executions stage",s)
        self.assertIn("CREATE TABLE sre_offline_revocation_fences",s)
        self.assertIn("PRIMARY KEY(project_id,run_id,content_version_id)",s)
        self.assertIn("BEFORE UPDATE OR DELETE ON sre_offline_revocation_fences",s)
        self.assertIn("M34 revoked offline run cannot recreate stage evidence",s)
        self.assertIn("BEFORE INSERT OR UPDATE ON sre_stage_executions",s)
        self.assertIn("BEFORE INSERT OR UPDATE ON sre_classification_results",s)
        self.assertIn("REVOKE ALL ON sre_offline_revocation_fences FROM PUBLIC",s)
        self.assertIn("run.execution_mode='offline_fixture'",s)
        self.assertIn("M34 revoked source cannot be recached",s)
        self.assertIn("BEFORE INSERT ON sre_local_embedding_cache",s)
        self.assertNotIn("DELETE FROM sre_live_opus_receipts",s)
        self.assertNotIn("run.execution_mode='live'\n      AND result",s)
        self.assertNotIn("DELETE FROM sre_live_budget_reservations",s)
        self.assertNotIn("DELETE FROM sre_live_human_reviews",s)

    def test_pre_send_financial_holds_released_only_without_paid_intent(self):
        s=self.sql
        self.assertIn("UPDATE sre_live_budget_reservations res",s)
        self.assertIn("release_reason='rights_revoked'",s)
        self.assertIn("res.state='reserved'",s)
        self.assertIn("'dispatch_intent','unknown','completed','failed_known'",s)
        self.assertIn("SELECT 1 FROM sre_live_opus_dispatch_intents d",s)
        self.assertIn("AND d.reservation_id=res.reservation_id",s)
        self.assertIn("UPDATE sre_live_opus_consents consent",s)
        self.assertIn("revoke_reason='security_revoked'",s)
        self.assertIn("consent.revoked_at IS NULL",s)
        self.assertIn("d.consent_id=consent.consent_id",s)
        self.assertIn("BEFORE INSERT ON sre_live_opus_consents",s)
        self.assertIn("BEFORE INSERT ON sre_live_budget_reservations",s)
        self.assertNotIn("DELETE FROM sre_live_budget_events",s)
        self.assertNotIn("UPDATE sre_live_opus_receipts",s)

    def test_separately_authorized_primary_s3_payload_purge_certificate(self):
        s=self.sql
        self.assertIn("CREATE TABLE sre_source_payload_purge_certificates",s)
        self.assertIn("PRIMARY KEY(project_id,content_version_id)",s)
        self.assertIn("REFERENCES sre_live_source_tombstones",s)
        self.assertIn("M34 cannot attest S3 purge without a safe absent primary row",s)
        self.assertIn("pin.reason_code NOT IN",s)
        self.assertIn("'privacy_delete','source_deleted','retention_expired'",s)
        self.assertIn("FROM sre_live_items i",s)
        self.assertIn("BEFORE UPDATE OR DELETE ON sre_source_payload_purge_certificates",s)
        self.assertIn("REVOKE ALL ON sre_source_payload_purge_certificates FROM PUBLIC",s)
        self.assertNotIn("DELETE FROM sre_live_items",s)
        self.assertNotIn("DELETE FROM sre_live_opus_dispatch_intents",s)

    def test_durable_outbox_and_bounded_ack_require_cancel_or_terminal(self):
        s=self.sql
        self.assertIn("CREATE TABLE sre_live_revocation_outbox",s)
        self.assertIn("UNIQUE(project_id,content_version_id,run_id)",s)
        self.assertIn("FOREIGN KEY(run_id,project_id)",s)
        self.assertIn("INSERT INTO sre_live_revocation_outbox",s)
        self.assertIn("M34 outbox cannot acknowledge an active run",s)
        self.assertIn("r.state='unknown' AND r.cancel_requested=TRUE",s)
        self.assertIn("r.state IN ('cancelled','completed','partial','failed')",s)
        self.assertNotIn("r.cancel_requested=TRUE OR",s)
        self.assertIn("WHERE state='pending'",s)
        self.assertIn("REVOKE ALL ON sre_live_revocation_outbox FROM PUBLIC",s)

    def test_immediate_atomic_run_fail_stop_and_no_reset(self):
        s=self.sql
        self.assertIn("UPDATE sre_live_runs r",s)
        self.assertIn("SET cancel_requested=TRUE,updated_at=clock_timestamp()",s)
        self.assertIn("UPDATE sre_offline_runs r",s)
        self.assertIn("UPDATE sre_classification_runs legacy",s)
        self.assertIn("M34 revoked LIVE source cannot restart",s)
        self.assertIn("M34 revoked S4 run cannot restart",s)
        self.assertIn("BEFORE UPDATE ON sre_live_runs",s)
        self.assertIn("BEFORE UPDATE ON sre_offline_runs",s)
        self.assertIn("r.state='unknown' AND r.cancel_requested=TRUE",s)
        self.assertIn("M34 outbox cannot acknowledge an active run",s)

    def test_read_and_all_new_derived_writes_blocked(self):
        s=self.sql
        self.assertIn("CREATE OR REPLACE VIEW sre_live_classification_results",s)
        self.assertIn("NOT EXISTS (\n    SELECT 1 FROM sre_live_source_tombstones revoked",s)
        for table in ("sre_live_items","sre_offline_items",
                      "sre_live_opus_quotes","sre_live_opus_dispatch_intents",
                      "sre_live_verified_pair_results","sre_live_human_reviews",
                      "sre_live_gold_benchmark_snapshots"):
            self.assertIn("BEFORE INSERT ON "+table,s)
        self.assertIn("M34 revoked source cannot create derived result",s)
        self.assertIn("M34 revoked source cannot enter gold snapshot",s)

    def test_no_raw_content_and_next_migration_prefix(self):
        table=self.sql[self.sql.index("CREATE TABLE sre_live_source_tombstones"):
                       self.sql.index("CREATE FUNCTION sre_m34_guard_tombstone")]
        for raw in ("raw_json","prompt_text","transcript_text",
                    "api_key","model_output","video_url"):
            self.assertNotIn(raw,table)
        self.assertIn("068_sre_live_source_revocation.sql",build_manifest())
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"069")


if __name__=="__main__":
    unittest.main()
",s)
        self.assertEqual(s.count("$m34_revoke$;"),1)
        self.assertEqual(s.count("$m34_cancel_reset$;"),1)

    def test_source_pin_and_monotone_tombstone(self):
        s=self.sql
        self.assertIn("CREATE TABLE sre_live_source_tombstones",s)
        self.assertIn("PRIMARY KEY(project_id,content_version_id)",s)
        self.assertIn("source_policy_version_id BIGINT NOT NULL",s)
        self.assertIn("M34 revocation does not match original tenant content",s)
        self.assertIn("M34 revocation tombstone is permanent",s)
        self.assertIn("BEFORE UPDATE OR DELETE ON sre_live_source_tombstones",s)
        self.assertIn("REVOKE ALL ON sre_live_source_tombstones FROM PUBLIC",s)

    def test_cache_and_offline_contexts_physically_purged_but_finance_kept(self):
        s=self.sql
        self.assertIn("DELETE FROM sre_local_embedding_cache",s)
        self.assertIn("DELETE FROM sre_offline_items",s)
        self.assertIn("DELETE FROM sre_classification_results result",s)
        self.assertIn("DELETE FROM sre_stage_executions stage",s)
        self.assertIn("CREATE TABLE sre_offline_revocation_fences",s)
        self.assertIn("PRIMARY KEY(project_id,run_id,content_version_id)",s)
        self.assertIn("BEFORE UPDATE OR DELETE ON sre_offline_revocation_fences",s)
        self.assertIn("M34 revoked offline run cannot recreate stage evidence",s)
        self.assertIn("BEFORE INSERT OR UPDATE ON sre_stage_executions",s)
        self.assertIn("BEFORE INSERT OR UPDATE ON sre_classification_results",s)
        self.assertIn("REVOKE ALL ON sre_offline_revocation_fences FROM PUBLIC",s)
        self.assertIn("run.execution_mode='offline_fixture'",s)
        self.assertIn("M34 revoked source cannot be recached",s)
        self.assertIn("BEFORE INSERT ON sre_local_embedding_cache",s)
        self.assertNotIn("DELETE FROM sre_live_opus_receipts",s)
        self.assertNotIn("run.execution_mode='live'\n      AND result",s)
        self.assertNotIn("DELETE FROM sre_live_budget_reservations",s)
        self.assertNotIn("DELETE FROM sre_live_human_reviews",s)

    def test_pre_send_financial_holds_released_only_without_paid_intent(self):
        s=self.sql
        self.assertIn("UPDATE sre_live_budget_reservations res",s)
        self.assertIn("release_reason='rights_revoked'",s)
        self.assertIn("res.state='reserved'",s)
        self.assertIn("'dispatch_intent','unknown','completed','failed_known'",s)
        self.assertIn("SELECT 1 FROM sre_live_opus_dispatch_intents d",s)
        self.assertIn("AND d.reservation_id=res.reservation_id",s)
        self.assertIn("UPDATE sre_live_opus_consents consent",s)
        self.assertIn("revoke_reason='security_revoked'",s)
        self.assertIn("consent.revoked_at IS NULL",s)
        self.assertIn("d.consent_id=consent.consent_id",s)
        self.assertIn("BEFORE INSERT ON sre_live_opus_consents",s)
        self.assertIn("BEFORE INSERT ON sre_live_budget_reservations",s)
        self.assertNotIn("DELETE FROM sre_live_budget_events",s)
        self.assertNotIn("UPDATE sre_live_opus_receipts",s)

    def test_separately_authorized_primary_s3_payload_purge_certificate(self):
        s=self.sql
        self.assertIn("CREATE TABLE sre_source_payload_purge_certificates",s)
        self.assertIn("PRIMARY KEY(project_id,content_version_id)",s)
        self.assertIn("REFERENCES sre_live_source_tombstones",s)
        self.assertIn("M34 cannot attest S3 purge without a safe absent primary row",s)
        self.assertIn("pin.reason_code NOT IN",s)
        self.assertIn("'privacy_delete','source_deleted','retention_expired'",s)
        self.assertIn("FROM sre_live_items i",s)
        self.assertIn("BEFORE UPDATE OR DELETE ON sre_source_payload_purge_certificates",s)
        self.assertIn("REVOKE ALL ON sre_source_payload_purge_certificates FROM PUBLIC",s)
        self.assertNotIn("DELETE FROM sre_live_items",s)
        self.assertNotIn("DELETE FROM sre_live_opus_dispatch_intents",s)

    def test_durable_outbox_and_bounded_ack_require_cancel_or_terminal(self):
        s=self.sql
        self.assertIn("CREATE TABLE sre_live_revocation_outbox",s)
        self.assertIn("UNIQUE(project_id,content_version_id,run_id)",s)
        self.assertIn("FOREIGN KEY(run_id,project_id)",s)
        self.assertIn("INSERT INTO sre_live_revocation_outbox",s)
        self.assertIn("M34 outbox cannot acknowledge an active run",s)
        self.assertIn("r.state='unknown' AND r.cancel_requested=TRUE",s)
        self.assertIn("r.state IN ('cancelled','completed','partial','failed')",s)
        self.assertNotIn("r.cancel_requested=TRUE OR",s)
        self.assertIn("WHERE state='pending'",s)
        self.assertIn("REVOKE ALL ON sre_live_revocation_outbox FROM PUBLIC",s)

    def test_immediate_atomic_run_fail_stop_and_no_reset(self):
        s=self.sql
        self.assertIn("UPDATE sre_live_runs r",s)
        self.assertIn("SET cancel_requested=TRUE,updated_at=clock_timestamp()",s)
        self.assertIn("UPDATE sre_offline_runs r",s)
        self.assertIn("UPDATE sre_classification_runs legacy",s)
        self.assertIn("M34 revoked LIVE source cannot restart",s)
        self.assertIn("M34 revoked S4 run cannot restart",s)
        self.assertIn("BEFORE UPDATE ON sre_live_runs",s)
        self.assertIn("BEFORE UPDATE ON sre_offline_runs",s)
        self.assertIn("r.state='unknown' AND r.cancel_requested=TRUE",s)
        self.assertIn("M34 outbox cannot acknowledge an active run",s)

    def test_read_and_all_new_derived_writes_blocked(self):
        s=self.sql
        self.assertIn("CREATE OR REPLACE VIEW sre_live_classification_results",s)
        self.assertIn("NOT EXISTS (\n    SELECT 1 FROM sre_live_source_tombstones revoked",s)
        for table in ("sre_live_items","sre_offline_items",
                      "sre_live_opus_quotes","sre_live_opus_dispatch_intents",
                      "sre_live_verified_pair_results","sre_live_human_reviews",
                      "sre_live_gold_benchmark_snapshots"):
            self.assertIn("BEFORE INSERT ON "+table,s)
        self.assertIn("M34 revoked source cannot create derived result",s)
        self.assertIn("M34 revoked source cannot enter gold snapshot",s)

    def test_no_raw_content_and_next_migration_prefix(self):
        table=self.sql[self.sql.index("CREATE TABLE sre_live_source_tombstones"):
                       self.sql.index("CREATE FUNCTION sre_m34_guard_tombstone")]
        for raw in ("raw_json","prompt_text","transcript_text",
                    "api_key","model_output","video_url"):
            self.assertNotIn(raw,table)
        self.assertIn("068_sre_live_source_revocation.sql",build_manifest())
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"069")


if __name__=="__main__":
    unittest.main()
