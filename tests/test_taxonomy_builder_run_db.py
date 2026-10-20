"""Block 6: real PostgreSQL runner integration with FAKE LLM responses only."""
import os,sys,tempfile,unittest,json
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import projects
from intelligence import migrate
from intelligence.taxonomy_builder.intake import get_intake_view,confirm_intake
from intelligence.taxonomy_builder.catalog import refresh_model_catalog,list_models
from intelligence.taxonomy_builder.pipeline_repository import save_draft,approve_draft
from intelligence.taxonomy_builder.cost_repository import create_quote,authorize_budget
from intelligence.taxonomy_builder.run_repository import (
    start_run,advance_run,submit_fact_review,publish_reviewed_topics,get_run,cancel_run)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.provider import ProviderUncertain
from test_taxonomy_builder_pipeline import provider_model,config,PRIMARY,SECOND

PROFILE={"name":"Calorie image coach","niche":"Health & Fitness",
         "product_type":"mobile_app","product_functions":"Считает калории по фото",
         "audience_problems":"Трудно отслеживать калории"}
def reply(role,inputs,cost=0.0009):
    if role=="fact_extraction":
        obj={"facts":[
          {"kind":"function","text":"Распознаёт калории","source_field":"product_functions",
           "source_quote":"Считает калории по фото"},
          {"kind":"problem","text":"Отслеживать калории","source_field":"audience_problems",
           "source_quote":"Трудно отслеживать калории"}]}
    elif role=="taxonomy_generation":
        facts=inputs["approved_facts"]
        obj={"topics":[
          {"name":"Калории по фото","relation":"direct","origin":"explicit",
           "fact_ids":[facts[0]["id"]],"definition":"Питание по фото","include":"Калории",
           "exclude":"Тренды без питания","positive_examples":["Как снимать тарелку"],
           "negative_examples":["Мотоспорт"]},
          {"name":"Как учитывать питание","relation":"problem_related","origin":"inferred",
           "fact_ids":[facts[1]["id"]],"definition":"Боль пользователей","include":"Ошибки учёта",
           "exclude":"Технологии автомобилей","positive_examples":["Ошибки калорий"],
           "negative_examples":["Дрифт"]}]}
    elif role=="taxonomy_critic":
        obj={"findings":[]}
    else:raise AssertionError(role)
    return {"id":"fixture-"+role,"model":"x/model",
            "choices":[{"finish_reason":"stop","message":{"content":json.dumps(obj,ensure_ascii=False)}}],
            "usage":{"cost":cost,"prompt_tokens":450,"completion_tokens":350,
                     "completion_tokens_details":{"reasoning_tokens":40}}}

