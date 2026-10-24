"""Stage12 true PostgreSQL RTB handoff: approved Context -> mock LLM -> two reviews -> SRE.

No external API, no platform download and no paid provider calls.
"""
import os
import json
import unittest

from test_taxonomy_builder_run_db import BuilderRunPostgres,reply
from intelligence.project_sources import evidence_jobs,context_service
from intelligence.taxonomy_builder.cost_repository import create_quote,authorize_budget
from intelligence.taxonomy_builder.run_repository import (
    start_run,advance_run,submit_fact_review,publish_reviewed_topics,get_run)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"Requires PostgreSQL17 CI service")
class ApprovedContextRTBFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BuilderRunPostgres.setUpClass()

    def setUp(self):
        self.dsn=os.environ["CM_TEST_PG_DSN"]
        BuilderRunPostgres.setUp(self)
        self.pid=self.project["id"]
        scheduled=evidence_jobs.import_profile(self.pid,{})
        for _ in range(scheduled["jobs_queued"]+8):
            evidence_jobs.run_one()
        all_rows=evidence_jobs.list_evidence(self.pid,limit=100)["items"]
        self.assertGreaterEqual(len(all_rows),2)
        for row in all_rows:
            if "Считает калории по фото" in row["exact_quote"] or "Трудно отслеживать калории" in row["exact_quote"]:
                evidence_jobs.review(self.pid,row["evidence_id"],{"decision":"approved"})
        draft=context_service.preview(self.pid)
        self.assertEqual(draft["context"]["coverage"]["sources_incomplete"],0)
        snap=context_service.create_snapshot(self.pid,{"expected_context_sha256":draft["context_sha256"]})
        self.context_id=snap["context_id"]
        context_service.decide(self.pid,self.context_id,{"decision":"approved",
            "expected_context_sha256":draft["context_sha256"],
            "acknowledge_partial":False,"excluded_source_ids":[]})
        self.quote=create_quote(self.pid,self.pipeline["pipeline_id"],context_id=self.context_id,
                                token_budget=2800,force_new=True)["quote"]
        self.assertEqual(self.quote["details"]["input_mode"],"approved_project_context_v1")
        self.consent=authorize_budget(self.pid,self.quote["quote_id"],"0.30")["consent"]
        self.calls=[]

    def sender(self,payload):
        role=payload["response_format"]["json_schema"]["name"].removeprefix("tb_")
        inp=json.loads(payload["messages"][1]["content"])
        self.calls.append(role)
        if role=="fact_extraction":
            selected=inp["context_evidence"]
            feature=next(row for row in selected if "Считает калории по фото" in row["text"])
            problem=next(row for row in selected if "Трудно отслеживать калории" in row["text"])
            response=reply(role,inp)
            response["choices"][0]["message"]["content"]=json.dumps({"facts":[
                {"kind":"function","text":"Считывает калории с фото",
                 "source_evidence_id":feature["evidence_id"],
                 "source_quote":"Считает калории по фото"},
                {"kind":"problem","text":"Трудность учёта калорий",
                 "source_evidence_id":problem["evidence_id"],
                 "source_quote":"Трудно отслеживать калории"}]},ensure_ascii=False)
        else:
            response=reply(role,inp)
            if role=="taxonomy_generation":
                facts=inp["approved_facts"]
                function_id=next(x["id"] for x in facts if x["kind"]=="function")
                problem_id=next(x["id"] for x in facts if x["kind"]=="problem")
                topics=json.loads(response["choices"][0]["message"]["content"])["topics"]
                topics[0]["fact_ids"]=[function_id]
                topics[1]["fact_ids"]=[problem_id]
                response["choices"][0]["message"]["content"]=json.dumps({"topics":topics},ensure_ascii=False)
        response["model"]=payload["model"]
        return response

    def test_context_quote_to_approved_taxonomy_without_paid_provider(self):
        rid=start_run(self.pid,self.quote["quote_id"],self.consent["consent_id"],
                      "approved-context-rtb-e2e-v1")["run_id"]
        self.assertEqual(get_run(self.pid,rid)["input_mode"],"approved_project_context_v1")
        self.assertEqual(advance_run(self.pid,rid,provider_call=self.sender)["status"],"fact_review")
        facts=get_run(self.pid,rid)["fact_proposals"]
        self.assertEqual(len(facts),2)
        self.assertTrue(all(x["source_evidence_id"] for x in facts))
        with self.assertRaises(PipelineError):
            submit_fact_review(self.pid,rid,[{"action":"edit","id":facts[0]["id"],"text":"invented"}])
        reviewed=submit_fact_review(self.pid,rid,[{"action":"approve","id":x["id"]} for x in facts])
        self.assertEqual(len(reviewed["approved_facts"]),2)
        self.assertEqual(advance_run(self.pid,rid,provider_call=self.sender)["status"],"topic_review")
        topics=get_run(self.pid,rid)["topic_proposals"]
        self.assertEqual(len(topics),2)
        publication=publish_reviewed_topics(self.pid,rid,
            [{"action":"approve","id":x["id"]} for x in topics])
        self.assertEqual(publication["status"],"approved")
        self.assertEqual(self.calls,["fact_extraction","taxonomy_generation"])
        self.assertEqual(get_run(self.pid,rid)["publication"]["version"],1)

if __name__=="__main__":unittest.main()
