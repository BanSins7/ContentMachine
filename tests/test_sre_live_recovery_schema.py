"""M15 additive SQL audit; actual triggers validated by PG17 suite."""
from __future__ import annotations

from pathlib import Path
import unittest

from intelligence.relevance.migration_guard import build_manifest,assess_applied

MIGRATION=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"/"056_sre_live_dispatch_fences.sql"


class M15DispatchFenceDDL(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=MIGRATION.read_text(encoding="utf-8")

    def test_fenced_append_only_attempt_log_no_paid_code(self):
        sql=self.sql
        self.assertIn("CREATE TABLE sre_live_attempt_fences",sql)
        self.assertIn("PRIMARY KEY",sql)
        self.assertIn("FOREIGN KEY (batch_id,run_id,project_id,item_id)",sql)
        self.assertIn("REFERENCES sre_live_batch_members(batch_id,run_id,project_id,item_id)",sql)
        self.assertIn("lease_generation BIGINT NOT NULL",sql)
        self.assertIn("worker_token UUID NOT NULL",sql)
        self.assertIn("UNIQUE (item_id,lease_generation)",sql)
        self.assertIn("request_sha256 TEXT NOT NULL",sql)
        self.assertIn("request_sha256 ~ '^[0-9a-f]{64}
        self.assertIn("'reserved','dispatch_intent','unknown','completed','failed_known'",sql)
        self.assertIn("created_at TIMESTAMPTZ",sql)
        self.assertIn("M15 immutable dispatch witness",sql)
        self.assertIn("M15 forbidden attempt recovery without audited reconciliation",sql)
        self.assertIn("M15 cannot dispatch with stale or invalid lease",sql)
        self.assertIn("current_lease.cancel_requested",sql)
        self.assertIn("lease_until <= clock_timestamp()",sql)
        self.assertIn("CREATE TRIGGER sre_m15_attempt_guard",sql)
        self.assertIn("CREATE TRIGGER sre_m15_attempt_no_delete",sql)
        self.assertIn("REVOKE ALL ON sre_live_attempt_fences FROM PUBLIC",sql)
        self.assertNotIn("DROP TABLE ",sql.upper())
        self.assertNotIn("TRUNCATE ",sql.upper())
        self.assertNotIn("OPENROUTER",sql.upper())

    def test_new_migration_is_required_without_touching_history(self):
        manifest=build_manifest()
        self.assertIn("056_sre_live_dispatch_fences.sql",manifest)
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"068")
        for legacy in ("016_sre_d1_intake_rights.sql",
                       "017_sre_offline_run_engine.sql",
                       "054_sre_live_core.sql",
                       "055_sre_live_batch_coverage.sql"):
            self.assertIn(legacy,manifest)


if __name__=="__main__":
    unittest.main()
",sql)
        self.assertEqual(sql.count("CREATE TABLE sre_live_attempt_fences"),1)
        self.assertEqual(sql.count("CREATE FUNCTION sre_m15_guard_attempt"),1)
        self.assertEqual(sql.count("REVOKE ALL ON sre_live_attempt_fences FROM PUBLIC"),1)
        self.assertIn("'reserved','dispatch_intent','unknown','completed','failed_known'",sql)
        self.assertIn("created_at TIMESTAMPTZ",sql)
        self.assertIn("M15 immutable dispatch witness",sql)
        self.assertIn("M15 forbidden attempt recovery without audited reconciliation",sql)
        self.assertIn("M15 cannot dispatch with stale or invalid lease",sql)
        self.assertIn("current_lease.cancel_requested",sql)
        self.assertIn("lease_until <= clock_timestamp()",sql)
        self.assertIn("CREATE TRIGGER sre_m15_attempt_guard",sql)
        self.assertIn("CREATE TRIGGER sre_m15_attempt_no_delete",sql)
        self.assertIn("REVOKE ALL ON sre_live_attempt_fences FROM PUBLIC",sql)
        self.assertNotIn("DROP TABLE ",sql.upper())
        self.assertNotIn("TRUNCATE ",sql.upper())
        self.assertNotIn("OPENROUTER",sql.upper())

    def test_new_migration_is_required_without_touching_history(self):
        manifest=build_manifest()
        self.assertIn("056_sre_live_dispatch_fences.sql",manifest)
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"068")
        for legacy in ("016_sre_d1_intake_rights.sql",
                       "017_sre_offline_run_engine.sql",
                       "054_sre_live_core.sql",
                       "055_sre_live_batch_coverage.sql"):
            self.assertIn(legacy,manifest)


if __name__=="__main__":
    unittest.main()
