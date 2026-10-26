"""M05: real PostgreSQL17 fresh, main-only upgrade, no-op reapply and drift.

Uses two isolated, disposable schemas in the CI database. No live provider,
network download, production connection, or permanent schema modifications.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from intelligence import migrate
from intelligence.relevance.migration_guard import (
    PINNED_SRE_GIT_BLOBS, assess_applied, build_manifest, preflight_read_only,
)

DSN = os.environ.get("CM_TEST_PG_DSN")


@unittest.skipUnless(DSN, "disposable PostgreSQL17 CM_TEST_PG_DSN required")
class SREForwardOnlyMigrationsPG(unittest.TestCase):
    def _isolated_schema(self, conn, prefix):
        name = prefix + uuid4().hex[:12]
        conn.execute("CREATE SCHEMA " + name)
        conn.execute("SET search_path TO " + name + ", public")
        return name

    def _drop_schema(self, conn, name):
        conn.execute("RESET search_path")
        conn.execute("DROP SCHEMA IF EXISTS " + name + " CASCADE")

    def test_fresh_install_upgrade_from_current_main_and_drift(self):
        import psycopg
        source = migrate.MIGRATIONS
        complete = build_manifest()
        with tempfile.TemporaryDirectory() as tmp, psycopg.connect(DSN, autocommit=True) as conn:
            main_dir = Path(tmp)
            for src in source.glob("[0-9]*.sql"):
                if src.name not in PINNED_SRE_GIT_BLOBS:
                    shutil.copy2(src, main_dir / src.name)

            # A newly installed combined SRE + main schema.
            fresh = self._isolated_schema(conn, "sre_m05_fresh_")
            try:
                start = preflight_read_only(conn)
                self.assertEqual(start["status"], "ready")
                self.assertFalse(start["ledger_present"])
                self.assertEqual(start["pending"], sorted(complete))
                first = migrate.apply_migrations(conn)
                self.assertEqual(first, sorted(complete))
                self.assertEqual(migrate.apply_migrations(conn), [])
                after = preflight_read_only(conn)
                self.assertEqual(after["status"], "ready")
                self.assertEqual(after["pending"], [])
                before_count = conn.execute(
                    "SELECT count(*) FROM schema_migrations").fetchone()[0]
                self.assertEqual(before_count, len(complete))
                self.assertEqual(conn.execute(
                    "SELECT version,sha256 FROM schema_migrations ORDER BY version"
                ).fetchall(), sorted(complete.items()))
                # A corrupt deployed checksum must never be accepted/rebased.
                conn.execute(
                    "UPDATE schema_migrations SET sha256=%s WHERE version=%s",
                    ("0" * 64, "016_sre_d1_intake_rights.sql"))
                blocked = preflight_read_only(conn)
                self.assertEqual(blocked["status"], "blocked")
                self.assertEqual(blocked["checksum_mismatch"],
                                 ["016_sre_d1_intake_rights.sql"])
                self.assertEqual(conn.execute(
                    "SELECT count(*) FROM schema_migrations").fetchone()[0],
                                 before_count)
            finally:
                self._drop_schema(conn, fresh)

            # An existing main installation already on 053, before SRE merges.
            upgraded = self._isolated_schema(conn, "sre_m05_upgrade_")
            try:
                with patch.object(migrate, "MIGRATIONS", main_dir):
                    old = migrate.apply_migrations(conn)
                self.assertEqual(old, sorted(set(complete) - set(PINNED_SRE_GIT_BLOBS)))
                prior = conn.execute(
                    "SELECT version,sha256 FROM schema_migrations ORDER BY version"
                ).fetchall()
                proposal = preflight_read_only(conn)
                self.assertEqual(proposal["status"], "ready")
                self.assertEqual(proposal["pending"], sorted(PINNED_SRE_GIT_BLOBS))
                self.assertEqual(proposal["retroactive_additions"],
                                 sorted(PINNED_SRE_GIT_BLOBS))
                # Upgrade exactly missing SRE SQL; deployed main hashes unchanged.
                added = migrate.apply_migrations(conn)
                self.assertEqual(added, sorted(PINNED_SRE_GIT_BLOBS))
                self.assertEqual(migrate.apply_migrations(conn), [])
                for name, digest in prior:
                    self.assertEqual(conn.execute(
                        "SELECT sha256 FROM schema_migrations WHERE version=%s",
                        (name,)).fetchone()[0], digest)
                ready = preflight_read_only(conn)
                self.assertEqual(ready["status"], "ready")
                self.assertEqual(ready["pending"], [])
                self.assertEqual(ready["applied_count"], len(complete))
                self.assertEqual(assess_applied(dict(
                    conn.execute("SELECT version,sha256 FROM schema_migrations")
                    .fetchall()))["pending"], [])
            finally:
                self._drop_schema(conn, upgraded)


if __name__ == "__main__":
    unittest.main()
