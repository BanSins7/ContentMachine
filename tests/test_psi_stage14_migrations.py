"""PSI Stage14 release migration gate: old schema upgrade, checksums, project isolation.

Runs inside a fresh, disposable PostgreSQL SCHEMA (same PostgreSQL17 database).
No production data can be touched by this test.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from intelligence import migrate

DSN = os.environ.get("CM_TEST_PG_DSN")


@unittest.skipUnless(DSN, "PostgreSQL 17 fixture required")
class SourceIntelligenceUpgrade(unittest.TestCase):
    def test_legacy_schema_upgrade_and_immutable_snapshot_replay(self):
        import psycopg
        from psycopg.types.json import Jsonb
        name = "psi_gate_" + uuid4().hex[:12]
        migrations = sorted(migrate.MIGRATIONS.glob("[0-9]*.sql"))
        legacy = [p for p in migrations if int(p.name[:3]) < 30]
        psi = [p for p in migrations if 30 <= int(p.name[:3]) <= 41]
        self.assertGreaterEqual(len(legacy), 10)
        self.assertEqual([p.name[:3] for p in psi],
                         [str(x).zfill(3) for x in range(30, 42)])
        with tempfile.TemporaryDirectory() as tmp, psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("CREATE SCHEMA " + name)
            try:
                conn.execute("SET search_path TO " + name + ", public")
                root = Path(tmp)
                for src in legacy:
                    shutil.copy2(src, root / src.name)
                with patch.object(migrate, "MIGRATIONS", root):
                    first = migrate.apply_migrations(conn)
                    self.assertEqual(first, [p.name for p in legacy])
                    self.assertEqual(migrate.apply_migrations(conn), [])
                pid = uuid4().hex
                profile = {"goal": "Baseline user goal", "feature": "Counts steps"}
                digest = hashlib.sha256(b"baseline-pin").hexdigest()
                conn.execute("""INSERT INTO project_registry(id,name,profile_revision,profile_hash,created_at,updated_at)
                               VALUES(%s,'Before Project Source Intelligence',1,%s,now(),now())""",
                             (pid, digest))
                conn.execute("""INSERT INTO project_profile_versions
                            (project_id,revision,schema_version,profile_hash,profile)
                            VALUES(%s,1,1,%s,%s)""", (pid, digest, Jsonb(profile)))
                first_profile = conn.execute("""SELECT profile::text,profile_hash FROM project_profile_versions
                            WHERE project_id=%s AND revision=1""", (pid,)).fetchone()
                with patch.object(migrate, "MIGRATIONS", migrate.MIGRATIONS):
                    second = migrate.apply_migrations(conn)
                self.assertEqual(second, [p.name for p in psi])
                self.assertEqual(migrate.apply_migrations(conn), [])
                self.assertEqual(conn.execute("""SELECT profile::text,profile_hash
                    FROM project_profile_versions WHERE project_id=%s AND revision=1""",
                    (pid,)).fetchone(), first_profile)
                self.assertEqual(conn.execute("""SELECT name FROM project_registry WHERE id=%s""",
                    (pid,)).fetchone()[0], "Before Project Source Intelligence")
                self.assertIsNotNone(conn.execute(
                    "SELECT to_regclass(%s)", (name + ".project_context_snapshots",)).fetchone()[0])
                self.assertIsNotNone(conn.execute(
                    "SELECT to_regclass(%s)", (name + ".tb_context_quote_bindings",)).fetchone()[0])
                count = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
                self.assertEqual(count, len(legacy) + len(psi))
                # A previously applied file is never silently edited/replayed.
                with tempfile.TemporaryDirectory() as modified:
                    path = Path(modified) / legacy[0].name
                    path.write_text(legacy[0].read_text(encoding="utf-8") +
                                    "\n-- bad post-deployment edit\n", encoding="utf-8")
                    with patch.object(migrate, "MIGRATIONS", Path(modified)):
                        with self.assertRaisesRegex(RuntimeError, "modified after deployment"):
                            migrate.apply_migrations(conn)
                self.assertEqual(conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0],
                                 count)
                self.assertEqual(conn.execute(
                    "SELECT profile_hash FROM project_profile_versions WHERE project_id=%s",
                    (pid,)).fetchone()[0], digest)
            finally:
                conn.execute("RESET search_path")
                conn.execute("DROP SCHEMA IF EXISTS " + name + " CASCADE")


if __name__ == "__main__":
    unittest.main()
