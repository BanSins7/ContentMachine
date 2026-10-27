"""M27 zero-cost exact Opus HTTP contract, fake all-hypothesis E2E, no live POST."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import json
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

from intelligence.relevance.opus_transport import (
    VERSION, URL, PROVIDER_POLICY, FakeOpusTransport,
    build_pinned_opus_transport_envelope, send_live_openrouter_opus,
    _one_https_post_for_future_gate, _build_https_request,
    MAX_RESPONSE_BYTES,
)
from intelligence.relevance.opus_evidence_verifier import verify_opus_responses
from intelligence.relevance.v1_contracts import SREContractError
from test_sre_opus_final_quote import M23Quote
from test_sre_opus_catalog_preflight import NOW,REV,TAG


INTENT="00000000-0000-4000-8000-000000000011"
ATTEMPT="00000000-0000-4000-8000-000000000012"
RESERVE="00000000-0000-4000-8000-000000000013"
RUN="00000000-0000-4000-8000-000000000014"


class OfflineHTTPResponse:
    def __init__(self,body,*,url=URL,status=200,media="application/json"):
        self.payload=body
        self.url=url
        self.status=status
        self.headers={"Content-Type":media}
    def __enter__(self):return self
    def __exit__(self,*_):return False
    def geturl(self):return self.url
    def read(self,n=-1):return self.payload[:n]


class RecordedOpener:
    def __init__(self,response=None,exc=None):
        self.response=response
        self.exc=exc
        self.requests=[]
    def open(self,request,timeout):
        self.requests.append((request,timeout))
        if self.exc is not None:raise self.exc
        return self.response


class M27OpusTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        M23Quote.setUpClass()
        cls.plan=M23Quote.m20
        cls.catalog=M23Quote.snapshot
        cls.quote=M23Quote().quote()

    def intent(self,idx=0):
        req=self.plan["requests"][idx]
        return {
            "schema_version":"sre-live-opus-dispatch-intent/v1",
            "status":"recorded_no_provider_receipt",
            "intent_id":INTENT,"attempt_id":ATTEMPT,
            "project_id":self.plan["project_id"],
            "run_id":RUN,"item_id":123,
            "request_ordinal":idx,
            "request_sha256":req["request_sha256"],
            "batch_manifest_sha256":self.plan["batch_manifest_sha256"],
            "reservation_id":RESERVE,
            "quote_sha256":self.quote["quote_sha256"],
            "reserved_upper_bound_usd":self.quote["reserve_upper_bound_usd"],
            "intent_at":"2026-10-27T10:00:00+00:00",
            "automatic_retry_allowed":False,
            "requires_exact_token_and_account_price_check":True,
            "provider_send_implemented":False,
            "dispatch_allowed":False,"paid_calls":0,
        }

    def envelope(self,idx=0,**kw):
        return build_pinned_opus_transport_envelope(
            self.plan,self.intent(idx),self.catalog,
            operator_expected_sha256=self.catalog["response_sha256"],
            operator_endpoint_tag=TAG,operator_model_revision=REV,
            now=NOW,**kw)

    def test_exact_model_route_schema_and_no_payment(self):
        env=self.envelope()
        self.assertEqual(env["schema_version"],VERSION)
        self.assertEqual(env["url"],URL)
        self.assertEqual(env["model_id"],"anthropic/claude-opus-5.5")
        self.assertEqual(env["body"]["model"],"anthropic/claude-opus-5.5")
        self.assertEqual(env["body"]["provider"],{**PROVIDER_POLICY,"only":[TAG]})
        self.assertEqual(env["body"]["max_tokens"],4096)
        self.assertEqual(env["body"]["temperature"],0)
        self.assertEqual(env["body"]["stream"],False)
        self.assertEqual(env["body"]["response_format"]["type"],"json_schema")
        self.assertEqual(env["body"]["response_format"]["json_schema"]["strict"],True)
        self.assertEqual(env["request_sha256"],self.plan["requests"][0]["request_sha256"])
        self.assertTrue(env["fake_only"])
        self.assertFalse(env["dispatch_allowed"])
        self.assertFalse(env["exact_provider_tokens_verified"])
        self.assertFalse(env["account_extra_fees_verified"])
        self.assertEqual(env["paid_calls"],0)

    def test_all_three_human_reviewed_c9_leaves_fake_e2e_no_network(self):
        from test_sre_opus_evidence_verifier import make_reply
        scripts={r["request_sha256"]:json.dumps(
            make_reply(r),ensure_ascii=False) for r in self.plan["requests"]}
        scripted=FakeOpusTransport(scripts)
        outputs=[]
        with patch.object(socket.socket,"connect",side_effect=AssertionError("NETWORK OPENED")):
            for i in range(self.plan["request_count"]):
                env=self.envelope(i)
                answer=scripted.execute(env)
                self.assertEqual(answer["source"],"synthetic_fixture_not_provider")
                self.assertFalse(answer["receipt_verified"])
                self.assertIsNone(answer["actual_charge_usd"])
                self.assertEqual(answer["paid_calls"],0)
                outputs.append({
                    "request_sha256":answer["request_sha256"],
                    "raw_json":answer["raw_answer"],
                })
        verified=verify_opus_responses(self.plan,outputs)
        self.assertEqual(verified["verified_category_count"],
                         self.plan["hypothesis_count"])
        self.assertEqual(verified["verified_request_count"],
                         self.plan["request_count"])
        self.assertFalse(verified["provider_receipts_authenticated"])
        self.assertFalse(verified["semantics_independently_confirmed"])
        self.assertEqual(verified["paid_calls"],0)
        with self.assertRaises(SREContractError) as repeated:
            scripted.execute(self.envelope(0))
        self.assertEqual(repeated.exception.code,"unknown_paid_outcome")

    def test_fake_cancel_missing_script_and_bad_structure_fails_closed(self):
        e=self.envelope()
        with self.assertRaises(SREContractError) as cancelled:
            FakeOpusTransport({e["request_sha256"]:'{}'}).execute(
                e,cancelled=True)
        self.assertEqual(cancelled.exception.code,"transport_cancelled")
        with self.assertRaises(SREContractError) as missing:
            FakeOpusTransport().execute(e)
        self.assertEqual(missing.exception.code,"fake_response_missing")
        edited=deepcopy(e)
        edited["body"]["model"]="openai/gpt-4o"
        with self.assertRaises(SREContractError):
            FakeOpusTransport({e["request_sha256"]:'{}'}).execute(edited)
        edited=deepcopy(e)
        edited["body"]["provider"]["allow_fallbacks"]=True
        with self.assertRaises(SREContractError):
            FakeOpusTransport({e["request_sha256"]:'{}'}).execute(edited)
        edited=deepcopy(e)
        edited["fake_only"]=False
        with self.assertRaises(SREContractError):
            FakeOpusTransport({e["request_sha256"]:'{}'}).execute(edited)
        edited=deepcopy(e)
        edited["body"]["messages"][0]["role"]="user"
        with self.assertRaises(SREContractError):
            FakeOpusTransport({e["request_sha256"]:'{}'}).execute(edited)

    def test_intent_and_catalog_changes_must_fail(self):
        def build(plan,intent,catalog,**override):
            kw=dict(operator_expected_sha256=self.catalog["response_sha256"],
                    operator_endpoint_tag=TAG,operator_model_revision=REV,
                    now=NOW)
            kw.update(override)
            return build_pinned_opus_transport_envelope(plan,intent,catalog,**kw)
        i=self.intent()
        i["request_sha256"]="0"*64
        with self.assertRaises(SREContractError):
            build(self.plan,i,self.catalog)
        altered=deepcopy(self.plan)
        altered["model_id"]="openrouter/auto"
        with self.assertRaises(SREContractError):
            build(altered,self.intent(),self.catalog)
        with self.assertRaises(SREContractError):
            build(self.plan,self.intent(),self.catalog,
                  operator_expected_sha256="0"*64)
        with self.assertRaises(SREContractError):
            build(self.plan,self.intent(),self.catalog,
                  operator_endpoint_tag="other")
        with self.assertRaises(SREContractError):
            self.envelope(timeout_seconds=1)
        with self.assertRaises(SREContractError):
            self.envelope(timeout_seconds=True)

    def test_real_live_dispatch_cannot_be_enabled_by_keyword_or_environment(self):
        fake=RecordedOpener()
        with patch.dict("os.environ",{"OPENROUTER_API_KEY":"synthetic_not_a_secret",
                                       "SRE_ENABLE_LIVE_PAID":"1"}), \
             patch("urllib.request.urlopen",side_effect=AssertionError("NETWORK")):
            with self.assertRaises(SREContractError) as blocked:
                send_live_openrouter_opus(
                    envelope=self.envelope(),api_key="not_the_real_key",
                    intent=self.intent(),opener=fake,live_enabled=True,
                    budget_unlimited=True)
        self.assertEqual(blocked.exception.code,"paid_transport_not_ready")
        self.assertEqual(fake.requests,[])

    def test_private_single_post_http_profile_with_only_injected_opener(self):
        # Test request construction without actual socket or provider key.
        envelope=self.envelope()
        raw=json.dumps({
            "id":"chatcmpl-offline",
            "model":"anthropic/claude-opus-5.5",
            "choices":[{"index":0,"message":{"role":"assistant","content":"{}"}}],
        },ensure_ascii=False).encode("utf-8")
        client=RecordedOpener(OfflineHTTPResponse(raw))
        with patch.object(socket.socket,"connect",side_effect=AssertionError("NETWORK OPENED")):
            answer=_one_https_post_for_future_gate(
                json.dumps(envelope["body"]).encode("utf-8"),
                api_key="test_offline_secret_never_real",timeout_seconds=5,
                opener=client)
        self.assertEqual(answer["id"],"chatcmpl-offline")
        request,timeout=client.requests[0]
        self.assertEqual(timeout,5)
        self.assertEqual(request.full_url,URL)
        self.assertEqual(request.get_method(),"POST")
        self.assertEqual(request.get_header("Content-type"),"application/json")
        self.assertEqual(request.get_header("Authorization"),
                         "Bearer test_offline_secret_never_real")

    def test_private_wire_errors_stay_unknown_without_retry_or_key_leak(self):
        body=b'{"model":"anthropic/claude-opus-5.5"}'
        cases=[
            (None,TimeoutError("secret-stub")),
            (OfflineHTTPResponse(b'{}',status=429),None),
            (OfflineHTTPResponse(b'{}',url="https://evil.example"),None),
            (OfflineHTTPResponse(b'{}',media="text/html"),None),
            (OfflineHTTPResponse(b'not-json'),None),
            (OfflineHTTPResponse(json.dumps({"id":"id","model":"openrouter/auto",
                                              "choices":[{}]}).encode()),None),
            (OfflineHTTPResponse(b'a'*(MAX_RESPONSE_BYTES+1)),None),
        ]
        for response,err in cases:
            client=RecordedOpener(response,err)
            with self.subTest(case=str(type(response)),error=type(err).__name__),\
                 self.assertRaises(SREContractError) as outcome:
                _one_https_post_for_future_gate(
                    body,api_key="test_offline_secret_never_real",opener=client)
            self.assertEqual(outcome.exception.code,"provider_outcome_unknown")
            self.assertNotIn("secret-stub",str(outcome.exception))
            self.assertEqual(len(client.requests),1)

    def test_import_is_no_paid_transport_dependency(self):
        test=subprocess.run([sys.executable,"-c",
            "import sys;import intelligence.relevance.opus_transport;"
            "assert 'httpx' not in sys.modules;"
            "assert 'requests' not in sys.modules;"
            "assert 'openai' not in sys.modules;"],
            capture_output=True,text=True,timeout=15)
        self.assertEqual(test.returncode,0,test.stderr)


if __name__=="__main__":
    unittest.main()
