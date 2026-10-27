"""M28 authenticated-generation financial shape and strict zero-cost fixtures.

The scripted metadata below is NOT an authenticated provider receipt: only
the production isolated HTTPS GET inside settle_live_sre_opus_generation can
confirm a generation, and the paid POST remains globally disabled.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch
import json
import socket
import unittest

from intelligence.relevance.opus_receipt import (
    VERSION,GENERATION_URL,MAX_GENERATION_BYTES,_decimal_cost,
    _read_generation_https,_provider_answer,validate_openrouter_generation,
    settle_live_sre_opus_generation,
)
from intelligence.relevance.v1_contracts import SREContractError

MODEL="anthropic/claude-opus-5.5"
GEN="gen-m28-offline-only"
PROJECT="a"*32
UUID="11111111-1111-4111-8111-111111111111"


def metadata():
    return {
        "id":GEN,"model":MODEL,"provider_name":"Anthropic",
        "is_byok":False,"cancelled":False,
        "tokens_prompt":160,"tokens_completion":45,
        "total_cost":Decimal("0.01000000001"),
        "num_fetches":0,"num_search_results":0,"num_media_prompt":0,
        "num_input_audio_prompt":0,"num_media_completion":0,
    }


class FakeHttpsResponse:
    def __init__(self,data,*,url=None,status=200,media="application/json"):
        self.data=data
        self.url=url or GENERATION_URL+"?id="+GEN
        self.status=status
        self.headers={"Content-Type":media}
    def geturl(self):return self.url
    def read(self,n=-1):return self.data[:n]
    def __enter__(self):return self
    def __exit__(self,*args):return False


class FakeOpener:
    def __init__(self,response):
        self.response=response
        self.requests=[]
    def open(self,request,timeout):
        self.requests.append((request,timeout))
        return self.response


class M28OpusReceiptOffline(unittest.TestCase):
    def check(self,data=None,**kw):
        return validate_openrouter_generation(
            data if data is not None else metadata(),
            generation_id=GEN,upper_bound_usd="1.000000000",
            max_input_tokens=1000,max_output_tokens=200,**kw)

    def test_exact_ceil_usd_decimal_not_float_not_price_estimate(self):
        result=self.check()
        self.assertEqual(result["schema_version"],VERSION)
        self.assertEqual(result["charged_usd"],"0.010000001")
        self.assertEqual(result["unspent_usd"],"0.989999999")
        self.assertFalse(result["provider_origin_authenticated"])
        self.assertFalse(result["total_account_fees_verified"])
        self.assertEqual(result["paid_calls_issued_here"],0)
        self.assertEqual(_decimal_cost(Decimal("0.000000000001")),
                         Decimal("0.000000001"))
        self.assertEqual(_decimal_cost(Decimal("0")),Decimal("0"))
        for invalid in (True,False,0.1,"0.1",None,float("nan"),
                        Decimal("Infinity"),Decimal("-0.01")):
            with self.subTest(invalid=str(invalid)),self.assertRaises(SREContractError):
                _decimal_cost(invalid)

    def test_missing_or_wrong_provider_model_aux_fees_and_byok_block(self):
        changes=[
            ("id","gen-foreign"),("model","openrouter/auto"),
            ("provider_name","DifferentVendor"),("is_byok",True),
            ("is_byok",None),("cancelled",True),("cancelled",None),
            ("num_search_results",2),("num_fetches",None),
            ("num_media_prompt",1),("total_cost",None),
            ("tokens_prompt",-1),("tokens_completion",True),
            ("tokens_prompt",1001),("tokens_completion",201),
            ("total_cost",Decimal("1.01")),
        ]
        for key,value in changes:
            x=metadata()
            x[key]=value
            with self.subTest(key=key,val=str(value)),self.assertRaises(SREContractError):
                self.check(x)

    def test_cost_cannot_silently_round_down_or_overflow(self):
        x=metadata()
        x["total_cost"]=Decimal("1.00000000001")
        with self.assertRaises(SREContractError) as over:
            self.check(x)
        self.assertEqual(over.exception.code,"provider_cost_exceeds_reserve")
        for bad in ("NaN",Decimal("NaN"),Decimal("9e50")):
            with self.subTest(bad=str(bad)),self.assertRaises(SREContractError):
                _decimal_cost(bad)
        with self.assertRaises(SREContractError):
            validate_openrouter_generation(metadata(),generation_id=GEN,
                upper_bound_usd=0.2,max_input_tokens=1000,max_output_tokens=200)

    def test_exact_read_only_https_generation_with_injected_offline_opener(self):
        response={"data":{**metadata(),"total_cost":0.125}}
        blob=json.dumps(response).encode("utf-8")
        fake=FakeOpener(FakeHttpsResponse(blob))
        with patch.object(socket.socket,"connect",
                          side_effect=AssertionError("UNEXPECTED NETWORK")):
            result,digest=_read_generation_https(
                GEN,"synthetic-offline-token-123456",opener=fake)
        self.assertEqual(result["id"],GEN)
        self.assertIsInstance(result["total_cost"],Decimal)
        self.assertEqual(result["total_cost"],Decimal("0.125"))
        self.assertEqual(len(digest),64)
        req,seconds=fake.requests[0]
        self.assertEqual(req.get_method(),"GET")
        self.assertEqual(req.full_url,GENERATION_URL+"?id="+GEN)
        self.assertEqual(seconds,8)
        self.assertEqual(req.get_header("Authorization"),
                         "Bearer synthetic-offline-token-123456")

    def test_redirect_bad_json_oversized_no_generation_or_wrong_media(self):
        examples=[
            FakeHttpsResponse(b'{}'),
            FakeHttpsResponse(b'nonsense'),
            FakeHttpsResponse(b'{"data":{}}',status=404),
            FakeHttpsResponse(b'{"data":{}}',media="text/html"),
            FakeHttpsResponse(b'{"data":{}}',url="https://elsewhere.invalid"),
            FakeHttpsResponse(b'x'*(MAX_GENERATION_BYTES+1)),
        ]
        for candidate in examples:
            fake=FakeOpener(candidate)
            with self.subTest(data_len=len(candidate.data)):
                with self.assertRaises(SREContractError):
                    _read_generation_https(
                        GEN,"synthetic-offline-token-123456",opener=fake)
                self.assertEqual(len(fake.requests),1)

    def test_response_model_id_matches_generation_without_claiming_semantics(self):
        response={"id":GEN,"model":MODEL,"choices":[
            {"index":0,"message":{"role":"assistant","content":"{}"}}]}
        self.assertEqual(_provider_answer(response,GEN),"{}")
        for v in ({"id":"gen-other"}, {"model":"openrouter/auto"},
                  {"choices":[]},{"choices":[{},{}]}):
            broken={**response,**v}
            with self.assertRaises(SREContractError):
                _provider_answer(broken,GEN)
        malformed=deepcopy(response)
        malformed["choices"][0]["message"]["content"]=None
        self.assertIsNone(_provider_answer(malformed,GEN))

    def test_unprivileged_settlement_cannot_open_database_or_network(self):
        class NeverDB:
            autocommit=False
            def transaction(self):raise AssertionError("DB reached")
        fake_response={"id":GEN,"model":MODEL,"choices":[]}
        with patch("intelligence.relevance.opus_receipt._read_generation_https",
                   side_effect=AssertionError("PROVIDER REACHED")):
            with self.assertRaises(SREContractError) as blocked:
                settle_live_sre_opus_generation(
                    NeverDB(),PROJECT,UUID,1,UUID,fake_response,GEN,
                    api_key="synthetic-offline-token-123456",
                    actor_id="foreign",actor_authorizer=lambda *a:False)
        self.assertEqual(blocked.exception.code,"actor_not_authorized")


if __name__=="__main__":
    unittest.main()
