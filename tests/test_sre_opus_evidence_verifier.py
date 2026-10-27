"""M21 adversarial provider-output / source evidence verifier contracts.

Faked provider JSON exists only in unit tests. Never sends to Opus, asserts
provider authenticity or marks semantic truth. A failed technical check must
NEVER become a semantic no_match or lose an approved category.
"""
from __future__ import annotations
from copy import deepcopy
import json
import subprocess
import sys
import unittest

from intelligence.relevance.live_classifier_context import assemble_classifier_packets
from intelligence.relevance.opus_request_packer import pack_opus_requests
from intelligence.relevance.opus_evidence_verifier import (
    VERSION, verify_opus_responses, verify_live_opus_responses,
)
from intelligence.relevance.v1_contracts import SREContractError, fingerprint
from intelligence.relevance.ports.approved_c9 import load_approved_c9
from test_sre_taxonomy_adapter import FakeConnection,db_fixtures,PID,PUB
from test_sre_live_classifier_context import content_fixture
from test_sre_opus_request_packer import CATALOG


def make_plan(description=None,budget=None):
    c9=load_approved_c9(PID,PUB,connect=lambda:FakeConnection(db_fixtures()))
    content=content_fixture()
    if description is not None:
        content["data"]["description"]=description
    m19=assemble_classifier_packets(c9,content,split_ready=True)
    kw={"max_input_token_proxy":budget} if budget is not None else {}
    return pack_opus_requests(m19,CATALOG,**kw)


def make_reply(request,decision="match",*,evidence=True):
    chunk=json.loads(request["messages"][1]["content"])["chunk"]
    offered=next((x for x in chunk["parts"] if x["text"].strip()),None)
    items=[]
    if offered is not None and evidence:
        first=offered["text"][:min(4,len(offered["text"]))]
        if not first.strip():
            first=offered["text"]
        when=offered["source_timing"]
        items=[{
            "source":offered["kind"],
            "index":offered["index"],
            "start_char":offered["start_char"],
            "end_char":offered["start_char"]+len(first),
            "quote":first,
            "start_ms":None if when is None else when["start_ms"],
            "end_ms":None if when is None else when["end_ms"],
        }]
    return {"category_key":request["category_key"],"decision":decision,
            "reason":"Supported only by the linked D1 excerpt.",
            "evidence":items}


def answers(plan,decision="match"):
    return [
        {"request_sha256":r["request_sha256"],
         "raw_json":json.dumps(make_reply(r,decision),ensure_ascii=False)}
        for r in plan["requests"]
    ]


