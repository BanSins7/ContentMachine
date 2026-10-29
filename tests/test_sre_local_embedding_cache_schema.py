"""M18 pure additive DDL tests. Real enforcement runs on disposable PG17."""
from __future__ import annotations

from pathlib import Path
import unittest

from intelligence.relevance.migration_guard import build_manifest,assess_applied

SCHEMA=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"/"057_sre_local_embedding_cache.sql"


class M18CacheSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=SCHEMA.read_text(encoding="utf-8")

    def test_full_project_taxonomy_model_content_pin_and_ttl(self):
        sql=self.sql
        self.assertIn("CREATE TABLE sre_local_embedding_cache",sql)
        self.assertIn("project_id TEXT NOT NULL REFERENCES sre_workspaces(project_id)",sql)
        self.assertIn("FOREIGN KEY(project_id,publication_id,taxonomy_version_id)",sql)
        self.assertIn("FOREIGN KEY(content_version_id,project_id,video_id,source_id,",sql)
        self.assertIn("source_policy_version_id,content_sha256)",sql)
        self.assertIn("ON DELETE CASCADE",sql)
        for col in ("taxonomy_sha256","contexts_sha256",
                    "model_identity_sha256","content_sha256","input_sha256",
                    "vectors_sha256"):
            self.assertIn(col+" TEXT NOT NULL",sql)
        self.assertIn("UNIQUE(project_id,publication_id,taxonomy_version_id,taxonomy_sha256",sql)
        self.assertIn("vector_count BETWEEN 2 AND 97",sql)
        self.assertIn("dimensions BETWEEN 1 AND 4096",sql)
        self.assertIn("octet_length(vectors::text)<=2000000",sql)
        self.assertIn("interval '24 hours'",sql)
        self.assertIn("sre_m18_entry_insert",sql)
        self.assertIn("sre_m18_entry_update",sql)
        self.assertIn("M18 embedding cache entries are immutable",sql)
        self.assertIn("REVOKE ALL ON sre_local_embedding_cache FROM PUBLIC",sql)
        for destructive in ("DROP TABLE","TRUNCATE","INSERT INTO sre_classification_results",
                            "OPENROUTER"):
            self.assertNotIn(destructive,sql.upper())
        # Revocation/deletion is explicitly allowed; immutable UPDATE is not.

    def test_required_new_057_and_next_058(self):
        names=build_manifest()
        self.assertIn("057_sre_local_embedding_cache.sql",names)
        for original in ("016_sre_d1_intake_rights.sql",
                         "017_sre_offline_run_engine.sql",
                         "054_sre_live_core.sql",
                         "055_sre_live_batch_coverage.sql",
                         "056_sre_live_dispatch_fences.sql"):
            self.assertIn(original,names)
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"069")


if __name__=="__main__":
    unittest.main()
