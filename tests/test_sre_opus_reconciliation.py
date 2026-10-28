"""M29 offline UNKNOWN accounting with no model POST or fabricated refunds."""
from __future__ import annotations

from contextlib import nullcontext
from decimal import Decimal
from unittest.mock import patch
import subprocess
import sys
import unittest

from intelligence.relevance.opus_reconciliation import (
    VERSION, SQL_AUDIT_INSERT, SQL_AUDIT_FIND, SQL_PROGRESS, SQL_RUN,
    SQL_SOURCE, reconcile_live_sre_opus_outcome,
    inspect_live_sre_opus_reconciliation,
)
from intelligence.relevance.v1_contracts import SREContractError, fingerprint

PID="a"*32
RID="11111111-1111-4111-8111-111111111111"
INTENT="22222222-2222-4222-8222-222222222222"
RES="33333333-3333-4333-8333-333333333333"
ATTEMPT="44444444-4444-4444-8444-444444444444"
REQUEST="c"*64
BATCH="d"*64
CATALOG="e"*64
QUOTE_BASE={
    "project_id":PID,"batch_manifest_sha256":BATCH,
    "catalog_sha256":CATALOG,"request_count":3,
    "per_request":[
        {"request_sha256":REQUEST},
        {"request_sha256":"f"*64},
        {"request_sha256":"9"*64},
    ],
}
QUOTE=fingerprint(QUOTE_BASE)
GID="gen-known-m29-test"


class Result:
    def __init__(self,row=None,rows=None):
        self.row=row
        self.rows=rows if rows is not None else []
    def fetchone(self):return self.row
    def fetchall(self):return self.rows


class FakeDB:
    autocommit=True
    class Info:
        class Status:
            name="IDLE"
        transaction_status=Status()
    info=Info()

    def __init__(self):
        self.calls=[]
        self.events={}
        self.settled=False

    def transaction(self):return nullcontext()

    def execute(self,query,params=()):
        self.calls.append(query)
        if "FROM sre_workspaces" in query:
            return Result((1,))
        if query==SQL_SOURCE:
            q={**QUOTE_BASE,"quote_sha256":QUOTE}
            return Result((
                INTENT,ATTEMPT,REQUEST,0,BATCH,RES,QUOTE,CATALOG,
                Decimal("1.000000000"),
                "settled" if self.settled else "reserved",
                Decimal("1.000000000"),
                "55555555-5555-4555-8555-555555555555" if self.settled else None,
                GID if self.settled else None,
                Decimal("0.100000000") if self.settled else None,
                q,
            ))
        if query==SQL_AUDIT_INSERT:
            _,_,_,_,kind,gid,_,_,_=params
            key=(kind,gid)
            if key not in self.events:self.events[key]=len(self.events)+1
            return Result((self.events[key],))
        if query==SQL_AUDIT_FIND:
            _,_,kind,gid=params
            return Result((self.events[(kind,gid)],"2026-10-28"))
        if query==SQL_RUN:
            return Result(("unknown",True,"live"))
        if query==SQL_PROGRESS:
            q={**QUOTE_BASE,"quote_sha256":QUOTE}
            return Result(rows=[(
                22,INTENT,0,REQUEST,
                "settled" if self.settled else "reserved",
                "55555555-5555-4555-8555-555555555555" if self.settled else None,
                GID if self.settled else None,
                Decimal("0.100000000") if self.settled else None,q,
            )])
        raise AssertionError("unexpected SQL "+query[:150])


def authorizer(pid,actor,action):
    return pid==PID and actor=="owner:77" and action in (
        "reconcile_sre_opus_outcome","read_sre_opus_reconciliation",
        "settle_sre_opus_generation",
    )


def call(conn,**kwargs):
    return reconcile_live_sre_opus_outcome(
        conn,PID,RID,22,INTENT,actor_id="owner:77",
        actor_authorizer=authorizer,**kwargs)