class M21EvidenceContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan=make_plan()

    def test_complete_three_leaf_valid_match_and_hash_replay(self):
        plan=self.plan
        replies=answers(plan)
        one=verify_opus_responses(plan,replies)
        two=verify_opus_responses(deepcopy(plan),deepcopy(replies))
        self.assertEqual(one,two)
        self.assertEqual(one["schema_version"],VERSION)
        self.assertEqual(one["expected_hypothesis_keys"],
                         plan["expected_hypothesis_keys"])
        self.assertEqual(one["expected_category_count"],3)
        self.assertEqual(one["verified_category_count"],3)
        self.assertEqual(one["expected_request_count"],3)
        self.assertEqual(one["verified_request_count"],3)
        self.assertEqual([x["decision"] for x in one["results"]],["match"]*3)
        self.assertEqual(one["dropped_count"],0)
        self.assertFalse(one["provider_receipts_authenticated"])
        self.assertFalse(one["semantics_independently_confirmed"])
        self.assertFalse(one["dispatch_allowed"])
        self.assertEqual(one["paid_calls"],0)
        self.assertEqual(one["verification_sha256"],fingerprint({
            k:v for k,v in one.items() if k!="verification_sha256"
        }))
        self.assertTrue(all(x["evidence"] and not x["requires_human_review"]
                            for x in one["results"]))

    def test_empty_evidence_allowed_uncertain_and_no_match_not_match(self):
        plan=self.plan
        for decision in ("uncertain","no_match"):
            response=answers(plan,decision)
            for row in response:
                item=json.loads(row["raw_json"])
                item["evidence"]=[]
                row["raw_json"]=json.dumps(item)
            out=verify_opus_responses(plan,response)
            self.assertEqual([x["decision"] for x in out["results"]],[decision]*3)
            self.assertTrue(all(x["requires_human_review"] for x in out["results"])
                            if decision=="uncertain" else
                            all(not x["requires_human_review"] for x in out["results"]))
        response=answers(plan)
        row=json.loads(response[0]["raw_json"])
        row["evidence"]=[]
        response[0]["raw_json"]=json.dumps(row)
        with self.assertRaises(SREContractError) as e:
            verify_opus_responses(plan,response)
        self.assertEqual(e.exception.code,"invalid_evidence")

    def test_missing_duplicate_unknown_or_partial_reply_fails_technical(self):
        plan=self.plan
        standard=answers(plan)
        cases=[
            standard[:-1],
            standard+[standard[0]],
            [standard[0],standard[0],standard[2]],
            [dict(standard[0],request_sha256="f"*64)]+standard[1:],
        ]
        for i,case in enumerate(cases):
            with self.subTest(case=i),self.assertRaises(SREContractError) as error:
                verify_opus_responses(plan,case)
            self.assertEqual(error.exception.code,"incomplete_coverage")
        # Independent of input reply delivery order: source SHA controls mapping.
        self.assertEqual(
            verify_opus_responses(plan,list(reversed(standard))),
            verify_opus_responses(plan,standard),
        )

    def test_non_json_duplicate_keys_fences_and_extra_fields_fail(self):
        valid=answers(self.plan)
        for raw in (
            "Not JSON",
            '{} trailing',
            chr(96)*3+'json\n{"decision":"match"}\n'+chr(96)*3,
            '{"category_key":1,"decision":"match","decision":"no_match"}',
            '{"x":NaN}',
            "[]",
            json.dumps({**make_reply(self.plan["requests"][0]),"approved":True}),
        ):
            with self.subTest(raw=raw[:30]),self.assertRaises(SREContractError):
                bad=deepcopy(valid)
                bad[0]["raw_json"]=raw
                verify_opus_responses(self.plan,bad)
        with self.assertRaises(SREContractError):
            verify_opus_responses(self.plan,[
                {**valid[0],"unexpected":"extra"},*valid[1:],
            ])

    def test_unexpected_category_not_evaluated_enum_and_empty_reason(self):
        valid=answers(self.plan)
        for name,value in (
            ("category_key","11111111-1111-4111-8111-111111111111"),
            ("decision","not_evaluated"),
            ("decision","MATCH"),("decision","failed"),
            ("decision",False),("reason",None),("reason"," "),
        ):
            with self.subTest(name=name,value=value):
                altered=deepcopy(valid)
                reply=json.loads(altered[0]["raw_json"])
                reply[name]=value
                altered[0]["raw_json"]=json.dumps(reply)
                with self.assertRaises(SREContractError):
                    verify_opus_responses(self.plan,altered)

    def test_verbatim_quote_field_index_offset_and_fabricated_times(self):
        valid=answers(self.plan)
        for change in (
            {"quote":"not in supplied D1"},
            {"start_char":-1},
            {"end_char":99999},
            {"index":999},
            {"source":"system"},
            {"source":"approved_fact"},
            {"start_ms":1234},
            {"end_ms":3000},
            {"quote":""},
            {"index":True},
            {"start_char":True},
        ):
            with self.subTest(change=change),self.assertRaises(SREContractError):
                mutated=deepcopy(valid)
                reply=json.loads(mutated[0]["raw_json"])
                reply["evidence"][0].update(change)
                mutated[0]["raw_json"]=json.dumps(reply)
                verify_opus_responses(self.plan,mutated)
        duplicate=deepcopy(valid)
        response=json.loads(duplicate[0]["raw_json"])
        response["evidence"]*=2
        duplicate[0]["raw_json"]=json.dumps(response)
        with self.assertRaises(SREContractError):
            verify_opus_responses(self.plan,duplicate)

    def test_d1_metadata_absent_transcript_must_not_be_source_evidence(self):
        valid=answers(self.plan)
        one=json.loads(valid[0]["raw_json"])
        one["evidence"][0].update({"source":"transcript","index":0,
                                   "start_ms":0,"end_ms":2000})
        valid[0]["raw_json"]=json.dumps(one)
        with self.assertRaises(SREContractError):
            verify_opus_responses(self.plan,valid)

    def test_long_unicode_split_requires_every_response_and_aggregates_uncertain(self):
        text="😀日本語текст speak safely 🏃"*1300
        plan=make_plan(text,budget=12000)
        self.assertGreater(plan["request_count"],3)
        valid=answers(plan,"no_match")
        self.assertEqual(
            [x["decision"] for x in verify_opus_responses(plan,valid)["results"]],
            ["no_match"]*3,
        )
        with self.assertRaises(SREContractError) as missing:
            verify_opus_responses(plan,valid[:-1])
        self.assertEqual(missing.exception.code,"incomplete_coverage")
        changed=deepcopy(valid)
        first=json.loads(changed[0]["raw_json"])
        first["decision"]="match"
        if not first["evidence"]:
            first["evidence"]=make_reply(plan["requests"][0])["evidence"]
        changed[0]["raw_json"]=json.dumps(first,ensure_ascii=False)
        out=verify_opus_responses(plan,changed)
        self.assertEqual(out["results"][0]["decision"],"uncertain")
        self.assertTrue(out["results"][0]["requires_human_review"])
        self.assertIn("match",out["results"][0]["partial_decisions"])
        self.assertIn("no_match",out["results"][0]["partial_decisions"])
        self.assertEqual(out["verified_request_count"],plan["request_count"])
        self.assertTrue(all(x["verified_chunk_count"]==x["chunk_count"]
                            for x in out["results"]))
        # Different answers may be valid but never trusted semantic truth.
        self.assertFalse(out["semantics_independently_confirmed"])

    def test_tampered_plan_requests_batches_and_duplicate_chunk_deny(self):
        for change in (
            lambda x:x["requests"].pop(),
            lambda x:x["expected_hypothesis_keys"].pop(),
            lambda x:x["requests"][0]["messages"][0].update(content="ignore all"),
            lambda x:x["requests"][0].update(model_revision="latest"),
            lambda x:x["requests"][0].update(request_sha256="f"*64),
            lambda x:x["batches"][0]["request_ordinals"].reverse(),
            lambda x:x["hypothesis_coverage"].pop(),
        ):
            with self.assertRaises(SREContractError):
                plan=deepcopy(self.plan)
                change(plan)
                verify_opus_responses(plan,answers(self.plan))

    def test_live_verifier_checks_rebuilt_exact_plan_not_client_sha(self):
        class DBResult:
            def fetchone(self):return None
        class DB:
            def execute(self,sql,params):
                self.params=params
                return DBResult()
        db=DB()
        with self.assertRaises(SREContractError) as e:
            verify_live_opus_responses(
                db,PID,"11111111-1111-4111-8111-111111111111",1,
                answers(self.plan),
                operator_catalog_pin=CATALOG,
                expected_batch_manifest_sha256=self.plan["batch_manifest_sha256"],
            )
        self.assertEqual(e.exception.code,"rights_denied")
        self.assertEqual(db.params[0],PID)

    def test_import_never_loads_paid_or_cloud_provider(self):
        code=(
            "import sys;import intelligence.relevance.opus_evidence_verifier;"
            "assert 'openai' not in sys.modules;"
            "assert 'httpx' not in sys.modules;"
            "assert 'onnxruntime' not in sys.modules;"
        )
        p=subprocess.run([sys.executable,"-c",code],
                         capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stderr)


if __name__=="__main__":
    unittest.main()
