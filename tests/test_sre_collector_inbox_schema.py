"""M35 additive migration static security checks, plus real PG17 when configured."""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from uuid import uuid4

from intelligence.relevance.migration_guard import assess_applied, build_manifest

SQL_PATH = (Path(__file__).resolve().parents[1] / "intelligence"
            / "migrations" / "069_sre_collector_inbox.sql")
SQL = SQL_PATH.read_text(encoding="utf-8")


class M35SchemaStaticTests(unittest.TestCase):
    def test_forward_only_schema_immutable_ack_quarantine_and_tenant(self):
        for token in (
            "CREATE TABLE sre_collector_inbox",
            "event_id UUID PRIMARY KEY",
            "REFERENCES sre_workspaces(project_id)",
            "event_sha256 TEXT NOT NULL",
            "state IN ('acked','quarantined')",
            "content_version_id BIGINT",
            "quarantine_reason",
            "CREATE FUNCTION sre_m35_guard_inbox()",
            "IF TG_OP <> 'INSERT' THEN",
            "CREATE TRIGGER sre_m35_inbox_integrity",
            "BEFORE INSERT OR UPDATE OR DELETE",
            "sre_project_discoveries",
            "tb_sre_taxonomy_links",
            "d.source_request_id IS NOT DISTINCT FROM NEW.source_request_id",
            "REVOKE ALL ON TABLE sre_collector_inbox FROM PUBLIC",
        ):
            self.assertIn(token, SQL)
        self.assertNotIn("DROP TABLE", SQL)
        self.assertNotIn("ALTER TABLE discoveries", SQL)
        self.assertNotIn("ALTER TABLE source_requests", SQL)
        self.assertNotIn("collector_query_ids::bigint", SQL.lower())
        self.assertEqual(len(re.findall(r"CREATE FUNCTION sre_m35_guard_inbox", SQL)), 1)
        self.assertEqual(SQL.count("$m35_inbox$"), 2)

    def test_manifest_includes_069_and_next_070(self):
        self.assertIn(SQL_PATH.name, build_manifest())
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"], "070")


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"), "disposable PostgreSQL17 required")
class M35RealPgSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.dsn = os.environ["CM_TEST_PG_DSN"]
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            migrate.apply_migrations(conn)

    def setUp(self):
        import psycopg
        self.conn = psycopg.connect(self.dsn)
        self.pid = uuid4().hex
        self.event = str(uuid4())
        self.pub = str(uuid4())
        self.conn.execute(
            """INSERT INTO project_registry
               (id,name,profile_revision,profile_hash,created_at,updated_at)
               VALUES (%s,'M35 fixture',1,%s,now(),now())""",
            (self.pid, "a" * 64),
        )
        self.conn.execute(
            "INSERT INTO sre_workspaces(project_id) VALUES (%s)", (self.pid,)
        )

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def _insert(self, state, content_id=None, digest=None, reason=None):
        self.conn.execute(
            """INSERT INTO sre_collector_inbox (
                 event_id,project_id,event_sha256,publication_id,
                 discovery_id,video_id,source_id,source_policy_version_id,
                 source_request_id,occurred_at,d1_persisted_at,
                 state,content_version_id,content_sha256,quarantine_reason,actor_id)
               VALUES (%s,%s,%s,%s,7,29,'fixture_source',11,NULL,
                       now(),now(),%s,%s,%s,%s,'sre:test')""",
            (self.event,self.pid,"b"*64,self.pub,state,content_id,digest,reason),
        )

    def test_minimal_quarantine_no_raw_payload_and_immutable(self):
        self._insert("quarantined", reason="missing_provenance")
        self.assertEqual(self.conn.execute(
            """SELECT state,content_version_id,quarantine_reason
               FROM sre_collector_inbox WHERE event_id=%s""",
            (self.event,),
        ).fetchone(), ("quarantined", None, "missing_provenance"))
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    "UPDATE sre_collector_inbox SET state='acked' WHERE event_id=%s",
                    (self.event,),
                )
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    "DELETE FROM sre_collector_inbox WHERE event_id=%s",
                    (self.event,),
                )

    def test_forged_ack_requires_d1_s3_and_approved_c9(self):
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self._insert("acked", content_id=10001, digest="c"*64)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_collector_inbox WHERE project_id=%s",
            (self.pid,),
        ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
