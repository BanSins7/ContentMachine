"""M26 zero-network committed intent unit gates; real PG17 suite elsewhere."""
from __future__ import annotations

import subprocess
import sys
import unittest
from unittest.mock import patch

from intelligence.relevance.opus_dispatch_intent import (
    VERSION, _committable, _lease,
    commit_live_sre_opus_dispatch_intent,
    read_committed_live_sre_opus_intent,
)
from intelligence.relevance.v1_contracts import SREContractError

PID="a"*32
ID="11111111-1111-4111-8111-111111111111"
SHA="f"*64


class FakeInfo:
    class Status:
        name="IDLE"
    transaction_status=Status()

class FakeConnection:
    autocommit=False
    info=FakeInfo()
    def transaction(self):
        raise AssertionError("unsafe connection entered SQL transaction")
    def execute(self,*args):
        raise AssertionError("unsafe connection entered SQL transaction")


class M26OfflineIntentUnit(unittest.TestCase):
    def test_no_nested_or_uncommitted_autocommit_false_connection(self):
        for val,name in ((False,"IDLE"),(True,"INTRANS"),(True,"INERROR")):
            c=FakeConnection()
            c.autocommit=val
            c.info.transaction_status.name=name
            with self.subTest(val=val,status=name):
                with self.assertRaises(SREContractError) as e:
                    _committable(c)
                self.assertEqual(e.exception.code,"dispatch_commit_not_guaranteed")
        good=FakeConnection()
        good.autocommit=True
        good.info.transaction_status.name="IDLE"
        _committable(good)

    def test_bad_worker_identity_or_run_cancel_denied(self):
        worker=ID
        live=("live","running",False,"running",worker,4,None,2,3,SHA)
        self.assertEqual(_lease(live,worker,4)[:3],(2,3,SHA))
        cases=(
            ("offline_fixture","running",False,"running",worker,4),
            ("live","pending_consent",False,"running",worker,4),
            ("live","running",True,"running",worker,4),
            ("live","running",False,"queued",worker,4),
            ("live","running",False,"running",ID.replace("1","2"),4),
            ("live","running",False,"running",worker,5),
        )
        for values in cases:
            with self.subTest(values=values),self.assertRaises(SREContractError):
                _lease((*values,None,2,3,SHA),worker,4)

    def test_missing_operator_permission_denied_before_any_database_write(self):
        db=FakeConnection()
        with self.assertRaises(SREContractError) as denied:
            commit_live_sre_opus_dispatch_intent(
                db,PID,ID,1,ID,ID,SHA,{},
                request_ordinal=0,worker_token=ID,lease_generation=1,
                actor_id="outsider",actor_authorizer=lambda *a:False,
                operator_expected_sha256=SHA,
                operator_endpoint_tag="one",operator_model_revision="model-123",
                operator_maximum_usd="20.000000000")
        self.assertEqual(denied.exception.code,"actor_not_authorized")

    def test_no_implicit_ordinal_or_reused_transaction(self):
        db=FakeConnection()
        kwargs=dict(request_ordinal=True,worker_token=ID,lease_generation=1,
                    actor_id="owner",actor_authorizer=lambda *a:True,
                    operator_expected_sha256=SHA,
                    operator_endpoint_tag="one",operator_model_revision="model-123",
                    operator_maximum_usd="20.000000000")
        with self.assertRaises(SREContractError):
            commit_live_sre_opus_dispatch_intent(
                db,PID,ID,1,ID,ID,SHA,{},**kwargs)
        kwargs["request_ordinal"]=0
        with self.assertRaises(SREContractError) as x:
            commit_live_sre_opus_dispatch_intent(
                db,PID,ID,1,ID,ID,SHA,{},**kwargs)
        self.assertEqual(x.exception.code,"dispatch_commit_not_guaranteed")
        with self.assertRaises(SREContractError):
            read_committed_live_sre_opus_intent(db,PID,ID,1,ID)

    def test_no_transport_or_paid_dependencies_on_import(self):
        p=subprocess.run([sys.executable,"-c",
            "import sys;import intelligence.relevance.opus_dispatch_intent;"
            "assert 'httpx' not in sys.modules;"
            "assert 'requests' not in sys.modules;"
            "assert 'openai' not in sys.modules;"
            "assert 'onnxruntime' not in sys.modules;"],
            capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(VERSION,"sre-live-opus-dispatch-intent/v1")


if __name__=="__main__":
    unittest.main()
