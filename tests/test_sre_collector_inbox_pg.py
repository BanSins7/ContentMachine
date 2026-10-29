"""M35 PostgreSQL17 actual H1/H2 C9 + persisted D1 optional Collector integration.

Runs only against CM_TEST_PG_DSN disposable test database. No Collector
installation, provider HTTP, paid Opus or cross-branch writes.
"""
from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from intelligence.relevance.collector_inbox import (
    backfill_selected_d1_discoveries, receive_collector_delivery,
)
from intelligence.relevance.v1_contracts import SREContractError, fingerprint


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"), "disposable PG17 required")
class M35CollectorInboxActualPG(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.dsn = os.environ["CM_TEST_PG_DSN"]
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            migrate.apply_migrations(conn)

    def setUp(self):
        import psycopg
        from test_sre_offline_pg import S4OfflinePostgres
        # Reuse the existing two-human-approved C9 fixture without inheriting
        # the S4 test methods or publishing an unreviewed fake taxonomy.
        fixture = S4OfflinePostgres(
            "test_full_pinned_run_replay_multi_label_and_run_immutability"
        )
        fixture.dsn = self.dsn
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.pid, self.pub = fixture.pid, fixture.pub
        self.conn = psycopg.connect(self.dsn, autocommit=True)
        self.addCleanup(self.conn.close)

        identity = uuid4().hex[:11]
        self.video = fixture.conn.execute(
            """INSERT INTO videos(platform,platform_video_id,canonical_url)
               VALUES ('youtube',%s,%s) RETURNING id""",
            (identity, "https://www.youtube.com/watch?v=" + identity),
        ).fetchone()[0]
        self.discovery = fixture.conn.execute(
            """INSERT INTO discoveries(video_id,source_id,method)
               VALUES (%s,%s,'manual_import') RETURNING id""",
            (self.video, fixture.source),
        ).fetchone()[0]
        fixture.conn.execute(
            """INSERT INTO source_video_metadata (
               video_id,source_id,source_policy_version_id,fetched_at,
               title,description,is_current)
               VALUES (%s,%s,%s,now(),'M35 human-approved source','D1',TRUE)""",
            (self.video,fixture.source,fixture.policy),
        )
        fixture.conn.commit()

    def _event(self, *, discovery=None):
        now = datetime.now(timezone.utc)
        e = {
            "schema_version": "sre.discovery_ready/v1",
            "event_id": str(uuid4()),
            "producer": "video_collector",
            "project_id": self.pid,
            "video_id": self.video,
            "discovery_id": self.discovery if discovery is None else discovery,
            "source_id": self.fixture.source,
            "source_policy_version_id": self.fixture.policy,
            "source_request_id": None,
            "collector_query_ids": [
                "55555555-5555-4555-8555-555555555555"
            ],
            "d1_persisted_at": (now-timedelta(seconds=3)).isoformat(),
            "occurred_at": now.isoformat(),
        }
        e["event_sha256"] = fingerprint(e)
        return e

    def _auth(self, project, actor, permission):
        return (project == self.pid and actor == "sre:test_producer"
                and permission in ("receive_sre_collector_delivery",
                                   "backfill_sre_d1"))

    def test_c9_d1_s3_atomic_ack_replay_and_explicit_backfill(self):
        event = self._event()
        first = receive_collector_delivery(
            self.conn,event,self.pub,actor_id="sre:test_producer",
            actor_authorizer=self._auth,
        )
        self.assertEqual(first["state"],"acked")
        self.assertTrue(first["readable_now"])
        self.assertFalse(first["replayed"])
        self.assertIsNotNone(first["content_version_id"])
        self.assertEqual(first["paid_calls"],0)
        second = receive_collector_delivery(
            self.conn,event,self.pub,actor_id="sre:test_producer",
            actor_authorizer=self._auth,
        )
        self.assertTrue(second["replayed"])
        self.assertEqual(second["content_version_id"],
                         first["content_version_id"])
        count = self.conn.execute(
            """SELECT count(*) FROM sre_content_versions
               WHERE project_id=%s AND video_id=%s""",
            (self.pid,self.video),
        ).fetchone()[0]
        self.assertEqual(count,1)
        backfill = backfill_selected_d1_discoveries(
            self.conn,self.pid,self.pub,[self.discovery],
            actor_id="sre:test_producer",actor_authorizer=self._auth,
        )
        self.assertFalse(backfill["collector_required"])
        self.assertEqual(backfill["records"][0]["content_version_id"],
                         first["content_version_id"])
        self.assertTrue(backfill["records"][0]["replayed"])
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM sre_collector_inbox WHERE
               project_id=%s AND state='acked'""",
            (self.pid,),
        ).fetchone()[0],1)

    def test_missing_d1_quarantine_and_project_authorization_required(self):
        event = self._event(discovery=9223372036854775807)
        with self.assertRaises(SREContractError):
            receive_collector_delivery(
                self.conn,event,self.pub,actor_id="sre:forged",
                actor_authorizer=self._auth,
            )
        quarantined = receive_collector_delivery(
            self.conn,event,self.pub,actor_id="sre:test_producer",
            actor_authorizer=self._auth,
        )
        self.assertEqual(quarantined["state"],"quarantined")
        self.assertEqual(quarantined["quarantine_reason"],"rights_denied")
        self.assertIsNone(quarantined["content_version_id"])
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM sre_content_versions
               WHERE project_id=%s AND video_id=%s""",
            (self.pid,self.video),
        ).fetchone()[0],0)
        again = receive_collector_delivery(
            self.conn,event,self.pub,actor_id="sre:test_producer",
            actor_authorizer=self._auth,
        )
        self.assertTrue(again["replayed"])
        self.assertEqual(again["state"],"quarantined")


if __name__=="__main__":
    unittest.main()
