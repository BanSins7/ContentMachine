"""M33 additive gold cohort is immutable and source-backed, never invented."""
from pathlib import Path
import unittest
from intelligence.relevance.migration_guard import build_manifest,assess_applied

SQL=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"/"067_sre_live_gold_benchmarks.sql"

class M33GoldBenchmarkSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=SQL.read_text(encoding="utf-8")

    def test_genuine_latest_human_review_and_model_evidence_required(self):
        s=self.sql
        self.assertIn("CREATE TABLE sre_live_gold_benchmark_snapshots",s)
        self.assertIn("REFERENCES sre_live_runs(run_id,project_id)",s)
        self.assertIn("UNIQUE(project_id,run_id,snapshot_sha256)",s)
        self.assertIn("FROM sre_live_human_reviews h",s)
        self.assertIn("JOIN sre_live_verified_pair_results v",s)
        self.assertIn("JOIN sre_live_opus_receipts t",s)
        self.assertIn("JOIN sre_live_opus_dispatch_intents d",s)
        self.assertIn("h.human_verified=TRUE",s)
        self.assertIn("newer.revision>h.revision",s)
        self.assertIn("original_row.model IS DISTINCT FROM sample->>'model_decision'",s)
        self.assertIn("original_row.human_decision IS DISTINCT FROM sample->>'gold_decision'",s)
        self.assertIn("original_row.quote_sha256 IS DISTINCT FROM sample->>'quote_sha256'",s)
        self.assertIn("original_row.catalog_sha256 IS DISTINCT FROM sample->>'catalog_sha256'",s)
        self.assertIn("original_row.request_sha256 IS DISTINCT FROM sample->>'request_sha256'",s)
        self.assertIn("original_row.generation_authenticated IS NOT TRUE",s)
        self.assertIn("validated_untrusted_output",s)

    def test_freeze_one_bounded_video_group_aware_cohort(self):
        s=self.sql
        self.assertIn("jsonb_array_length(manifest) BETWEEN 1 AND 1000",s)
        self.assertIn("holdout_percent BETWEEN 10 AND 90",s)
        self.assertIn("min_evaluation_examples BETWEEN 1 AND 1000",s)
        self.assertIn("'split'",s)
        self.assertIn("NOT IN ('train','eval')",s)
        self.assertIn("M33 duplicate source category in immutable cohort",s)
        self.assertIn("jsonb_object_keys(sample))<>15",s)
        self.assertIn("M33 cancelled, unknown or non-LIVE source run",s)
        self.assertIn("BEFORE UPDATE OR DELETE ON sre_live_gold_benchmark_snapshots",s)
        self.assertIn("REVOKE ALL ON sre_live_gold_benchmark_snapshots FROM PUBLIC",s)

    def test_privacy_source_and_forward_only_migration(self):
        s=self.sql
        table=s[s.index("CREATE TABLE sre_live_gold_benchmark_snapshots"):
                s.index("CREATE INDEX sre_m33_gold_project_time")]
        for forbidden in ("raw_response","api_key","prompt_text",
                          "transcript_text","freeform_note",
                          "provider_completion"):
            self.assertNotIn(forbidden,table)
        self.assertNotIn("UPDATE sre_live_human_reviews",s)
        self.assertNotIn("UPDATE sre_live_verified_pair_results",s)
        self.assertIn("067_sre_live_gold_benchmarks.sql",build_manifest())
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"071")


if __name__=="__main__":
    unittest.main()
