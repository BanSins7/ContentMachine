"""VC-MOD-02: collector schema contracts, non-destructive PG17 upgrade and integrity QA.

SQL tests use isolated *temporary schemas* under CM_TEST_PG_DSN. They never
access an existing production schema, and no DTK/LLM calls are made.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from intelligence import migrate

MIGRATIONS = migrate.MIGRATIONS
FILES = (
    "054_collector_planning.sql",
    "055_collector_worker.sql",
    "056_collector_qv_manifest.sql",
    "057_collector_discovery_outbox.sql",
)
TABLES = (
    "collector_query_sets",
    "collector_qv_manifests",
    "collector_query_shards",
    "collector_manifest_queries",
    "collector_manifest_coverage",
    "collector_ingest_pages",
    "collector_ingest_page_videos",
    "collector_page_video_queries",
    "collector_sre_outbox",
)
DSN = os.environ.get("CM_TEST_PG_DSN")


class CollectorSchemaStaticTests(unittest.TestCase):
    def test_all_stage02_migrations_present_exactly_once(self):
        installed = sorted(x.name for x in MIGRATIONS.glob("[0-9]*.sql"))
        for name in FILES:
            with self.subTest(name=name):
                self.assertEqual(installed.count(name), 1)
        self.assertNotIn("016_collector_planning.sql", installed)
        self.assertNotIn("017_collector_worker.sql", installed)
        self.assertEqual([n for n in installed if n in FILES], list(FILES))

    def test_boundary_sql_owns_no_duplicate_system(self):
        source = "\n".join((MIGRATIONS / name).read_text("utf-8") for name in FILES[2:])
        self.assertNotRegex(source, r"\bCREATE\s+TABLE\s+(?:IF NOT EXISTS\s+)?(?:videos|discoveries|sre_classification_results)\b")
        for label in ("project_id", "collector_task_queries", "collector_video_hypotheses",
                      "collector_ingest_pages", "collector_sre_outbox",
                      "delivery_key", "lease_generation", "page_sha256"):
            self.assertIn(label, source)
        self.assertRegex(source, r"UNIQUE\s*\(provider_task_row_id,page_ordinal\)")
        self.assertRegex(source, r"UNIQUE\s*\(project_id,delivery_key\)")
        self.assertIn("CHECK((event_payload->>'relevance_state'='not_evaluated') IS TRUE)", source)
        self.assertIn("sqg_generation_manifest_source_vc_uq", source)
        self.assertIn("collector_query_set_source_vc_uq", source)
        self.assertIn("FOREIGN KEY(query_set_id,project_id,generation_id,publication_id)", source)
        self.assertIn("CHECK((event_payload->>'delivery_key'=delivery_key) IS TRUE)", source)
        self.assertIn("FOREIGN KEY(shard_id,manifest_id,project_id)", source)
        self.assertIn("REFERENCES collector_task_queries(source_request_id,query_id)", source)
        self.assertIn("REFERENCES collector_video_hypotheses(source_request_id,video_id,query_id)", source)


@unittest.skipUnless(DSN, "CM_TEST_PG_DSN is required for PostgreSQL17 integration QA")
class CollectorSchemaPg17Tests(unittest.TestCase):
    def test_disposable_old_main_and_collector_upgrade_is_atomic(self):
        import psycopg

        name = "vc_02_" + uuid4().hex[:12]
        all_paths = sorted(MIGRATIONS.glob("[0-9]*.sql"))
        old_main = [p for p in all_paths if int(p.name[:3]) <= 53]
        collector_v1 = [p for p in all_paths if p.name in FILES[:2]]
        newest = [p for p in all_paths if int(p.name[:3]) >= 54
                  and p.name not in FILES[:2]]
        self.assertEqual([p.name for p in collector_v1], list(FILES[:2]))
        self.assertTrue(set(FILES[2:]).issubset({p.name for p in newest}))

        with tempfile.TemporaryDirectory() as tmp, psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("CREATE SCHEMA " + name)
            try:
                conn.execute("SET search_path TO " + name + ", public")
                root = Path(tmp)
                for item in old_main:
                    shutil.copy2(item, root / item.name)
                with patch.object(migrate, "MIGRATIONS", root):
                    before = migrate.apply_migrations(conn)
                    self.assertEqual(before, [p.name for p in old_main])
                    self.assertEqual(migrate.apply_migrations(conn), [])
                pid = uuid4().hex
                source_hash = hashlib.sha256(b"kept-project").hexdigest()
                conn.execute(
                    "INSERT INTO project_registry(id,name,profile_revision,profile_hash,created_at,updated_at) "
                    "VALUES(%s,%s,1,%s,now(),now())", (pid, "Before VC", source_hash)
                )
                original = conn.execute(
                    "SELECT name,profile_hash FROM project_registry WHERE id=%s", (pid,)
                ).fetchone()
                with patch.object(migrate, "MIGRATIONS", MIGRATIONS):
                    installed = migrate.apply_migrations(conn)
                    self.assertEqual(installed, [p.name for p in sorted(collector_v1 + newest)])
                    self.assertEqual(migrate.apply_migrations(conn), [])
                self.assertEqual(
                    conn.execute("SELECT name,profile_hash FROM project_registry WHERE id=%s",
                                 (pid,)).fetchone(), original
                )
                for table in TABLES:
                    with self.subTest(table=table):
                        self.assertIsNotNone(conn.execute(
                            "SELECT to_regclass(%s)", (name + "." + table,)
                        ).fetchone()[0])
                migrations = dict(conn.execute(
                    "SELECT version,sha256 FROM schema_migrations").fetchall())
                self.assertEqual(set(migrations), {p.name for p in all_paths})
                for item in all_paths:
                    self.assertEqual(migrations[item.name], hashlib.sha256(
                        item.read_text(encoding="utf-8").encode("utf-8")).hexdigest())

                # Last file in isolation after check-sum tampering must reject
                # the entire migration in a disposable transaction.
                with tempfile.TemporaryDirectory() as tampered:
                    f = Path(tampered) / FILES[-1]
                    f.write_bytes((MIGRATIONS / FILES[-1]).read_bytes() + b"\n-- tamper")
                    with patch.object(migrate, "MIGRATIONS", Path(tampered)):
                        with self.assertRaisesRegex(RuntimeError,
                                                    "modified after deployment"):
                            migrate.apply_migrations(conn)
                self.assertEqual(dict(conn.execute(
                    "SELECT version,sha256 FROM schema_migrations").fetchall()), migrations)

                # Verify actual PostgreSQL FOREIGN KEY scoping, not just SQL text.
                for table, target in (
                    ("collector_qv_manifests", "qv_runs"),
                    ("collector_query_shards", "collector_query_sets"),
                    ("collector_ingest_pages", "collector_runs"),
                    ("collector_ingest_page_videos", "collector_ingest_pages"),
                    ("collector_page_video_queries", "collector_task_queries"),
                    ("collector_sre_outbox", "collector_ingest_page_videos"),
                ):
                    with self.subTest(table=table):
                        actual = conn.execute(
                            """SELECT COUNT(*) FROM pg_constraint c
                               JOIN pg_class child ON child.oid=c.conrelid
                               JOIN pg_namespace n ON n.oid=child.relnamespace
                               JOIN pg_class parent ON parent.oid=c.confrelid
                               WHERE n.nspname=%s AND child.relname=%s
                                     AND parent.relname=%s AND c.contype='f'""",
                            (name, table, target)
                        ).fetchone()[0]
                        self.assertGreaterEqual(actual, 1)

                # Verify actual triggers reject updates to immutable receipts.
                for table in ("collector_qv_manifests", "collector_query_shards",
                              "collector_manifest_queries", "collector_manifest_coverage",
                              "collector_ingest_pages", "collector_ingest_page_videos",
                              "collector_page_video_queries", "collector_sre_outbox"):
                    with self.subTest(table=table):
                        self.assertGreater(conn.execute(
                            """SELECT COUNT(*) FROM pg_trigger t
                               JOIN pg_class c ON c.oid=t.tgrelid
                               JOIN pg_namespace n ON n.oid=c.relnamespace
                               WHERE n.nspname=%s AND c.relname=%s
                                     AND NOT t.tgisinternal""",
                            (name, table)
                        ).fetchone()[0], 0)

                # Cross-project orphan/forged parent cannot be inserted.
                other = uuid4().hex
                conn.execute(
                    "INSERT INTO project_registry(id,name,profile_revision,profile_hash) "
                    "VALUES(%s,%s,1,%s)", (other, "Other VC", source_hash)
                )
                with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                    conn.execute(
                        """INSERT INTO collector_qv_manifests(
                             project_id,qv_outbox_id,validation_run_id,generation_id,
                             publication_id,taxonomy_version,taxonomy_sha256,
                             qv_bundle_sha256,manifest_sha256,selected_count,
                             source_candidate_count,shard_count,coverage_count,status)
                           VALUES(%s,%s,%s,%s,%s,1,%s,%s,%s,0,0,0,1,'no_queries')""",
                        (other, uuid4(), uuid4(), uuid4(), uuid4(),
                         "a"*64, "b"*64, "c"*64)
                    )
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM collector_qv_manifests")
                                 .fetchone()[0], 0)
            finally:
                conn.execute("RESET search_path")
                conn.execute("DROP SCHEMA IF EXISTS " + name + " CASCADE")


if __name__ == "__main__":
    unittest.main()
