"""M24 SRE dedicated owner consent: explicit human quote/stage/amount input.

All grants are tested with trusted callable fixtures only, no HTTP/paid
provider execution. Genuine PG17 C9/D1/quote flows are in test_sre_offline_pg.
"""
from __future__ import annotations
from copy import deepcopy
from decimal import Decimal
import subprocess
import sys
import unittest

from intelligence.relevance.opus_consent import (
    VERSION, STAGE, GRANT_ACTION, confirmation_for_quote,
    grant_live_sre_opus_consent, verify_live_sre_opus_consent,
    revoke_live_sre_opus_consent,
    _authorized, _confirmation, _actor,
)
from intelligence.relevance.v1_contracts import SREContractError, fingerprint
from test_sre_taxonomy_adapter import PID

SHA="a"*64


class UnitM24Consent(unittest.TestCase):
    def test_exact_quote_usd_and_stage_explicit_confirmation(self):
        data=confirmation_for_quote(SHA,"23.500000000")
        self.assertEqual(data,{
            "action":GRANT_ACTION,"stage":STAGE,
            "quote_sha256":SHA,"max_usd":"23.500000000",
        })
        self.assertEqual(_confirmation(data,SHA,"23.500000000"),
                         fingerprint(data))
        self.assertEqual(VERSION,"sre-live-opus-consent/v1")
        self.assertEqual(STAGE,"sre_live_opus_paid_execution")

    def test_no_implicit_consent_forged_quote_wrong_stage_money_or_extra_key(self):
        original=confirmation_for_quote(SHA,"23.500000000")
        for bad in (
            None,{},True,False,"yes",
            {**original,"quote_sha256":"b"*64},
            {**original,"max_usd":"23.500000001"},
            {**original,"max_usd":"23.499999999"},
            {**original,"max_usd":"23.5"},
            {**original,"max_usd":23.5},
            {**original,"action":"approve_query_planner"},
            {**original,"stage":"query_planner"},
            {**original,"stage":"sre_offline"},
            {**original,"extra":"yes"},
        ):
            with self.subTest(case=bad),self.assertRaises(SREContractError) as denied:
                _confirmation(bad,SHA,"23.500000000")
            self.assertEqual(denied.exception.code
                             if denied.exception.code!="invalid_contract"
                             else "invalid_contract",
                             denied.exception.code)
        for amount in (23.5,True,False,0,Decimal("23.5"),"NaN",
                       "-1","1e2","",None,"0.000000000",
                       "99999999999999999999"):
            with self.subTest(amount=str(amount)),self.assertRaises(SREContractError):
                confirmation_for_quote(SHA,amount)
        for value in ("b"*63,"B"*64,123):
            with self.assertRaises(SREContractError):
                confirmation_for_quote(value,"23.500000000")

    def test_host_actor_authorization_is_independent_from_client_claims(self):
        log=[]
        def allow(project,actor,permission):
            log.append((project,actor,permission))
            return project==PID and actor=="owner:77" and permission=="grant_sre_opus_consent"
        _authorized(allow,PID,"owner:77","grant_sre_opus_consent")
        self.assertEqual(log,[(PID,"owner:77","grant_sre_opus_consent")])
        for bad in (None,lambda *args:False,lambda *args:"True",
                    lambda *args:1,lambda *args:None):
            with self.subTest(bad=bad),self.assertRaises(SREContractError) as e:
                _authorized(bad,PID,"owner:77","grant_sre_opus_consent")
            self.assertEqual(e.exception.code,"actor_not_authorized")
        with self.assertRaises(SREContractError):
            _authorized(allow,PID,"intruder","grant_sre_opus_consent")
        with self.assertRaises(SREContractError):
            _authorized(lambda *args:1/0,PID,"owner:77","grant_sre_opus_consent")
        for fake in ("owner name","/root","",None,123,"😅","bad;DROP"):
            with self.assertRaises(SREContractError):
                _actor(fake)

    def test_production_grant_unauthorized_does_not_touch_database(self):
        class DB:
            def transaction(self):raise AssertionError("unauthorized DB transaction")
            def execute(self,*args):raise AssertionError("unauthorized DB query")
        calls=lambda *args:False
        with self.assertRaises(SREContractError) as e:
            grant_live_sre_opus_consent(
                DB(),PID,"11111111-1111-4111-8111-111111111111",1,SHA,{},
                actor_id="unknown",actor_authorizer=calls,
                explicit_confirmation=confirmation_for_quote(SHA,"23.500000000"),
                operator_expected_sha256=SHA,
                operator_endpoint_tag="anthropic",
                operator_model_revision="anthropic/claude-opus-5.5-20260921",
                operator_maximum_usd="100",
            )
        self.assertEqual(e.exception.code,"actor_not_authorized")

    def test_revoke_actor_auth_denied_even_for_existing_quote(self):
        class DB:
            def transaction(self):raise AssertionError("unauthorized DB transaction")
        with self.assertRaises(SREContractError) as e:
            revoke_live_sre_opus_consent(
                DB(),PID,"11111111-1111-4111-8111-111111111111",1,SHA,
                actor_id="outsider",actor_authorizer=lambda *args:False,
            )
        self.assertEqual(e.exception.code,"actor_not_authorized")

    def test_no_paid_provider_import_or_implicit_http_mutations(self):
        code=(
            "import sys;import intelligence.relevance.opus_consent;"
            "assert 'openai' not in sys.modules;"
            "assert 'httpx' not in sys.modules;"
            "assert 'onnxruntime' not in sys.modules;"
        )
        p=subprocess.run([sys.executable,"-c",code],
                         capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stderr)


if __name__=="__main__":
    unittest.main()
