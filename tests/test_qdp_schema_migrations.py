"""FIX-03 static + optional disposable PG17 schema verification."""
from __future__ import annotations

import os
import unittest

from intelligence.qualified_data.contracts import QDPError
from intelligence.qualified_data.schema import (
    BASE, INTEGRATION, INBOX_QUARANTINE, GROUP_PREPARATIONS, GROUP_METADATA_FIX, DELIVERY_ATTEMPTS, DELIVERY_CASCADE_FIX, INVALIDATION_GENERATION, HUMAN_CURRENT_READ, OPERATIONS_INDEXES, ROOT, migration_topology, verify_unchanged_base_sql,
    require_qdp_schema,
)

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None

DSN = os.environ.get("CM_TEST_PG_DSN")


class SchemaStaticTests(unittest.TestCase):
    def test_original_qdp_migration_is_bitwise_unchanged(self):
        verify_unchanged_base_sql()

    def test_integration_migration_is_forward_only_and_sql_present(self):
        sql = (ROOT / INTEGRATION).read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE qdp_sre_inbox", sql)
        for table in ("qdp_run_manifests","qdp_item_lineage","qdp_outbox",
                      "qdp_outbox_deliveries","qdp_delivery_receipts",
                      "qdp_invalidations"):
            self.assertIn("CREATE TABLE " + table, sql)
        self.assertIn("CREATE VIEW qdp_unrevoked_preparations", sql)
        quarantine = (ROOT / INBOX_QUARANTINE).read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE qdp_sre_inbox_quarantine", quarantine)
        self.assertIn("UNIQUE(event_id,observed_sha256,reason_code)", quarantine)
        current=(ROOT/GROUP_PREPARATIONS).read_text(encoding="utf-8")
        self.assertIn("CREATE VIEW qdp_current_base_enrichments",current)
        self.assertIn("CREATE VIEW qdp_group_current_preparations",current)
        forward=(ROOT/GROUP_METADATA_FIX).read_text(encoding="utf-8")
        self.assertIn("CREATE OR REPLACE VIEW qdp_current_base_enrichments",forward)
        self.assertNotIn("AND i.status IN",forward)
        journal=(ROOT/DELIVERY_ATTEMPTS).read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE qdp_delivery_attempt_log",journal)
        self.assertIn("BEFORE UPDATE OR DELETE",journal)
        self.assertNotIn("DROP TABLE",journal.upper())
        cascade=(ROOT/DELIVERY_CASCADE_FIX).read_text(encoding="utf-8")
        self.assertIn("CREATE OR REPLACE FUNCTION qdp_reject_delivery_attempt_log_mutation",cascade)
        self.assertIn("NOT EXISTS",cascade)
        self.assertNotIn("DROP TABLE",cascade.upper())
        rev=(ROOT/INVALIDATION_GENERATION).read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN invalidation_generation",rev)
        self.assertIn("NULLS NOT DISTINCT",rev)
        human=(ROOT/HUMAN_CURRENT_READ).read_text(encoding="utf-8")
        self.assertIn("CREATE OR REPLACE VIEW qdp_group_current_preparations",human)
        self.assertIn("sre_human_labels",human)
        indexes=(ROOT/OPERATIONS_INDEXES).read_text(encoding="utf-8")
        self.assertIn("idx_qdp_17_run_events_page",indexes)
        self.assertNotIn("DROP TABLE",indexes.upper())
        self.assertNotIn("DROP TABLE", sql.upper())
        self.assertNotIn("ALTER TABLE sre_", sql)
        # Provenance must persist for excluded/review videos with no qdp_item.
        lineage = sql.split("CREATE TABLE qdp_item_lineage (", 1)[1]
        lineage = lineage.split("CREATE INDEX idx_qdp_item_lineage_result", 1)[0]
        self.assertNotIn("REFERENCES qdp_items", lineage)
        self.assertIn("REFERENCES sre_project_videos", lineage)

    def test_latest_qdp_view_keeps_metadata_and_group_rights_independent(self):
        # The 074 modernization must not undo the 043 metadata fix.
        from intelligence.qualified_data.schema import CURRENT_METADATA_RESTORE
        sql=(ROOT/CURRENT_METADATA_RESTORE).read_text(encoding="utf-8")
        self.assertIn("CREATE OR REPLACE VIEW qdp_current_base_enrichments",sql)
        self.assertNotIn("AND i.status IN",sql)
        self.assertIn("qdp_sre_live_tombstones",sql)
        self.assertIn("sre_m36_live_only_export",sql)
        self.assertIn("outbox.state='acked'",sql)
        self.assertNotIn("CREATE OR REPLACE VIEW qdp_group_current_preparations",sql)

    def test_local_and_cross_module_migration_sort_order(self):
        files = [p.name for p in ROOT.glob("[0-9]*.sql")]
        report = migration_topology(files)
        self.assertTrue(report["after_base"])
        self.assertTrue(report["peer_live_sre_migration"])
        # Installed main already includes the peer SRE migration. Simulated
        # future peers must not duplicate an already-installed file.
        future = files + [x for x in (
            "016_collector_planning.sql", "017_collector_worker.sql",
            "016_analytics_unified.sql", "017_group_a_technical_extraction.sql")
            if x not in files]
        simulated = migration_topology(future)
        self.assertTrue(simulated["peer_live_sre_migration"])
        self.assertTrue(simulated["group_a_migration"])

    def test_missing_dependency_and_duplicates_fail_closed(self):
        with self.assertRaises(QDPError):
            migration_topology([BASE])
        with self.assertRaises(QDPError):
            migration_topology([BASE,INTEGRATION,INBOX_QUARANTINE,GROUP_PREPARATIONS,GROUP_METADATA_FIX,DELIVERY_ATTEMPTS,DELIVERY_CASCADE_FIX,INVALIDATION_GENERATION,HUMAN_CURRENT_READ,HUMAN_CURRENT_READ])