@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"Disposable Postgres fixture required")
class BuilderRunPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.dsn=os.environ["CM_TEST_PG_DSN"]
        with psycopg.connect(cls.dsn,autocommit=True) as conn:
            migrate.apply_migrations(conn)
    def setUp(self):
        import psycopg
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch=mock.patch.dict(os.environ,{"CM_PROJECTS_DIR":self.tmp.name,
                     "CM_DB_ENABLED":"1","CM_DB_DSN":self.dsn})
        patch.start();self.addCleanup(patch.stop)
        self.project=projects.create_project(PROFILE)
        self.other=projects.create_project(PROFILE)
        for p in (self.project,self.other):
            v=get_intake_view(p["id"])
            confirm_intake(p["id"],v["profile_revision"],v["profile_hash"])
        with psycopg.connect(self.dsn) as conn:
            refresh_model_catalog(conn,fetcher=lambda:[
                provider_model(PRIMARY),provider_model(SECOND,temperature=True)])
            entries={x["model_id"]:x for x in list_models(conn)}
        self.cfg=config(entries[PRIMARY]["catalog_version_id"],
                        entries[SECOND]["catalog_version_id"])
        save_draft(self.project["id"],self.cfg,0)
        self.pipeline=approve_draft(self.project["id"],1)
        quote=create_quote(self.project["id"],self.pipeline["pipeline_id"])["quote"]
        consent=authorize_budget(self.project["id"],quote["quote_id"],"0.30")["consent"]
        self.quote=quote;self.consent=consent
        self.calls=[]
    def _start(self,key="block6-test-key-001"):
        return start_run(self.project["id"],self.quote["quote_id"],
                         self.consent["consent_id"],key)["run_id"]
    def _fake(self,body):
        role="fact_extraction" if body["response_format"]["json_schema"]["name"].endswith("fact_extraction") else (
             "taxonomy_generation" if body["response_format"]["json_schema"]["name"].endswith("taxonomy_generation") else "taxonomy_critic")
        inputs=json.loads(body["messages"][1]["content"])
        self.calls.append(role)
        model=body["model"]
        result=reply(role,inputs)
        result["model"]=model
        return result
    def test_two_human_gates_and_immutable_taxonomy(self):
        pid=self.project["id"]
        rid=self._start()
        self.assertEqual(get_run(pid,rid)["status"],"extraction_pending")
        self.assertEqual(advance_run(pid,rid,provider_call=self._fake)["status"],"fact_review")
        self.assertEqual(self.calls,["fact_extraction"])
        facts=get_run(pid,rid)["fact_proposals"]
        self.assertEqual(len(facts),2)
        with self.assertRaises(PipelineError):
            advance_run(pid,rid,provider_call=self._fake)
        decisions=[{"id":x["id"],"action":"approve"} for x in facts]
        self.assertEqual(submit_fact_review(pid,rid,decisions)["status"],"generation_pending")
        self.assertEqual(advance_run(pid,rid,provider_call=self._fake)["status"],"topic_review")
        state=get_run(pid,rid)
        self.assertEqual(len(state["topic_proposals"]),2)
        published=publish_reviewed_topics(pid,rid,[{"id":x["id"],"action":"approve"}
            for x in state["topic_proposals"]])
        self.assertEqual(published["taxonomy_version"],1)
        self.assertEqual(published["sre_taxonomy_version"],1)
        from intelligence.relevance.repository import get_approved_context
        import psycopg
        with psycopg.connect(self.dsn) as conn:
            sre=get_approved_context(conn,pid)
            self.assertEqual(sre["taxonomy_version_id"],published["sre_taxonomy_version_id"])
            self.assertEqual({c["name"] for c in sre["categories"]},
                             {t["name"] for t in published["topics"]})
            self.assertEqual({c["relation_type"] for c in sre["categories"]},
                             {"direct","problem_related"})
            self.assertEqual(sum(len(c["related_fact_ids"]) for c in sre["categories"]),2)
            self.assertTrue(all(e["example_kind"]=="illustrative"
                for c in sre["categories"] for key in ("positive","negative")
                for e in c["examples"][key]))
            self.assertEqual(conn.execute("""SELECT count(*) FROM tb_sre_taxonomy_links
                   WHERE project_id=%s AND run_id=%s""",(pid,rid)).fetchone()[0],1)
        self.assertEqual(get_run(pid,rid)["status"],"approved")
        self.assertGreater(float(get_run(pid,rid)["spent_usd"]),0)
        with self.assertRaises(PipelineError):
            publish_reviewed_topics(pid,rid,[])
        with self.assertRaises(PipelineError):
            advance_run(pid,rid,provider_call=self._fake)
        self.assertEqual(self.calls,["fact_extraction","taxonomy_generation"])
        import psycopg
        with psycopg.connect(self.dsn) as conn:
            with self.assertRaises(Exception):
                with conn.transaction():
                    conn.execute("DELETE FROM tb_taxonomy_publications WHERE run_id=%s",(rid,))
    def test_no_partial_sre_publication_after_invalid_review(self):
        import psycopg
        pid=self.project["id"];rid=self._start()
        advance_run(pid,rid,provider_call=self._fake)
        f=get_run(pid,rid)["fact_proposals"]
        submit_fact_review(pid,rid,[{"id":v["id"],"action":"approve"} for v in f])
        advance_run(pid,rid,provider_call=self._fake)
        t=get_run(pid,rid)["topic_proposals"]
        invalid=[{"id":x["id"],"action":"reject"} for x in t]
        with self.assertRaises(PipelineError):
            publish_reviewed_topics(pid,rid,invalid)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("""SELECT count(*) FROM tb_taxonomy_publications
                WHERE run_id=%s""",(rid,)).fetchone()[0],0)
            self.assertEqual(conn.execute("""SELECT count(*) FROM tb_sre_taxonomy_links
                WHERE run_id=%s""",(rid,)).fetchone()[0],0)
        self.assertEqual(get_run(pid,rid)["status"],"topic_review")

    def test_idempotent_run_creation_no_premature_cost(self):
        rid=self._start()
        same=self._start()
        self.assertEqual(same,rid)
        self.assertEqual(float(get_run(self.project["id"],rid)["spent_usd"]),0)
        with self.assertRaises(PipelineError):
            start_run(self.project["id"],self.quote["quote_id"],
                "00000000-0000-0000-0000-000000000000",
                "block6-test-key-001")
        with self.assertRaises(PipelineError):
            get_run(self.other["id"],rid)
    def test_spending_consent_cannot_fund_two_distinct_runs(self):
        first=self._start()
        with self.assertRaises(PipelineError) as ctx:
            self._start("different-run-key-987654")
        self.assertEqual(ctx.exception.http_status,409)
        self.assertEqual(get_run(self.project["id"],first)["status"],"extraction_pending")

    def test_cancellation_before_any_paid_request(self):
        rid=self._start()
        self.assertEqual(cancel_run(self.project["id"],rid)["status"],"cancelled")
        with self.assertRaises(PipelineError):
            advance_run(self.project["id"],rid,provider_call=self._fake)
        self.assertEqual(self.calls,[])
    def test_missing_usage_blocks_retry_and_holds_reservation(self):
        rid=self._start()
        result=advance_run(self.project["id"],rid,provider_call=lambda body:{
            "choices":[{"message":{"content":"{}"}}]})
        self.assertEqual(result["status"],"uncertain")
        run=get_run(self.project["id"],rid)
        self.assertGreater(float(run["reserved_usd"]),0)
        self.assertEqual(float(run["spent_usd"]),0)
        with self.assertRaises(PipelineError):
            advance_run(self.project["id"],rid,provider_call=self._fake)
    def test_insufficient_budget_fails_without_provider_charge(self):
        rid=self._start()
        import psycopg
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE tb_runs SET authorized_usd=0.000001 WHERE id=%s",(rid,))
        result=advance_run(self.project["id"],rid,provider_call=self._fake)
        self.assertEqual(result["status"],"paused_budget")
        self.assertEqual(self.calls,[])
    def test_provider_submicro_dollar_charge_preserved_exactly(self):
        rid=self._start()
        def high_precision_price(body):
            role="fact_extraction"
            inputs=json.loads(body["messages"][1]["content"])
            result=reply(role,inputs,cost="0.000048472")
            result["model"]=body["model"]
            return result
        result=advance_run(self.project["id"],rid,provider_call=high_precision_price)
        self.assertEqual(result["status"],"fact_review")
        self.assertEqual(result["charged_usd"],"0.000048472")
        self.assertEqual(float(get_run(self.project["id"],rid)["spent_usd"]),0.000048472)
        self.assertEqual(get_run(self.project["id"],rid)["stages"][0]["usage"]["cost_usd"],
                         "0.000048472")

    def test_hallucinated_fact_still_bills_once_and_halts(self):
        rid=self._start()
        def invalid(body):
            res=self._fake(body)
            res["choices"][0]["message"]["content"]=json.dumps({"facts":[{
                "kind":"function","text":"Функция не существовала",
                "source_field":"product_functions","source_quote":"Создает новые лекарства"}]})
            return res
        state=advance_run(self.project["id"],rid,provider_call=invalid)
        self.assertEqual(state["status"],"failed")
        self.assertGreater(float(get_run(self.project["id"],rid)["spent_usd"]),0)
        with self.assertRaises(PipelineError):advance_run(self.project["id"],rid,provider_call=self._fake)
    def test_http_launch_requires_header_and_is_idempotent(self):
        import app,threading
        from http.server import ThreadingHTTPServer
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            path=f"/api/projects/{self.project['id']}/taxonomy-builder/runs"
            body={"quote_id":self.quote["quote_id"],
                  "consent_id":self.consent["consent_id"],
                  "idempotency_key":"api-block6-unique-001"}
            def send(path,body=None,header=True):
                h={"Host":"localhost:8765"}
                if body is not None and header:h["X-ContentMachine"]="1"
                req=Request(f"http://127.0.0.1:{server.server_port}"+path,
                  headers=h,data=json.dumps(body).encode() if body is not None else None,
                  method="POST" if body is not None else "GET")
                try:
                    with urlopen(req,timeout=9) as r:return r.status,json.loads(r.read())
                except HTTPError as e:return e.code,json.loads(e.read())
            self.assertEqual(send(path,body,header=False)[0],403)
            code,result=send(path,body)
            self.assertEqual(code,201,result)
            rid=result["run_id"]
            again=send(path,body)
            self.assertEqual(again[1]["run_id"],rid)
            self.assertEqual(send(path+"/"+rid)[1]["status"],"extraction_pending")
            self.assertEqual(send(path+"/"+rid+"/cancel",{})[1]["status"],"cancelled")

        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
