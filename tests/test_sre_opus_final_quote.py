"""M23 exact SHA-bound, immutable conservative Decimal USD quote tests.

The Opus model and endpoint response are OFFLINE fixtures. No actual provider
tokenizer, charge, API key, consent grant or model POST is used/claimed.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING
import json
import subprocess
import sys
import unittest

from intelligence.relevance.opus_final_quote import (
    VERSION, build_final_opus_quote, verify_final_opus_quote,
    _usd, USD_QUANT,
)
from intelligence.relevance.opus_request_packer import pack_opus_requests
from intelligence.relevance.live_classifier_context import assemble_classifier_packets
from intelligence.relevance.v1_contracts import SREContractError, fingerprint
from test_sre_opus_catalog_preflight import NOW,REV,TAG,PRICE,captured,preflight,catalog_payload
from test_sre_taxonomy_adapter import FakeConnection,db_fixtures,PID,PUB
from test_sre_live_classifier_context import content_fixture
from intelligence.relevance.ports.approved_c9 import load_approved_c9


class M23Quote(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot=captured()
        cls.pin=preflight(cls.snapshot)["m20_catalog_pin"]
        cls.c9=load_approved_c9(PID,PUB,connect=lambda:FakeConnection(db_fixtures()))
        cls.m20=pack_opus_requests(
            assemble_classifier_packets(cls.c9,content_fixture()),cls.pin,
        )
        cls.kw={
            "operator_expected_sha256":cls.snapshot["response_sha256"],
            "operator_endpoint_tag":TAG,
            "operator_model_revision":REV,
            "operator_maximum_usd":"1000.000000000",
        }

    def quote(self,m20=None,snapshot=None,**overrides):
        kw={**self.kw,"now":NOW}
        kw.update(overrides)
        return build_final_opus_quote(
            m20 if m20 is not None else self.m20,
            snapshot if snapshot is not None else self.snapshot,
            **kw,
        )

    def verify(self,q,m20=None,snapshot=None,**overrides):
        kw={**self.kw,"now":NOW+timedelta(seconds=20)}
        kw.update(overrides)
        return verify_final_opus_quote(
            q,
            m20 if m20 is not None else self.m20,
            snapshot if snapshot is not None else self.snapshot,
            **kw,
        )

    def test_decimal_full_endpoint_token_upper_bound_and_consistent_replay(self):
        q=self.quote()
        self.assertEqual(q,self.quote(deepcopy(self.m20),deepcopy(self.snapshot)))
        self.assertEqual(q["schema_version"],VERSION)
        self.assertEqual(q["status"],"issued_immutable_upper_bound_not_approved")
        self.assertEqual(q["project_id"],PID)
        self.assertEqual(q["publication_id"],PUB)
        self.assertEqual(q["batch_manifest_sha256"],self.m20["batch_manifest_sha256"])
        self.assertEqual(q["catalog_sha256"],self.snapshot["response_sha256"])
        self.assertEqual(q["model_revision"],REV)
        self.assertEqual(q["endpoint_tag"],TAG)
        self.assertEqual(q["currency"],"USD")
        self.assertEqual(q["request_count"],3)
        self.assertEqual(q["hypothesis_count"],3)
        self.assertEqual(q["expected_hypothesis_keys"],
                         self.c9["hypothesis_keys"])
        self.assertEqual(q["min_total_usd"],"0.000000000")
        self.assertEqual(q["max_total_usd"],q["reserve_upper_bound_usd"])
        self.assertEqual(q["input_token_upper_bound_per_request"],1000000-4096-1024)
        self.assertEqual(q["total_input_token_upper_bound"],
                         (1000000-4096-1024)*3)
        self.assertEqual(q["total_output_token_upper_bound"],4096*3)
        self.assertEqual(q["requested_input_token_proxy"],50000)
        self.assertFalse(q["token_count_exact"])
        self.assertTrue(q["requires_exact_token_check_before_send"])
        self.assertTrue(q["requires_account_price_and_extra_fee_check"])
        self.assertTrue(q["requires_dedicated_sre_consent"])
        self.assertTrue(q["requires_atomic_budget_reservation"])
        self.assertFalse(q["dispatch_allowed"])
        self.assertEqual(q["paid_calls"],0)
        summed=Decimal(PRICE["prompt"])+sum(Decimal(PRICE[k]) for k in (
            "input_cache_read","input_cache_write","input_cache_write_1h"))
        total=(summed*Decimal(1000000-4096-1024)+
               Decimal(PRICE["completion"])*Decimal(4096))*3
        self.assertEqual(Decimal(q["max_total_usd"]),
                         total.quantize(USD_QUANT,rounding=ROUND_CEILING))
        self.assertEqual(len(q["per_request"]),3)
        self.assertEqual([x["request_sha256"] for x in q["per_request"]],
                         [x["request_sha256"] for x in self.m20["requests"]])
        self.assertTrue(all(not row["exact_input_token_count_known"]
                            and not row["actual_paid_dispatch_permitted"]
                            for row in q["per_request"]))
        self.assertEqual(q["quote_sha256"],fingerprint({
            k:v for k,v in q.items() if k!="quote_sha256",
        }))
        ok=self.verify(q)
        self.assertTrue(ok["quote_verified"])
        self.assertEqual(ok["quote_sha256"],q["quote_sha256"])
        self.assertFalse(ok["dispatch_allowed"])
        self.assertFalse(ok["consent_granted"])
        self.assertEqual(ok["paid_calls"],0)

    def test_min_max_exact_catalog_price_and_per_request_quote_pins(self):
        q=self.quote()
        self.assertEqual(q["expires_in_seconds_at_issue"],180)
        for changed in (
            lambda obj:obj["requests"][0].update(max_output_tokens=10),
            lambda obj:obj["requests"].reverse(),
            lambda obj:obj.update(content_sha256="a"*64),
            lambda obj:obj.update(batch_manifest_sha256="f"*64),
            lambda obj:obj.update(model_revision="anthropic/claude-opus-5.5-other"),
        ):
            with self.subTest(change=changed),self.assertRaises(SREContractError):
                other=deepcopy(self.m20)
                changed(other)
                self.verify(q,m20=other)
        drift=catalog_payload()
        drift["data"]["endpoints"][0]["pricing"]["completion"]="0.000030"
        newer=captured(drift)
        with self.assertRaises(SREContractError):
            self.verify(q,snapshot=newer,
                        operator_expected_sha256=newer["response_sha256"])
        other=deepcopy(q)
        other["reserve_upper_bound_usd"]="0.000000001"
        with self.assertRaises(SREContractError):
            self.verify(other)
        other=deepcopy(q)
        other["reserve_upper_bound_usd"]="0.000000001"
        other["quote_sha256"]=fingerprint({
            k:v for k,v in other.items() if k!="quote_sha256",
        })
        with self.assertRaises(SREContractError):
            self.verify(other)
        other=deepcopy(q)
        other["per_request"][0]["request_sha256"]="f"*64
        other["quote_sha256"]=fingerprint({
            k:v for k,v in other.items() if k!="quote_sha256",
        })
        with self.assertRaises(SREContractError):
            self.verify(other)

    def test_insufficient_usd_cap_refused_without_downsizing_or_fallback(self):
        for maximum in ("0.000000000","0.000000001","1.00","10.00"):
            with self.subTest(maximum=maximum),self.assertRaises(SREContractError) as e:
                self.quote(operator_maximum_usd=maximum)
            self.assertIn(e.exception.code,("quote_budget_exceeded","invalid_quote_amount"))
        with self.assertRaises(SREContractError) as e:
            self.quote(operator_maximum_usd="0.01",now=NOW)
        self.assertEqual(e.exception.code,"quote_budget_exceeded")
        for bad in (True,0,1.0,Decimal("100"),"NaN","-1","1e3",
                    "1.2345678901","999999999999999999",""):
            with self.subTest(bad=str(bad)),self.assertRaises(SREContractError):
                self.quote(operator_maximum_usd=bad)
        with self.assertRaises(SREContractError):
            self.quote(quote_ttl_seconds=301)
        with self.assertRaises(SREContractError):
            self.quote(quote_ttl_seconds=0)

    def test_expiry_floored_to_catalog_ttl_and_not_extended_by_replay(self):
        q=self.quote(quote_ttl_seconds=300)
        self.assertEqual(q["expires_in_seconds_at_issue"],300)
        self.assertEqual(q["expires_at"],
                         (NOW+timedelta(seconds=300)).isoformat(
                             timespec="microseconds").replace("+00:00","Z"))
        with self.assertRaises(SREContractError) as expired:
            self.verify(q,now=NOW+timedelta(seconds=300))
        self.assertEqual(expired.exception.code,"quote_expired")
        almost=NOW-timedelta(seconds=899,microseconds=200000)
        snap=captured(when=almost)
        # Time-to-live remaining less than one SECOND is fail closed.
        with self.assertRaises(SREContractError):
            self.quote(snapshot=snap,
                       operator_expected_sha256=snap["response_sha256"],
                       quote_ttl_seconds=180)

    def test_no_wrong_provider_or_evidence_of_real_token_usage(self):
        result=self.quote()
        for key in ("provider_receipts_authenticated","token_count_exact",
                    "dispatch_allowed"):
            self.assertFalse(result[key])
        self.assertGreater(Decimal(result["reserve_upper_bound_usd"]),Decimal("1"))
        self.assertEqual(result["token_bound_method"],
                         "full_pinned_endpoint_context_minus_output_and_headroom")
        self.assertTrue(result["requires_live_catalog_revalidation"])
        self.assertIn("1000000",str(result["input_token_upper_bound_per_request"]+5120))
        for extra in ("quote_ready","consent_granted","api_key","private_catalog"):
            self.assertNotIn(extra,result)

    def test_import_does_not_load_provider_client(self):
        code=(
            "import sys;import intelligence.relevance.opus_final_quote;"
            "assert 'openai' not in sys.modules;"
            "assert 'httpx' not in sys.modules;"
            "assert 'onnxruntime' not in sys.modules;"
        )
        p=subprocess.run([sys.executable,"-c",code],
                         capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stderr)


if __name__=="__main__":
    unittest.main()
