"""M34 permanent source tombstone, cache purge, SQL fencing and outbox checks."""
from pathlib import Path
import unittest
from intelligence.relevance.migration_guard import build_manifest,assess_applied

SQL=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"/"068_sre_live_source_revocation.sql"

class M34RevocationSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=SQL.read_text(encoding="utf-8")

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
        self.assertIn("run.execution_mode='offline_fixture'",s)
        self.assertIn("M34 revoked source cannot be recached",s)
        self.assertIn("BEFORE INSERT ON sre_local_embedding_cache",s)
        self.assertNotIn("DELETE FROM sre_live_opus_receipts",s)
        self.assertNotIn("run.execution_mode='live'\n      AND result",s)
        self.assertNotIn("DELETE FROM sre_live_budget_reservations",s)
        self.assertNotIn("DELETE FROM sre_live_human_reviews",s)

    def test_durable_outbox_and_bounded_ack_require_cancel_or_terminal(self):
        s=self.sql
        self.assertIn("CREATE TABLE sre_live_revocation_outbox",s)
        self.assertIn("UNIQUE(project_id,content_version_id,run_id)",s)
        self.assertIn("FOREIGN KEY(run_id,project_id)",s)
        self.assertIn("INSERT INTO sre_live_revocation_outbox",s)
        self.assertIn("M34 outbox cannot acknowledge an active run",s)
        self.assertIn("r.cancel_requested=TRUE",s)
        self.assertIn("WHERE state='pending'",s)
        self.assertIn("REVOKE ALL ON sre_live_revocation_outbox FROM PUBLIC",s)

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
