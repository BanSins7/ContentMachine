"""GB-MOD-03: pure schema checks and opt-in disposable PostgreSQL integration."""
from __future__ import annotations
import concurrent.futures
import hashlib
import os
import re
import unittest
from uuid import uuid4

from intelligence.migrate import MIGRATIONS, apply_migrations
from intelligence.group_b.repository import (
    MIGRATION_NAME, RUNTIME_TABLES, _sha256_migration,
    inspect_runtime_schema, legacy_migration_unchanged, require_runtime_schema,
)
from intelligence.group_b.errors import DependencyUnavailable

SQL = MIGRATIONS / MIGRATION_NAME
HASH = "a" * 64


class GroupBStaticRuntime(unittest.TestCase):
    def test_migration_sequence_and_legacy_immutability(self):
        files = [p.name for p in sorted(MIGRATIONS.glob("[0-9]*.sql"))]
        self.assertIn(MIGRATION_NAME, files)
        self.assertLess(files.index("053_ke_c_qualified_execution.sql"),
                        files.index(MIGRATION_NAME))
        if "054_group_a_module_core.sql" in files:
            self.assertLess(files.index("054_group_a_module_core.sql"),
                            files.index(MIGRATION_NAME))
        self.assertEqual(len(files), len(set(files)))
        self.assertTrue(legacy_migration_unchanged())
        self.assertEqual(_sha256_migration(MIGRATION_NAME),
                         hashlib.sha256(SQL.read_bytes()).hexdigest())

    def test_owned_tables_and_composite_foreign_keys(self):
        data = SQL.read_text(encoding="utf-8")
        tables = re.findall(r"^CREATE TABLE (group_b_\w+)\s*\(", data, re.M)
        self.assertEqual(set(tables), set(RUNTIME_TABLES))
        self.assertEqual(len(tables), 8)
        for text in (
            "FOREIGN KEY(project_id,inbox_id,video_id,source_id)",
            "FOREIGN KEY(project_id,quote_id,run_id)",
            "FOREIGN KEY(project_id,approval_id,run_id,quote_id)",
            "trg_group_b_run_lineage", "trg_group_b_inbox_guard",
            "trg_group_b_quote_immutable", "trg_group_b_review_immutable",
        ):
            self.assertIn(text, data)
        for forbidden in ("DROP TABLE", "ALTER TABLE content_b_", "CREATE TABLE content_b_"):
            self.assertNotIn(forbidden, data)