class M29UnknownReconciliation(unittest.TestCase):
    def test_missing_id_is_idempotent_unknown_and_never_network(self):
        db=FakeDB()
        with patch("intelligence.relevance.opus_reconciliation.settle_live_sre_opus_generation",
                   side_effect=AssertionError("paid/GET called")):
            first=call(db)
            twice=call(db)
        self.assertEqual(first,twice)
        self.assertEqual(first["reason_code"],"unknown_no_generation_id")
        self.assertEqual(first["status"],"unknown_reserved_no_repost")
        self.assertEqual(first["original_reservation_state"],"reserved")
        self.assertIsNone(first["receipt_id"])
        self.assertFalse(first["automatic_paid_retry_allowed"])
        self.assertFalse(first["next_paid_request_allowed"])
        self.assertEqual(first["paid_post_calls_issued_here"],0)

    def test_known_id_settles_only_via_independent_m28_service(self):
        db=FakeDB()
        def verified(*args,**kw):
            self.assertIsNone(args[5])
            self.assertEqual(args[6],GID)
            db.settled=True
            return {"status":"provider_generation_accounted"}
        with patch("intelligence.relevance.opus_reconciliation.settle_live_sre_opus_generation",
                   side_effect=verified) as service:
            done=call(db,generation_id=GID,api_key="mock-only-key-12345678")
        self.assertEqual(service.call_count,1)
        self.assertEqual(done["status"],"generation_accounted_still_no_repost")
        self.assertEqual(done["reason_code"],"generation_accounted")
        self.assertEqual(done["actual_provider_cost_usd"],"0.100000000")
        self.assertFalse(done["next_paid_request_allowed"])
        with patch("intelligence.relevance.opus_reconciliation.settle_live_sre_opus_generation",
                   side_effect=AssertionError("second GET")):
            replay=call(db,generation_id=GID)
        self.assertEqual(replay,done)

    def test_404_timeout_and_untrusted_cost_never_free_hold(self):
        for code in ("generation_unverified","provider_outcome_unknown",
                     "provider_cost_exceeds_reserve","account_fee_unverified"):
            db=FakeDB()
            with self.subTest(code=code),patch(
                "intelligence.relevance.opus_reconciliation.settle_live_sre_opus_generation",
                side_effect=SREContractError(code,"generation","not proven"),
            ):
                pending=call(db,generation_id=GID,api_key="mock-only-key-12345678")
                self.assertEqual(pending["status"],"unknown_reserved_no_repost")
                self.assertEqual(pending["reason_code"],
                                 "generation_lookup_unverified")
                self.assertIsNone(pending["actual_provider_cost_usd"])
                self.assertEqual(pending["original_reservation_state"],"reserved")

    def test_progress_reports_all_missing_ordinals_but_never_resumes(self):
        db=FakeDB()
        result=inspect_live_sre_opus_reconciliation(
            db,PID,RID,actor_id="owner:77",actor_authorizer=authorizer)
        self.assertEqual(result["known_request_count"],3)
        self.assertEqual(result["remaining_request_ordinals"],[0,1,2])
        self.assertEqual(result["outstanding_unknown_intents"],1)
        self.assertFalse(result["next_paid_request_allowed"])
        db.settled=True
        result=inspect_live_sre_opus_reconciliation(
            db,PID,RID,actor_id="owner:77",actor_authorizer=authorizer)
        self.assertEqual(result["accounted_request_ordinals"],[0])
        self.assertEqual(result["remaining_request_ordinals"],[1,2])
        self.assertFalse(result["all_hypotheses_complete"])
        self.assertFalse(result["next_paid_request_allowed"])

    def test_wrong_actor_missing_key_and_foreign_generation_fail_closed(self):
        db=FakeDB()
        with self.assertRaises(SREContractError) as e:
            reconcile_live_sre_opus_outcome(
                db,PID,RID,22,INTENT,actor_id="stranger",
                actor_authorizer=authorizer)
        self.assertEqual(e.exception.code,"actor_not_authorized")
        with self.assertRaises(SREContractError) as e:
            call(db,generation_id=GID)
        self.assertEqual(e.exception.code,"credentials_missing")
        with self.assertRaises(SREContractError) as e:
            call(db,provider_response={"id":GID})
        self.assertEqual(e.exception.code,"generation_unverified")
        db.settled=True
        with self.assertRaises(SREContractError) as e:
            call(db,generation_id="gen-other")
        self.assertEqual(e.exception.code,"receipt_already_settled")

    def test_module_import_has_no_provider_or_paid_client(self):
        p=subprocess.run([sys.executable,"-c",
            "import sys;import intelligence.relevance.opus_reconciliation;"
            "assert 'httpx' not in sys.modules;"
            "assert 'requests' not in sys.modules;"
            "assert 'openai' not in sys.modules;"],
            capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(VERSION,"sre-opus-unknown-reconciliation/v1")


if __name__=="__main__":
    unittest.main()
