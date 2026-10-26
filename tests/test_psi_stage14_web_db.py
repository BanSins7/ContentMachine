"""Stage14 fixture C: mocked website URL -> one consented Sonnet-web-fetch
request -> stored extracted sections -> Evidence provenance -> Context approval.

Provider-reported web text is *not* independently verified website bytes.
"""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_web_jobs_db import WebJobsDB, URL
from intelligence.project_sources import evidence_jobs, context_service
from intelligence.taxonomy_builder.context_binding import ui_preflight, MODE


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"), "PostgreSQL17 required")
class WebToContext(WebJobsDB):
    def test_website_extraction_evidence_approval_and_context(self):
        import psycopg
        from psycopg.types.json import Jsonb

        # The WebJobsDB source setup intentionally has no questionnaire rows.
        # A Stage11 context also needs a real, pinned questionnaire revision.
        with psycopg.connect(self.__class_dsn()) as conn:
            conn.execute("""INSERT INTO project_profile_versions
                (project_id,revision,schema_version,profile_hash,profile)
                VALUES(%s,1,1,%s,%s)""",
                (self.pid,"a"*64,Jsonb({"goal":"Learn audience needs",
                                         "niche":"Health & Fitness"})))
        quote=self.jobs.cost_quote(self.pid,self.sid)
        request={"quote_id":quote["quote_id"],
                 "approve_estimate_usd":quote["estimated_usd"]}
        task=self.jobs.enqueue(self.pid,self.sid,request)
        self.assertTrue(task["created"])
        called=[]
        def fake(url,key):
            called.append((url,key))
            payload=self._mock_result(url,key)
            # A provider mock must have EXACT quote offsets; the earlier
            # generic Stage05 job fixture doesn't exercise Stage10 offsets.
            for items in ("sections","claims"):
                for item in payload[items]:
                    quote=item["quote"]
                    begin=payload["page_text"].find(quote)
                    self.assertGreaterEqual(begin,0)
                    item["quote_start"]=begin
                    item["quote_end"]=begin+len(quote)
            return payload
        completed=self.jobs.run_one(send=fake,key_provider=lambda:"ci-key")
        self.assertEqual(completed["state"],"completed")
        self.assertEqual(called,[(URL,"ci-key")])
        extracted=self.jobs.result(self.pid,self.sid,task["job_id"])
        self.assertEqual(extracted["result"]["trust_level"],"model_reported_unverified")
        self.assertFalse(extracted["result"]["source_content_verified"])
        self.assertEqual(extracted["result"]["requested_url"],URL)
        self.assertEqual(extracted["result"]["sections"][0]["quote"],"photographs")
        self.assertFalse(extracted["result"]["provenance"].get("independent_http_capture",False))

        enqueued=evidence_jobs.enqueue(self.pid,self.sid,{})
        self.assertEqual(enqueued["state"],"queued")
        self.assertEqual(evidence_jobs.run_one()["state"],"completed")
        found=evidence_jobs.list_evidence(self.pid,sid=self.sid,limit=100)["items"]
        self.assertTrue(found)
        self.assertTrue(all(f["trust_label"]=="model_reported_unverified" for f in found))
        self.assertTrue(all(f["citation"].startswith("project-source://") for f in found))
        for e in found:
            self.assertEqual(e["evidence_kind"],"external_claim")
            self.assertEqual(e["human_decision"],None)
            evidence_jobs.review(self.pid,e["evidence_id"],{"decision":"approved"})
        with patch.object(context_service,"_pid",return_value={}),patch.object(
                context_service,"_conn",side_effect=lambda:psycopg.connect(self.__class_dsn())):
            draft=context_service.preview(self.pid)
            self.assertEqual(draft["context"]["coverage"]["sources_incomplete"],0)
            snap=context_service.create_snapshot(
                self.pid,{"expected_context_sha256":draft["context_sha256"]})
            approved=context_service.decide(self.pid,snap["context_id"],{
                "decision":"approved",
                "expected_context_sha256":draft["context_sha256"],
                "acknowledge_partial":False,"excluded_source_ids":[]})
            self.assertEqual(approved["decision"],"approved")
        # The model-reported provenance survives explicit human review. An
        # approval is NOT a cryptographic verification of HTTP response bytes.
        with patch("intelligence.taxonomy_builder.pipeline_repository._pid",
                   return_value=self.pid),patch(
                "intelligence.taxonomy_builder.pipeline_repository._connect",
                side_effect=lambda:psycopg.connect(self.__class_dsn())):
            eligible=ui_preflight(self.pid,snap["context_id"],1200)
            self.assertEqual(eligible["input_mode"],MODE)
            self.assertTrue(eligible["evidence"])
            self.assertTrue(all(x["trust_label"]=="model_reported_unverified"
                                for x in eligible["evidence"]))
            self.assertTrue(all(x["allowed_fact_kinds"]==["problem","audience"]
                                for x in eligible["evidence"]))
            self.assertTrue(eligible["no_provider_call"])

    @staticmethod
    def __class_dsn():
        return os.environ["CM_TEST_PG_DSN"]


if __name__=="__main__":
    unittest.main()
