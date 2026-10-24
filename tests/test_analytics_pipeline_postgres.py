"""Disposable-PG integration: real queue -> unified partial -> revoke -> purge.

Zero network, zero LLM/media calls and no fabricated complete C score.
Requires CM_TEST_PG_DSN; all test records are isolated and cleaned.
"""
from __future__ import annotations
import json
import os
import unittest
from unittest import mock
from uuid import uuid4

from intelligence import migrate
from intelligence.analytics.queue import enqueue_batch, process_one, purge_unreadable
from intelligence.analytics.service import list_unified, run_qualification
from intelligence.analytics.contracts import AnalyticsError

@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"Disposable PostgreSQL required")
class QualifiedPipelinePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.dsn=os.environ["CM_TEST_PG_DSN"]
        with psycopg.connect(cls.dsn,autocommit=True) as conn:
            migrate.apply_migrations(conn)

    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn=psycopg.connect(self.dsn,autocommit=True,row_factory=dict_row)
        self.pid=uuid4().hex
        self.sid="aql-ci-"+uuid4().hex
        self.video_code=uuid4().hex
        self.conn.execute("""
          INSERT INTO project_registry
          (id,name,profile_revision,profile_hash,created_at,updated_at)
          VALUES (%s,'Analytics fixture',1,%s,now(),now())
        """,(self.pid,"a"*64))
        self.conn.execute("INSERT INTO sre_workspaces(project_id) VALUES(%s)",(self.pid,))
        self.conn.execute("""
          INSERT INTO sources
          (id,provider,source_type,status,can_store_metadata,
           can_analyze_media,can_store_metrics,can_derive_scores)
          VALUES (%s,'unit-fixture','commercial','enabled',TRUE,TRUE,TRUE,TRUE)
        """,(self.sid,))
        perms={right:True for right in (
            "can_store_metadata","can_analyze_media","can_store_metrics","can_derive_scores")}
        self.policy_id=self.conn.execute("""
          INSERT INTO source_policy_versions
          (source_id,version,is_active,permissions,reviewed_at,retention_policy)
          VALUES (%s,1,TRUE,%s::jsonb,now(),
                  '{"derived_retention_days":30}'::jsonb) RETURNING id
        """,(self.sid,json.dumps(perms))).fetchone()["id"]
        self.vid=self.conn.execute("""
          INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
          VALUES ('tiktok',%s,%s,'video') RETURNING id
        """,(self.video_code,"https://www.tiktok.com/@ci/video/"+self.video_code)).fetchone()["id"]
        self.conn.execute("""
          INSERT INTO sre_project_videos(project_id,video_id) VALUES(%s,%s)
        """,(self.pid,self.vid))
        self.env=mock.patch.dict(os.environ,{"CM_DB_DSN":self.dsn})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        # Tear down only our fixture; leave other CI tests untouched.
        self.conn.execute("DELETE FROM analytics_qualification_jobs WHERE project_id=%s",(self.pid,))
        self.conn.execute("DELETE FROM analytics_unified_snapshots WHERE project_id=%s",(self.pid,))
        self.conn.execute("DELETE FROM sre_project_videos WHERE project_id=%s",(self.pid,))
        self.conn.execute("DELETE FROM sre_workspaces WHERE project_id=%s",(self.pid,))
        self.conn.execute("DELETE FROM videos WHERE id=%s",(self.vid,))
        self.conn.execute("DELETE FROM source_policy_versions WHERE id=%s",(self.policy_id,))
        self.conn.execute("DELETE FROM sources WHERE id=%s",(self.sid,))
        self.conn.execute("DELETE FROM project_registry WHERE id=%s",(self.pid,))
        self.conn.close()

    def test_queue_idempotence_partial_and_read_revocation(self):
        jobs=enqueue_batch(self.pid,self.sid,[self.vid],"aql-pg-batch-001")
        self.assertEqual(len(jobs),1)
        again=enqueue_batch(self.pid,self.sid,[self.vid],"aql-pg-batch-001")
        self.assertEqual(jobs[0]["id"],again[0]["id"])
        self.assertIsNone(process_one(project_id=uuid4().hex))
        done=process_one(project_id=self.pid)
        self.assertEqual(done["state"],"partial")
        self.assertIsNotNone(done["snapshot_id"])
        self.assertIsNone(process_one(project_id=self.pid))
        self.assertEqual(list_unified(self.pid,qualified_only=True),[])
        read=list_unified(self.pid,qualified_only=False)
        self.assertEqual(len(read),1)
        self.assertEqual(read[0]["status"],"partial")
        self.assertIn("a_missing_or_incomplete",read[0]["blockers"])
        self.assertIn("c_metric_missing",read[0]["blockers"])
        self.assertIn("no_approved_relevance",read[0]["blockers"])
        # Same key is replay-safe, without a second snapshot.
        replay=run_qualification(self.pid,self.vid,self.sid,jobs[0]["request_key"])
        self.assertEqual(replay["id"],done["snapshot_id"])
        # Live revocation invalidates the rights-filtered view immediately.
        self.conn.execute("UPDATE source_policy_versions SET is_active=FALSE WHERE id=%s",(self.policy_id,))
        self.assertEqual(list_unified(self.pid,qualified_only=False),[])
        with self.assertRaises(AnalyticsError):
            run_qualification(self.pid,self.vid,self.sid,"new-aql-key-12345")
        removed=purge_unreadable()
        self.assertGreaterEqual(removed["deleted_unified_snapshots"],1)
        self.assertGreaterEqual(removed["deleted_revoked_jobs"],1)

if __name__=="__main__":
    unittest.main()
