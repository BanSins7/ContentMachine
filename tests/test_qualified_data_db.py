"""Disposable PostgreSQL 17 integration tests for the full local QDP path.

Run only with CM_TEST_PG_DSN pointing at a NON-PRODUCTION disposable database.
"""
from __future__ import annotations

import json
import os
import unittest
import uuid

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None

from intelligence.qualified_data.contracts import QDPError
from intelligence.qualified_data import repository as qdp

DSN = os.environ.get("CM_TEST_PG_DSN")


@unittest.skipUnless(DSN and psycopg is not None, "Disposable CM_TEST_PG_DSN required")
class QualifiedDataDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from intelligence.migrate import apply_migrations
        with psycopg.connect(DSN, autocommit=True) as conn:
            apply_migrations(conn)

    def setUp(self):
        self.pid = uuid.uuid4().hex
        self.key = "qdp-" + uuid.uuid4().hex
        self.source = "qdp_test_" + uuid.uuid4().hex[:12]
        self.category = str(uuid.uuid4())
        self.factory = lambda: psycopg.connect(DSN, autocommit=True, row_factory=dict_row)
        with self.factory() as c:
            c.execute(
                """INSERT INTO project_registry(id,name,profile_revision,profile_hash,created_at,updated_at)
                   VALUES (%s,'QDP test',1,%s,now(),now())""",
                (self.pid,"a"*64))
            c.execute("INSERT INTO sre_workspaces(project_id) VALUES (%s)",(self.pid,))
            c.execute(
                """INSERT INTO sources(id,provider,source_type,status,can_store_metadata,
                                        can_store_metrics,can_derive_scores,can_analyze_media)
                   VALUES (%s,'qdp_test','manual','enabled',true,false,false,false)""",
                (self.source,))
            self.policy = c.execute(
                """INSERT INTO source_policy_versions(source_id,version,is_active,permissions)
                   VALUES (%s,1,true,%s::jsonb) RETURNING id""",
                (self.source,json.dumps({"can_store_metadata":True}))).fetchone()["id"]
            self.vid = c.execute(
                """INSERT INTO videos(platform,platform_video_id,canonical_url,title)
                   VALUES ('tiktok',%s,%s,'fitness routine') RETURNING id""",
                (uuid.uuid4().hex,"https://www.tiktok.com/@test/video/7123456789012345678")
            ).fetchone()["id"]
            c.execute(
                """INSERT INTO source_video_metadata
                   (video_id,source_id,source_policy_version_id,fetched_at,title,published_at)
                   VALUES (%s,%s,%s,now(),'fitness routine',now()-interval '2 days')""",
                (self.vid,self.source,self.policy))
            self.taxonomy = c.execute(
                """INSERT INTO sre_taxonomy_versions(project_id,version,status)
                   VALUES (%s,1,'draft') RETURNING id""",
                (self.pid,)).fetchone()["id"]
            c.execute(
                """INSERT INTO sre_taxonomy_categories
                   (project_id,taxonomy_version_id,category_key,name,relation_type,
                    definition,rules,related_fact_ids,review_status,independent)
                   VALUES (%s,%s,%s,'fitness','direct','fitness content',%s::jsonb,
                           '[]'::jsonb,'approved',true)""",
                (self.pid,self.taxonomy,self.category,
                 json.dumps({"include":["fitness"],"exclude":[],
                             "positive_examples":[],"negative_examples":[]})))
            c.execute(
                """UPDATE sre_taxonomy_versions SET status='approved',
                    approved_by='test',approved_at=now() WHERE id=%s""",
                (self.taxonomy,))
            self.pipeline = c.execute(
                """INSERT INTO sre_pipeline_configs(project_id,name,version,config,created_by)
                   VALUES (%s,'QDP-fixture',1,'{"stages":[]}'::jsonb,'test')
                   RETURNING id""", (self.pid,)).fetchone()["id"]
            c.execute(
                """INSERT INTO sre_project_videos(project_id,video_id)
                   VALUES (%s,%s)""",(self.pid,self.vid))
            content_version = c.execute(
                """INSERT INTO sre_content_versions
                   (project_id,video_id,revision,content,source_id,source_policy_version_id)
                   VALUES (%s,%s,1,'{"title":"fitness routine"}'::jsonb,%s,%s)
                   RETURNING id""",
                (self.pid,self.vid,self.source,self.policy)).fetchone()["id"]
            self.sre_run = str(c.execute(
                """INSERT INTO sre_classification_runs
                   (project_id,taxonomy_version_id,pipeline_id,resolved_models,status)
                   VALUES (%s,%s,%s,'{}'::jsonb,'completed') RETURNING id""",
                (self.pid,self.taxonomy,self.pipeline)).fetchone()["id"])
            c.execute(
                """INSERT INTO sre_classification_results
                   (run_id,project_id,video_id,content_version_id,taxonomy_version_id,
                    category_key,decision,reason,evidence,stage_trace)
                   VALUES (%s::uuid,%s,%s,%s,%s,%s::uuid,'match','evidence present',
                    '[{"source":"title","quote":"fitness"}]'::jsonb,'[]'::jsonb)""",
                (self.sre_run,self.pid,self.vid,content_version,self.taxonomy,self.category))

    def tearDown(self):
        with self.factory() as c:
            c.execute("DELETE FROM qdp_runs WHERE project_id=%s",(self.pid,))
            c.execute("DELETE FROM jobs WHERE job_type=%s AND payload->>'project_id'=%s",
                      (qdp.JOB_TYPE,self.pid))
            c.execute("DELETE FROM sre_workspaces WHERE project_id=%s",(self.pid,))
            c.execute("DELETE FROM project_registry WHERE id=%s",(self.pid,))
            c.execute("DELETE FROM videos WHERE id=%s",(self.vid,))
            c.execute("DELETE FROM sources WHERE id=%s",(self.source,))

    def test_end_to_end_local_pipeline_and_revocation(self):
        before=qdp.preview(self.pid,self.sre_run,connect=self.factory)
        self.assertEqual(before["totals"]["qualified"],1)
        result=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        self.assertEqual(result["totals"]["qualified"],1)
        replay=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        self.assertTrue(replay["replayed"])
        with self.assertRaises(QDPError):
            qdp.create_run(self.pid,self.sre_run,self.key,{"max_age_days":1},
                           connect=self.factory)
        run_id=result["run_id"]
        processed=qdp.work_once(connect=self.factory)
        self.assertEqual(processed["video_id"],self.vid)
        detail=qdp.get_run(self.pid,run_id,connect=self.factory)
        self.assertEqual(detail["run"]["status"],"partial")
        self.assertEqual(detail["items"][0]["metadata"]["title"],"fitness routine")
        self.assertEqual(detail["items"][0]["groups"]["A"]["state"],"blocked")
        self.assertEqual(detail["items"][0]["groups"]["C"]["reason_code"],"score_rights_missing")
        with self.factory() as c:
            self.assertEqual(c.execute(
                "SELECT count(*) AS count FROM qdp_enrichments WHERE run_id=%s::uuid",
                (run_id,)).fetchone()["count"],1)
            c.execute("UPDATE sources SET status='disabled' WHERE id=%s",(self.source,))
        after=qdp.get_run(self.pid,run_id,connect=self.factory)
        self.assertIsNone(after["items"][0]["metadata"])
        self.assertEqual(after["items"][0]["reason_code"],
                         "source_policy_inactive_or_expired")
        self.assertEqual(qdp.purge_revoked(connect=self.factory),1)

    def test_human_rejection_and_freshness(self):
        with self.factory() as c:
            c.execute(
                """INSERT INTO sre_human_labels
                   (project_id,video_id,taxonomy_version_id,category_key,label,reviewer)
                   VALUES (%s,%s,%s,%s::uuid,'no_match','test')""",
                (self.pid,self.vid,self.taxonomy,self.category))
        result=qdp.preview(self.pid,self.sre_run,connect=self.factory)
        self.assertEqual(result["totals"]["qualified"],0)
        self.assertEqual(result["totals"]["excluded"],1)

    def test_cross_project_run_is_not_readable(self):
        result=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        with self.assertRaises(QDPError) as ctx:
            qdp.get_run("f"*32,result["run_id"],connect=self.factory)
        self.assertEqual(ctx.exception.status,404)


if __name__ == "__main__":
    unittest.main()
