"""SP-16A actual pre-057 -> latest PostgreSQL17 migration upgrade gate.

Destructive database creation is allowed ONLY with CM_SP16_RUN_PG17=1,
against the exact disposable cm_sp16_test database. This creates one
disposable sibling database (cm_sp16_upgrade), never drops an existing DB
and refuses any real/non-test target. It uses intelligence.migrate itself,
NOT a second SQL migrator.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest
from unittest.mock import patch

UPGRADE_DATABASE = "cm_sp16_upgrade"
CONTROL_DATABASE = "cm_sp16_test"


class SP16AActualUpgradePG17(unittest.TestCase):
    def test_real_upgrade_to_new_lease_and_invoice_migrations(self):
        if os.getenv("CM_SP16_RUN_PG17") != "1":
            self.skipTest("SP16 isolated PostgreSQL test was not explicitly enabled")
        dsn = os.getenv("CM_TEST_PG_DSN", "")
        if not dsn:
            self.fail("Real SP16 PostgreSQL CI requires CM_TEST_PG_DSN")
        import psycopg
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        from intelligence import migrate

        args = conninfo_to_dict(dsn)
        if args.get("dbname") != CONTROL_DATABASE:
            self.fail("SP16 refuses unverified or non-disposable database")
        with psycopg.connect(dsn, connect_timeout=5) as control:
            version = int(control.execute("SHOW server_version_num").fetchone()[0])
            self.assertTrue(170000 <= version < 180000, "PostgreSQL 17 is required")

        # Explicit exact-name sibling DB, created empty only. If it exists,
        # CREATE DATABASE fails; no DROP, TRUNCATE or reuse of user data.
        admin_dsn = make_conninfo(dsn, dbname="postgres")
        with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as admin:
            admin.execute(f"CREATE DATABASE {UPGRADE_DATABASE}")
        target = make_conninfo(dsn, dbname=UPGRADE_DATABASE)
        with TemporaryDirectory(prefix="cm_sp16_before_057_") as tmp:
            initial = Path(tmp)
            available = sorted(migrate.MIGRATIONS.glob("[0-9]*.sql"))
            older = [path for path in available if path.name[:3].isdigit()
                     and int(path.name[:3]) <= 56]
            self.assertTrue(older, "Missing old real repo migration history")
            self.assertTrue(any(p.name.startswith("056_") for p in older))
            self.assertTrue(any(p.name.startswith("057_") for p in available))
            self.assertTrue(any(p.name.startswith("058_") for p in available))
            for path in older:
                (initial / path.name).write_bytes(path.read_bytes())
            with psycopg.connect(target, connect_timeout=5) as conn:
                with patch.object(migrate, "MIGRATIONS", initial):
                    first = migrate.apply_migrations(conn)
                self.assertEqual(len(first), len(older))
                before = {
                    row[0]: row[1] for row in conn.execute(
                        "SELECT version,sha256 FROM schema_migrations"
                    ).fetchall()
                }
                self.assertNotIn("057_sp_supervisor_leases.sql", before)
                self.assertNotIn("058_sp_invoice_reconciliations.sql", before)
                self.assertEqual(len(before), len(older))

                changed = migrate.apply_migrations(conn)
                self.assertIn("057_sp_supervisor_leases.sql", changed)
                self.assertIn("058_sp_invoice_reconciliations.sql", changed)
                self.assertTrue(all(
                    name not in before for name in changed
                ), "Upgrade cannot re-run already applied migrations")
                after = {
                    row[0]: row[1] for row in conn.execute(
                        "SELECT version,sha256 FROM schema_migrations"
                    ).fetchall()
                }
                self.assertTrue(all(after[name] == checksum
                                    for name, checksum in before.items()))
                self.assertEqual(migrate.apply_migrations(conn), [])
                for table in ("sp_supervisor_leases",
                              "sp_invoice_reconciliations"):
                    self.assertTrue(conn.execute(
                        """SELECT EXISTS (
                             SELECT 1 FROM information_schema.tables
                              WHERE table_schema='public' AND table_name=%s
                           )""", (table,),
                    ).fetchone()[0])
                self.assertGreaterEqual(len(after), len(before) + 2)


if __name__ == "__main__":
    unittest.main()
