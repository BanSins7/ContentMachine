"""PostgreSQL integration checks for tenant boundaries, references and immutable history."""
import os
import sys
import unittest
from pathlib import Path
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from intelligence import migrate
from intelligence.relevance import repository as repo
from intelligence.relevance.fact_review import (
    store_fact_proposals,review_fact,get_approved_facts,add_manual_fact)
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
        self.conn.execute("SELECT 1")
        self.a=uuid4().hex
        self.b=uuid4().hex
        lookup=lambda pid:{"id":pid}
        repo.create_workspace(self.conn,self.a,project_lookup=lookup)
        repo.create_workspace(self.conn,self.b,project_lookup=lookup)
    def tearDown(self):
        self.conn.rollback()
        self.conn.close()
    def _category(self,fact_ids=None):
        ids=fact_ids or []
        return {"category_key":str(uuid4()),"name":"Ошибки при похудении",
                "relation_type":"problem_related","origin":"inferred",
                "independent":not bool(ids),
                "definition":"Конкретная ошибка мешает похудеть",
                "rules":{"include":["Объяснение ошибки"],"exclude":["Личная история"],
                         "positive_examples":["Калории в напитках"],
                         "negative_examples":["Комплекс на бицепс"]},
                "related_fact_ids":ids}
    def _approve(self,project):
        category=self._category()
        tid=repo.create_taxonomy_draft(self.conn,project,[category])
        repo.review_category(self.conn,project,tid,category["category_key"],"approved","owner")
        repo.approve_taxonomy(self.conn,project,tid,"owner")
        return tid,category
    def _profile(self,project):
        p={"niche_code":"health_fitness","product_type":"app",
           "functions_text":"Приложение считает калории по фото",
           "problems_text":"Люди не понимают сколько едят"}
        return repo.save_profile(self.conn,project,p),p
    def test_workspace_rejects_orphan_local_id(self):
        with self.assertRaises(ContractError):
            repo.create_workspace(self.conn,uuid4().hex,project_lookup=lambda _:None)
    def test_cross_project_isolation_and_immutability(self):
        aid,cat=self._approve(self.a)
        with self.assertRaises(ContractError):
            repo.get_approved_context(self.conn,self.b)
        context=repo.get_approved_context(self.conn,self.a)
        self.assertEqual(context["taxonomy_version_id"],aid)
        self.assertEqual(context["categories"][0]["origin"],"inferred")
        self.assertEqual(context["categories"][0]["relation_type"],"problem_related")
        for command in (
            "UPDATE sre_taxonomy_categories SET name='tampered' WHERE taxonomy_version_id=%s",
            "DELETE FROM sre_category_examples WHERE taxonomy_version_id=%s",
            "UPDATE sre_taxonomy_versions SET approved_by='other' WHERE id=%s"):
            with self.assertRaises(Exception):
                with self.conn.transaction():
                    self.conn.execute(command,(aid,))
        with self.assertRaises(ContractError):
            repo.review_category(self.conn,self.b,aid,cat["category_key"],"rejected","owner")
    def test_cross_project_and_unapproved_fact_links_are_rejected(self):
        av,ap=self._profile(self.a)
        bv,bp=self._profile(self.b)
        alien=add_manual_fact(self.conn,self.b,bv,"problem","Не могу похудеть","owner")
        row=self._category([alien])
        with self.assertRaises(Exception):
            with self.conn.transaction():
                repo.create_taxonomy_draft(self.conn,self.a,[row])
        pending=store_fact_proposals(self.conn,self.a,av,ap,[
            {"kind":"function","original_quote":"считает калории по фото",
             "normalized_text":"Подсчёт калорий по фото"}])[0]
        with self.assertRaises(Exception):
            with self.conn.transaction():
                repo.create_taxonomy_draft(self.conn,self.a,[self._category([pending])])
        review_fact(self.conn,self.a,pending,"owner","approved")
        tid=repo.create_taxonomy_draft(self.conn,self.a,[self._category([pending])])
        count=self.conn.execute("SELECT count(*) FROM sre_category_fact_links WHERE project_id=%s",
                                (self.a,)).fetchone()[0]
        self.assertEqual(count,1)
        self.assertIsInstance(tid,int)
    def test_example_types_video_project_fk_and_published_guard(self):
        category=self._category()
        uid=uuid4().hex
        video_id=self.conn.execute("""INSERT INTO videos
          (platform,platform_video_id,canonical_url) VALUES
          ('youtube',%s,%s) RETURNING id""",(uid,"https://youtube.com/watch?v="+uid)).fetchone()[0]
        self.conn.execute("INSERT INTO sre_project_videos(project_id,video_id) VALUES(%s,%s)",
                          (self.a,video_id))
        category["examples"]={
          "positive":[{"text":"Real case","example_kind":"verified_video","video_id":video_id}],
          "negative":[{"text":"Illustrative","example_kind":"illustrative"}]}
        tid=repo.create_taxonomy_draft(self.conn,self.a,[category])
        repo.review_category(self.conn,self.a,tid,category["category_key"],"approved","owner")
        repo.approve_taxonomy(self.conn,self.a,tid,"owner")
        examples=repo.get_approved_context(self.conn,self.a)["categories"][0]["examples"]
        self.assertEqual(examples["positive"][0]["video_id"],video_id)
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute("""INSERT INTO sre_category_examples
                    (project_id,taxonomy_version_id,category_key,polarity,example_kind,
                     example_text) VALUES(%s,%s,%s,'positive','illustrative','t')""",
                     (self.a,tid,category["category_key"]))
    def test_source_rights_are_fail_closed_on_content(self):
        uid=uuid4().hex
        video=self.conn.execute("""INSERT INTO videos
          (platform,platform_video_id,canonical_url)
          VALUES ('youtube',%s,%s) RETURNING id""",
          (uid,"https://youtube.com/watch?v="+uid)).fetchone()[0]
        self.conn.execute("INSERT INTO sre_project_videos(project_id,video_id) VALUES (%s,%s)",
                          (self.a,video))
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute("""INSERT INTO sre_content_versions
                    (project_id,video_id,revision,content)
                    VALUES (%s,%s,1,%s::jsonb)""",
                    (self.a,video,'{"title":"Unsafe import"}'))
    def test_fact_review_and_pipeline_versions(self):
        version,profile=self._profile(self.a)
        ids=store_fact_proposals(self.conn,self.a,version,profile,[
            {"kind":"function","original_quote":"считает калории по фото",
             "normalized_text":"Подсчёт калорий по фото"}])
        review_fact(self.conn,self.a,ids[0],"owner","approved")
        self.assertEqual(len(get_approved_facts(self.conn,self.a,version)),1)
        manual=add_manual_fact(self.conn,self.a,version,"problem","Контроль веса","owner")
        self.assertEqual(len(get_approved_facts(self.conn,self.a,version)),2)
        with self.assertRaises(ContractError):
            review_fact(self.conn,self.a,ids[0],"owner","rejected")
        pid=repo.save_pipeline(self.conn,self.a,"strong-only",strong_only(),"owner",activate=True)
        next_pid=repo.save_pipeline(self.conn,self.a,"strong-only",strong_only(),"owner",activate=True)
        self.assertNotEqual(pid,next_pid)
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute("UPDATE sre_pipeline_configs SET name='changed' WHERE id=%s",(pid,))
