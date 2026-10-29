"""SRE M42 migration 062 forensic checks: no SQL writes, no checksum bypass."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest

from intelligence.relevance.m42_migration_062_audit import (
    FILENAME, KNOWN_BROKEN_GIT_BLOB, ORIGINAL_VALID_GIT_BLOB,
    Migration062Blocked, inspect_062_installed, inspect_062_source,
)


class Result:
    def __init__(self, one=None, rows=None):
        self.one, self.rows = one, rows

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


class ReadOnlyPG:
    autocommit = True

    def __init__(self, *, ledger=True, installed=None, invalid=False):
        self.ledger = ledger
        self.installed = installed
        self.invalid = invalid
        self.queries = []
        self.transactions = 0

    @contextmanager
    def transaction(self):
        self.transactions += 1
        yield

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        if self.invalid:
            raise RuntimeError("unavailable database")
        if sql == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY":
            return Result()
        if sql == "SELECT to_regclass('public.schema_migrations')":
            return Result(one=("schema_migrations",) if self.ledger else (None,))
        if sql.startswith("SELECT sha256 FROM public.schema_migrations"):
            assert params == (FILENAME,)
            return Result(rows=[] if self.installed is None else [(self.installed,)])
        raise AssertionError("unexpected or mutating SQL")


class Migration062AuditTest(unittest.TestCase):
    def test_known_checkout_is_corrupt_but_report_never_claims_pass(self):
        report = inspect_062_source()
        self.assertEqual(report["git_blob_sha1"], KNOWN_BROKEN_GIT_BLOB)
        self.assertEqual(report["reason"], "062_known_malformed_sql")
        self.assertTrue(report["missing_input_tokens_column"])
        self.assertTrue(report["corrupted_evidence_constraint"])
        self.assertIn("CREATE INDEX sre_m28_receipts_project_recorded",
                      report["duplicate_ddl_names"])
        self.assertFalse(report["safe_to_apply"])
        self.assertEqual(report["status"], "blocked")

    def test_missing_and_symlink_source_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(inspect_062_source(root=root)["reason"],
                             "062_missing_or_symlink")
            file = root / FILENAME
            try:
                file.symlink_to(root / "real.sql")
            except (OSError, NotImplementedError):
                pass
            else:
                self.assertEqual(inspect_062_source(root=root)["reason"],
                                 "062_missing_or_symlink")

    def test_unrecognized_sql_is_not_assumed_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / FILENAME).write_text("SELECT 1;\n", encoding="utf-8")
            report = inspect_062_source(root=Path(temp))
            self.assertEqual(report["reason"], "062_unreviewed_sql_blob")
            self.assertFalse(report["safe_to_apply"])
            self.assertNotEqual(report["git_blob_sha1"], ORIGINAL_VALID_GIT_BLOB)

    def test_fresh_database_cannot_bypass_sql_blocker(self):
        conn = ReadOnlyPG(ledger=False)
        report = inspect_062_installed(conn)
        self.assertEqual(report["ledger_state"], "ledger_absent")
        self.assertIsNone(report["installed_sql_sha256"])
        self.assertFalse(report["safe_to_apply"])
        self.assertTrue(all(
            sql.startswith("SELECT ") or sql.startswith("SET TRANSACTION ")
            for sql, _ in conn.queries
        ))
        self.assertEqual(conn.transactions, 1)

    def test_installed_062_absent_still_blocked(self):
        conn = ReadOnlyPG(installed=None)
        report = inspect_062_installed(conn)
        self.assertEqual(report["ledger_state"], "062_not_installed")
        self.assertFalse(report["safe_to_apply"])

    def test_exact_checkout_checksum_cannot_authorize_malformed_source(self):
        sha = inspect_062_source()["sql_sha256"]
        report = inspect_062_installed(ReadOnlyPG(installed=sha))
        self.assertEqual(report["ledger_state"], "installed_exact_checkout")
        self.assertEqual(report["reason"], "062_known_malformed_sql")
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["safe_to_apply"])

    def test_older_deployed_checksum_mismatch_requires_human_review(self):
        conn = ReadOnlyPG(installed="a" * 64)
        report = inspect_062_installed(conn)
        self.assertEqual(report["ledger_state"], "installed_checksum_differs")
        self.assertEqual(report["operator_action"],
                         "review_exact_deployed_schema_and_checksum_before_repair")
        self.assertEqual(report["installed_sql_sha256"], "a" * 64)
        self.assertFalse(report["schema_changed"])
        self.assertEqual(report["paid_calls"], 0)

    def test_invalid_ledger_hash_and_unavailable_database_are_blocked(self):
        for conn in (ReadOnlyPG(installed="untrusted"),
                     ReadOnlyPG(invalid=True)):
            with self.subTest(conn=conn), self.assertRaises(Migration062Blocked):
                inspect_062_installed(conn)

    def test_non_autocommit_denied_before_any_sql(self):
        conn = ReadOnlyPG()
        conn.autocommit = False
        with self.assertRaises(Migration062Blocked):
            inspect_062_installed(conn)
        self.assertEqual(conn.queries, [])


if __name__ == "__main__":
    unittest.main()
