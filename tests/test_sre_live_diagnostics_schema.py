"""M31 SQL diagnostic checkpoints are project-owned, minimal and immutable."""
from __future__ import annotations
from pathlib import Path
import unittest
from intelligence.relevance.migration_guard import build_manifest,assess_applied

SQL=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"/"065_sre_live_diagnostic_checkpoints.sql"


class M31DiagnosticSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=SQL.read_text(encoding="utf-8")

    def test_project_owned_one_snapshot_and_immutable(self):
        s=self.sql
        self.assertIn("CREATE TABLE sre_live_diagnostic_checkpoints",s)
        self.assertIn("REFERENCES sre_live_runs(run_id,project_id)",s)
        self.assertIn("UNIQUE(project_id,run_id,snapshot_sha256)",s)
        self.assertIn("CREATE TRIGGER sre_m31_checkpoint_insert",s)
        self.assertIn("BEFORE UPDATE OR DELETE ON sre_live_diagnostic_checkpoints",s)
        self.assertIn("REVOKE ALL ON sre_live_diagnostic_checkpoints FROM PUBLIC",s)
        self.assertIn("M31 checkpoint counts or Decimal USD untrusted",s)

    def test_sql_recomputes_all_sensitive_financial_and_coverage_counters(self):
        s=self.sql
        for name in (
            "FROM sre_live_runs","FROM sre_live_items",
            "FROM sre_live_expected_pairs","FROM sre_live_verified_pair_results",
            "FROM sre_live_opus_dispatch_intents","FROM sre_live_opus_receipts",
            "FROM sre_live_budget_reservations","FROM sre_live_budget_events",
            "FROM sre_live_opus_reconciliation_events",
        ):
            self.assertIn(name,s)
        self.assertIn("sum(jsonb_array_length(expected_hypothesis_keys))",s)
        self.assertIn("coalesce(sum(t.charged_usd),0)",s)
        self.assertIn("run_state",s)
        self.assertIn("unsettled_intents",s)
        self.assertIn("inference_spent_usd NUMERIC(24,9)",s)
        self.assertIn("reserved_usd NUMERIC(24,9)",s)
        self.assertIn("M31 checkpoint alert priority is not source-computed",s)

    def test_does_not_store_raw_prompts_messages_or_provider_credentials(self):
        start=self.sql.index("CREATE TABLE sre_live_diagnostic_checkpoints")
        end=self.sql.index("CREATE INDEX sre_m31_diagnostics_project_time")
        fields=self.sql[start:end].lower()
        for forbidden in (
            "prompt json","raw_response","source_text","api_key",
            "transcript","provider_response","message_body",
            "reason text","evidence json",
        ):
            self.assertNotIn(forbidden,fields)
        self.assertIn("actor_id TEXT",self.sql)
        self.assertNotIn("HTTP POST",self.sql.split("CREATE TABLE",1)[-1])

    def test_migration_065_is_required_and_next_066(self):
        self.assertIn("065_sre_live_diagnostic_checkpoints.sql",build_manifest())
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"067")


if __name__=="__main__":
    unittest.main()
