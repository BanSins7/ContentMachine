"""Block 2: real questionnaire → review → immutable confirmation → Studio gate.

No model provider, pricing, or external web requests are performed.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app
import projects
from intelligence import migrate
from intelligence.taxonomy_builder import intake

PROFILE={
  "name":"Photo Calories",
  "niche":"Health & Fitness",
  "product_type":"mobile_app",
  "product_functions":"Приложение считает калории по фото",
  "audience_problems":"Людям трудно учитывать калории",
  "product_description":"Дневник питания",
}

class IntakeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch=mock.patch.dict(os.environ,{
            "CM_PROJECTS_DIR":self.temp.name,"CM_DB_ENABLED":"0",
        })
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_incomplete_profile_still_saves_locally(self):
        saved=projects.create_project({"name":"Draft"})
        view=intake.get_intake_view(saved["id"])
        self.assertEqual(view["next_step"],"questionnaire_review")
        self.assertFalse(view["can_confirm"])
        self.assertTrue(any(b["field"]=="product_type" for b in view["blockers"]))
        self.assertEqual(view["profile_db_sync"],"disabled")

    def test_no_product_does_not_invent_functions(self):
        project=projects.create_project({
            "niche":"Health & Fitness","product_type":"no_product",
            "audience_problems":"Люди не умеют планировать нагрузку",
        })
        view=intake.get_intake_view(project["id"])
        self.assertNotIn("product_functions",[x["field"] for x in view["blockers"]])
        self.assertFalse(view["can_confirm"])  # PG disabled

    def test_invalid_niche_rejected_for_builder_not_project_creation(self):
        project=projects.create_project({**PROFILE,"niche":"Car reviews"})
        view=intake.get_intake_view(project["id"])
        self.assertTrue(any(x["field"]=="niche" for x in view["blockers"]))

    def test_invalid_or_missing_profile_returns_404(self):
        with self.assertRaises(intake.IntakeProblem) as caught:
            intake.get_intake_view("../../private")
        self.assertEqual(caught.exception.http_status,404)
        with self.assertRaises(intake.IntakeProblem):
            intake.get_intake_view("0"*32)

    def test_cannot_confirm_without_postgres(self):
        project=projects.create_project(PROFILE)
        source=projects.taxonomy_input(project["id"])
        with self.assertRaises(intake.IntakeProblem) as caught:
            intake.confirm_intake(project["id"],source["profile_revision"],source["profile_hash"])
        self.assertEqual(caught.exception.http_status,503)

class IntakeHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch=mock.patch.dict(os.environ,{
            "CM_PROJECTS_DIR":self.temp.name,"CM_DB_ENABLED":"0",
        })
        self.patch.start()
        self.addCleanup(self.patch.stop)
    def request(self,path,payload=None,with_header=True):
        headers={"Host":"localhost:8765"}
        if payload is not None and with_header:
            headers["X-ContentMachine"]="1"
        r=Request("http://127.0.0.1:"+str(self.server.server_port)+path,
                  data=json.dumps(payload).encode() if payload is not None else None,
                  headers=headers,method="POST" if payload is not None else "GET")
        try:
            with urlopen(r,timeout=5) as out:
                return out.status,json.loads(out.read())
        except HTTPError as exc:
            return exc.code,json.loads(exc.read())

    def test_http_read_review_and_refuse_paid_handoff_without_db(self):
        code,result=self.request("/api/projects",PROFILE)
        self.assertEqual(code,201)
        pid=result["project"]["id"]
        code,status=self.request("/api/projects/"+pid+"/taxonomy-builder/intake")
        self.assertEqual(code,200)
        self.assertEqual(status["profile_revision"],1)
        self.assertEqual(status["profile"]["product_functions"],PROFILE["product_functions"])
        self.assertFalse(status["pipeline_studio_available"])
        self.assertIn("PostgreSQL",status["database_blocker"])
        code,error=self.request("/api/projects/"+pid+"/taxonomy-builder/intake/confirm",{
            "expected_revision":status["profile_revision"],"expected_hash":status["profile_hash"]})
        self.assertEqual(code,503)
        code,error=self.request("/api/projects/"+pid+"/taxonomy-builder/intake/confirm",{
            "expected_revision":1,"expected_hash":status["profile_hash"]},with_header=False)
        self.assertEqual(code,403)
        code,_=self.request("/api/projects/invalid/taxonomy-builder/intake")
        self.assertEqual(code,404)

@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"No PostgreSQL fixture")
class IntakePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.dsn=os.environ["CM_TEST_PG_DSN"]
        with psycopg.connect(cls.dsn,autocommit=True) as conn:
            migrate.apply_migrations(conn)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch=mock.patch.dict(os.environ,{
            "CM_PROJECTS_DIR":self.temp.name,"CM_DB_ENABLED":"1","CM_DB_DSN":self.dsn,
        })
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.project=projects.create_project(PROFILE)
    def tearDown(self):
        import psycopg
        with psycopg.connect(self.dsn) as conn:
            conn.execute("DELETE FROM project_registry WHERE id=%s",(self.project["id"],))

    def test_confirm_is_idempotent_and_pipeline_gate_opens(self):
        pid=self.project["id"]
        first=intake.get_intake_view(pid)
        self.assertTrue(first["can_confirm"])
        self.assertFalse(first["confirmed"])
        approved=intake.confirm_intake(pid,first["profile_revision"],first["profile_hash"])
        self.assertTrue(approved["confirmed"])
        self.assertTrue(approved["pipeline_studio_available"])
        self.assertEqual(approved["next_step"],"pipeline_studio")
        self.assertTrue(approved["pipeline_configuration_available"])
        repeated=intake.confirm_intake(pid,first["profile_revision"],first["profile_hash"])
        self.assertTrue(repeated["confirmed"])
        import psycopg
        with psycopg.connect(self.dsn) as conn:
            count=conn.execute("SELECT count(*) FROM tb_intake_confirmations WHERE project_id=%s",(pid,)).fetchone()[0]
        self.assertEqual(count,1)

    def test_edited_project_is_new_version_and_old_approval_stays_history(self):
        pid=self.project["id"]
        state=intake.get_intake_view(pid)
        intake.confirm_intake(pid,state["profile_revision"],state["profile_hash"])
        edit=projects.update_project(pid,{
            "expected_revision":1,"product_functions":"Дневник калорий",
        })
        self.assertEqual(edit["profile_revision"],2)
        current=intake.get_intake_view(pid)
        self.assertFalse(current["confirmed"])
        self.assertFalse(current["pipeline_studio_available"])
        self.assertNotEqual(state["profile_hash"],current["profile_hash"])
        with self.assertRaises(intake.IntakeProblem) as caught:
            intake.confirm_intake(pid,1,state["profile_hash"])
        self.assertEqual(caught.exception.http_status,409)
        newer=intake.confirm_intake(pid,current["profile_revision"],current["profile_hash"])
        self.assertTrue(newer["confirmed"])
        import psycopg
        with psycopg.connect(self.dsn) as conn:
            versions=conn.execute("""SELECT profile_revision FROM tb_intake_confirmations
                 WHERE project_id=%s ORDER BY profile_revision""",(pid,)).fetchall()
        self.assertEqual(versions,[(1,),(2,)])

    def test_confirm_requires_current_version_and_project_isolation(self):
        pid=self.project["id"]
        view=intake.get_intake_view(pid)
        with self.assertRaises(intake.IntakeProblem):
            intake.confirm_intake(pid,1,"a"*64)
        other=projects.create_project(PROFILE)
        try:
            others=intake.get_intake_view(other["id"])
            self.assertFalse(others["confirmed"])
            intake.confirm_intake(pid,view["profile_revision"],view["profile_hash"])
            self.assertFalse(intake.get_intake_view(other["id"])["confirmed"])
        finally:
            import psycopg
            with psycopg.connect(self.dsn) as conn:
                conn.execute("DELETE FROM project_registry WHERE id=%s",(other["id"],))

    def test_http_post_is_idempotent_and_stale_revision_is_rejected(self):
        server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        def req(path,payload=None):
            headers={"Host":"localhost:8765"}
            if payload is not None: headers["X-ContentMachine"]="1"
            request=Request("http://127.0.0.1:"+str(server.server_port)+path,
                 data=json.dumps(payload).encode() if payload is not None else None,
                 headers=headers,method="POST" if payload is not None else "GET")
            try:
                with urlopen(request,timeout=5) as response:
                    return response.status,json.loads(response.read())
            except HTTPError as err:
                return err.code,json.loads(err.read())
        try:
            url="/api/projects/"+self.project["id"]+"/taxonomy-builder/intake"
            code,source=req(url)
            self.assertEqual(code,200)
            data={"expected_revision":1,"expected_hash":source["profile_hash"]}
            code,confirm=req(url+"/confirm",data)
            self.assertEqual(code,200)
            self.assertTrue(confirm["confirmed"])
            code,again=req(url+"/confirm",data)
            self.assertEqual(code,200)
            projects.update_project(self.project["id"],{
                "expected_revision":1,"audience_problems":"Переедание"})
            code,err=req(url+"/confirm",data)
            self.assertEqual(code,409)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
