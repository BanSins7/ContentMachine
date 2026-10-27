"""GC-MOD-03 PG17 schema/upgrade QA. No live source data or scoring.

PG tests run ONLY on an explicit disposable database. Never reuse production.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "intelligence" / "migrations"
MIGRATION = MIGRATIONS / "056_group_c_runtime.sql"
COHORT_MIGRATION = MIGRATIONS / "057_group_c_cohort_freezes.sql"
REVIEW_MIGRATION = MIGRATIONS / "058_group_c_human_reviews.sql"
ACTIVATION_MIGRATION = MIGRATIONS / "059_group_c_config_activation.sql"


class GroupCRuntimeStatic(unittest.TestCase):
    def test_additive_standalone_schema(self):
        src = MIGRATION.read_text(encoding="utf-8").lower()
        self.assertTrue(src.startswith("-- gc-mod-03:"))
        self.assertNotRegex(src, r"\b(drop\s+table|truncate\s+table|alter\s+table)\b")
        for table in ("group_c_inbox", "group_c_inbox_conflicts", "group_c_runs",
                      "group_c_jobs", "group_c_revisions", "group_c_outbox"):
            self.assertIn("create table " + table + " (", src)
        for existing in ("videos", "metric_snapshots", "source_video_metadata",
                         "calculated_metrics", "metric_calculation_configs"):
            self.assertNotRegex(src, r"create\s+table\s+" + existing + r"\b")
        self.assertIn("references metric_snapshots(id,video_id)", src)
        self.assertIn("references source_video_metadata(id,video_id)", src)
        self.assertIn("references calculated_metrics(id)", src)
        self.assertIn("group c run requires durably accepted inbox", src)
        self.assertIn("group c outbox requires committed matching complete c score", src)
        self.assertIn("group c inbox identity immutable", src)
        self.assertIn("group c revision is append-only", src)
        self.assertIn("lease_generation bigint not null default 0", src)
        self.assertIn("unique(project_id,idempotency_sha256)", src)
        self.assertNotRegex(src, r"(?m)^\s*(?:select|insert into|update)\s+(?:qdp_|ga_|group_b_)")

    def test_migration_filename_sequence_and_neighbour_collision(self):
        names = [p.name for p in MIGRATIONS.glob("[0-9]*.sql")]
        self.assertEqual(names.count(MIGRATION.name), 1)
        # This independent branch must NOT copy sibling Group A/B migrations.
        # Sibling 054/055 migrations may be present after independent merges.
        self.assertTrue(COHORT_MIGRATION.is_file())
        self.assertTrue(REVIEW_MIGRATION.is_file())
        self.assertTrue(ACTIVATION_MIGRATION.is_file())
        self.assertEqual(names.count(ACTIVATION_MIGRATION.name),1)
        self.assertGreater(sorted(names).index(ACTIVATION_MIGRATION.name),sorted(names).index(REVIEW_MIGRATION.name))
        self.assertEqual(names.count(REVIEW_MIGRATION.name),1)
        self.assertGreater(sorted(names).index(REVIEW_MIGRATION.name), sorted(names).index(COHORT_MIGRATION.name))
        self.assertEqual(names.count(COHORT_MIGRATION.name), 1)
        self.assertGreater(sorted(names).index(COHORT_MIGRATION.name),
                           sorted(names).index(MIGRATION.name))
        self.assertTrue(all(not x.startswith("056_") or x == MIGRATION.name for x in names))
        self.assertTrue((MIGRATIONS / "003_performance_c.sql").is_file())
        self.assertTrue((MIGRATIONS / "004_performance_c_integrity.sql").is_file())
        self.assertGreater(sorted(names).index(MIGRATION.name),
                           sorted(names).index("053_ke_c_qualified_execution.sql"))


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB") == "YES",
                     "requires CM_TEST_PG_DSN + GC_TEST_DISPOSABLE_DB=YES on disposable PG17")
class GroupCRuntimePostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.migrate = migrate
        cls.conn = psycopg.connect(os.environ["CM_TEST_PG_DSN"], autocommit=True)
        version = int(cls.conn.execute("SHOW server_version_num").fetchone()[0])
        if not (170000 <= version < 180000):
            raise AssertionError("GC stage03 PG verification requires PostgreSQL 17")
        if os.getenv("GC_TEST_MIGRATE_MODE") == "upgrade":
            # Simulate an existing, already-migrated main DB: apply every
            # migration EXCEPT new Group C 056, then restore original path and
            # apply only the new migration. No dropped/truncated objects.
            with tempfile.TemporaryDirectory(prefix="gc-upgrade-") as temp:
                shadow = Path(temp)
                for source in MIGRATIONS.glob("[0-9]*.sql"):
                    if source.name not in (MIGRATION.name, COHORT_MIGRATION.name, REVIEW_MIGRATION.name, ACTIVATION_MIGRATION.name):
                        shutil.copyfile(source, shadow / source.name)
                original = migrate.MIGRATIONS
                try:
                    migrate.MIGRATIONS = shadow
                    cls.baseline_installed = migrate.apply_migrations(cls.conn)
                finally:
                    migrate.MIGRATIONS = original
            before = cls.conn.execute("SELECT to_regclass('public.group_c_inbox')").fetchone()[0]
            if before is not None:
                raise AssertionError("pre-056 database unexpectedly has Group C runtime")
            cls.installed = migrate.apply_migrations(cls.conn)
            if cls.installed != [MIGRATION.name, COHORT_MIGRATION.name, REVIEW_MIGRATION.name, ACTIVATION_MIGRATION.name]:
                raise AssertionError("upgrade must apply ONLY Group C 056+057+058+059")
        else:
            cls.installed = migrate.apply_migrations(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.project = uuid4().hex
        self.source = "gc-ci-" + uuid4().hex
        self.conn.execute("""INSERT INTO project_registry
          (id,name,profile_revision,profile_hash,created_at,updated_at)
          VALUES (%s,'Group C disposable',1,%s,now(),now())""",
                          (self.project, "a" * 64))
        self.conn.execute("""INSERT INTO sources
          (id,provider,source_type,status,can_store_metrics,can_derive_scores)
          VALUES (%s,'gc_test','manual','enabled',TRUE,TRUE)""", (self.source,))
        self.video = self.conn.execute("""INSERT INTO videos
          (platform,platform_video_id,canonical_url,c_content_type)
          VALUES ('youtube',%s,%s,'video') RETURNING id""",
          ("gc-ci-" + uuid4().hex,"https://www.youtube.com/watch?v=" + uuid4().hex[:11])).fetchone()[0]
        self.event = str(uuid4())
        self.run_id = str(uuid4())

    def tearDown(self):
        self.conn.execute("DELETE FROM project_registry WHERE id=%s", (self.project,))
        self.conn.execute("DELETE FROM videos WHERE id=%s", (self.video,))
        self.conn.execute("DELETE FROM sources WHERE id=%s", (self.source,))

    def insert_inbox(self):
        self.conn.execute("""INSERT INTO group_c_inbox
          (event_id,project_id,video_id,qdp_run_id,producer_schema,input_sha256,payload_sha256)
          VALUES (%s,%s,%s,%s,'qdp-handoff/v1',%s,%s)""",
          (self.event,self.project,self.video,str(uuid4()),"b" * 64,"c" * 64))

    def accept(self):
        self.conn.execute("""UPDATE group_c_inbox SET state='accepted',accepted_at=now()
          WHERE event_id=%s""", (self.event,))

    def insert_run(self, *, video=None, input_sha=None):
        return self.conn.execute("""INSERT INTO group_c_runs
          (id,project_id,video_id,inbox_event_id,qdp_input_sha256,source_id,idempotency_sha256)
          VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
          (self.run_id,self.project,self.video if video is None else video,
           self.event,"b"*64 if input_sha is None else input_sha,self.source,"d"*64)).fetchone()[0]

    def test_fresh_schema_upgrade_sha_and_reentry(self):
        expected = hashlib.sha256(MIGRATION.read_bytes()).hexdigest()
        persisted = self.conn.execute("""SELECT sha256 FROM schema_migrations
             WHERE version='056_group_c_runtime.sql'""").fetchone()
        self.assertEqual(persisted, (expected,))
        self.assertEqual(self.migrate.apply_migrations(self.conn), [])
        if os.getenv("GC_TEST_MIGRATE_MODE") == "upgrade":
            self.assertEqual(self.installed, [MIGRATION.name, COHORT_MIGRATION.name, REVIEW_MIGRATION.name, ACTIVATION_MIGRATION.name])
        else:
            self.assertIn(MIGRATION.name, self.installed)
        for table in ("group_c_inbox", "group_c_inbox_conflicts", "group_c_runs",
                      "group_c_jobs", "group_c_revisions", "group_c_outbox"):
            self.assertIsNotNone(self.conn.execute("SELECT to_regclass(%s)",
              ("public."+table,)).fetchone()[0])
        # No shadow post/metric tables.
        self.assertIsNone(self.conn.execute("SELECT to_regclass('public.group_c_metric_snapshots')").fetchone()[0])

    def test_inbox_run_job_revision_happy_path(self):
        self.insert_inbox()
        self.accept()
        self.assertEqual(str(self.insert_run()), self.run_id)
        job_id = self.conn.execute("""INSERT INTO group_c_jobs
          (project_id,run_id,video_id,job_kind,idempotency_sha256)
          VALUES (%s,%s,%s,'score',%s) RETURNING id""",
          (self.project,self.run_id,self.video,"e"*64)).fetchone()[0]
        self.assertEqual(self.conn.execute("SELECT state FROM group_c_jobs WHERE id=%s",(job_id,)).fetchone()[0],"queued")
        self.conn.execute("""UPDATE group_c_jobs SET state='running',attempts=1,
          lease_generation=1,lease_token=%s,lease_until=now()+interval '10 minutes',
          worker_id='gc-test' WHERE id=%s""",(str(uuid4()),job_id))
        self.conn.execute("""UPDATE group_c_jobs SET state='completed',lease_token=NULL,
          lease_until=NULL,worker_id=NULL WHERE id=%s""",(job_id,))
        self.assertEqual(self.conn.execute("SELECT state FROM group_c_jobs WHERE id=%s",(job_id,)).fetchone()[0],"completed")
        self.conn.execute("""INSERT INTO group_c_revisions
          (project_id,run_id,video_id,revision,revision_kind,evidence_sha256,evidence)
          VALUES (%s,%s,%s,1,'new_snapshot',%s,'{}'::jsonb)""",
          (self.project,self.run_id,self.video,"f"*64))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM group_c_revisions WHERE run_id=%s",(self.run_id,)).fetchone()[0],1)

    def test_event_replay_conflict_and_immutability(self):
        self.insert_inbox()
        with self.assertRaises(Exception):
            self.insert_inbox()
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE group_c_inbox SET payload_sha256=%s WHERE event_id=%s", ("f"*64,self.event))
        self.conn.execute("""INSERT INTO group_c_inbox_conflicts
          (event_id,observed_payload_sha256,observed_input_sha256,reason_code)
          VALUES (%s,%s,%s,'payload_conflict')""",(self.event,"f"*64,"b"*64))
        with self.assertRaises(Exception):
            self.conn.execute("""UPDATE group_c_inbox_conflicts SET reason_code='identity_conflict'
              WHERE event_id=%s""",(self.event,))
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE group_c_inbox SET state='accepted' WHERE event_id=%s", (self.event,))

    def test_no_run_without_verified_inbox_and_no_fake_completion(self):
        self.insert_inbox()
        with self.assertRaises(Exception):
            self.insert_run()
        self.accept()
        with self.assertRaises(Exception):
            self.insert_run(input_sha="f"*64)
        self.insert_run()
        with self.assertRaises(Exception):
            self.conn.execute("""UPDATE group_c_runs SET state='completed',completed_at=now()
              WHERE id=%s""",(self.run_id,))
        with self.assertRaises(Exception):
            self.conn.execute("""INSERT INTO group_c_outbox
              (project_id,run_id,video_id,calculated_metric_id,event_key,payload_sha256,payload)
              VALUES (%s,%s,%s,1,'groupc-fixture-event',%s,'{}'::jsonb)""",
              (self.project,self.run_id,self.video,"a"*64))

    def test_jobs_fencing_and_idempotency(self):
        self.insert_inbox()
        self.accept()
        self.insert_run()
        with self.assertRaises(Exception):
            self.insert_run()
        job_id = self.conn.execute("""INSERT INTO group_c_jobs
          (project_id,run_id,video_id,job_kind,idempotency_sha256)
          VALUES (%s,%s,%s,'score',%s) RETURNING id""",
          (self.project,self.run_id,self.video,"a"*64)).fetchone()[0]
        with self.assertRaises(Exception):
            self.conn.execute("""INSERT INTO group_c_jobs
              (project_id,run_id,video_id,job_kind,idempotency_sha256)
              VALUES (%s,%s,%s,'score',%s)""",
              (self.project,self.run_id,self.video,"a"*64))
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE group_c_jobs SET state='running' WHERE id=%s",(job_id,))
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE group_c_jobs SET idempotency_sha256=%s WHERE id=%s",("b"*64,job_id))
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE group_c_runs SET video_id=0 WHERE id=%s",(self.run_id,))

    def test_revision_no_rewrite(self):
        self.insert_inbox()
        self.accept()
        self.insert_run()
        self.conn.execute("""INSERT INTO group_c_revisions
          (project_id,run_id,video_id,revision,revision_kind,evidence_sha256,evidence)
          VALUES (%s,%s,%s,1,'manual_review',%s,'{}'::jsonb)""",
          (self.project,self.run_id,self.video,"f"*64))
        with self.assertRaises(Exception):
            self.conn.execute("""UPDATE group_c_revisions SET evidence='{"oops":1}'::jsonb
              WHERE run_id=%s""",(self.run_id,))
        with self.assertRaises(Exception):
            self.conn.execute("""INSERT INTO group_c_revisions
              (project_id,run_id,video_id,revision,revision_kind,evidence_sha256,evidence)
              VALUES (%s,%s,%s,1,'manual_review',%s,'{}'::jsonb)""",
              (self.project,self.run_id,self.video,"f"*64))


if __name__ == "__main__":
    unittest.main()
