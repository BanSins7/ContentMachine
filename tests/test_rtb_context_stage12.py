"""Stage 12 offline strict evidence-to-RTB contracts; no external/provider calls."""
import unittest
from unittest.mock import patch
from uuid import uuid4
import hashlib

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError,sha256
from intelligence.taxonomy_builder.run_contracts import (
    reviewable_context_fact_batch,review_context_facts,prompt_for,schema_for)
from intelligence.taxonomy_builder.context_binding import context_material,MODE
from intelligence.project_sources.context_builder import sha as context_sha

class ContextFactsTests(unittest.TestCase):
    def setUp(self):
        self.eid=str(uuid4())
        self.input={"input_mode":MODE,"context_binding":{"context_id":str(uuid4())},
           "context_evidence":[{
             "evidence_id":self.eid,"text":"The device measures daily step count.",
             "allowed_fact_kinds":["function","problem"],"source_id":str(uuid4()),
             "source_version_id":str(uuid4()),"source_type":"questionnaire_field",
             "citation":"profile:features:0-35","knowledge_scope":"operational_product",
             "evidence_kind":"user_claim","trust_label":"user_claim",
           }]}
        self.valid={"facts":[{"kind":"function","text":"Measures daily steps",
            "source_evidence_id":self.eid,"source_quote":"measures daily step count"}]}

    def test_exact_evidence_and_origin_preserved(self):
        batch=reviewable_context_fact_batch(self.valid,self.input)
        self.assertEqual(len(batch["facts"]),1)
        fact=batch["facts"][0]
        self.assertEqual(fact["source_evidence_id"],self.eid)
        self.assertEqual(fact["evidence_kind"],"user_claim")
        approved=review_context_facts([fact],[{"id":fact["id"],"action":"approve"}])
        self.assertEqual(approved[0]["origin"],"context_source_reviewed")
        self.assertEqual(approved[0]["source_quote"],"measures daily step count")

    def test_forged_id_and_hallucinated_quote_quarantined(self):
        good=self.valid["facts"][0]
        response={"facts":[good,{**good,"source_evidence_id":str(uuid4())},
          {**good,"source_quote":"step counter invented"},
          {**good,"source_quote":"Measures daily step count"}]}
        result=reviewable_context_fact_batch(response,self.input)
        self.assertEqual(len(result["facts"]),1)
        self.assertEqual(len(result["excluded_facts"]),3)

    def test_external_claim_cannot_be_product_function(self):
        self.input["context_evidence"][0]["evidence_kind"]="external_claim"
        self.input["context_evidence"][0]["allowed_fact_kinds"]=["problem","audience"]
        with self.assertRaises(PipelineError):
            reviewable_context_fact_batch(self.valid,self.input)
        self.valid["facts"][0]["kind"]="problem"
        self.assertEqual(len(reviewable_context_fact_batch(self.valid,self.input)["facts"]),1)

    def test_review_requires_explicit_yes_no_for_all(self):
        fact=reviewable_context_fact_batch(self.valid,self.input)["facts"][0]
        with self.assertRaises(PipelineError):
            review_context_facts([fact],[{"action":"edit","id":fact["id"],"text":"Unverified"}])
        with self.assertRaises(PipelineError):
            review_context_facts([fact],[{"action":"add","kind":"function","text":"Unverified"}])
        with self.assertRaises(PipelineError):
            review_context_facts([fact],[])
        with self.assertRaises(PipelineError):
            review_context_facts([fact],[{"action":"reject","id":fact["id"]}])

    def test_prompt_schema_and_untrusted_data_boundary(self):
        schema=schema_for("fact_extraction",context_mode=True)
        keys=schema["schema"]["properties"]["facts"]["items"]["required"]
        self.assertIn("source_evidence_id",keys)
        self.assertNotIn("source_field",keys)
        msgs=prompt_for("fact_extraction",self.input,"facts-context-v1")
        self.assertIn("untrusted DATA",msgs[0]["content"])
        self.assertIn(self.eid,msgs[1]["content"])
        self.assertEqual(schema_for("fact_extraction")["schema"]["properties"]["facts"]["items"]["required"],
                         ["kind","text","source_field","source_quote"])
        with self.assertRaises(PipelineError):
            schema_for("taxonomy_generation",context_mode=True)

class BindingUnitTests(unittest.TestCase):
    def test_approved_context_source_pins_and_budget(self):
        pid=uuid4().hex;cid=str(uuid4());sid=str(uuid4());vid=str(uuid4());eid=str(uuid4())
        text="An evidenced audience problem about sleep and routines."
        source={"source_id":sid,"source_type":"research_document","sha256_kind":"source_byte_sha256"}
        ev={"evidence_id":eid,"source_id":sid,"source_version_id":vid,"kind":"external_claim",
            "knowledge_scope":"general_research","review_decision":"approved",
            "trust_label":"extracted_document_unverified","citation":"doc:page1:1-30",
            "exact_text_sha256":hashlib.sha256(text.encode()).hexdigest()}
        data={"schema_version":"project-context/v1","project_id":pid,"source_manifest":[source],
              "evidence_manifest":[ev],"unavailable_or_excluded_sources":[]}
        snap={"approved":True,"approval":{"approval_id":str(uuid4()),"decision":"approved"},
              "context":data,"context_sha256":context_sha(data),
              "profile_revision":1,"profile_hash":"a"*64}
        class Conn:
            def execute(self,query,params):
                return self
            def fetchall(self):
                return [(eid,text)]
        with patch("intelligence.taxonomy_builder.context_binding._snapshot",return_value=snap),patch(
             "intelligence.taxonomy_builder.context_binding._assemble",return_value=data):
            material=context_material(Conn(),pid,cid,500)
            self.assertEqual(material["binding"]["selected_evidence_ids"],[eid])
            self.assertEqual(material["selection"][0]["allowed_fact_kinds"],["problem","audience"])
            self.assertEqual(material["binding"]["context_sha256"],context_sha(data))
            with self.assertRaises(PipelineError):
                context_material(Conn(),pid,cid,99)
            snap["approval"]["decision"]="rejected"
            with self.assertRaises(PipelineError):
                context_material(Conn(),pid,cid,500)

if __name__=="__main__": unittest.main()
