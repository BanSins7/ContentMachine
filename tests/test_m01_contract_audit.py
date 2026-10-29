"""Offline deterministic M01 inventory regression; no database or network."""
from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.m01_contract_audit import (
    MANIFEST, load_manifest, scan_migrations, scan_contracts,
    scan_guards, scan_repository, check_disposable_ledger,
)


class TestM01ContractAudit(unittest.TestCase):
    def test_snapshot_is_pinned_and_has_every_handoff(self):
        manifest = load_manifest()
        heads = {p["name"]: p["head"] for p in manifest["branch_heads"]}
        self.assertEqual(len(manifest["edges"]), 12)
        self.assertIn("main", heads)
        self.assertIn("feature/sre-production-v1-20261023", heads)
        self.assertIn("feature/patterns-insights-full-module", heads)
        self.assertIn("integration/full-pipeline-to-pie-audit-20261029", heads)
        for name, sha in heads.items():
            self.assertRegex(sha, r"^[0-9a-f]{40}$", name)
        self.assertEqual(len(set(e["id"] for e in manifest["edges"])), 12)

    def test_every_handoff_has_producer_consumer_schema_and_oracle(self):
        result = scan_contracts(load_manifest())
        self.assertEqual(result["count"], 12)
        self.assertEqual(result["problems"], [])
        for item in result["edges"]:
            self.assertTrue(item["source_paths_present"], item["edge_id"])
            self.assertTrue(item["test_oracle_defined"], item["edge_id"])
            self.assertFalse(item["oracle_executed"])

    def test_migration_filename_semantics_and_inventory(self):
        inv = scan_migrations()
        frozen = json.loads((
            MANIFEST.parent / "M01_SQL_FILE_INVENTORY_20261029.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(inv["count"], frozen["count"])
        self.assertEqual(set(inv["ordered_filenames"]),
                         {item["filename"] for item in frozen["files"]})
        self.assertFalse(inv["duplicate_full_filenames"])
        self.assertGreater(inv["shared_numeric_prefixes"].get("054", 0), 1)
        self.assertRegex(inv["sha256_by_filename"]
                         ["062_sre_live_opus_receipts.sql"], r"^[0-9a-f]{64}$")

    def test_guards_are_present_but_do_not_mean_e2e(self):
        self.assertTrue(scan_guards()["source_guards_present"])
        report = scan_repository()
        self.assertTrue(report["static_inventory_complete"])
        self.assertEqual(report["findings"], [])
        self.assertFalse(report["postgres_sql_migrations_executed"])
        self.assertFalse(report["real_pipeline_ever_validated_by_this_audit"])
        self.assertFalse(report["uat_or_production_pass"])

    def test_db_ledger_requires_disposable_opt_in(self):
        inv = scan_migrations()
        with patch.dict(os.environ, {"CM_M01_DISPOSABLE_DB":"NO",
                                     "CM_M01_DB_DSN":"postgresql://not-used"}):
            result = check_disposable_ledger(inv)
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["reason"], "CM_M01_DISPOSABLE_DB_must_be_YES")

    def test_db_ledger_denies_missing_dsn_without_network(self):
        inv = scan_migrations()
        env = {key:value for key,value in os.environ.items()
               if key != "CM_M01_DB_DSN"}
        env["CM_M01_DISPOSABLE_DB"] = "YES"
        with patch.dict(os.environ, env, clear=True):
            result = check_disposable_ledger(inv)
        self.assertEqual(result["reason"], "CM_M01_DB_DSN_not_configured")

    def test_main_pr42_065_hotfix_is_pinned(self):
        from scripts.m01_contract_audit import MIGRATIONS
        items=json.loads((MANIFEST.parent/"M01_SQL_FILE_INVENTORY_20261029.json").read_text(encoding="utf-8"))
        blob=next(x for x in items["files"] if x["filename"]=="065_sre_live_diagnostic_checkpoints.sql")
        self.assertEqual(blob["git_blob_sha"],"ac6527ce3463d65c8f04a39819ef12f25380cbb7")
        sql=(MIGRATIONS/"065_sre_live_diagnostic_checkpoints.sql").read_text(encoding="utf-8")
        self.assertIn("    END) THEN",sql)

    def test_sql_062_original_corruption_not_in_integrated_source(self):
        from scripts.m01_contract_audit import MIGRATIONS
        sql = (MIGRATIONS / "062_sre_live_opus_receipts.sql").read_text(
            encoding="utf-8")
        self.assertNotIn("evidence_sha256 ~ '^[0-9a-f]{64} BIGINT", sql)
        self.assertEqual(sql.count("CREATE TABLE sre_live_opus_receipts"), 1)
        self.assertEqual(sql.count("CREATE TRIGGER sre_m28_receipt_insert_guard"), 1)

if __name__ == "__main__":
    unittest.main()
