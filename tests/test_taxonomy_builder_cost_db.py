"""Block 5: DB cost quote/consent lifecycle and local HTTP, no external provider calls."""
import os,sys,tempfile,unittest
from pathlib import Path
from decimal import Decimal
from unittest import mock
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import projects
from intelligence import migrate
from intelligence.taxonomy_builder.intake import get_intake_view,confirm_intake
from intelligence.taxonomy_builder.catalog import refresh_model_catalog,list_models
from intelligence.taxonomy_builder.pipeline_repository import save_draft,approve_draft,get_pipeline_state
from intelligence.taxonomy_builder.cost_repository import create_quote,get_cost_state,authorize_budget,revoke_consent
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from test_taxonomy_builder_pipeline import provider_model,config,PRIMARY,SECOND

PROFILE={"name":"Calorie Tracker","niche":"Health & Fitness","product_type":"mobile_app",
         "product_functions":"Считает калории по фотографии","audience_problems":"Сложно отслеживать питание"}
@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"No disposable PostgreSQL database")
class CostPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.dsn=os.environ["CM_TEST_PG_DSN"]
        with psycopg.connect(cls.dsn,autocommit=True) as c:
            migrate.apply_migrations(c)
    def setUp(self):
        import psycopg
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env=mock.patch.dict(os.environ,{"CM_PROJECTS_DIR":self.temp.name,
          "CM_DB_ENABLED":"1","CM_DB_DSN":self.dsn})
        self.env.start();self.addCleanup(self.env.stop)
        self.p=projects.create_project(PROFILE)
        self.other=projects.create_project(PROFILE)
        with psycopg.connect(self.dsn) as conn:
            refresh_model_catalog(conn,fetcher=lambda:[
              provider_model(PRIMARY),
              provider_model(SECOND,temperature=True)])
            models={m["model_id"]:m for m in list_models(conn)}
        chosen=config(models[PRIMARY]["catalog_version_id"],models[SECOND]["catalog_version_id"])
        for pid in (self.p["id"],self.other["id"]):
            v=get_intake_view(pid)
            confirm_intake(pid,v["profile_revision"],v["profile_hash"])
        self.saved=save_draft(self.p["id"],chosen,0)
        self.approved=approve_draft(self.p["id"],1)
        self.chosen=chosen
    def _quote(self,**kwargs):
        return create_quote(self.p["id"],self.approved["pipeline_id"],**kwargs)["quote"]
    def test_cost_quote_estimated_and_idempotent(self):
        first=self._quote()
        reused=create_quote(self.p["id"],self.approved["pipeline_id"])
        self.assertTrue(reused["reused"])
        self.assertEqual(reused["quote"]["quote_id"],first["quote_id"])
        self.assertEqual(first["details"]["config_sha256"],self.approved["config_sha256"])
        self.assertEqual(len(first["details"]["stages"]),2)
        self.assertEqual(get_cost_state(self.p["id"])["status"],"quote_ready")
        self.assertFalse(get_cost_state(self.p["id"])["launch_available"])
    def test_requires_exact_project_and_approved_current_pipeline(self):
        with self.assertRaises(PipelineError):
            create_quote(self.other["id"],self.approved["pipeline_id"])
        self._quote()
        cfg=config(self.chosen["stages"][0]["catalog_version_id"],
                   self.chosen["stages"][1]["catalog_version_id"])
        cfg["stages"][1]["temperature"]=0.6
        save_draft(self.p["id"],cfg,1)
        with self.assertRaises(PipelineError):
            create_quote(self.p["id"],self.approved["pipeline_id"])
        with self.assertRaises(PipelineError):
            get_cost_state(self.p["id"])
    def test_budget_consent_and_revocation(self):
        quote=self._quote()
        limit="0.50"
        consent=authorize_budget(self.p["id"],quote["quote_id"],limit)
        self.assertEqual(consent["status"],"spending_authorized")
        self.assertFalse(consent["launch_available"])
        self.assertEqual(get_cost_state(self.p["id"])["status"],"spending_authorized")
        repeat=authorize_budget(self.p["id"],quote["quote_id"],limit)
        self.assertEqual(repeat["consent"]["consent_id"],consent["consent"]["consent_id"])
        with self.assertRaises(PipelineError):
            authorize_budget(self.p["id"],quote["quote_id"],"0.75")
        with self.assertRaises(PipelineError):
            revoke_consent(self.other["id"],consent["consent"]["consent_id"])
        revoke_consent(self.p["id"],consent["consent"]["consent_id"])
        self.assertEqual(get_cost_state(self.p["id"])["status"],"quote_ready")
        with self.assertRaises(PipelineError):
            authorize_budget(self.p["id"],quote["quote_id"],limit)
        newer=self._quote(force_new=True)
        self.assertNotEqual(newer["quote_id"],quote["quote_id"])
        self.assertEqual(authorize_budget(self.p["id"],newer["quote_id"],limit)["status"],
                         "spending_authorized")
    def test_quote_expires_and_price_change_blocks_consent(self):
        import psycopg
        quote=self._quote()
        with psycopg.connect(self.dsn) as conn:
            # Old quotes are immutable; simulate time travel using a future
            # clock instead of directly modifying expiry in production.
            with mock.patch("intelligence.taxonomy_builder.cost_repository._now",
                            return_value=__import__("datetime").datetime.now(
                              __import__("datetime").timezone.utc)+
                              __import__("datetime").timedelta(hours=1)):
                with self.assertRaises(PipelineError):
                    authorize_budget(self.p["id"],quote["quote_id"],"0.50")
        with psycopg.connect(self.dsn) as conn:
            changed=provider_model(PRIMARY)
            changed["pricing"]["prompt"]="0.00015"
            refresh_model_catalog(conn,fetcher=lambda:[
              changed,provider_model(SECOND,temperature=True)])
        with self.assertRaises(PipelineError):
            authorize_budget(self.p["id"],quote["quote_id"],"0.50")
        self.assertEqual(get_cost_state(self.p["id"])["status"],"quote_stale")
    def test_edit_questionnaire_invalidates_quote(self):
        quote=self._quote()
        projects.update_project(self.p["id"],{
          "expected_revision":1,"audience_problems":"Изменившиеся проблемы"})
        with self.assertRaises(PipelineError):
            authorize_budget(self.p["id"],quote["quote_id"],"0.50")
    def test_documents_require_explicit_exclusion_ack(self):
        import base64
        with_docs=projects.create_project({**PROFILE,"name":"With Research",
          "research_files":[{"name":"notes.txt",
            "data_base64":base64.b64encode(b"some research text").decode("ascii")}]})
        pid=with_docs["id"]
        info=get_intake_view(pid)
        confirm_intake(pid,info["profile_revision"],info["profile_hash"])
        save_draft(pid,self.chosen,0)
        approved=approve_draft(pid,1)
        quote=create_quote(pid,approved["pipeline_id"])["quote"]
        self.assertEqual(quote["details"]["assumptions"]["excluded_documents_count"],1)
        with self.assertRaises(PipelineError):
            authorize_budget(pid,quote["quote_id"],"0.50")
        recorded=authorize_budget(pid,quote["quote_id"],"0.50",
                                   acknowledge_documents_excluded=True)
        self.assertEqual(recorded["status"],"spending_authorized")
    def test_http_quote_consent_revoke_and_no_launch_endpoint(self):
        import app,threading,json
        from http.server import ThreadingHTTPServer
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        pid=self.p["id"]
        def send(path,data=None,authorized=True):
            headers={"Host":"localhost:8765"}
            if data is not None and authorized:headers["X-ContentMachine"]="1"
            req=Request("http://127.0.0.1:"+str(server.server_port)+path,
                        data=json.dumps(data).encode() if data is not None else None,
                        headers=headers,method="POST" if data is not None else "GET")
            try:
                with urlopen(req,timeout=6) as r:return r.status,json.loads(r.read())
            except HTTPError as e:return e.code,json.loads(e.read())
        root=f"/api/projects/{pid}/taxonomy-builder/cost"
        try:
            code,result=send(root)
            self.assertEqual(code,200);self.assertEqual(result["status"],"quote_required")
            code,denied=send(root+"/quote",{"pipeline_id":self.approved["pipeline_id"]},
                             authorized=False)
            self.assertEqual(code,403)
            code,result=send(root+"/quote",{"pipeline_id":self.approved["pipeline_id"]})
            self.assertEqual(code,200,result)
            quote=result["quote"]
            code,approved=send(root+"/consent",{"quote_id":quote["quote_id"],
                                                 "authorized_usd":"0.50"})
            self.assertEqual(code,200,approved)
            self.assertEqual(approved["status"],"spending_authorized")
            code,denied=send(root+"/consent",{"quote_id":quote["quote_id"],
                                                "authorized_usd":"0.60"})
            self.assertEqual(code,409)
            code,result=send(root+"/revoke",{"consent_id":approved["consent"]["consent_id"]})
            self.assertEqual(code,200)
            self.assertTrue(result["revoked"])
            code,missing=send("/api/projects/"+pid+"/taxonomy-builder/runs",{})
            # The route now exists, but refuses a run without quote, consent
            # and a valid idempotency key. No paid LLM request is made.
            self.assertEqual(code,422)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
