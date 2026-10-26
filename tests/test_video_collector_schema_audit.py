"""VC-MOD-02: read-only migration preflight regression tests."""
from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.collector_schema_audit import audit_history

BASE = {
    "054_collector_planning.sql": b"contents 054",
    "055_collector_worker.sql": b"contents 055",
    "056_collector_qv_manifest.sql": b"contents 056",
    "057_collector_discovery_outbox.sql": b"contents 057",
    "016_query_validation.sql": b"contents qv",
    "053_ke_c_qualified_execution.sql": b"contents ke",
}


class CollectorMigrationAuditTests(unittest.TestCase):
    def use_files(self, records, files=BASE):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, content in files.items():
                (root / name).write_bytes(content)
            return audit_history(records, root)

    @staticmethod
    def sha(name):
        return hashlib.sha256(BASE[name]).hexdigest()

    def test_empty_database_safe_but_pending(self):
        a = self.use_files([])
        self.assertTrue(a["safe_to_run_standard_migrator"])
        self.assertEqual(a["collector_versions_pending"],
                         sorted(k for k in BASE if k.startswith(("054_", "055_", "056_", "057_"))))

    def test_current_schema_checksums_safe(self):
        rows = [(k, self.sha(k)) for k in BASE]
        a = self.use_files(rows)
        self.assertEqual(a["blockers"], [])
        self.assertEqual(a["collector_versions_pending"], [])

    def test_detects_old_collector_branch_install(self):
        a = self.use_files([("016_collector_planning.sql", "0"*64),
                            ("017_collector_worker.sql", "1"*64)])
        self.assertFalse(a["safe_to_run_standard_migrator"])
        self.assertEqual(a["legacy_collector_versions"],
                         ["016_collector_planning.sql", "017_collector_worker.sql"])
        self.assertIn("legacy_collector_migration_names", a["blockers"])

    def test_previously_installed_checksum_change_blocks(self):
        a = self.use_files([("054_collector_planning.sql", "0"*64)])
        self.assertFalse(a["safe_to_run_standard_migrator"])
        self.assertIn("previously_applied_checksum_mismatch", a["blockers"])
        self.assertEqual(a["changed_applied_files"], ["054_collector_planning.sql"])

    def test_missing_applied_file_blocks(self):
        a = self.use_files([("999_unknown.sql", "f"*64)])
        self.assertFalse(a["safe_to_run_standard_migrator"])
        self.assertIn("installed_migration_file_missing", a["blockers"])

    def test_collector_installed_without_main_qv_anchor_blocks(self):
        a = self.use_files([("056_collector_qv_manifest.sql",
                             self.sha("056_collector_qv_manifest.sql"))])
        self.assertFalse(a["safe_to_run_standard_migrator"])
        self.assertIn("collector_installed_without_main_anchors", a["blockers"])


if __name__ == "__main__":
    unittest.main()
