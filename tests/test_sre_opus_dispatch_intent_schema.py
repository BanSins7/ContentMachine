"""M26 immutable one-way per-request intent schema gates."""
from pathlib import Path
import unittest

from intelligence.relevance.migration_guard import build_manifest,assess_applied

FILE=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"/"061_sre_live_opus_dispatch_intents.sql"


class M26IntentSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=FILE.read_text(encoding="utf-8")

    def test_composite_fks_commit_fencing_money_and_paid_outcome(self):
        s=self.sql
        self.assertIn("CREATE TABLE sre_live_opus_dispatch_intents",s)
        self.assertIn("FOREIGN KEY (project_id,run_id,item_id,quote_sha256,reservation_id)",s)
        self.assertIn("REFERENCES sre_live_budget_reservations",s)
        self.assertIn("REFERENCES sre_live_attempt_fences",s)
        self.assertIn("REFERENCES sre_live_opus_consents",s)
        self.assertIn("REFERENCES sre_live_batch_members",s)
        self.assertIn("UNIQUE (project_id,run_id,item_id,request_sha256)",s)
        self.assertIn("UNIQUE (project_id,reservation_id)",s)
        self.assertIn("reserved_usd NUMERIC(24,9)",s)
        self.assertIn("model_id='anthropic/claude-opus-5.5'",s)
        self.assertIn("proof.attempt_state <> 'dispatch_intent'",s)
        self.assertIn("proof.current_generation <> NEW.lease_generation",s)
        self.assertIn("proof.budget_state <> 'reserved'",s)
        self.assertIn("proof.revoked_at IS NOT NULL",s)
        self.assertIn("proof.quote_expires <= clock_timestamp()",s)
        self.assertIn("M26 outstanding or prior Opus send intent",s)
        self.assertIn("BEFORE UPDATE OR DELETE ON sre_live_opus_dispatch_intents",s)
        self.assertIn("REVOKE ALL ON sre_live_opus_dispatch_intents FROM PUBLIC",s)
        self.assertNotIn("httpx",s)
        self.assertNotIn("OPENROUTER_API_KEY",s)
        self.assertNotIn("INSERT INTO sre_classification_results",s)

    def test_required_061_and_next_062(self):
        manifest=build_manifest()
        self.assertIn("061_sre_live_opus_dispatch_intents.sql",manifest)
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"062")


if __name__=="__main__":
    unittest.main()
