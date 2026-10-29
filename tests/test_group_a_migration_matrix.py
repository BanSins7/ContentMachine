"""GA23 real PG17 fresh/upgrade/replay migration tests, disposable schemas.

Test runner requires *both* CM_TEST_PG_DSN and CM_GA23_DISPOSABLE_ACK=yes.
Creates a randomly named isolated schema, not a production migration run.
All connection writes are scoped to this disposable test schema and reverted.
"""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
from uuid import uuid4
import unittest

from intelligence.migrate import MIGRATIONS,apply_migrations

ENABLED=bool(os.getenv("CM_TEST_PG_DSN")) and (
    os.getenv("CM_GA23_DISPOSABLE_ACK")=="yes"
)
GA_MIGRATIONS=(
    "054_group_a_module_core.sql",
    "055_group_a_handoff_inbox.sql",
    "056_group_a_immutable_video_snapshots.sql",
    "057_group_a_runtime_orchestration.sql",
    "058_group_a_api_observability.sql",
    "059_group_a_qdp_revocation_bridge.sql",
)


@unittest.skipUnless(ENABLED,
    "GA23 requires explicit disposable CM_TEST_PG_DSN + CM_GA23_DISPOSABLE_ACK=yes")
class GA23MigrationMatrixPG17(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.schema="ga23_"+uuid4().hex
        self.admin=psycopg.connect(
            os.environ["CM_TEST_PG_DSN"],autocommit=True
        )
        self.admin.execute('CREATE SCHEMA "'+self.schema+'"')
        self.addCleanup(self.cleanup_schema)
        self.db=psycopg.connect(
            os.environ["CM_TEST_PG_DSN"],autocommit=True
        )
        self.addCleanup(self.db.close)
        # schema name comes solely from uuid4, not user input
        self.db.execute('SET search_path TO "'+self.schema+'", public')
        version=self.db.execute("SHOW server_version_num").fetchone()[0]
        if int(version)//10000!=17:
            self.skipTest("GA23 migration matrix requires PostgreSQL 17")

    def cleanup_schema(self):
        try:
            self.admin.execute('DROP SCHEMA IF EXISTS "'+self.schema+'" CASCADE')
        finally:
            self.admin.close()

    def _inventory(self):
        return {
            version:digest for version,digest in self.db.execute(
                "SELECT version,sha256 FROM schema_migrations"
            ).fetchall()
        }

    def _assert_migration_checksums(self):
        installed=self._inventory()
        for filename in GA_MIGRATIONS:
            self.assertIn(filename,installed)
            expected=sha256(
                (MIGRATIONS/filename).read_bytes()
            ).hexdigest()
            self.assertEqual(installed[filename],expected)
        return installed

    def test_fresh_install_all_GA_054_through_058_and_repeat(self):
        installed=apply_migrations(self.db)
        self.assertTrue(set(GA_MIGRATIONS).issubset(installed))
        initial=self._assert_migration_checksums()
        self.assertEqual(apply_migrations(self.db),[])
        self.assertEqual(self._inventory(),initial)
        self.assertIsNotNone(self.db.execute(
            "SELECT to_regclass('ga20_locally_current_results')"
        ).fetchone()[0])
        self.assertIsNotNone(self.db.execute(
            "SELECT to_regclass('ga21_operator_events')"
        ).fetchone()[0])

    def test_existing_main_schema_upgrade_then_GA_054_058(self):
        with self.db.transaction():
            self.db.execute(
                """CREATE TABLE schema_migrations (
                     version TEXT PRIMARY KEY,
                     sha256 TEXT NOT NULL,
                     applied_at TIMESTAMPTZ DEFAULT now()
                   )"""
            )
            for path in sorted(MIGRATIONS.glob("[0-9]*.sql")):
                # Ensure pre-Group-A lineage is a real migration baseline;
                # target GA and independently sorted later peer migrations
                # install only during the upgrade phase.
                if path.name>="054_group_a_module_core.sql":
                    continue
                self.db.execute(path.read_text(encoding="utf-8"))
                self.db.execute(
                    "INSERT INTO schema_migrations(version,sha256) VALUES (%s,%s)",
                    (path.name,sha256(path.read_bytes()).hexdigest())
                )
        self.assertTrue(set(GA_MIGRATIONS).issubset(
            set(apply_migrations(self.db))
        ))
        initial=self._assert_migration_checksums()
        self.assertEqual(apply_migrations(self.db),[])
        self.assertEqual(self._inventory(),initial)

    def test_checksum_drift_is_a_blocking_error(self):
        apply_migrations(self.db)
        before=self._assert_migration_checksums()
        self.db.execute(
            "UPDATE schema_migrations SET sha256=%s WHERE version=%s",
            ("f"*64,GA_MIGRATIONS[0])
        )
        with self.assertRaisesRegex(RuntimeError,"Migration modified after deployment"):
            apply_migrations(self.db)
        # Test fixture repair, never change repository migration files.
        self.db.execute(
            "UPDATE schema_migrations SET sha256=%s WHERE version=%s",
            (before[GA_MIGRATIONS[0]],GA_MIGRATIONS[0])
        )
        self.assertEqual(apply_migrations(self.db),[])


if __name__=="__main__":
    unittest.main()
