"""M23 immutable PostgreSQL quote schema and forward-only migration guards."""
from __future__ import annotations
from pathlib import Path
import unittest
from intelligence.relevance.migration_guard import build_manifest,assess_applied

SCHEMA=(Path(__file__).resolve().parents[1]/"intelligence"/"migrations"/
        "058_sre_live_opus_quotes.sql")


class M23QuoteSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=SCHEMA.read_text(encoding="utf-8")

    def test_project_run_item_foreign_keys_exact_sha_and_decimal_immutability(self):
        sql=self.sql
        self.assertIn("CREATE TABLE sre_live_opus_quotes",sql)
        self.assertIn("FOREIGN KEY(run_id,project_id)",sql)
        self.assertIn("REFERENCES sre_live_runs(run_id,project_id) ON DELETE CASCADE",sql)
        self.assertIn("FOREIGN KEY(item_id,run_id,project_id)",sql)
        self.assertIn("REFERENCES sre_live_items(id,run_id,project_id) ON DELETE CASCADE",sql)
        self.assertIn("UNIQUE(project_id,quote_sha256)",sql)
        self.assertIn("NUMERIC(24,9)",sql)
        self.assertIn("max_usd>0",sql)
        self.assertIn("min_usd=0",sql)
        self.assertIn("issued_at + INTERVAL '300 seconds'",sql)
        self.assertIn("quote->>'batch_manifest_sha256'=batch_manifest_sha256",sql)
        self.assertIn("quote->>'catalog_sha256'=catalog_sha256",sql)
        self.assertIn("quote->>'requires_dedicated_sre_consent'='true'",sql)
        self.assertIn("quote->>'requires_atomic_budget_reservation'='true'",sql)
        self.assertIn("quote->>'dispatch_allowed'='false'",sql)
        self.assertIn("quote->>'paid_calls'='0'",sql)
        self.assertIn("sre_m23_quote_no_update",sql)
        self.assertNotIn("sre_m23_quote_no_delete",sql)
        self.assertIn("REVOKE ALL ON sre_live_opus_quotes FROM PUBLIC",sql)
        for forbidden in ("DROP TABLE","TRUNCATE","INSERT INTO sre_classification_results",
                          "INSERT INTO sre_live_attempt_fences"):
            self.assertNotIn(forbidden,sql.upper())

    def test_next_059_and_058_required(self):
        manifest=build_manifest()
        self.assertIn("058_sre_live_opus_quotes.sql",manifest)
        self.assertIn("057_sre_local_embedding_cache.sql",manifest)
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"066")


if __name__=="__main__":
    unittest.main()
