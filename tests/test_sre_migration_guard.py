"""M05 migration safety: isolated, offline, no PostgreSQL or paid network."""
from __future__ import annotations

from contextlib import nullcontext
import io
from pathlib import Path
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout

from intelligence.migrate import MIGRATIONS
from intelligence.relevance.migration_guard import (
    MigrationBlocked, PINNED_SRE_GIT_BLOBS, REQUIRED_MAIN,
    assess_applied, build_manifest, main, preflight_read_only,
)


class FakeResult:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)

    def fetchall(self):
        return self.value


class FakeConnection:
    def __init__(self, ledger=None):
        self.ledger = ledger
        self.statements = []

    def transaction(self):
        return nullcontext()

    def execute(self, statement):
        self.statements.append(statement)
        if statement.startswith("SET TRANSACTION"):
            return FakeResult(None)
        if statement.startswith("SELECT to_regclass"):
            return FakeResult("schema_migrations" if self.ledger is not None else None)
        if statement.startswith("SELECT version"):
            return FakeResult(sorted(self.ledger.items()))
        raise AssertionError("preflight attempted an unexpected SQL command: " + statement)


class SREMigrationManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = build_manifest()

    def test_full_main_qv_psi_knowledge_union_and_immutable_sre(self):
        self.assertTrue(REQUIRED_MAIN <= self.manifest.keys())
        self.assertTrue(PINNED_SRE_GIT_BLOBS.keys() <= self.manifest.keys())
        self.assertIn("016_query_validation.sql", self.manifest)
        self.assertIn("016_sre_d1_intake_rights.sql", self.manifest)
        self.assertIn("017_sre_offline_run_engine.sql", self.manifest)
        self.assertIn("053_ke_c_qualified_execution.sql", self.manifest)
        self.assertTrue(all(len(v) == 64 for v in self.manifest.values()))

    def test_fresh_database_needs_every_version(self):
        result = assess_applied(None)
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["ledger_present"])
        self.assertEqual(result["pending"], sorted(self.manifest))
        self.assertEqual(result["next_sre_migration_prefix"], "054")
        self.assertEqual(result["retroactive_additions"], [])

    def test_installed_full_schema_is_noop(self):
        result = assess_applied(self.manifest)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["pending"], [])
        self.assertEqual(result["checksum_mismatch"], [])
        self.assertEqual(result["applied_count"], len(self.manifest))

    def test_main_only_upgrade_is_explicit_retroactive_sre_addition(self):
        installed = {n: h for n, h in self.manifest.items()
                     if n not in PINNED_SRE_GIT_BLOBS}
        result = assess_applied(installed)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["pending"], sorted(PINNED_SRE_GIT_BLOBS))
        self.assertEqual(result["retroactive_additions"], sorted(PINNED_SRE_GIT_BLOBS))

    def test_unknown_deployed_version_blocks_upgrade(self):
        installed = dict(self.manifest)
        installed["054_unknown_external.sql"] = "0" * 64
        result = assess_applied(installed)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reasons"], ["unknown_applied_version"])
        self.assertEqual(result["unknown_applied"], ["054_unknown_external.sql"])

    def test_changed_applied_checksum_blocks_without_guessing(self):
        installed = dict(self.manifest)
        installed["016_query_validation.sql"] = "0" * 64
        installed["016_sre_d1_intake_rights.sql"] = "bad"
        result = assess_applied(installed)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reasons"], ["applied_checksum_mismatch"])
        self.assertEqual(result["checksum_mismatch"],
                         ["016_query_validation.sql", "016_sre_d1_intake_rights.sql"])

    def test_historical_sre_sql_change_cannot_be_rebaselined(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for p in MIGRATIONS.glob("[0-9]*.sql"):
                shutil.copyfile(p, root / p.name)
            sql = root / "017_sre_offline_run_engine.sql"
            sql.write_text(sql.read_text(encoding="utf-8") +
                           "\n-- history rewrite is forbidden\n", encoding="utf-8")
            with self.assertRaisesRegex(MigrationBlocked, "historical_sre_sql_modified"):
                build_manifest(root)

    def test_missing_main_baseline_blocks_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for p in MIGRATIONS.glob("[0-9]*.sql"):
                shutil.copyfile(p, root / p.name)
            (root / "053_ke_c_qualified_execution.sql").unlink()
            with self.assertRaisesRegex(MigrationBlocked, "missing_baseline_migrations"):
                build_manifest(root)

    def test_future_unique_sre_migration_number_follows_latest_sql(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for p in MIGRATIONS.glob("[0-9]*.sql"):
                shutil.copyfile(p, root / p.name)
            (root / "054_other_module.sql").write_text("SELECT 1;\n", encoding="utf-8")
            self.assertEqual(assess_applied(None, root=root)
                             ["next_sre_migration_prefix"], "055")

    def test_preflight_no_ledger_is_read_only_and_non_creating(self):
        conn = FakeConnection()
        result = preflight_read_only(conn)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["applied_count"], 0)
        self.assertEqual(conn.statements[0],
                         "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        self.assertEqual(len(conn.statements), 2)

    def test_preflight_with_ledger_is_read_only(self):
        conn = FakeConnection(self.manifest)
        result = preflight_read_only(conn)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["pending"], [])
        self.assertEqual(len(conn.statements), 3)
        self.assertTrue(all(s.startswith(("SET TRANSACTION", "SELECT "))
                            for s in conn.statements))

    def test_manifest_cli_no_connection_needed(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--manifest"])
        self.assertEqual(code, 0)
        self.assertIn('"status": "ready"', buf.getvalue())
        self.assertNotIn("postgresql://", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
