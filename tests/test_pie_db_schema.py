"""PIE-MOD-04 database contract QA. No real UAE or paid provider is invoked.

PostgreSQL integration executes *only* with explicit disposable CM_TEST_PG_DSN.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "intelligence" / "migrations" / "200_pie_standalone_foundation.sql"
ALL_TABLES = (
    "pie_research_runs", "pie_dataset_manifests", "pie_dataset_members",
    "pie_comparison_sets", "pie_pattern_candidates", "pie_pattern_validations",
    "pie_insight_versions", "pie_insight_reviews", "pie_spend_authorizations",
    "pie_model_attempts", "pie_model_attempt_events", "pie_jobs",
    "pie_outbox", "pie_invalidation_events",
)
IMMUTABLE_TABLES = tuple(x for x in ALL_TABLES if x != "pie_jobs")


class PIEPostgresStaticTests(unittest.TestCase):
    def test_one_additive_isolated_migration_no_legacy_aliases(self):
        text = MIGRATION.read_text(encoding="utf-8")
        self.assertTrue(MIGRATION.name.startswith("200_pie_"))
        for name in ALL_TABLES:
            self.assertIn("CREATE TABLE " + name + " (", text)
            self.assertIn("pie_", name)
        for prohibited in ("DROP TABLE", "TRUNCATE ", "DELETE FROM ",
                           "CREATE TABLE videos", "CREATE TABLE analytics_unified_snapshots",
                           "CREATE TABLE calculated_metrics", "ALTER TABLE "):
            self.assertNotIn(prohibited, text.upper() if prohibited == "ALTER TABLE " else text)

    def test_fk_scope_and_evidence_immutability_declared(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("REFERENCES project_registry(id)", sql)
        self.assertIn("REFERENCES source_policy_versions(id)", sql)
        self.assertIn("REFERENCES videos(id)", sql)
        self.assertGreaterEqual(sql.count("FOREIGN KEY(project_id,"), 10)
        self.assertEqual(sql.count("EXECUTE FUNCTION pie_reject_evidence_update()"), len(IMMUTABLE_TABLES))
        self.assertIn("FOR UPDATE", sql)  # serialized spend approval reservation
        self.assertIn("UNIQUE(project_id,request_sha256)", sql)
        self.assertNotIn("CREATE TRIGGER pie_jobs_immutable", sql)

    def test_local_hash_stable_and_no_fake_migration_history(self):
        raw = MIGRATION.read_bytes()
        self.assertGreater(len(raw), 12000)
        self.assertEqual(raw, MIGRATION.read_text(encoding="utf-8").encode("utf-8"))
        self.assertNotIn(b"INSERT INTO schema_migrations", raw)
        self.assertNotIn(b"INSERT INTO pie_", raw)


@unittest.skipUnless(os.environ.get("CM_TEST_PG_DSN"), "Disposable CM_TEST_PG_DSN not set")
class PIEPostgresContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate

        cls.psycopg = psycopg
        cls.migrate = migrate
        cls.db = psycopg.connect(os.environ["CM_TEST_PG_DSN"], autocommit=True)
        name = cls.db.execute("SELECT current_database()").fetchone()[0]
        if "test" not in name or name in {"contentmachine", "postgres", "template1"}:
            cls.db.close()
            raise AssertionError("PIE schema tests require an explicitly named disposable test database")
        migrate.apply_migrations(cls.db)  # Fresh schema, including 200 PIE migration

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "db") and not cls.db.closed:
            cls.db.close()

    def setUp(self):
        self.one = uuid4().hex
        self.two = uuid4().hex
        self.s1 = "pie-test-source-" + uuid4().hex
        self.s2 = "pie-test-source-" + uuid4().hex
        self.sha = "a" * 64
        self.db.execute("""INSERT INTO project_registry
            (id,name,profile_revision,profile_hash,created_at,updated_at)
            VALUES (%s,'PIE synthetic test',1,%s,now(),now()),
                   (%s,'PIE synthetic test 2',1,%s,now(),now())""",
            (self.one, self.sha, self.two, self.sha))
        for source in (self.s1, self.s2):
            self.db.execute("""INSERT INTO sources(id,provider,source_type,status)
                VALUES (%s,'pie_test','manual','enabled')""", (source,))
        self.p1 = self.db.execute("""INSERT INTO source_policy_versions
            (source_id,version,is_active) VALUES (%s,1,FALSE) RETURNING id""",
            (self.s1,)).fetchone()[0]
        self.p2 = self.db.execute("""INSERT INTO source_policy_versions
            (source_id,version,is_active) VALUES (%s,1,FALSE) RETURNING id""",
            (self.s2,)).fetchone()[0]

    def tearDown(self):
        # Project ON DELETE CASCADE legitimately purges PIE derivatives.
        self.db.execute("DELETE FROM project_registry WHERE id IN (%s,%s)", (self.one,self.two))
        self.db.execute("DELETE FROM sources WHERE id IN (%s,%s)", (self.s1,self.s2))

    def run_row(self, project=None, source=None, policy=None):
        return self.db.execute("""INSERT INTO pie_research_runs
           (project_id,source_id,source_policy_version_id,calculation_config_id,
            design_sha256,engine_version,as_of)
           VALUES (%s,%s,%s,1,%s,'pie-fixture/v1',now()) RETURNING id""",
           (self.one if project is None else project,
            self.s1 if source is None else source,
            self.p1 if policy is None else policy,self.sha)).fetchone()[0]

    def dataset(self, run, project=None, digest=None):
        return self.db.execute("""INSERT INTO pie_dataset_manifests
          (project_id,run_id,dataset_sha256,manifest_sha256,producer_run_key,
           input_schema,included_count,excluded_count,selection_spec,provenance)
          VALUES (%s,%s,%s,%s,'synthetic-uuid','uae-to-pie/v1',1,0,'{}','{}') RETURNING id""",
          (self.one if project is None else project,run,digest or self.sha,"b"*64)).fetchone()[0]

    def post(self):
        key=uuid4().hex
        return self.db.execute("""INSERT INTO videos(platform,platform_video_id,canonical_url)
          VALUES ('youtube',%s,%s) RETURNING id""",
          (key,"https://www.youtube.com/watch?v="+key)).fetchone()[0]

    def test_fresh_idempotent_migration_and_version_checksum(self):
        self.assertEqual(self.migrate.apply_migrations(self.db),[])
        sha = hashlib.sha256(MIGRATION.read_bytes()).hexdigest()
        actual = self.db.execute("SELECT sha256 FROM schema_migrations WHERE version=%s",
                                 (MIGRATION.name,)).fetchone()
        self.assertEqual(actual,(sha,))
        for name in ALL_TABLES:
            self.assertIsNotNone(self.db.execute("SELECT to_regclass(%s)",(name,)).fetchone()[0])
        self.assertIsNotNone(self.db.execute("SELECT to_regclass('videos')").fetchone()[0])

    def test_source_policy_mismatch_and_unknown_project_fail_closed(self):
        with self.assertRaises(self.psycopg.Error):
            self.run_row(source=self.s1,policy=self.p2)
        with self.assertRaises(self.psycopg.Error):
            self.run_row(project="f"*32)
        self.assertIsNotNone(self.run_row())

    def test_project_isolation_and_same_dataset_comparison_reference(self):
        run=self.run_row()
        ds=self.dataset(run)
        with self.assertRaises(self.psycopg.Error):
            self.dataset(run,project=self.two,digest="c"*64)
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("""INSERT INTO pie_comparison_sets
              (project_id,dataset_id,definition_sha256,definition)
              VALUES (%s,%s,%s,'{}')""",(self.two,ds,self.sha))
        comparison = self.db.execute("""INSERT INTO pie_comparison_sets
          (project_id,dataset_id,definition_sha256,definition)
          VALUES (%s,%s,%s,'{}') RETURNING id""",(self.one,ds,self.sha)).fetchone()[0]
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("""INSERT INTO pie_pattern_candidates
               (project_id,dataset_id,comparison_id,hypothesis_sha256,evidence_sha256,candidate)
               VALUES (%s,%s,%s,%s,%s,'{}')""",(self.two,ds,comparison,self.sha,"b"*64))
        other_dataset=self.dataset(run,digest="c"*64)
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("""INSERT INTO pie_pattern_candidates
               (project_id,dataset_id,comparison_id,hypothesis_sha256,evidence_sha256,candidate)
               VALUES (%s,%s,%s,%s,%s,'{}')""",(self.one,other_dataset,comparison,self.sha,"b"*64))

    def test_membership_video_identity_dedup_and_update_immutable(self):
        run=self.run_row()
        ds=self.dataset(run)
        post=self.post()
        try:
            self.db.execute("""INSERT INTO pie_dataset_members
                (project_id,dataset_id,video_id,uae_snapshot_id,row_sha256,included)
                VALUES (%s,%s,%s,9,%s,TRUE)""",(self.one,ds,post,self.sha))
            with self.assertRaises(self.psycopg.Error):
                self.db.execute("""INSERT INTO pie_dataset_members
                    (project_id,dataset_id,video_id,uae_snapshot_id,row_sha256,included)
                    VALUES (%s,%s,%s,10,%s,TRUE)""",(self.one,ds,post,self.sha))
            with self.assertRaises(self.psycopg.Error):
                self.db.execute("UPDATE pie_dataset_members SET row_sha256=%s WHERE video_id=%s",
                                ("b"*64,post))
            with self.assertRaises(self.psycopg.Error):
                self.db.execute("""INSERT INTO pie_dataset_members
                    (project_id,dataset_id,video_id,included)
                    VALUES (%s,%s,%s,FALSE)""",(self.one,ds,post+9999999))
            with self.assertRaises(self.psycopg.Error):
                self.db.execute("UPDATE pie_research_runs SET status='invalidated' WHERE id=%s",(run,))
        finally:
            self.db.execute("DELETE FROM videos WHERE id=%s",(post,))

    def test_duplicate_dataset_and_invalid_unknown_evidence(self):
        run=self.run_row()
        ds=self.dataset(run)
        with self.assertRaises(self.psycopg.Error):
            self.dataset(run)
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("UPDATE pie_dataset_manifests SET evidence_state='verified' WHERE id=%s",(ds,))
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("""INSERT INTO pie_dataset_manifests
              (project_id,run_id,dataset_sha256,manifest_sha256,producer_run_key,
               input_schema,included_count,excluded_count,selection_spec,provenance)
              VALUES (%s,%s,%s,%s,'x','uae-pattern-handoff/v1',2000,0,'{}','{}')""",
              (self.one,run,"d"*64,"b"*64))

    def test_budget_reservation_limit_replay_and_no_paid_calls(self):
        auth=self.db.execute("""INSERT INTO pie_spend_authorizations
           (project_id,approved_by,approved_usd,expires_at,approval_sha256)
           VALUES (%s,'synthetic-reviewer',2,now()+interval '1 hour',%s) RETURNING id""",
           (self.one,self.sha)).fetchone()[0]
        request="a"*64
        attempt=self.db.execute("""INSERT INTO pie_model_attempts
            (project_id,authorization_id,request_sha256,reserved_usd)
            VALUES (%s,%s,%s,1.5) RETURNING id""",(self.one,auth,request)).fetchone()[0]
        self.assertIsNotNone(attempt)
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("""INSERT INTO pie_model_attempts
                (project_id,authorization_id,request_sha256,reserved_usd)
                VALUES (%s,%s,%s,0.1)""",(self.one,auth,request))
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("""INSERT INTO pie_model_attempts
                (project_id,authorization_id,request_sha256,reserved_usd)
                VALUES (%s,%s,%s,1)""",(self.one,auth,"b"*64))
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("UPDATE pie_model_attempts SET reserved_usd=0 WHERE id=%s",(attempt,))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM pie_model_attempt_events WHERE project_id=%s", (self.one,)).fetchone()[0],0)

    def test_append_only_insights_outbox_and_job_fencing_schema(self):
        run=self.run_row()
        job=self.db.execute("""INSERT INTO pie_jobs
          (project_id,run_id,kind,idempotency_key,max_attempts)
          VALUES (%s,%s,'dataset','synthetic-idem-key',3) RETURNING id""",
          (self.one,run)).fetchone()[0]
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("""INSERT INTO pie_jobs
              (project_id,run_id,kind,idempotency_key,max_attempts)
              VALUES (%s,%s,'dataset','synthetic-idem-key',3)""",(self.one,run))
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("UPDATE pie_jobs SET state='leased' WHERE id=%s",(job,))
        event=self.db.execute("""INSERT INTO pie_outbox
          (project_id,run_id,event_key,event_type,payload_sha256,payload)
          VALUES (%s,%s,'event-one','test_event',%s,'{}') RETURNING id""",
          (self.one,run,self.sha)).fetchone()[0]
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("UPDATE pie_outbox SET payload=%s WHERE id=%s",('{"x":1}',event))
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("""INSERT INTO pie_outbox
              (project_id,run_id,event_key,event_type,payload_sha256,payload)
              VALUES (%s,%s,'event-two','test_event',%s,'{}')""",
              (self.two,run,self.sha))


@unittest.skipUnless(os.environ.get("CM_TEST_PG_UPGRADE_DSN"),
                     "Disposable CM_TEST_PG_UPGRADE_DSN not set")
class PIEUpgradeMigrationTests(unittest.TestCase):
    def test_existing_main_install_then_add_pie_and_verify_checksum(self):
        import psycopg
        from intelligence import migrate
        with psycopg.connect(os.environ["CM_TEST_PG_UPGRADE_DSN"],autocommit=True) as conn:
            name=conn.execute("SELECT current_database()").fetchone()[0]
            if "test" not in name or name in {"postgres","contentmachine"}:
                self.fail("Must use explicitly disposable test database")
            with tempfile.TemporaryDirectory() as d:
                base=Path(d)
                for path in sorted(migrate.MIGRATIONS.glob("[0-9]*.sql")):
                    if path.name != MIGRATION.name:
                        (base / path.name).write_bytes(path.read_bytes())
                original=migrate.MIGRATIONS
                try:
                    migrate.MIGRATIONS=base
                    installed=migrate.apply_migrations(conn)
                    self.assertTrue(installed)
                    self.assertIsNone(conn.execute("SELECT to_regclass('pie_research_runs')").fetchone()[0])
                finally:
                    migrate.MIGRATIONS=original
            upgraded=migrate.apply_migrations(conn)
            self.assertEqual(upgraded,[MIGRATION.name])
            self.assertEqual(migrate.apply_migrations(conn),[])
            self.assertIsNotNone(conn.execute("SELECT to_regclass('pie_research_runs')").fetchone()[0])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=%s",
                (MIGRATION.name,)).fetchone()[0],1)


if __name__ == "__main__":
    unittest.main()
