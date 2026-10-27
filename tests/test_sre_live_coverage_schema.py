"""M13 read-only SQL audit. Integration in disposable PG17 validates behavior."""
from __future__ import annotations

from pathlib import Path
import unittest

from intelligence.relevance.migration_guard import build_manifest, assess_applied

SQL_PATH=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"/"055_sre_live_batch_coverage.sql"


class M13CoverageDDL(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=SQL_PATH.read_text(encoding="utf-8")

    def test_additive_owner_fk_and_immutable_coverage(self):
        s=self.sql
        self.assertIn("CREATE TABLE sre_live_batch_members",s)
        self.assertIn("CREATE TABLE sre_live_expected_pairs",s)
        self.assertIn("UNIQUE(run_id,item_id)",s)
        self.assertIn("PRIMARY KEY(run_id,item_id,category_key)",s)
        self.assertIn("REFERENCES sre_live_batches(batch_id,run_id,project_id)",s)
        self.assertIn("REFERENCES sre_live_items(id,run_id,project_id)",s)
        self.assertIn("REFERENCES sre_live_batch_members(batch_id,run_id,project_id,item_id)",s)
        self.assertIn("DEFAULT 'not_evaluated'",s)
        self.assertIn("'blocked_rights','blocked_budget'",s)
        self.assertIn("sre_m13_guard_settlement",s)
        for tbl in ("sre_live_items","sre_live_batches","sre_live_runs"):
            self.assertIn("BEFORE UPDATE ON "+tbl,s)
        for step in ("sre_m13_member_insert","sre_m13_pair_insert",
                     "sre_m13_member_update","sre_m13_pair_delete"):
            self.assertIn("CREATE TRIGGER "+step,s)
        for term in ("SELECT count(*)","total_pairs<>expected_pairs",
                     "unresolved_pairs>0","evaluated_pairs=0"):
            self.assertIn(term,s)
        self.assertNotIn("DROP TABLE ",s.upper())
        self.assertNotIn("TRUNCATE ",s.upper())
        self.assertNotIn("INSERT INTO sre_classification_results",s)
        self.assertNotIn("OPENROUTER",s.upper())
        self.assertIn("REVOKE ALL ON sre_live_batch_members,sre_live_expected_pairs FROM PUBLIC",s)

    def test_unapplied_forward_only_version_and_next_prefix(self):
        manifest=build_manifest()
        self.assertIn("055_sre_live_batch_coverage.sql",manifest)
        self.assertIn("054_sre_live_core.sql",manifest)
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"063")


if __name__=="__main__":
    unittest.main()
