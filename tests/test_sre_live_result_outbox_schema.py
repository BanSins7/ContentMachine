"""M36 schema source guards, migration manifest and disposable PG17 denials."""
from __future__ import annotations

import os
from pathlib import Path
import unittest
from uuid import uuid4

from intelligence.relevance.migration_guard import assess_applied,build_manifest

SQL_FILE=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"/"070_sre_live_result_outbox.sql"
SQL=SQL_FILE.read_text(encoding="utf-8")


class M36SchemaTests(unittest.TestCase):
    def test_true_paid_source_and_human_review_required_by_live_read_view(self):
        for part in (
            "CREATE VIEW sre_m36_live_reviewed_pairs",
            "FROM sre_live_verified_pair_results",
            "JOIN sre_live_opus_receipts receipt",
            "JOIN sre_readable_content_versions c",
            "FROM sre_live_human_reviews h",
            "receipt.generation_authenticated IS TRUE",
            "receipt.evidence_status='validated_untrusted_output'",
            "r.execution_mode='live' AND r.state='completed'",
            "h.base_result_id=v.result_id",
            "sre_live_source_tombstones",
        ):
            self.assertIn(part,SQL)

    def test_outbox_sql_rejects_fake_ready_and_forged_decision_coverage(self):
        for part in (
            "CREATE TABLE sre_live_result_outbox",
            "CHECK(kind IN ('ready','invalidated'))",
            "CREATE UNIQUE INDEX sre_m36_one_ready_per_live_item",
            "CREATE UNIQUE INDEX sre_m36_one_invalidation_per_ready",
            "BEFORE INSERT OR UPDATE OR DELETE ON sre_live_result_outbox",
            "M36 only current completed LIVE source/approved C9 result can publish",
            "M36 incomplete receipt-backed human reviewed C9 coverage",
            "NEW.payload->'decisions' IS DISTINCT FROM expected_decisions",
            "M36 UNKNOWN paid intent cannot publish",
            "OLD.state='acked'",
            "NEW.lease_generation<>OLD.lease_generation+1",
        ):
            self.assertIn(part,SQL)
        self.assertNotIn("CREATE OR REPLACE FUNCTION sre_m34_",SQL)
        self.assertNotIn("INSERT INTO sre_classification_results",SQL)
        self.assertNotIn("DROP TABLE",SQL)

    def test_durable_revocation_intent_and_live_only_sql_export(self):
        for part in (
            "CREATE TABLE sre_live_result_invalidation_intents",
            "sre_m36_source_tombstone_intent",
            "AFTER INSERT ON sre_live_source_tombstones",
            "sre_m36_human_supersession_intent",
            "AFTER INSERT ON sre_live_human_reviews",
            "CREATE VIEW sre_m36_live_only_export",
            "WHERE e.kind='ready'",
            "REVOKE ALL ON sre_live_result_outbox FROM PUBLIC",
            "REVOKE ALL ON sre_m36_live_only_export FROM PUBLIC",
        ):
            self.assertIn(part,SQL)

    def test_forward_only_070_and_migration_next_072_after_m39(self):
        self.assertIn("070_sre_live_result_outbox.sql",build_manifest())
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"072")
        self.assertEqual(SQL.count("$m36_outbox$"),2)
        self.assertEqual(SQL.count("$m36_intent$"),2)
        self.assertEqual(SQL.count("$m36_revoke$"),2)
        self.assertEqual(SQL.count("$m36_review$"),2)


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"disposable PostgreSQL17 required")
class M36PostgresNegativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.dsn=os.environ["CM_TEST_PG_DSN"]
        with psycopg.connect(cls.dsn,autocommit=True) as conn:
            migrate.apply_migrations(conn)

    def setUp(self):
        import psycopg
        self.conn=psycopg.connect(self.dsn)
        self.project=uuid4().hex
        self.conn.execute(
            """INSERT INTO project_registry
             (id,name,profile_revision,profile_hash,created_at,updated_at)
             VALUES (%s,'M36 fixture',1,%s,now(),now())""",
            (self.project,"a"*64))
        self.conn.execute("INSERT INTO sre_workspaces(project_id) VALUES (%s)",
                          (self.project,))

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_nonexistent_live_run_cannot_forge_ready_event(self):
        run=str(uuid4());ev=str(uuid4())
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    """INSERT INTO sre_live_result_outbox
                    (event_id,project_id,run_id,item_id,content_version_id,
                     kind,payload,event_sha256,actor_id)
                    VALUES(%s,%s,%s,100,200,'ready','{}'::jsonb,%s,'sre:test')""",
                    (ev,self.project,run,"f"*64))
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_live_result_outbox WHERE project_id=%s",
            (self.project,)).fetchone()[0],0)

    def test_no_synthetic_fixture_is_exposed_through_live_sql_view(self):
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_m36_live_only_export WHERE project_id=%s",
            (self.project,)).fetchone()[0],0)

    def test_invalid_invalidation_without_ready_source_rejected(self):
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    """INSERT INTO sre_live_result_invalidation_intents
                      (project_id,ready_event_id,reason_code)
                    VALUES(%s,%s,'source_revoked')""",
                    (self.project,str(uuid4())))
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_live_result_invalidation_intents WHERE project_id=%s",
            (self.project,)).fetchone()[0],0)


if __name__=="__main__":
    unittest.main()
