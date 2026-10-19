"""Optional integration tests use an isolated PostgreSQL database (CI service)."""
import os
import sys
import unittest
from pathlib import Path
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from intelligence import migrate
from intelligence.relevance import repository as repo
from intelligence.relevance.fact_review import store_fact_proposals,review_fact,get_approved_facts
from intelligence.relevance.contracts import ContractError
from intelligence.relevance.presets import strong_only

@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"CM_TEST_PG_DSN not configured")
class ProjectWorkspacePostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.dsn=os.environ["CM_TEST_PG_DSN"]
        with psycopg.connect(cls.dsn,autocommit=True) as c:
            migrate.apply_migrations(c)
    def setUp(self):
        import psycopg
        self.conn=psycopg.connect(self.dsn)
        self.conn.execute("SELECT 1")  # begins outer transaction; nested service blocks are savepoints
        self.a=uuid4().hex
        self.b=uuid4().hex
        repo.create_workspace(self.conn,self.a)
        repo.create_workspace(self.conn,self.b)
    def tearDown(self):
        self.conn.rollback()
        self.conn.close()
    def _category(self):
        return {"category_key":str(uuid4()),"name":"Ошибки при похудении",
                "relation_type":"problem","definition":"Конкретная ошибка мешает похудеть",
                "rules":{"include":["Объяснение ошибки"],"exclude":["Личная история"],
                         "positive_examples":["Калории в напитках"],
                         "negative_examples":["Комплекс на бицепс"]},
                "related_fact_ids":[]}
    def _approve(self, project):
        category=self._category()
        tid=repo.create_taxonomy_draft(self.conn,project,[category])
        repo.review_category(self.conn,project,tid,category["category_key"],"approved","owner")
        repo.approve_taxonomy(self.conn,project,tid,"owner")
        return tid,category
    def test_cross_project_isolation_and_immutable_approval(self):
        aid,category=self._approve(self.a)
        with self.assertRaises(ContractError):
            repo.get_approved_context(self.conn,self.b)
        context=repo.get_approved_context(self.conn,self.a)
        self.assertEqual(context["taxonomy_version_id"],aid)
        with self.conn.transaction():
            with self.assertRaises(Exception):
                with self.conn.transaction():
                    self.conn.execute("""UPDATE sre_taxonomy_categories SET name='tampered'
                      WHERE taxonomy_version_id=%s""",(aid,))
        with self.assertRaises(ContractError):
            repo.review_category(self.conn,self.b,aid,category["category_key"],"rejected","owner")
    def test_versioning_and_pipeline_preset(self):
        first,_=self._approve(self.a)
        second,_=self._approve(self.a)
        self.assertNotEqual(first,second)
        self.assertEqual(repo.get_approved_context(self.conn,self.a)["taxonomy_version_id"],second)
        pid=repo.save_pipeline(self.conn,self.a,"strong-only",strong_only(),"owner",activate=True)
        self.assertIsInstance(pid,int)
        self.assertEqual(self.conn.execute("SELECT active_pipeline_id FROM sre_workspaces WHERE project_id=%s",
                        (self.a,)).fetchone()[0],pid)
        with self.assertRaises(ContractError):
            repo.get_approved_context(self.conn,self.b)
    def test_fact_review_preserves_quote_and_status(self):
        profile={"niche_code":"health_fitness","product_type":"app",
                 "functions_text":"Приложение считает калории по фото",
                 "problems_text":"Люди не понимают сколько едят"}
        v=repo.save_profile(self.conn,self.a,profile)
        ids=store_fact_proposals(self.conn,self.a,v,profile,[
            {"kind":"function","original_quote":"считает калории по фото",
             "normalized_text":"Подсчёт калорий по фото"}])
        review_fact(self.conn,self.a,ids[0],"owner","approved")
        self.assertEqual(len(get_approved_facts(self.conn,self.a,v)),1)
        with self.assertRaises(ContractError):
            review_fact(self.conn,self.a,ids[0],"owner","rejected")
