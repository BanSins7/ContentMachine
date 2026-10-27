"""M25 isolated forward-only money ledger schema/guard QA."""
from pathlib import Path
import unittest
from intelligence.relevance.migration_guard import build_manifest,assess_applied

PATH=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"/"060_sre_live_budget_ledger.sql"


class M25BudgetSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=PATH.read_text(encoding="utf-8")

    def test_decimal_cap_atomic_insert_and_no_double_reserve(self):
        sql=self.sql
        self.assertIn("CREATE TABLE sre_live_budget_accounts",sql)
        self.assertIn("CREATE TABLE sre_live_budget_reservations",sql)
        self.assertIn("CREATE TABLE sre_live_budget_events",sql)
        self.assertIn("NUMERIC(24,9)",sql)
        self.assertIn("reserved_usd+spent_usd<=cap_usd",sql)
        self.assertIn("ON DELETE CASCADE",sql)
        self.assertIn("UNIQUE(project_id,consent_id)",sql)
        self.assertIn("sre_m25_one_active_item",sql)
        self.assertIn("AFTER INSERT ON sre_live_budget_reservations",sql)
        self.assertIn("cap_usd>=spent_usd+reserved_usd+NEW.amount_usd",sql)
        self.assertIn("FOR SHARE OF c,q",sql)
        self.assertIn("pin.revoked_at IS NOT NULL",sql)
        self.assertIn("pin.expires_at<=clock_timestamp()",sql)
        self.assertIn("NEW.amount_usd",sql)
        self.assertIn("'dispatch_intent','unknown','completed','failed_known'",sql)
        self.assertIn("M25 possible paid dispatch: retain reserve",sql)
        self.assertIn("one-way release",sql)
        self.assertIn("REVOKE ALL ON sre_live_budget_accounts FROM PUBLIC",sql)
        self.assertIn("REVOKE ALL ON sre_live_budget_events FROM PUBLIC",sql)
        self.assertNotIn("INSERT INTO sre_live_attempt_fences",sql)
        self.assertNotIn("UPDATE sre_live_runs",sql)
        self.assertNotIn("httpx",sql)

    def test_required_060_and_next_061(self):
        m=build_manifest()
        self.assertIn("060_sre_live_budget_ledger.sql",m)
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"061")


if __name__=="__main__":
    unittest.main()
