"""M22 zero-cost exact OpenRouter Opus 5.5 endpoint/catalog contracts.

All catalog responses here are OFFLINE FIXTURES. Their self-SHA is not
provider authenticity; production requires independent operator approval.
No API POST, paid model generation, user credentials or live quote made.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from intelligence.relevance.opus_catalog_preflight import (
    MODEL_ID, CATALOG_URL, PREFLIGHT_VERSION, SNAPSHOT_VERSION,
    capture_exact_opus_catalog, preflight_exact_opus_catalog,
    preflight_exact_opus_batch,
)
from intelligence.relevance.v1_contracts import SREContractError, fingerprint

NOW=datetime(2026,10,27,10,0,tzinfo=timezone.utc)
REV="anthropic/claude-opus-5.5-20260921"
TAG="anthropic"
PRICE={
    "prompt":"0.000004","completion":"0.000020",
    "input_cache_read":"0.0000002",
    "input_cache_write":"0.000005",
    "input_cache_write_1h":"0.000008",
}
SUPPORT=[
    "response_format","structured_outputs","max_tokens","reasoning_effort",
]
ENDPOINT={
    "name":"Anthropic | "+REV,
    "model_id":MODEL_ID,
    "provider_name":"Anthropic",
    "tag":TAG,
    "status":0,
    "context_length":1000000,
    "max_completion_tokens":128000,
    "supported_parameters":SUPPORT,
    "pricing":PRICE,
}


def catalog_payload():
    return {
        "data":{
            "id":MODEL_ID,
            "name":"Anthropic: Claude Opus 5.5",
            "endpoints":[deepcopy(ENDPOINT)],
        },
    }


def captured(payload=None,when=None):
    data=deepcopy(payload if payload is not None else catalog_payload())
    return {
        "schema_version":SNAPSHOT_VERSION,
        "source_url":CATALOG_URL,
        "captured_at":(when or NOW).isoformat().replace("+00:00","Z"),
        "response":data,
        "response_sha256":fingerprint(data),
    }


def preflight(snapshot=None,**overrides):
    snapshot=snapshot if snapshot is not None else captured()
    kwargs={
        "operator_expected_sha256":snapshot["response_sha256"],
        "operator_endpoint_tag":TAG,
        "operator_model_revision":REV,
        "requested_max_output_tokens":4096,
        "requested_input_proxy":50000,
        "now":NOW,
    }
    kwargs.update(overrides)
    return preflight_exact_opus_catalog(snapshot,**kwargs)


class OfflineResponse:
    def __init__(self,data,*,url=CATALOG_URL,status=200,
                 content_type="application/json"):
        self.data=data
        self.url=url
        self.status=status
        self.headers={"Content-Type":content_type}
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def geturl(self):return self.url
    def read(self,n=-1):return self.data[:n]


class OfflineOpener:
    def __init__(self,response):
        self.response=response
        self.calls=[]
    def open(self,request,timeout):
        self.calls.append((request,timeout))
        return self.response


class M22CatalogPreflight(unittest.TestCase):
    def test_exact_opus5_5_json_endpoint_price_and_routing_without_quote(self):
        result=preflight()
        self.assertEqual(result["schema_version"],PREFLIGHT_VERSION)
        self.assertEqual(result["status"],"eligible_for_M23_quote_only")
        self.assertEqual(result["model_id"],MODEL_ID)
        self.assertEqual(result["model_revision"],REV)
        self.assertEqual(result["endpoint_tag"],TAG)
        self.assertTrue(result["structured_outputs_supported"])
        self.assertTrue(result["response_format_json_schema_supported"])
        self.assertEqual(result["routing"],{
            "only":["anthropic"],"allow_fallbacks":False,
        })
        self.assertEqual(result["context_window_tokens"],1000000)
        self.assertEqual(result["max_completion_tokens"],128000)
        self.assertEqual(result["pricing_usd_per_token"],{
            **PRICE,"completion":"0.000020",
        })
        self.assertEqual(result["m20_catalog_pin"]["model_id"],MODEL_ID)
        self.assertEqual(result["m20_catalog_pin"]["model_revision"],REV)
        self.assertEqual(result["m20_catalog_pin"]["catalog_sha256"],
                         captured()["response_sha256"])
        self.assertFalse(result["quote_ready"])
        self.assertFalse(result["dispatch_allowed"])
        self.assertFalse(result["token_count_exact"])
        self.assertFalse(result["temperature_send_supported"])
        self.assertTrue(result["requires_true_token_check"])
        self.assertTrue(result["requires_live_catalog_revalidation"])
        self.assertTrue(result["requires_provider_endpoint_enforcement"])
        self.assertEqual(result["paid_calls"],0)
        self.assertEqual(result["preflight_sha256"],fingerprint({
            k:v for k,v in result.items() if k!="preflight_sha256"
        }))
        self.assertEqual(result,preflight())

    def test_response_sha_from_separate_operator_authority_required(self):
        snap=captured()
        with self.assertRaises(SREContractError) as err:
            preflight(snap,operator_expected_sha256="0"*64)
        self.assertEqual(err.exception.code,"snapshot_mismatch")
        snap["response"]["data"]["endpoints"][0]["pricing"]["prompt"]="0.0000001"
        with self.assertRaises(SREContractError):
            preflight(snap)
        snap=captured()
        snap["source_url"]="https://evil.example/catalog"
        with self.assertRaises(SREContractError):
            preflight(snap)
        snap=captured()
        snap["schema_version"]="broken"
        with self.assertRaises(SREContractError):
            preflight(snap)

    def test_stale_future_timestamp_and_explicit_ttl_fail_closed(self):
        for dt in (NOW-timedelta(seconds=901),
                   NOW+timedelta(seconds=31)):
            with self.subTest(at=dt),self.assertRaises(SREContractError) as err:
                preflight(captured(when=dt))
            self.assertEqual(err.exception.code,"stale_catalog")
        self.assertEqual(preflight(captured(when=NOW-timedelta(seconds=900)))
                         ["model_id"],MODEL_ID)
        for ttl in (0,901,3600,True,-1,"900",1.5):
            with self.subTest(ttl=ttl),self.assertRaises(SREContractError):
                preflight(max_age_seconds=ttl)
        snap=captured()
        snap["captured_at"]="2026-10-27T10:00:00"  # naive
        with self.assertRaises(SREContractError):
            preflight(snap)
        snap=captured()
        snap["captured_at"]="not-a-date"
        with self.assertRaises(SREContractError):
            preflight(snap)

    def test_only_exact_model_no_opus5_or_sonnet_fallback(self):
        for model in ("anthropic/claude-opus-5","anthropic/claude-sonnet-5.5",
                      "anthropic/claude-opus-5.5:free"):
            obj=catalog_payload()
            obj["data"]["id"]=model
            with self.subTest(model=model),self.assertRaises(SREContractError) as e:
                preflight(captured(obj))
            self.assertEqual(e.exception.code,"model_unavailable")
        for version in (
            "anthropic/claude-opus-5.5-latest","claude-opus-5.5",
            "anthropic/claude-opus-5.5",REV+"-fake",
        ):
            with self.subTest(revision=version):
                if version.endswith("-fake"):
                    with self.assertRaises(SREContractError):
                        preflight(operator_model_revision=version)
                else:
                    with self.assertRaises(SREContractError):
                        preflight(operator_model_revision=version)

    def test_missing_json_schema_structured_outputs_or_unavailable_endpoint(self):
        for bad in (
            lambda e:e["supported_parameters"].remove("structured_outputs"),
            lambda e:e["supported_parameters"].remove("response_format"),
            lambda e:e.update(supported_parameters=None),
            lambda e:e.update(status=1),
            lambda e:e.update(status=True),
            lambda e:e.update(model_id="anthropic/claude-opus-5"),
            lambda e:e.update(name="Anthropic | wrong-revision"),
        ):
            obj=catalog_payload()
            bad(obj["data"]["endpoints"][0])
            with self.subTest(mutated=obj["data"]["endpoints"][0]),self.assertRaises(SREContractError):
                preflight(captured(obj))
        obj=catalog_payload()
        obj["data"]["endpoints"]=[]
        with self.assertRaises(SREContractError):
            preflight(captured(obj))
        obj=catalog_payload()
        obj["data"]["endpoints"].append(deepcopy(obj["data"]["endpoints"][0]))
        with self.assertRaises(SREContractError):
            preflight(captured(obj))
        with self.assertRaises(SREContractError):
            preflight(operator_endpoint_tag="not-a-real-provider")
        with self.assertRaises(SREContractError):
            preflight(operator_endpoint_tag="../evil")

    def test_precise_decimal_pricing_missing_nonfinite_float_or_negative_blocks(self):
        for field in PRICE:
            for value in (None,0,0.000004,"NaN","-0.01","", "1e-6",
                          "1000","0.00000000000000000001"):
                obj=catalog_payload()
                obj["data"]["endpoints"][0]["pricing"][field]=value
                with self.subTest(field=field,value=value),self.assertRaises(SREContractError):
                    preflight(captured(obj))
        for field in ("prompt","completion"):
            obj=catalog_payload()
            obj["data"]["endpoints"][0]["pricing"][field]="0"
            with self.assertRaises(SREContractError):
                preflight(captured(obj))
        obj=catalog_payload()
        obj["data"]["endpoints"][0].pop("pricing")
        with self.assertRaises(SREContractError):
            preflight(captured(obj))

    def test_context_output_and_operator_proxy_budget_fail_closed(self):
        for kw in (
            {"requested_max_output_tokens":0},
            {"requested_max_output_tokens":200000},
            {"requested_max_output_tokens":True},
            {"requested_input_proxy":3000},
            {"requested_input_proxy":250001},
            {"requested_input_proxy":True},
        ):
            with self.subTest(kw=kw),self.assertRaises(SREContractError):
                preflight(**kw)
        obj=catalog_payload()
        obj["data"]["endpoints"][0]["max_completion_tokens"]=0
        with self.assertRaises(SREContractError):
            preflight(captured(obj))
        obj=catalog_payload()
        obj["data"]["endpoints"][0]["context_length"]=4500
        with self.assertRaises(SREContractError):
            preflight(captured(obj))

    def test_exact_catalog_get_only_no_redirect_no_post_no_token_credentials(self):
        opener=OfflineOpener(OfflineResponse(
            json.dumps(catalog_payload(),separators=(",",":")).encode("utf-8")))
        capture=capture_exact_opus_catalog(opener=opener,now=NOW)
        self.assertEqual(capture,captured())
        self.assertEqual(len(opener.calls),1)
        req,timeout=opener.calls[0]
        self.assertEqual(req.get_method(),"GET")
        self.assertEqual(req.full_url,CATALOG_URL)
        self.assertEqual(timeout,5)
        self.assertNotIn("Authorization",req.headers)
        self.assertEqual(preflight(capture)["model_id"],MODEL_ID)
        for resp in (
            OfflineResponse(b"{}",url="https://other.example/redirect"),
            OfflineResponse(b"{}",status=404),
            OfflineResponse(b"{}",content_type="text/html"),
            OfflineResponse(b"{broken"),
            OfflineResponse(b"x"*(2*1024*1024+1)),
        ):
            with self.subTest(status=resp.status),self.assertRaises(SREContractError):
                capture_exact_opus_catalog(
                    opener=OfflineOpener(resp),now=NOW,
                )
        with self.assertRaises(SREContractError):
            capture_exact_opus_catalog(timeout_seconds=0,
                                       opener=opener,now=NOW)

    def test_exact_m20_sha_pinned_batch_quote_preflight_has_no_spending_authority(self):
        from intelligence.relevance.live_classifier_context import assemble_classifier_packets
        from intelligence.relevance.opus_request_packer import pack_opus_requests
        from intelligence.relevance.ports.approved_c9 import load_approved_c9
        from test_sre_taxonomy_adapter import FakeConnection,db_fixtures,PID,PUB
        from test_sre_live_classifier_context import content_fixture
        c9=load_approved_c9(PID,PUB,connect=lambda:FakeConnection(db_fixtures()))
        m19=assemble_classifier_packets(c9,content_fixture())
        snap=captured()
        verified=preflight(snap)
        m20=pack_opus_requests(m19,verified["m20_catalog_pin"])
        kw={
            "operator_expected_sha256":snap["response_sha256"],
            "operator_endpoint_tag":TAG,
            "operator_model_revision":REV,
            "now":NOW,
        }
        result=preflight_exact_opus_batch(m20,snap,**kw)
        self.assertEqual(result["status"],"eligible_for_M23_quote_only")
        self.assertEqual(result["model_id"],MODEL_ID)
        self.assertEqual(result["batch_manifest_sha256"],m20["batch_manifest_sha256"])
        self.assertEqual(result["catalog_preflight_sha256"],verified["preflight_sha256"])
        self.assertEqual(result["request_count"],3)
        self.assertEqual(result["pricing_usd_per_token"],verified["pricing_usd_per_token"])
        self.assertEqual(result["requested_input_token_proxy"],50000)
        self.assertFalse(result["quote_ready"])
        self.assertFalse(result["token_count_exact"])
        self.assertFalse(result["dispatch_allowed"])
        self.assertEqual(result["paid_calls"],0)
        self.assertEqual(result["batch_preflight_sha256"],fingerprint({
            k:v for k,v in result.items() if k!="batch_preflight_sha256"
        }))
        self.assertEqual(result,preflight_exact_opus_batch(deepcopy(m20),snap,**kw))
        for mutate in (
            lambda b:b.update(model_id="anthropic/claude-opus-5"),
            lambda b:b.update(model_revision="anthropic/claude-opus-5.5-old"),
            lambda b:b.update(catalog_sha256="f"*64),
            lambda b:b.update(catalog_pin_sha256="0"*64),
            lambda b:b["requests"].pop(),
            lambda b:b["requests"][0].update(max_output_tokens=8000),
        ):
            bad=deepcopy(m20)
            mutate(bad)
            bad["batch_manifest_sha256"]=fingerprint({
                k:v for k,v in bad.items() if k!="batch_manifest_sha256"
            })
            with self.assertRaises(SREContractError):
                preflight_exact_opus_batch(bad,snap,**kw)
        with self.assertRaises(SREContractError) as stale:
            preflight_exact_opus_batch(
                m20,captured(when=NOW-timedelta(minutes=16)),**kw,
            )
        self.assertEqual(stale.exception.code,"stale_catalog")
        # Even a model with identical source content but a different quoted
        # price catalog must produce a different batch SHA and refuse reuse.
        altered=catalog_payload()
        altered["data"]["endpoints"][0]["pricing"]["prompt"]="0.000007"
        newer=captured(altered)
        with self.assertRaises(SREContractError):
            preflight_exact_opus_batch(
                m20,newer,
                **{**kw,"operator_expected_sha256":newer["response_sha256"]},
            )

    def test_import_no_provider_or_paid_client(self):
        code=(
            "import sys; import intelligence.relevance.opus_catalog_preflight;"
            "assert 'openai' not in sys.modules;"
            "assert 'httpx' not in sys.modules;"
            "assert 'onnxruntime' not in sys.modules;"
        )
        output=subprocess.run([sys.executable,"-c",code],
                              capture_output=True,text=True,timeout=15)
        self.assertEqual(output.returncode,0,output.stderr)


if __name__=="__main__":
    unittest.main()
