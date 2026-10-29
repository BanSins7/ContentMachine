"""M25 zero-cost budget port failures; no provider or real spending."""
from __future__ import annotations
import subprocess
import sys
import unittest
from decimal import Decimal

from intelligence.relevance.opus_budget import (
    VERSION, _money, _balance,
    configure_live_sre_budget, reserve_live_sre_opus_budget,
    release_pre_dispatch_sre_budget,
)
from intelligence.relevance.v1_contracts import SREContractError
from test_sre_taxonomy_adapter import PID

SHA="a"*64
RUN="11111111-1111-4111-8111-111111111111"


class M25BudgetUnit(unittest.TestCase):
    def test_decimal_rejects_floats_exponents_negative_nan_bools_and_zero(self):
        self.assertEqual(_money("1.000000001","cap"),Decimal("1.000000001"))
        for x in (None,False,True,1,1.0,Decimal("1"),"NaN","Infinity",
                  "-0.01","1e2","1E+2","0","0.000000000",
                  "1.0000000001","","-1","0001"):
            with self.subTest(x=x),self.assertRaises(SREContractError):
                _money(x,"cap")
        self.assertEqual(VERSION,"sre-live-budget-ledger/v1")

    def test_balance_uses_exact_decimal_not_float(self):
        data=_balance(PID,(Decimal("5.000000001"),Decimal("1.500000000"),
                           Decimal("0"),Decimal("0.200000000"),"owner:7"))
        self.assertEqual(data["available_usd"],"3.500000001")
        self.assertEqual(data["spent_usd"],"0.000000000")
        self.assertFalse(data["dispatch_allowed"])
        self.assertEqual(data["paid_calls"],0)

    def test_missing_authenticated_host_denied_before_db(self):
        class DB:
            def transaction(self):
                raise AssertionError("untrusted request entered database")
            def execute(self,*args):
                raise AssertionError("untrusted request entered database")
        commands=(
            lambda:configure_live_sre_budget(
                DB(),PID,actor_id="outsider",actor_authorizer=lambda *a:False,
                project_cap_usd="5"),
            lambda:reserve_live_sre_opus_budget(
                DB(),PID,RUN,1,SHA,{},
                actor_id="outsider",actor_authorizer=lambda *a:False,
                operator_expected_sha256=SHA,operator_endpoint_tag="test",
                operator_model_revision="model",operator_maximum_usd="5"),
            lambda:release_pre_dispatch_sre_budget(
                DB(),PID,RUN,actor_id="outsider",
                actor_authorizer=lambda *a:False),
        )
        for f in commands:
            with self.assertRaises(SREContractError) as e:
                f()
            self.assertEqual(e.exception.code,"actor_not_authorized")

    def test_release_rejects_bad_reason_before_sql(self):
        class DB:
            def transaction(self):
                raise AssertionError("invalid reason entered database")
        with self.assertRaises(SREContractError) as e:
            release_pre_dispatch_sre_budget(
                DB(),PID,RUN,actor_id="owner",
                actor_authorizer=lambda *a:True,reason="invoice_paid")
        self.assertEqual(e.exception.code,"invalid_budget_release")

    def test_import_is_local_and_never_opens_transport(self):
        p=subprocess.run([sys.executable,"-c",
            "import sys;import intelligence.relevance.opus_budget;"
            "assert 'httpx' not in sys.modules;"
            "assert 'openai' not in sys.modules;"
            "assert 'onnxruntime' not in sys.modules;"],
            capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stderr)


if __name__=="__main__":
    unittest.main()