@unittest.skipUnless(DSN and psycopg is not None,
                     "Requires disposable CM_TEST_PG_DSN PostgreSQL 17")
class PostgresIntegrationMigrationTests(unittest.TestCase):
    def factory(self):
        return psycopg.connect(DSN,autocommit=True,row_factory=dict_row)

    def test_fresh_or_upgrade_and_repeat_is_noop(self):
        from intelligence.migrate import apply_migrations
        # migrate.apply_migrations uses positional rows; dict_row is reserved
        # for QDP repository/preflight assertions after migration.
        with psycopg.connect(DSN,autocommit=True) as migrator:
            apply_migrations(migrator)
            self.assertEqual(apply_migrations(migrator), [])
        with self.factory() as db:
            result = require_qdp_schema(db)
            self.assertIn(INTEGRATION,result["required"])
            self.assertIn(DELIVERY_ATTEMPTS,result["required"])
            self.assertIn(INVALIDATION_GENERATION,result["required"])
            self.assertIn(HUMAN_CURRENT_READ,result["required"])
            self.assertIn(OPERATIONS_INDEXES,result["required"])
            rows = db.execute(
                """SELECT conname FROM pg_constraint
                   WHERE conrelid='qdp_item_lineage'::regclass
                     AND contype='f'""").fetchall()
            self.assertGreaterEqual(len(rows),3)

    def test_tampered_checksum_detected_without_persisting_tamper(self):
        from intelligence.migrate import apply_migrations
        class RollbackSentinel(Exception):
            pass
        with psycopg.connect(DSN,autocommit=True) as db:
            apply_migrations(db)
            try:
                with db.transaction():
                    db.execute(
                      "UPDATE schema_migrations SET sha256=%s WHERE version=%s",
                      ("0"*64,INTEGRATION))
                    with self.assertRaises(RuntimeError):
                        apply_migrations(db)
                    raise RollbackSentinel()
            except RollbackSentinel:
                pass
        with self.factory() as db:
            require_qdp_schema(db)

    def test_inbox_constraints_and_fact_immutability(self):
        import uuid
        pid=uuid.uuid4().hex
        eid=str(uuid.uuid4())
        from intelligence.migrate import apply_migrations
        with psycopg.connect(DSN,autocommit=True) as migrator:
            apply_migrations(migrator)
        with self.factory() as db:
            db.execute("""INSERT INTO project_registry
                          (id,name,profile_revision,profile_hash,created_at,updated_at)
                          VALUES (%s,'qdp schema check',1,%s,now(),now())""",
                       (pid,"a"*64))
            try:
                db.execute(
                    """INSERT INTO qdp_sre_inbox
                       (event_id,project_id,sre_run_id,schema_version,event_type,
                        payload_sha256,payload)
                       VALUES (%s::uuid,%s,%s::uuid,'sre-analytics-event/v1',
                               'sre.classification_run_completed',%s,'{}'::jsonb)""",
                    (eid,pid,str(uuid.uuid4()),"b"*64))
                with self.assertRaises(psycopg.errors.UniqueViolation):
                    db.execute(
                        """INSERT INTO qdp_sre_inbox
                           (event_id,project_id,sre_run_id,schema_version,event_type,
                            payload_sha256,payload)
                           VALUES (%s::uuid,%s,%s::uuid,'sre-analytics-event/v1',
                                   'sre.classification_run_completed',%s,'{}'::jsonb)""",
                        (eid,pid,str(uuid.uuid4()),"b"*64))
                with self.assertRaises(psycopg.errors.CheckViolation):
                    db.execute("UPDATE qdp_sre_inbox SET state='accepted' WHERE event_id=%s::uuid",
                               (eid,))
                db.execute(
                    "UPDATE qdp_sre_inbox SET state='accepted',processed_at=now() WHERE event_id=%s::uuid",
                    (eid,))
                self.assertEqual(
                    db.execute("SELECT state FROM qdp_sre_inbox WHERE event_id=%s::uuid",
                               (eid,)).fetchone()["state"],"accepted")
            finally:
                db.execute("DELETE FROM qdp_sre_inbox WHERE event_id=%s::uuid",(eid,))
                db.execute("DELETE FROM project_registry WHERE id=%s",(pid,))


if __name__ == "__main__":
    unittest.main()
