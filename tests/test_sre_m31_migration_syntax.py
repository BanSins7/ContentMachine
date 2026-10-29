"""Regression for M31 PL/pgSQL CASE parsing and migration error locality."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from intelligence import migrate


class M31MigrationSyntaxTests(TestCase):
    def test_nested_case_then_does_not_terminate_plpgsql_if_condition(self):
        path = Path(migrate.__file__).parent / "migrations" / (
            "065_sre_live_diagnostic_checkpoints.sql"
        )
        sql = path.read_text(encoding="utf-8")
        self.assertIn("IF NEW.alert_code IS DISTINCT FROM (CASE", sql)
        self.assertIn(
            "    END) THEN\n"
            "    RAISE EXCEPTION 'M31 checkpoint alert priority is not source-computed';",
            sql,
        )
        self.assertEqual(sql.count("$m31_checkpoint$"), 2)

    def test_failing_sql_reports_migration_filename_without_false_ledger_entry(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "001_broken.sql"
            path.write_text("INVALID SQL STATEMENT;", encoding="utf-8")
            class Cursor:
                def fetchone(self): return None
            class Connection:
                def __init__(self):
                    self.committed = False
                    self.inserted = False
                @contextmanager
                def transaction(self):
                    try:
                        yield
                    except Exception:
                        raise
                    else:
                        self.committed = True
                def execute(self, sql, args=()):
                    if sql == "INVALID SQL STATEMENT;":
                        raise ValueError("simulated PostgreSQL syntax error")
                    if sql.startswith("SELECT sha256 FROM schema_migrations"):
                        return Cursor()
                    if sql.startswith("INSERT INTO schema_migrations"):
                        self.inserted = True
                    return Cursor()
            conn = Connection()
            with patch.object(migrate, "MIGRATIONS", Path(tmp)):
                with self.assertRaisesRegex(
                    RuntimeError, r"Migration SQL failed: 001_broken\.sql"
                ) as failure:
                    migrate.apply_migrations(conn)
            self.assertIsInstance(failure.exception.__cause__, ValueError)
            self.assertFalse(conn.committed)
            self.assertFalse(conn.inserted)
