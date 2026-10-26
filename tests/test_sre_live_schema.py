"""M11 DDL audit without DB/network: standalone LIVE mode and immutable pins.

The actual PostgreSQL17 FK/trigger behavior is exercised in test_sre_offline_pg
and fresh/main-only upgrade in test_sre_migration_guard_pg. This is not an
alternative to a live PostgreSQL acceptance result.
"""
from pathlib import Path
import hashlib
import re
import unittest

from intelligence.relevance.migration_guard import (
    PINNED_SRE_GIT_BLOBS, build_manifest, assess_applied,
)

ROOT=Path(__file__).resolve().parents[1]
SQL_PATH=ROOT/"intelligence"/"migrations"/"054_sre_live_core.sql"


class LiveSchemaContractUnit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=SQL_PATH.read_text(encoding="utf-8")

    def test_sequential_migration_and_forward_only_checksum(self):
        manifest=build_manifest()
        self.assertIn(SQL_PATH.name,manifest)
        self.assertEqual(
            manifest[SQL_PATH.name],
            hashlib.sha256(self.sql.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"055")
        self.assertEqual(
            sorted(PINNED_SRE_GIT_BLOBS),
            ["016_sre_d1_intake_rights.sql","017_sre_offline_run_engine.sql"],
        )

    def test_three_live_only_tables_and_structural_fks(self):
        for table in ("sre_live_runs","sre_live_items","sre_live_batches"):
            self.assertRegex(self.sql,r"CREATE TABLE\s+"+table+r"\s*\(")
        self.assertIn("REFERENCES tb_sre_taxonomy_links(",self.sql)
        self.assertIn("REFERENCES sre_pipeline_configs(id,project_id)",self.sql)
        self.assertIn("REFERENCES sre_live_runs(run_id,project_id)",self.sql)
        self.assertIn("REFERENCES sre_project_discoveries(project_id,discovery_id)",self.sql)
        self.assertIn("REFERENCES sre_content_versions(id,project_id,video_id,source_id,",self.sql)
        self.assertIn("REFERENCES source_video_metadata(id)",self.sql)
        self.assertIn("CHECK(execution_mode='live')",self.sql)
        self.assertIn("UNIQUE(project_id,idempotency_key)",self.sql)
        self.assertIn("UNIQUE(run_id,video_id)",self.sql)
        self.assertIn("intake_fingerprint IS NULL",self.sql)
        self.assertIn("c.provenance IS DISTINCT FROM NEW.origin",self.sql)

    def test_immutable_manifest_sha_and_lease_guards(self):
        for guard in ("sre_m11_guard_run","sre_m11_guard_item",
                      "sre_m11_guard_batch","sre_m11_check_content_origin",
                      "sre_m11_block_delete"):
            self.assertRegex(self.sql,r"CREATE FUNCTION "+guard+r"\(")
        self.assertGreaterEqual(self.sql.count("NEW.lease_generation<OLD.lease_generation"),3)
        self.assertGreaterEqual(self.sql.count("NEW.worker_token IS DISTINCT FROM OLD.worker_token"),3)
        for field in ("taxonomy_sha256","pipeline_sha256","manifest_sha256",
                      "content_sha256","source_policy_version_id",
                      "metadata_observation_id","content_revision","input_sha256"):
            self.assertIn(field,self.sql)
        for table in ("sre_live_runs","sre_live_items","sre_live_batches"):
            self.assertRegex(
                self.sql,
                r"CREATE TRIGGER sre_m11_no_\w+_delete BEFORE DELETE ON "+table,
            )
        for name in ("origin","run","item","batch","delete"):
            self.assertIn("END; $sre_m11_"+name+"$;",self.sql)

    def test_no_paid_execution_or_prior_schema_destructive_rewrites(self):
        for forbidden in (
            "DROP TABLE ","DROP COLUMN ","TRUNCATE ","OPENROUTER",
            "CREATE TABLE IF NOT EXISTS sre_offline_runs",
            "INSERT INTO sre_classification_results",
            "INSERT INTO sre_stage_executions",
        ):
            self.assertNotIn(forbidden,self.sql.upper())
        # Creating the new service namespace, indexes and functions is allowed.
        self.assertEqual(self.sql.count("CREATE TABLE sre_live_"),3)
        self.assertEqual(len(re.findall(r"CREATE TRIGGER sre_m11_",self.sql)),7)


if __name__=="__main__":
    unittest.main()
