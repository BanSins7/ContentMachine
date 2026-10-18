"""D1.1 Discovery schema regression and PostgreSQL integration tests."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intelligence import migrate

SCHEMA = Path(__file__).resolve().parents[1] / "intelligence" / "migrations" / "002_discovery.sql"
NEW_TABLES = (
    "source_policy_versions", "search_runs", "source_requests",
    "source_video_metadata", "creator_topics", "video_topics",
    "monitor_targets", "source_quota_ledger", "data_quality_flags", "human_labels",
)


class MigrationStaticTests(unittest.TestCase):
    def test_additive_migration_is_present(self):
        sql = SCHEMA.read_text(encoding="utf-8").lower()
        for forbidden in ("drop table", "truncate ", "delete from", "update videos"):
            self.assertNotIn(forbidden, sql)
        self.assertIn("alter table sources", sql)
        self.assertIn("lease_generation", sql)
        self.assertIn("on discoveries(discovery_key)", sql)


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"), "CM_TEST_PG_DSN not provided")
class DiscoveryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.conn = psycopg.connect(os.environ["CM_TEST_PG_DSN"], autocommit=True)
        migrate.apply_migrations(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.source_id = "d11-" + uuid4().hex
        self.runs = []
        self.videos = []
        self.jobs = []
        self.conn.execute(
            "INSERT INTO sources(id,provider,source_type) VALUES (%s,'test','manual')",
            (self.source_id,),
        )

    def tearDown(self):
        for video in self.videos:
            self.conn.execute("DELETE FROM videos WHERE id=%s", (video,))
        for run in self.runs:
            self.conn.execute("DELETE FROM search_runs WHERE id=%s", (run,))
        for job in self.jobs:
            self.conn.execute("DELETE FROM jobs WHERE id=%s", (job,))
        self.conn.execute("DELETE FROM source_requests WHERE source_id=%s", (self.source_id,))
        self.conn.execute("DELETE FROM source_video_metadata WHERE source_id=%s", (self.source_id,))
        self.conn.execute("DELETE FROM monitor_targets WHERE source_id=%s", (self.source_id,))
        self.conn.execute("DELETE FROM source_quota_ledger WHERE source_id=%s", (self.source_id,))
        self.conn.execute("DELETE FROM source_policy_versions WHERE source_id=%s", (self.source_id,))
        self.conn.execute("DELETE FROM sources WHERE id=%s", (self.source_id,))

    def _run(self):
        run = self.conn.execute(
            """INSERT INTO search_runs(idempotency_key,config,planner_version)
               VALUES (%s,'{}'::jsonb,'D1.1') RETURNING id""",
            (uuid4().hex,),
        ).fetchone()[0]
        self.runs.append(run)
        return run

    def _video(self):
        value = uuid4().hex
        video = self.conn.execute(
            """INSERT INTO videos(platform,platform_video_id,canonical_url)
               VALUES ('youtube',%s,%s) RETURNING id""",
            (value, "https://www.youtube.com/watch?v=" + value),
        ).fetchone()[0]
        self.videos.append(video)
        return video

    def test_all_tables_exist_and_migration_is_reentrant(self):
        self.assertEqual(migrate.apply_migrations(self.conn), [])
        for table in NEW_TABLES:
            with self.subTest(table=table):
                result = self.conn.execute("SELECT to_regclass(%s)", ("public." + table,)).fetchone()[0]
                self.assertIsNotNone(result)
        versions = self.conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        self.assertIn(("001_foundation.sql",), versions)
        self.assertIn(("002_discovery.sql",), versions)

    def test_new_source_rights_default_to_deny(self):
        values = self.conn.execute(
            """SELECT can_search,can_store_metadata,can_store_metrics,
                      can_derive_scores,can_analyze_media FROM sources WHERE id=%s""",
            (self.source_id,),
        ).fetchone()
        self.assertEqual(values, (False, False, False, False, False))

    def test_one_active_policy_and_typed_capabilities(self):
        self.conn.execute(
            "INSERT INTO source_policy_versions(source_id,version,is_active) VALUES (%s,1,TRUE)",
            (self.source_id,),
        )
        with self.assertRaises(Exception):
            self.conn.execute(
                "INSERT INTO source_policy_versions(source_id,version,is_active) VALUES (%s,2,TRUE)",
                (self.source_id,),
            )
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO source_policy_versions(source_id,version,capabilities)
                   VALUES (%s,3,'[]'::jsonb)""",
                (self.source_id,),
            )
        self.conn.execute(
            "INSERT INTO source_policy_versions(source_id,version) VALUES (%s,2)",
            (self.source_id,),
        )

    def test_run_request_and_attempt_uniqueness(self):
        run = self._run()
        key = "request:" + uuid4().hex
        for attempt in (1, 2):
            self.conn.execute(
                """INSERT INTO source_requests(run_id,source_id,logical_request_key,attempt_no)
                   VALUES (%s,%s,%s,%s)""",
                (run,self.source_id,key,attempt),
            )
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO source_requests(run_id,source_id,logical_request_key,attempt_no)
                   VALUES (%s,%s,%s,2)""",
                (run,self.source_id,key),
            )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM source_requests WHERE run_id=%s",(run,)).fetchone()[0],
            2,
        )

    def test_discoveries_keep_legacy_and_dedupe_by_new_key(self):
        video = self._video()
        run = self._run()
        key = "discovery:" + uuid4().hex
        self.conn.execute(
            """INSERT INTO discoveries(video_id,source_id,method,run_id,discovery_key)
               VALUES (%s,%s,'fixture',%s,%s)""",
            (video,self.source_id,run,key),
        )
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO discoveries(video_id,source_id,method,run_id,discovery_key)
                   VALUES (%s,%s,'fixture',%s,%s)""",
                (video,self.source_id,run,key),
            )
        self.conn.execute(
            "INSERT INTO discoveries(video_id,source_id,method) VALUES (%s,%s,'legacy')",
            (video,self.source_id),
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM discoveries WHERE video_id=%s",(video,)).fetchone()[0],
            2,
        )

    def test_metadata_ttl_and_missing_vs_zero(self):
        video = self._video()
        self.conn.execute(
            """INSERT INTO source_video_metadata(video_id,source_id,fetched_at,title,published_at)
               VALUES (%s,%s,now(),'Example',NULL)""",
            (video,self.source_id),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT title,published_at FROM source_video_metadata WHERE video_id=%s",(video,)
            ).fetchone(),
            ("Example",None),
        )
        self.conn.execute(
            """INSERT INTO metric_snapshots(
                   video_id,source_id,observed_at,views,likes,shares,observation_time_assumed
               ) VALUES (%s,%s,now(),0,NULL,NULL,TRUE)""",
            (video,self.source_id),
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT views,likes,shares,observation_time_assumed
                   FROM metric_snapshots WHERE video_id=%s""", (video,)
            ).fetchone(),
            (0,None,None,True),
        )
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO source_video_metadata(video_id,source_id,fetched_at,expires_at)
                   VALUES (%s,%s,now(),now()-interval '1 day')""",
                (video,self.source_id),
            )

    def test_monitor_requires_exactly_one_target(self):
        video = self._video()
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO monitor_targets(target_type,source_id,next_due_at)
                   VALUES ('video',%s,now())""",
                (self.source_id,),
            )
        self.conn.execute(
            """INSERT INTO monitor_targets(target_type,video_id,source_id,next_due_at)
               VALUES ('video',%s,%s,now())""",
            (video,self.source_id),
        )
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO monitor_targets(target_type,video_id,source_id,next_due_at)
                   VALUES ('video',%s,%s,now())""",
                (video,self.source_id),
            )

    def test_quota_and_spend_limits_are_enforced(self):
        self.conn.execute(
            """INSERT INTO source_quota_ledger(
                 source_id,bucket_name,window_start_at,window_end_at,
                 limit_units,reserved_units,consumed_units,spend_limit_usd,reserved_usd,spent_usd)
               VALUES (%s,'daily',date_trunc('day',now()),date_trunc('day',now())+interval '1 day',
                       100,99,1,1.00,0.50,0.50)""",
            (self.source_id,),
        )
        with self.assertRaises(Exception):
            self.conn.execute(
                """UPDATE source_quota_ledger SET reserved_units=101
                   WHERE source_id=%s AND bucket_name='daily'""",
                (self.source_id,),
            )
        with self.assertRaises(Exception):
            self.conn.execute(
                """UPDATE source_quota_ledger SET reserved_usd=1.01
                   WHERE source_id=%s AND bucket_name='daily'""",
                (self.source_id,),
            )

    def test_job_generation_defaults_zero(self):
        from intelligence.jobs import enqueue
        job = enqueue(self.conn, "discovery.fixture", "d11job:" + uuid4().hex, {"test": True})
        self.jobs.append(job)
        generation = self.conn.execute(
            "SELECT lease_generation FROM jobs WHERE id=%s",(job,)
        ).fetchone()[0]
        self.assertEqual(generation, 0)