def safe_test_dsn():
    raw = os.getenv("CM_TEST_PG_DSN", "")
    if not raw:
        return None
    from psycopg.conninfo import conninfo_to_dict
    name = conninfo_to_dict(raw).get("dbname", "")
    if "test" not in name.lower() and "_ci" not in name.lower():
        raise RuntimeError("Refusing non-disposable PostgreSQL database")
    return raw


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"), "No disposable PostgreSQL DSN")
class GroupBPostgresRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.dsn = safe_test_dsn()
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            cls.first_install = apply_migrations(conn)
            assert inspect_runtime_schema(conn).installed

    def setUp(self):
        import psycopg
        self.conn = psycopg.connect(self.dsn, autocommit=False)
        self.pid = uuid4().hex
        self.pid2 = uuid4().hex
        self.source = "gb-test-" + uuid4().hex
        self.source2 = "gb-test-" + uuid4().hex
        key = uuid4().hex
        self.conn.execute(
            """INSERT INTO project_registry(id,name,profile_revision,profile_hash,created_at,updated_at)
            VALUES (%s,'test1',1,%s,now(),now()),(%s,'test2',1,%s,now(),now())""",
            (self.pid,HASH,self.pid2,HASH))
        self.conn.execute(
            """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
            VALUES (%s,'test','owned','enabled',TRUE),(%s,'test','owned','enabled',TRUE)""",
            (self.source,self.source2))
        self.vid = self.conn.execute(
            """INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
            VALUES ('tiktok',%s,%s,'video') RETURNING id""",
            (key, "https://example.test/video/"+key)).fetchone()[0]

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def inbox(self, *, event=None):
        return self.conn.execute(
            """INSERT INTO group_b_inbox(project_id,video_id,source_id,event_key,qdp_run_id,
                producer_schema,event_sha256,preparation_sha256)
            VALUES (%s,%s,%s,%s,%s,'qdp-handoff/v1',%s,%s) RETURNING id""",
            (self.pid,self.vid,self.source,event or uuid4().hex,uuid4(),HASH,"b"*64)
        ).fetchone()[0]

    def run(self, inbox_id, *, project=None, a_id=None, kind="video"):
        return self.conn.execute(
            """INSERT INTO group_b_runs(project_id,inbox_id,video_id,source_id,
                content_type,video_a_analysis_id,config_sha256,idempotency_key)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (project or self.pid,inbox_id,self.vid,self.source,kind,a_id,"c"*64,uuid4().hex)
        ).fetchone()[0]

    def test_second_application_noop_and_readonly_inspection(self):
        self.assertTrue(inspect_runtime_schema(self.conn).installed)
        require_runtime_schema(self.conn)
        self.assertEqual(apply_migrations(self.conn), [])

    def test_composite_fk_and_idempotency(self):
        inbox = self.inbox(event="same-event")
        run = self.run(inbox)
        self.conn.execute(
            "INSERT INTO group_b_block_states(project_id,run_id,block) VALUES (%s,%s,'B1')",
            (self.pid,run))
        self.assertEqual(self.conn.execute(
            "SELECT status FROM group_b_block_states WHERE run_id=%s",(run,)
        ).fetchone()[0],"not_run")
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.inbox(event="same-event")
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.run(inbox,project=self.pid2)
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    "INSERT INTO group_b_block_states(project_id,run_id,block) VALUES (%s,%s,'B2')",
                    (self.pid2,run))

    def test_source_mismatched_a_pointer_rejected(self):
        a = self.conn.execute(
            """INSERT INTO video_feature_a_analyses(video_id,source_id,analysis_key,
               analysis_version,method_name)
               VALUES (%s,%s,%s,'v1','fixture') RETURNING id""",
            (self.vid,self.source2,uuid4().hex)
        ).fetchone()[0]
        inbox = self.inbox()
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.run(inbox,a_id=a)
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.run(inbox,a_id=a,kind="carousel")

    def test_model_attempt_fk_and_append_only_review(self):
        run = self.run(self.inbox())
        quote = self.conn.execute(
            """INSERT INTO group_b_model_quotes(project_id,run_id,model_id,request_sha256,
             evidence_sha256,quote_sha256,quoted_usd,pricing_snapshot,quote_details,expires_at)
             VALUES (%s,%s,'provider/model',%s,%s,%s,1,'{}','{}',now()+interval '10 minutes')
             RETURNING id""",(self.pid,run,HASH,"b"*64,"c"*64)
        ).fetchone()[0]
        approval = self.conn.execute(
            """INSERT INTO group_b_model_approvals(project_id,run_id,quote_id,
             approved_by,authorized_usd) VALUES (%s,%s,%s,'tester',1) RETURNING id""",
            (self.pid,run,quote)).fetchone()[0]
        self.conn.execute(
            """INSERT INTO group_b_model_attempts(project_id,run_id,quote_id,approval_id,
             request_key,request_sha256,reserved_usd) VALUES (%s,%s,%s,%s,%s,%s,1)""",
            (self.pid,run,quote,approval,uuid4().hex,HASH))
        self.conn.execute(
            """INSERT INTO group_b_review_events(project_id,run_id,revision,reviewer_id,
             decision_sha256,decision) VALUES (%s,%s,1,'tester',%s,'{}')""",
            (self.pid,run,HASH))
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    "UPDATE group_b_model_quotes SET quoted_usd=2 WHERE id=%s",(quote,))
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    "UPDATE group_b_review_events SET revision=2 WHERE run_id=%s",(run,))
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    """INSERT INTO group_b_model_approvals(project_id,run_id,quote_id,approved_by,authorized_usd)
                    VALUES (%s,%s,%s,'foreign',1)""",(self.pid2,run,quote))

    def test_sha_tamper_detected_without_persistent_mutation(self):
        self.conn.execute(
            "UPDATE schema_migrations SET sha256=%s WHERE version=%s",
            ("0"*64,MIGRATION_NAME))
        with self.assertRaisesRegex(RuntimeError,"Migration modified"):
            apply_migrations(self.conn)
        self.assertFalse(inspect_runtime_schema(self.conn).checksum_matches)
        with self.assertRaises(DependencyUnavailable):
            require_runtime_schema(self.conn)

    def test_concurrent_reapplication_serializes(self):
        def again(_):
            import psycopg
            with psycopg.connect(self.dsn,autocommit=True) as conn:
                return apply_migrations(conn)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(again,range(4)))
        self.assertEqual(results,[[],[],[],[]])


if __name__ == "__main__":
    unittest.main()
