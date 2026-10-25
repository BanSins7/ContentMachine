"""B1 Stage03: coverage-preserving immutable inventory, purely offline."""
from __future__ import annotations
import copy
import hashlib
import unittest
from uuid import uuid4

from intelligence.project_sources import context_builder as a
from intelligence.knowledge_extraction.inventory import build_inventory,check_fragment_text
from intelligence.knowledge_extraction.contracts import ContractError
from intelligence.knowledge_extraction.source_routing import normalize_routing,route_source

class Stage03Inventory(unittest.TestCase):
    def _fixture(self,kind="research_document",schema="document-segments/v1",segments=2):
        pid=uuid4().hex;sid=str(uuid4());vid=str(uuid4());oid=str(uuid4());eid=str(uuid4())
        if schema=="document-segments/v1":
            data={"schema_version":schema,"status":"completed","parser_version":"parser-v1",
                  "segments":[{"text":f"Source factual segment {i}.",
                               "locator":{"page":i+1,"start_char":0,
                                          "end_char":len(f"Source factual segment {i}.")}}
                              for i in range(segments)]}
        elif schema=="web-source-extract/v1":
            txt="External model-reported web claim. This is not independent HTTP verification."
            data={"schema_version":schema,"page_text":txt,
                  "page_text_sha256":hashlib.sha256(txt.encode()).hexdigest(),
                  "trust_level":"model_reported_unverified","fetch_status":"completed"}
        else:
            data={"schema_version":"video-intelligence/v1","project_id":pid,
                  "source_id":sid,"video_source_version_id":vid,
                  "video_sha256":"a"*64,"processing_status":"completed"}
        evidence=[]
        if schema!="video-intelligence/v1":
            q="Source factual segment 0." if schema=="document-segments/v1" else "External model-reported web claim."
            evidence=[{"id":eid,"source_id":sid,"version_id":vid,"output_id":oid,
                "quote":q,"kind":"external_claim","quality":"complete",
                "review_status":"pending","trust":"model_reported_unverified" if schema=="web-source-extract/v1" else "local_document_extraction",
                "scope":"general_research","locator":{"kind":"text","start_char":0,"end_char":len(q)},
                "citation":"source:"+sid+":quote:0","review_decision":"approved","created_at":None}]
        sources=[{"id":sid,"version_id":vid,"source_type":kind,"label":"user provided",
                  "canonical_url":"https://example.org/page" if "website" in kind else None,
                  "status":"completed","initial_status":"completed","content_sha256":"a"*64,
                  "byte_size":100,"version_no":1,"media_type":"text/plain",
                  "locator":{"kind":"fixture"},"recorded_at":None,"tombstoned":False}]
        outputs=[{"id":oid,"source_id":sid,"version_id":vid,
                  "output_type":"video_analysis" if schema=="video-intelligence/v1" else "web_segments" if schema=="web-source-extract/v1" else "document_segments",
                  "schema_version":data["schema_version"],"output_sha256":a.sha(data),
                  "output":data,"created_at":None}]
        ctx=a.build(project_id=pid,profile_revision=1,profile_hash="b"*64,
                    profile_data={},source_rows=sources,output_rows=outputs,evidence_rows=evidence)
        missing=bool(ctx["unavailable_or_excluded_sources"])
        env={"project_id":pid,"context_id":str(uuid4()),"context_sha256":a.sha(ctx),
             "profile_revision":1,"profile_hash":"b"*64,
             "approval":{"decision":"approved_partial" if missing else "approved"},
             "context":ctx}
        return env,{oid:data},{},sid

    def test_full_document_all_501_segments_not_only_one_evidence(self):
        env,outputs,questionnaire,_=self._fixture(segments=501)
        body,fragments,video=build_inventory(env,outputs,questionnaire)
        self.assertEqual(len(fragments),501)
        self.assertEqual(len(env["context"]["evidence_manifest"]),1)
        self.assertTrue(body["text_coverage_complete"])
        self.assertEqual(body["coverage"]["fragments_indexed"],501)
        self.assertEqual(body["coverage"]["text_outputs_indexed"],1)
        self.assertEqual(body["readiness"],"ready")
        self.assertEqual(video,[])
        self.assertNotIn("Source factual segment 0.",str(body))
        self.assertEqual(body["fragments"],fragments)
        self.assertEqual(a.sha(body),a.sha(build_inventory(env,outputs,questionnaire)[0]))
        self.assertEqual(len({x["fragment_id"] for x in fragments}),501)
        first=next(x for x in fragments if x["segment_index"]==0)
        self.assertEqual(first["evidence_ids"],env["context"]["evidence_ids"])
        self.assertTrue(all(not x["evidence_ids"] for x in fragments if x["segment_index"]!=0))

    def test_unknown_document_not_own_product_without_review(self):
        env,outputs,questionnaire,sid=self._fixture()
        body,fragments,_=build_inventory(env,outputs,questionnaire)
        self.assertTrue(all(f["source_role"]=="other" and f["subject_id"] is None for f in fragments))
        route={sid:{"role":"own_product","subject_id":None,"confirmed_by":"local_owner"}}
        confirmed,fs,_=build_inventory(env,outputs,questionnaire,routing_config={"source_routes":route})
        self.assertTrue(all(f["source_role"]=="own_product" for f in fs))
        self.assertTrue(all(f["subject_id"]==env["project_id"] for f in fs))
        self.assertNotEqual(body["config_sha256"],confirmed["config_sha256"])
        with self.assertRaises(ContractError):
            build_inventory(env,outputs,questionnaire,routing_config={"source_routes":{
                sid:{"role":"own_product","subject_id":None,"confirmed_by":"model"}}})
        with self.assertRaises(ContractError):
            build_inventory(env,outputs,questionnaire,routing_config={"source_routes":{
                str(uuid4()):{"role":"competitor","subject_id":None,"confirmed_by":"local_owner"}}})

    def test_web_report_stays_model_reported_unverified_and_complete(self):
        env,outputs,qs,_=self._fixture("product_website","web-source-extract/v1")
        bundle,fs,_=build_inventory(env,outputs,qs)
        self.assertEqual(len(fs),1)
        self.assertEqual(fs[0]["trust_label"],"model_reported_unverified")
        self.assertEqual(fs[0]["source_role"],"own_product")
        self.assertEqual(fs[0]["char_count"],len(next(iter(outputs.values()))["page_text"]))
        self.assertTrue(bundle["text_coverage_complete"])
        bad=copy.deepcopy(outputs)
        next(iter(bad.values()))["page_text_sha256"]="a"*64
        bundle,fs,_=build_inventory(env,bad,qs)
        self.assertEqual(fs,[])
        self.assertEqual(bundle["readiness"],"blocked")
        self.assertEqual(bundle["uncovered_required"][0]["reason"],"web_report_integrity")

    def test_document_partial_and_invalid_segment_explicitly_block(self):
        env,outputs,qs,_=self._fixture()
        next(iter(outputs.values()))["status"]="partial"
        next(iter(outputs.values()))["segments"][1]["locator"]["end_char"]=999
        bundle,fs,_=build_inventory(env,outputs,qs)
        self.assertEqual(len(fs),1)
        self.assertEqual(bundle["readiness"],"blocked")
        self.assertEqual(bundle["coverage"]["uncovered_required"],2)
        self.assertIn("document_processing_incomplete",[x["reason"] for x in bundle["uncovered_required"]])
        self.assertIn("document_offsets_invalid",[x["reason"] for x in bundle["uncovered_required"]])

    def test_video_only_preserves_pinned_refs_even_when_a_excludes_source(self):
        env,outputs,qs,_=self._fixture("user_video_reference","video-intelligence/v1")
        bundle,fragments,video=build_inventory(env,outputs,qs)
        self.assertFalse(fragments)
        self.assertEqual(len(video),1)
        self.assertEqual(video[0]["schema_version"],"video-intelligence/v1")
        self.assertEqual(bundle["approval_decision"],"approved_partial")
        self.assertEqual(bundle["readiness"],"approved_partial")
        self.assertNotIn("must never reach MiMo",str(bundle))
        self.assertEqual(bundle["provider_calls"],0)

    def test_profile_metadata_video_links_not_treated_as_text(self):
        env,outputs,qs,_=self._fixture()
        env["context"]["profile"]["fields"].update({
           "product_description":"Pinned product statement",
           "video_references":["https://www.youtube.com/watch?v=abc12345678"],
           "competitor_socials":["https://www.instagram.com/example/"],
           "promoter_accounts":["https://www.tiktok.com/@example"],
        })
        env["context_sha256"]=a.sha(env["context"])
        body,fragments,_=build_inventory(env,outputs,qs)
        profiles=[f for f in fragments if f["origin_kind"]=="context_profile"]
        self.assertEqual(len(profiles),1)
        self.assertEqual(profiles[0]["source_role"],"own_product")
        self.assertEqual(profiles[0]["field_path"],"product_description")
        self.assertEqual(body["coverage"]["profile_fragments"],1)

    def test_profile_questionnaire_source_identity_not_faked_and_no_duplicate(self):
        env,outputs,qs,sid=self._fixture()
        original=env["context"]["source_manifest"][0]
        original["source_type"]="questionnaire_field"
        original["sha256"]=hashlib.sha256(b"Alpha beta").hexdigest()
        original["byte_size"]=len(b"Alpha beta")
        env["context"]["analysis_refs"]=[]
        original["analysis_refs"]=[]
        env["context"]["profile"]["fields"]["product_description"]="Alpha beta"
        env["context_sha256"]=a.sha(env["context"])
        # The pure inventory consumes server-verified original profile leaf.
        qs[sid]={"field_path":"product_description","text":"Alpha beta"}
        bundle,fs,_=build_inventory(env,{},qs)
        self.assertEqual(len(fs),1)
        self.assertEqual(fs[0]["origin_kind"],"questionnaire_source")
        self.assertEqual(fs[0]["source_id"],sid)
        self.assertEqual(fs[0]["field_path"],"product_description")
        self.assertEqual(bundle["coverage"]["profile_fragments"],1)
        qs[sid]["text"]="tampered"
        bundle,_,_=build_inventory(env,{},qs)
        self.assertEqual(bundle["readiness"],"blocked")

    def test_exact_text_resolver_validation(self):
        env,outputs,qs,_=self._fixture()
        _,fs,_=build_inventory(env,outputs,qs)
        with self.assertRaises(ContractError):
            check_fragment_text(fs[0],"incorrect text")
        self.assertEqual(len(fs[0]["text_sha256"]),64)


if __name__=="__main__":unittest.main()
