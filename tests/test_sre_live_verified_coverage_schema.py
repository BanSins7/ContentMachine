"""M30 SQL cannot complete with fake/paid-only/partial C9 coverage."""
from pathlib import Path
import unittest
from intelligence.relevance.migration_guard import assess_applied,build_manifest

SQL=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"/"064_sre_live_verified_coverage.sql"


class M30VerifiedCoverageSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ddl=SQL.read_text(encoding="utf-8")

    def test_one_original_pair_to_one_verified_paid_evidence(self):
        s=self.ddl
        self.assertIn("CREATE TABLE sre_live_verified_pair_results",s)
        self.assertIn("UNIQUE(project_id,run_id,item_id,category_key)",s)
        self.assertIn("UNIQUE(project_id,receipt_id)",s)
        self.assertIn("REFERENCES sre_live_expected_pairs",s)
        self.assertIn("REFERENCES sre_live_opus_receipts",s)
        self.assertIn("generation_authenticated IS NOT TRUE",s)
        self.assertIn("validated_untrusted_output",s)
        self.assertIn("NEW.verified_chunk_sha256",s)
        self.assertIn("count_by_category<>1",s)
        self.assertIn("M30 cannot finish a split/multi-request category",s)
        self.assertIn("BEFORE UPDATE OR DELETE ON sre_live_verified_pair_results",s)
        self.assertIn("REVOKE ALL ON sre_live_verified_pair_results FROM PUBLIC",s)

    def test_pair_cannot_be_marked_evaluated_without_proof(self):
        s=self.ddl
        self.assertIn("DROP CONSTRAINT IF EXISTS sre_live_expected_pairs_state_check",s)
        self.assertIn("sre_m30_expected_pair_states",s)
        self.assertIn("BEFORE UPDATE ON sre_live_expected_pairs",s)
        self.assertIn("v.decision=NEW.state",s)
        self.assertIn("M30 cancelled or inactive pair cannot acquire a decision",s)
        self.assertIn("r.cancel_requested=FALSE AND i.state='running'",s)
        self.assertIn("t.evidence_status='validated_untrusted_output'",s)
        self.assertIn("t.generation_authenticated IS TRUE",s)

    def test_item_batch_run_completed_partial_guards(self):
        s=self.ddl
        for table in ("sre_live_items","sre_live_batches","sre_live_runs"):
            self.assertIn("BEFORE UPDATE ON "+table,s)
        self.assertIn("proven_pairs<>evaluated_pairs",s)
        self.assertIn("evaluated_pairs<>expected_pairs",s)
        self.assertIn("evaluated_pairs=0 OR evaluated_pairs>=expected_pairs",s)
        self.assertIn("M30 partial run cannot hide active unfinished item",s)
        self.assertIn("M30 partial batch cannot hide active item",s)
        self.assertIn("M30 unresolved possibly paid provider intent",s)
        self.assertIn("M30 terminal state cannot be downgraded or rewritten",s)
        self.assertIn("M30 cancelled run cannot claim terminal classification",s)
        self.assertIn("M30 cancelled or failed original run cannot publish results",s)
        self.assertIn("NEW.finished_items<>NEW.expected_items",s)
        self.assertIn("NEW.blocked_items<>0",s)
        self.assertIn("b.state<>'completed'",s)
        self.assertNotIn("INSERT INTO sre_classification_results",s)
        self.assertNotIn("https://",s)
        self.assertNotIn("CREATE OR REPLACE FUNCTION sre_m13_",s)

    def test_forward_only_required_064_next_065(self):
        self.assertIn("064_sre_live_verified_coverage.sql",build_manifest())
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"067")


if __name__=="__main__":
    unittest.main()
