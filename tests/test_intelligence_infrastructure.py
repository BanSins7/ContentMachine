"""Infrastructure contracts; with CM_TEST_PG_DSN set also runs integration tests."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intelligence import config, health, jobs, migrate, bootstrap
from intelligence.contracts import SourceRights

class BasicContracts(unittest.TestCase):
    def test_db_is_opt_in(self):
        with mock.patch.dict(os.environ, {"CM_DB_ENABLED": "0"}):
            self.assertEqual(health.status()["database"], "not_configured")

    def test_health_never_exposes_dsn(self):
        with mock.patch.object(config, "enabled", return_value=True), mock.patch.object(config, "dsn", return_value="postgresql://name:secret@invalid.local/nope"):
            value = str(health.status())
            self.assertNotIn("secret", value)
            self.assertNotIn("invalid.local", value)

    def test_missing_source_rights_are_denied(self):
        rights = SourceRights("manual")
        self.assertFalse(rights.can_derive_scores)
        self.assertFalse(rights.can_analyze_media)
        self.assertFalse(rights.can_store_metrics)

    def test_bootstrap_does_not_rotate_secret(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(bootstrap, "ENV_FILE", Path(folder) / ".env"):
            self.assertTrue(bootstrap.write_local_env())
            old = (Path(folder)/".env").read_text()
            self.assertFalse(bootstrap.write_local_env())
            self.assertEqual(old, (Path(folder)/".env").read_text())

class PostgreSQLIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dsn = os.getenv("CM_TEST_PG_DSN")
        if not dsn:
            raise unittest.SkipTest("CM_TEST_PG_DSN not provided")
        import psycopg
        cls.conn = psycopg.connect(dsn, autocommit=True)
        migrate.apply_migrations(cls.conn)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def test_migration_idempotence(self):
        self.assertEqual(migrate.apply_migrations(self.conn), [])
        for name in ("sources", "videos", "creators", "metric_snapshots", "jobs",
                     "discoveries", "analytics_scores", "batches", "video_analyses"):
            row = self.conn.execute("SELECT to_regclass(%s)", ("public."+name,)).fetchone()
            self.assertIsNotNone(row[0], name)

    def test_jobs_claim_and_complete(self):
        import uuid
        key = "test-"+uuid.uuid4().hex
        with self.conn.transaction():
            job_id = jobs.enqueue(self.conn, "test", key, {"sample": 1})
            self.assertEqual(jobs.enqueue(self.conn, "test", key, {"sample": 2}), job_id)
        with self.conn.transaction():
            leased = jobs.lease_next(self.conn, "test-runner")
            self.assertIsNotNone(leased)
            self.assertEqual(leased["id"], job_id)
            self.assertEqual(leased["payload"], {"sample": 1})
            self.assertFalse(jobs.complete(self.conn, job_id, "other-worker"))
            self.assertTrue(jobs.complete(self.conn, job_id, "test-runner", {"done": True}))
            self.assertIsNone(jobs.lease_next(self.conn, "test-runner"))
        self.conn.execute("DELETE FROM jobs WHERE id=%s", (job_id,))

    def test_snapshot_null_not_zero_and_rights(self):
        import uuid
        sid = "test-"+uuid.uuid4().hex
        uid = "video-"+uuid.uuid4().hex
        self.conn.execute("INSERT INTO sources(id,provider,source_type) VALUES (%s,'test','manual')", (sid,))
        self.conn.execute("""INSERT INTO videos(platform,platform_video_id,canonical_url)
          VALUES ('youtube',%s,'https://www.youtube.com/shorts/test')""", (uid,))
        row = self.conn.execute("SELECT id FROM videos WHERE platform_video_id=%s", (uid,)).fetchone()
        video_id = row[0]
        self.conn.execute("""INSERT INTO metric_snapshots(video_id,source_id,observed_at,views,likes,shares)
           VALUES (%s,%s,now(),100,0,NULL)""", (video_id, sid))
        result = self.conn.execute("SELECT views,likes,shares FROM metric_snapshots WHERE video_id=%s", (video_id,)).fetchone()
        self.assertEqual(result, (100,0,None))
        with self.assertRaises(Exception):
            self.conn.execute("""INSERT INTO metric_snapshots(video_id,source_id,observed_at,views)
                VALUES (%s,%s,now(),-1)""", (video_id,sid))
        self.conn.execute("DELETE FROM videos WHERE id=%s", (video_id,))
        self.conn.execute("DELETE FROM sources WHERE id=%s", (sid,))
