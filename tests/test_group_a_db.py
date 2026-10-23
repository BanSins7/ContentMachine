"""Disposable PostgreSQL 17 integration of QDP -> Group A A1 -> existing A003 schema.

Only run with CM_TEST_PG_DSN. Approved taxonomy is immutable: fixtures are
intentionally left in the disposable CI DB and must never use production DSNs.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import unittest
import uuid

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg=None
    dict_row=None

from intelligence.qualified_data import repository as qdp
from intelligence.group_a import repository as ga
from intelligence.group_a.contracts import Scene,TechnicalMeasurement,GroupAError

DSN=os.environ.get("CM_TEST_PG_DSN")


@unittest.skipUnless(DSN and psycopg,"Use disposable CM_TEST_PG_DSN only")
class GroupADatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from intelligence.migrate import apply_migrations
        with psycopg.connect(DSN,autocommit=True) as conn:
            apply_migrations(conn)

    def setUp(self):
        self.dir=TemporaryDirectory()
        self.path=Path(self.dir.name)/"fixture.mp4"
        self.path.write_bytes(b"local authorized media fixture")
        self.pid=uuid.uuid4().hex
        self.source="group-a-fixture-"+uuid.uuid4().hex
        self.factory=lambda: psycopg.connect(DSN,autocommit=True,row_factory=dict_row)
        key=str(uuid.uuid4())
        with self.factory() as c:
            c.execute(
                """INSERT INTO project_registry
                   (id,name,profile_revision,profile_hash,created_at,updated_at)
                   VALUES (%s,'Group A test',1,%s,now(),now())""",
                (self.pid,"b"*64))
            c.execute("INSERT INTO sre_workspaces(project_id) VALUES (%s)",(self.pid,))
            c.execute(
                """INSERT INTO sources(id,provider,source_type,status,
                   can_store_metadata,can_analyze_media)
                   VALUES (%s,'test','owned','enabled',true,true)""",(self.source,))
            self.policy=c.execute(
                """INSERT INTO source_policy_versions
                   (source_id,version,is_active,permissions)
                   VALUES (%s,1,true,%s::jsonb) RETURNING id""",
                (self.source,json.dumps({"can_store_metadata":True,
                                         "can_analyze_media":True}))).fetchone()["id"]
            self.vid=c.execute(
                """INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
                   VALUES ('tiktok',%s,'https://www.tiktok.com/@fixture/video/77777777777','video')
                   RETURNING id""",(uuid.uuid4().hex,)).fetchone()["id"]
            c.execute(
                """INSERT INTO source_video_metadata
                   (video_id,source_id,source_policy_version_id,fetched_at,title,published_at)
                   VALUES (%s,%s,%s,now(),'test evidence',now()-interval '2 days')""",
                (self.vid,self.source,self.policy))
            self.asset=c.execute(
                """INSERT INTO media_assets(video_id,source_id,media_kind,local_path,permission)
                   VALUES (%s,%s,'video',%s,'analysis_allowed') RETURNING id""",
                (self.vid,self.source,str(self.path))).fetchone()["id"]
            taxonomy=c.execute(
                """INSERT INTO sre_taxonomy_versions(project_id,version,status)
                   VALUES (%s,1,'draft') RETURNING id""",
                (self.pid,)).fetchone()["id"]
            category=str(uuid.uuid4())
            c.execute(
                """INSERT INTO sre_taxonomy_categories
                   (project_id,taxonomy_version_id,category_key,name,relation_type,
                    definition,rules,related_fact_ids,review_status,independent)
                   VALUES (%s,%s,%s,'test','direct','test text',%s::jsonb,
                    '[]'::jsonb,'approved',true)""",
                (self.pid,taxonomy,category,json.dumps({"include":["test"],
                   "exclude":[],"positive_examples":[],"negative_examples":[]})))
            c.execute(
                """UPDATE sre_taxonomy_versions SET status='approved',
                   approved_by='fixture',approved_at=now() WHERE id=%s""",(taxonomy,))
            pipeline=c.execute(
                """INSERT INTO sre_pipeline_configs(project_id,name,version,config,created_by)
                   VALUES (%s,'fixture',1,'{"stages":[]}'::jsonb,'fixture') RETURNING id""",
                (self.pid,)).fetchone()["id"]
            c.execute(
                "INSERT INTO sre_project_videos(project_id,video_id) VALUES (%s,%s)",
                (self.pid,self.vid))
            cv=c.execute(
                """INSERT INTO sre_content_versions(project_id,video_id,revision,content,
                   source_id,source_policy_version_id)
                   VALUES (%s,%s,1,'{"title":"test evidence"}'::jsonb,%s,%s) RETURNING id""",
                (self.pid,self.vid,self.source,self.policy)).fetchone()["id"]
            sre_run=str(c.execute(
                """INSERT INTO sre_classification_runs
                   (project_id,taxonomy_version_id,pipeline_id,resolved_models,status)
                   VALUES (%s,%s,%s,'{}'::jsonb,'completed') RETURNING id""",
                (self.pid,taxonomy,pipeline)).fetchone()["id"])
            c.execute(
                """INSERT INTO sre_classification_results
                   (run_id,project_id,video_id,content_version_id,taxonomy_version_id,
                    category_key,decision,reason,evidence,stage_trace)
                   VALUES (%s::uuid,%s,%s,%s,%s,%s::uuid,'match','test evidence',
                    '[{"source":"title","quote":"test"}]'::jsonb,'[]'::jsonb)""",
                (sre_run,self.pid,self.vid,cv,taxonomy,category))
        qdp_run=qdp.create_run(self.pid,sre_run,"qdp-fixture-"+uuid.uuid4().hex,
                               connect=self.factory)
        self.run=qdp_run["run_id"]
        result=qdp.work_once(connect=self.factory,worker_id="group-a-qdp-fixture")
        if result is None or result["video_id"]!=self.vid:
            raise AssertionError("Fixture failed to qualify/prep a Group A-ready video")

    def tearDown(self):
        self.dir.cleanup()
        # DB fixtures are random, in a disposable database; approved SRE
        # categories are immutable, so do NOT bypass triggers for cleanup.

    def measurement(self,path,config):
        return TechnicalMeasurement(Decimal("4.000"),"9:16",(
            Scene(1,Decimal("0"),Decimal("1.250")),
            Scene(2,Decimal("1.250"),Decimal("4.000"))))

    def test_qdp_to_group_a_and_existing_feature_a_summary(self):
        created=ga.create_task(self.pid,self.run,self.vid,connect=self.factory)
        repeated=ga.create_task(self.pid,self.run,self.vid,connect=self.factory)
        self.assertEqual(created["task_id"],repeated["task_id"])
        self.assertTrue(repeated["replayed"])
        outcome=ga.work_once(connect=self.factory,worker_id="ga-fixture",
                             extractor=self.measurement,roots=[self.dir.name])
        self.assertEqual(outcome["status"],"completed")
        detail=ga.task_details(self.pid,created["task_id"],connect=self.factory)
        self.assertEqual(detail["status"],"completed")
        self.assertEqual(detail["analysis"]["duration_sec"],Decimal("4.000"))
        self.assertEqual(detail["analysis"]["scene_count"],2)
        self.assertEqual(detail["analysis"]["speech_status"],"not_run")
        self.assertEqual(len(detail["scenes"]),2)
        self.assertEqual(detail["analysis"]["analysis_state"],"partial")
        with self.factory() as c:
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM video_feature_a_scenes s
                   JOIN video_feature_a_analyses a ON a.id=s.analysis_id
                   WHERE a.video_id=%s AND a.media_asset_id=%s""",
                (self.vid,self.asset)).fetchone()["n"],2)

    def test_revocation_prevents_media_processing(self):
        created=ga.create_task(self.pid,self.run,self.vid,connect=self.factory)
        with self.factory() as c:
            c.execute("UPDATE sources SET status='disabled' WHERE id=%s",(self.source,))
        called=[]
        def extractor(path,config):
            called.append(True)
            return self.measurement(path,config)
        result=ga.work_once(connect=self.factory,extractor=extractor,
                            roots=[self.dir.name])
        self.assertEqual(result["status"],"blocked")
        self.assertFalse(called)
        self.assertIsNone(ga.task_details(self.pid,created["task_id"],
                                         connect=self.factory)["analysis"])

    def test_revocation_hides_completed_result_at_read_time(self):
        created=ga.create_task(self.pid,self.run,self.vid,connect=self.factory)
        ga.work_once(connect=self.factory,extractor=self.measurement,
                     roots=[self.dir.name])
        with self.factory() as c:
            c.execute("UPDATE source_policy_versions SET is_active=false WHERE id=%s",
                      (self.policy,))
        detail=ga.task_details(self.pid,created["task_id"],connect=self.factory)
        self.assertEqual(detail["status"],"blocked")
        self.assertIsNone(detail["analysis"])

    def test_cross_project_denial_and_incorrect_kind(self):
        created=ga.create_task(self.pid,self.run,self.vid,connect=self.factory)
        with self.assertRaises(GroupAError) as ctx:
            ga.task_details("f"*32,created["task_id"],connect=self.factory)
        self.assertEqual(ctx.exception.http_status,404)
        with self.factory() as c:
            c.execute("UPDATE videos SET content_kind='photo_carousel' WHERE id=%s",(self.vid,))
        with self.assertRaises(GroupAError):
            ga.create_task(self.pid,self.run,self.vid,{"scene_threshold":0.55},
                           connect=self.factory)

    def test_two_workers_claim_once(self):
        created=ga.create_task(self.pid,self.run,self.vid,connect=self.factory)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[
                pool.submit(ga.work_once,connect=self.factory,
                            worker_id="group-a-worker-"+str(i),
                            extractor=self.measurement,roots=[self.dir.name])
                for i in range(2)]
            results=[f.result() for f in futures]
        self.assertEqual(sum(x is not None for x in results),1)
        self.assertEqual(ga.task_details(self.pid,created["task_id"],
                                        connect=self.factory)["status"],"completed")


if __name__=="__main__":
    unittest.main()
