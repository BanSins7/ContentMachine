"""M29 forward-only immutable provider outcome audit and no-repost fence."""
from pathlib import Path
import unittest

from intelligence.relevance.migration_guard import assess_applied,build_manifest

FILE=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"/"063_sre_live_opus_reconciliation.sql"


class M29ReconciliationSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=FILE.read_text(encoding="utf-8")

    def test_exact_m26_receipt_fks_and_unresolved_vs_confirmed(self):
        s=self.sql
        self.assertIn("CREATE TABLE sre_live_opus_reconciliation_events",s)
        self.assertIn("REFERENCES sre_live_opus_dispatch_intents",s)
        self.assertIn("REFERENCES sre_live_opus_receipts",s)
        self.assertIn("ON DELETE RESTRICT",s)
        self.assertIn("unknown_no_generation_id",s)
        self.assertIn("generation_lookup_unverified",s)
        self.assertIn("generation_accounted",s)
        self.assertIn("sre_m29_one_confirmed_receipt",s)
        self.assertIn("sre_m29_one_event_per_reason",s)
        self.assertIn("state_info.reserve_state<>'settled'",s)
        self.assertIn("state_info.real_generation IS DISTINCT FROM NEW.generation_id",s)
        self.assertIn("state_info.generation_authenticated IS NOT TRUE",s)
        self.assertIn("state_info.reserve_state<>'reserved'",s)
        self.assertIn("BEFORE UPDATE OR DELETE ON sre_live_opus_reconciliation_events",s)
        self.assertIn("REVOKE ALL ON sre_live_opus_reconciliation_events FROM PUBLIC",s)
        self.assertNotIn("HTTP POST",s.split("CREATE TABLE",1)[-1])
        self.assertNotIn("UPDATE sre_live_runs SET state='running'",s)
        self.assertNotIn("INSERT INTO sre_classification_results",s)

    def test_next_prefix_and_required_forward_only_m29(self):
        self.assertIn("063_sre_live_opus_reconciliation.sql",build_manifest())
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"067")


if __name__=="__main__":
    unittest.main()
