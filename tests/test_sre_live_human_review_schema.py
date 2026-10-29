"""M32 original-model-preserving immutable human-review SQL checks."""
from pathlib import Path
import unittest
from intelligence.relevance.migration_guard import build_manifest,assess_applied

SQL=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"/"066_sre_live_human_reviews.sql"

class M32HumanReviewSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=SQL.read_text(encoding="utf-8")

    def test_immutable_review_model_receipt_and_prior_lineage(self):
        s=self.sql
        self.assertIn("CREATE TABLE sre_live_human_reviews",s)
        self.assertIn("REFERENCES sre_live_verified_pair_results",s)
        self.assertIn("REFERENCES sre_live_human_reviews",s)
        self.assertIn("UNIQUE(project_id,run_id,item_id,category_key,revision)",s)
        self.assertIn("UNIQUE(project_id,prior_review_id)",s)
        self.assertIn("human_verified IS TRUE",s)
        self.assertIn("original_model_decision",s)
        self.assertIn("quote_sha256 TEXT NOT NULL",s)
        self.assertIn("catalog_sha256 TEXT NOT NULL",s)
        self.assertIn("original_row.quote_sha256 IS DISTINCT FROM NEW.quote_sha256",s)
        self.assertIn("original_row.catalog_sha256 IS DISTINCT FROM NEW.catalog_sha256",s)
        self.assertIn("sre_m32_guard_human_review()",s)
        self.assertIn("M32 stale/foreign/forked supersession denied",s)
        self.assertIn("M32 cannot rewrite existing human review chain",s)
        self.assertIn("BEFORE UPDATE OR DELETE ON sre_live_human_reviews",s)
        self.assertIn("REVOKE ALL ON sre_live_human_reviews FROM PUBLIC",s)

    def test_correction_cannot_erase_model_or_act_on_cancelled_run(self):
        s=self.sql
        self.assertIn("NEW.original_model_decision",s)
        self.assertIn("NEW.human_decision<>NEW.original_model_decision",s)
        self.assertIn("NEW.human_decision=prior_row.human_decision",s)
        self.assertIn("original_row.cancel_requested IS TRUE",s)
        self.assertIn("original_row.current_pair_state IS DISTINCT FROM",s)
        self.assertIn("original_row.evidence_status<>'validated_untrusted_output'",s)
        self.assertNotIn("UPDATE sre_live_verified_pair_results",s)
        self.assertNotIn("UPDATE sre_live_expected_pairs",s)
        self.assertNotIn("INSERT INTO sre_classification_results",s)

    def test_privacy_and_next_schema_migration(self):
        s=self.sql.lower()
        tbl=s[s.index("create table sre_live_human_reviews"):s.index("create index sre_m32_human_queue")]
        for forbidden in (
            "note_body","prompt","transcript","raw_source",
            "provider_response","api_key","reason_text",
        ):
            self.assertNotIn(forbidden,tbl)
        self.assertIn("066_sre_live_human_reviews.sql",build_manifest())
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"069")


if __name__=="__main__":
    unittest.main()
