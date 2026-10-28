"""M28 PostgreSQL financial receipt schema cannot fake free spending."""
from pathlib import Path
import unittest

from intelligence.relevance.migration_guard import assess_applied,build_manifest

SQL=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"/"062_sre_live_opus_receipts.sql"


class M28ReceiptSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=SQL.read_text(encoding="utf-8")

    def test_original_pins_exact_m26_m25_and_append_only(self):
        s=self.sql
        self.assertIn("CREATE TABLE sre_live_opus_receipts",s)
        self.assertIn("REFERENCES sre_live_opus_dispatch_intents",s)
        self.assertIn("UNIQUE(project_id,intent_id)",s)
        self.assertIn("UNIQUE(project_id,generation_id)",s)
        self.assertIn("UNIQUE(project_id,reservation_id)",s)
        self.assertIn("charged_usd NUMERIC(24,9)",s)
        self.assertIn("charged_usd<=held_usd",s)
        self.assertIn("generation_authenticated BOOLEAN NOT NULL",s)
        self.assertIn("response_sha256 TEXT NOT NULL",s)
        self.assertIn("evidence_status TEXT NOT NULL",s)
        self.assertIn("BEFORE UPDATE OR DELETE ON sre_live_opus_receipts",s)
        self.assertIn("REVOKE ALL ON sre_live_opus_receipts FROM PUBLIC",s)
        self.assertNotIn("Authorization: Bearer",s)
        self.assertNotIn("INSERT INTO sre_classification_results",s)

    def test_account_and_reservation_settlement_in_single_receipt_insert(self):
        s=self.sql
        self.assertIn("CREATE OR REPLACE FUNCTION sre_m25_budget_account_guard()",s)
        self.assertIn("pg_trigger_depth()<2",s)
        self.assertIn("NEW.spent_usd=OLD.spent_usd+t.charged_usd",s)
        self.assertIn("CREATE OR REPLACE FUNCTION sre_m25_budget_reservation_guard()",s)
        self.assertIn("CREATE OR REPLACE FUNCTION sre_m25_budget_post_account()",s)
        self.assertIn("NEW.state='settled'",s)
        self.assertIn("reserved_usd=reserved_usd-OLD.amount_usd",s)
        self.assertIn("spent_usd=spent_usd+charged",s)
        self.assertIn("released_usd=released_usd+OLD.amount_usd-charged",s)
        self.assertIn("CREATE TRIGGER sre_m28_receipt_after_insert",s)
        self.assertIn("state='settled',settled_at=clock_timestamp()",s)
        self.assertIn("event_kind='settled' AND amount_usd>=0",s)
        self.assertIn("sre_m28_reservation_states",s)

    def test_next_prefix(self):
        self.assertIn("062_sre_live_opus_receipts.sql",build_manifest())
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"064")


if __name__=="__main__":
    unittest.main()
