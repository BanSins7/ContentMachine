"""SRE storage integration: snapshot attribution, human corrections and source rights."""
from __future__ import annotations
import os
import sys
import unittest
from uuid import uuid4
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from intelligence import migrate
from intelligence.relevance import repository as repo
from intelligence.relevance import storage
from intelligence.relevance.contracts import ContractError
from intelligence.relevance.presets import strong_only

@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"CM_TEST_PG_DSN not configured")
class StoragePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.dsn=os.environ["CM_TEST_PG_DSN"]
        with psycopg.connect(cls.dsn,autocommit=True) as conn:
            migrate.apply_migrations(conn)
    def setUp(self):
        import psycopg
        self.conn=psycopg.connect(self.dsn)
        self.conn.execute("SELECT 1")
        self.project=uuid4().hex
        self.other=uuid4().hex
        fn=lambda pid:{"id":pid}
        repo.create_workspace(self.conn,self.project,project_lookup=fn)
        repo.create_workspace(self.conn,self.other,project_lookup=fn)
        uid=uuid4().hex
        self.video=self.conn.execute("""INSERT INTO videos
                 (platform,platform_video_id,canonical_url,title) VALUES
                 ('youtube',%s,%s,%s) RETURNING id""",
                 (uid,"https://youtube.com/watch?v="+uid,"Калории в напитках")).fetchone()[0]
        storage.attach_video(self.conn,self.project,self.video)
        self.category={"category_key":str(uuid4()),"name":"Ошибки при похудении",
                       "relation_type":"problem_related","origin":"inferred",
                       "independent":True,"related_fact_ids":[],
                       "definition":"Ошибки, которые мешают похудению",
                       "rules":{"include":["Разбор ошибки"],"exclude":["Тренировки"],
                                "positive_examples":["Калории напитков"],
                                "negative_examples":["Бицепсы"]}}
        self.tid=repo.create_taxonomy_draft(self.conn,self.project,[self.category])
        repo.review_category(self.conn,self.project,self.tid,self.category["category_key"],"approved","owner")
        repo.approve_taxonomy(self.conn,self.project,self.tid,"owner")
        self.pipeline_id=repo.save_pipeline(self.conn,self.project,"benchmark",strong_only(),"owner",activate=True)
    def tearDown(self):
        self.conn.rollback()
        self.conn.close()
    def _run(self):
        return storage.create_run(self.conn,self.project,self.tid,self.pipeline_id,{
            "strong_llm":{"provider":"test","model_id":"fixture",
                          "model_version":"v1","prompt_version":"test1"}}, {"dataset_id":"fixture"})
    def _content(self):
        source_id="sre-test-"+uuid4().hex
        self.conn.execute("""INSERT INTO sources(id,provider,source_type,status,
               can_store_metadata,can_analyze_media)
               VALUES (%s,'fixture','manual','enabled',TRUE,FALSE)""",(source_id,))
        policy_id=self.conn.execute("""INSERT INTO source_policy_versions
                 (source_id,version,is_active) VALUES (%s,1,TRUE) RETURNING id""",
                 (source_id,)).fetchone()[0]
        return self.conn.execute("""INSERT INTO sre_content_versions
             (project_id,video_id,revision,content,source_id,source_policy_version_id,
              completeness)
             VALUES (%s,%s,1,%s::jsonb,%s,%s,'metadata_only') RETURNING id""",
             (self.project,self.video,'{"title":"Калории в напитках"}',
              source_id,policy_id)).fetchone()[0]
    def test_verified_classification_and_snapshot_storage(self):
        content=self._content()
        run=self._run()
        result=storage.record_result(self.conn,self.project,run,self.video,content,
             self.category["category_key"],"match","Explicit calories example",
             [{"source":"title","quote":"Калории в напитках"}],[])
        row=self.conn.execute("""SELECT reason,decision,evidence,stage_trace
              FROM sre_classification_results WHERE id=%s""",(result,)).fetchone()
        self.assertEqual(row[0],"Explicit calories example")
        self.assertEqual(row[1],"match")
        self.assertEqual(row[2][0]["source"],"title")
        self.assertEqual(row[3],[])
        with self.assertRaises(ContractError):
            storage.record_result(self.conn,self.project,run,self.video,content,
                self.category["category_key"],"match","Fabricated",
                [{"source":"title","quote":"No such text"}],[])
    def test_run_model_snapshot_is_mandatory(self):
        with self.assertRaises(ContractError):
            storage.create_run(self.conn,self.project,self.tid,self.pipeline_id,{}, {})
        with self.assertRaises(ContractError):
            storage.create_run(self.conn,self.other,self.tid,self.pipeline_id,
                               {"x":{"provider":"fixture"}} ,{})
    def test_human_label_correction_keeps_one_current_value(self):
        first=storage.record_human_label(self.conn,self.project,self.video,self.tid,
             self.category["category_key"],"match","owner","first")
        second=storage.record_human_label(self.conn,self.project,self.video,self.tid,
             self.category["category_key"],"no_match","owner","corrected")
        self.assertNotEqual(first,second)
        latest=self.conn.execute("""SELECT id,label FROM sre_human_labels WHERE
             project_id=%s AND video_id=%s AND superseded_at IS NULL""",
             (self.project,self.video)).fetchall()
        self.assertEqual(latest,[(second,"no_match")])
        with self.assertRaises(ContractError):
            storage.record_human_label(self.conn,self.other,self.video,self.tid,
                 self.category["category_key"],"match","owner")
    def test_same_project_wrong_run_taxonomy_is_rejected(self):
        other_category={**self.category,"category_key":str(uuid4())}
        tid2=repo.create_taxonomy_draft(self.conn,self.project,[other_category],parent_id=self.tid)
        repo.review_category(self.conn,self.project,tid2,other_category["category_key"],"approved","owner")
        repo.approve_taxonomy(self.conn,self.project,tid2,"owner")
        run=self._run()
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute("""INSERT INTO sre_classification_results
                   (run_id,project_id,video_id,taxonomy_version_id,category_key,decision,
                    evidence,stage_trace,reason) VALUES
                   (%s,%s,%s,%s,%s,'no_match','[]'::jsonb,'[]'::jsonb,'test')""",
                   (run,self.project,self.video,tid2,other_category["category_key"]))

    def test_rights_revocation_blocks_future_classification(self):
        revision=self._content()
        source_id=self.conn.execute("""SELECT source_id FROM sre_content_versions
                     WHERE id=%s""",(revision,)).fetchone()[0]
        self.conn.execute("UPDATE sources SET can_store_metadata=FALSE WHERE id=%s",(source_id,))
        run=self._run()
        with self.assertRaises(ContractError):
            storage.record_result(self.conn,self.project,run,self.video,revision,
                self.category["category_key"],"match","Should be inaccessible",
                [{"source":"title","quote":"Калории в напитках"}],[])
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_readable_content_versions WHERE id=%s",
            (revision,)).fetchone()[0],0)

    def test_rejected_category_is_never_classified(self):
        rejected={**self.category,"category_key":str(uuid4()),"name":"Rejected topic"}
        tid=repo.create_taxonomy_draft(self.conn,self.project,
                                        [self.category,rejected],parent_id=self.tid)
        repo.review_category(self.conn,self.project,tid,self.category["category_key"],"approved","owner")
        repo.review_category(self.conn,self.project,tid,rejected["category_key"],"rejected","owner")
        repo.approve_taxonomy(self.conn,self.project,tid,"owner")
        run=storage.create_run(self.conn,self.project,tid,self.pipeline_id,{
            "strong_llm":{"provider":"test","model_id":"fixture",
                          "model_version":"v1","prompt_version":"p1"}},{})
        revision=self._content()
        with self.assertRaises(ContractError):
            storage.record_result(self.conn,self.project,run,self.video,revision,
                rejected["category_key"],"no_match","Rejected by user",[],[])
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute("""INSERT INTO sre_classification_results
                  (run_id,project_id,video_id,taxonomy_version_id,category_key,
                   decision,evidence,stage_trace,reason)
                  VALUES(%s,%s,%s,%s,%s,'no_match','[]'::jsonb,'[]'::jsonb,'wrong')""",
                  (run,self.project,self.video,tid,rejected["category_key"]))
