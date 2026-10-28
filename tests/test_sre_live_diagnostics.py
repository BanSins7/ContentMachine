"""M31 entirely offline: privacy-safe diagnostics and immutable checkpoint contracts.

These fake database rows are deterministic fixtures, NOT actual PG/paid
provider or pretrained ONNX acceptance.
"""
from __future__ import annotations

from contextlib import nullcontext
from decimal import Decimal
from datetime import datetime, timezone
import json
import unittest

from intelligence.relevance.live_diagnostics import (
    VERSION,_snapshot,_alert,read_sre_live_diagnostics,
    read_sre_live_pair_trace,record_sre_live_diagnostic_checkpoint,
    SQL_RUN,SQL_ITEMS,SQL_EXPECT,SQL_PAIRS,SQL_PROVEN,SQL_INTENTS,
    SQL_RECEIPTS,SQL_RESERVES,SQL_BUDGET_EVENTS,SQL_RECON_EVENTS,
    SQL_PROJECT_BUDGET,SQL_TRACE,SQL_INSERT,SQL_EXISTING,
    SQL_ATTEMPTS,SQL_MODELS,SQL_INTENT_TRACE,SQL_REVOKED_RUN,
)
from intelligence.relevance.v1_contracts import SREContractError

PID="a"*32
RUN="11111111-1111-4111-8111-111111111111"
ITEM=42
KEY="22222222-2222-4222-8222-222222222222"
SHA="b"*64
USD=lambda x: Decimal(str(x))
PERMISSIONS={
    "read_sre_live_diagnostics","trace_sre_live_pair",
    "record_sre_diagnostic_checkpoint",
}


def allowed(pid,actor,action):
    return pid==PID and actor=="owner:77" and action in PERMISSIONS


class Cursor:
    def __init__(self,row=None,rows=None):
        self.row=row
        self.rows=[] if rows is None else rows
    def fetchone(self):return self.row
    def fetchall(self):return self.rows


class FakeDB:
    autocommit=True
    class Info:
        class Status:
            name="IDLE"
        transaction_status=Status()
    info=Info()

    def __init__(self,mode="unknown"):
        self.mode=mode
        self.commands=[]
        self.saved={}
        self.last_checkpoint=0

    def transaction(self):return nullcontext()

    def execute(self,statement,params=()):
        self.commands.append(statement)
        if statement.startswith("SET TRANSACTION"):return Cursor()
        if "FROM sre_workspaces" in statement:return Cursor((1,))
        if statement==SQL_REVOKED_RUN:
            return Cursor((1 if getattr(self,"revoked",False) else 0,))
        if statement==SQL_RUN:
            return Cursor(("unknown" if self.mode=="unknown" else "running",
                           self.mode=="unknown",1,0,0,"live"))
        if statement==SQL_ITEMS:
            return Cursor(rows=[("unknown" if self.mode=="unknown" else "running",1)])
        if statement==SQL_EXPECT:return Cursor((1,3))
        if statement==SQL_PAIRS:
            if self.mode=="partial":
                return Cursor(rows=[("match",1),("not_evaluated",2)])
            return Cursor(rows=[("not_evaluated",3)])
        if statement==SQL_PROVEN:
            return Cursor((1,1) if self.mode=="partial" else (0,0))
        if statement==SQL_INTENTS:
            return Cursor((1,1 if self.mode=="unknown" else 0))
        if statement==SQL_RECEIPTS:
            if self.mode=="unknown":return Cursor((0,USD("0")))
            return Cursor((1,USD("0.250000000")))
        if statement==SQL_RESERVES:
            if self.mode=="unknown":
                return Cursor(rows=[("reserved",1,USD("1.000000000"))])
            return Cursor(rows=[("settled",1,USD("1.000000000"))])
        if statement==SQL_BUDGET_EVENTS:
            if self.mode=="unknown":
                return Cursor(rows=[("reserved",1)])
            return Cursor(rows=[("reserved",1),("settled",1)])
        if statement==SQL_RECON_EVENTS:
            if self.mode=="unknown":
                return Cursor(rows=[("unknown_no_generation_id",1)])
            return Cursor(rows=[("generation_accounted",1)])
        if statement==SQL_ATTEMPTS:
            return Cursor(rows=[("unknown",1)])
        if statement==SQL_MODELS:
            return Cursor(rows=[(
                "anthropic/claude-opus-5.5","m31-test-revision",
                SHA,SHA,0,
            )])
        if statement==SQL_INTENT_TRACE:
            if params[-1]!=KEY:return Cursor(None)
            if self.mode=="unknown":
                return Cursor((
                    "55555555-5555-4555-8555-555555555555",SHA,0,
                    "anthropic/claude-opus-5.5","m31-test-revision",SHA,
                    None,None,None,None,None,None,None,None,
                ))
            return Cursor((
                "55555555-5555-4555-8555-555555555555",SHA,0,
                "anthropic/claude-opus-5.5","m31-test-revision",SHA,
                None,"66666666-6666-4666-8666-666666666666",
                USD("0.250000000"),
                datetime(2026,10,28,9,30,tzinfo=timezone.utc),
                120,34,True,"source_unavailable",
            ))
        if statement==SQL_PROJECT_BUDGET:
            if self.mode=="unknown":
                return Cursor((USD("5.000000000"),USD("1.000000000"),
                               USD("0.000000000"),USD("0.000000000")))
            return Cursor((USD("5.000000000"),USD("0.000000000"),
                           USD("0.250000000"),USD("0.750000000")))
        if statement==SQL_TRACE:
            if params[0]!=KEY:return Cursor(None)
            if self.mode=="unknown":
                return Cursor(("unknown","not_evaluated",None,None,None,
                    None,None,None,None,None,None,None,None,None,None,None))
            return Cursor(("running","not_evaluated",None,None,None,
                None,None,None,None,None,None,None,None,None,None,None))
        if statement==SQL_INSERT:
            key=params[4]
            if key in self.saved:return Cursor(None)
            self.last_checkpoint+=1
            identity="33333333-3333-4333-8333-"+str(self.last_checkpoint).zfill(12)
            self.saved[key]=identity
            return Cursor((identity,))
        if statement==SQL_EXISTING:
            return Cursor((self.saved[params[2]],) if params[2] in self.saved else None)
        raise AssertionError("unexpected diagnostic SQL: "+statement[:110])


class M31OfflineDiagnostics(unittest.TestCase):
    def test_unknown_outcome_keeps_hold_alert_and_hides_content(self):
        db=FakeDB()
        report=read_sre_live_diagnostics(db,PID,RUN,actor_id="owner:77",
                                         actor_authorizer=allowed)
        self.assertEqual(report["schema_version"],VERSION)
        self.assertEqual(report["alert_code"],"unknown_paid_outcome")
        self.assertEqual(report["counts"]["expected_pairs"],3)
        self.assertEqual(report["counts"]["proven_pairs"],0)
        self.assertEqual(report["counts"]["unsettled_intents"],1)
        self.assertEqual(report["counts"]["technical_attempts"],1)
        self.assertEqual(report["pinned_model_requests"][0]["request_sha256"],SHA)
        self.assertFalse(report["d1_rights_rechecked_at_observation"])
        self.assertEqual(report["run_reserved_usd"],"1.000000000")
        self.assertEqual(report["run_confirmed_inference_spent_usd"],"0.000000000")
        self.assertEqual(report["project_budget_usd"]["cap"],"5.000000000")
        self.assertFalse(report["paid_post_allowed"])
        self.assertFalse(report["provider_fee_tax_proven"])
        self.assertFalse(report["semantic_ground_truth_proven"])
        blob=json.dumps(report)
        for forbidden in ("Authorization","Bearer ","api_key","prompt",
                          "reason","raw_json","transcript","D1 source words"):
            self.assertNotIn(forbidden,blob)
        self.assertTrue(any(s.startswith("SET TRANSACTION") for s in db.commands))
        self.assertTrue(all("INSERT" not in s and "UPDATE" not in s
                            for s in db.commands))

    def test_paid_without_valid_evidence_is_not_classified(self):
        db=FakeDB("paid")
        report=read_sre_live_diagnostics(db,PID,RUN,actor_id="owner:77",
                                         actor_authorizer=allowed)
        self.assertEqual(report["alert_code"],"cost_without_complete_evidence")
        self.assertEqual(report["counts"]["confirmed_receipts"],1)
        self.assertEqual(report["counts"]["proven_pairs"],0)
        self.assertEqual(report["run_reserved_usd"],"0.000000000")
        self.assertEqual(report["run_confirmed_inference_spent_usd"],"0.250000000")
        self.assertEqual(report["counts"]["unproven_pairs"],3)

    def test_partial_one_leaf_is_diagnostic_not_run_completion(self):
        report=read_sre_live_diagnostics(
            FakeDB("partial"),PID,RUN,
            actor_id="owner:77",actor_authorizer=allowed)
        self.assertEqual(report["alert_code"],"incomplete_classification")
        self.assertEqual(report["counts"]["proven_pairs"],1)
        self.assertEqual(report["counts"]["unproven_pairs"],2)
        self.assertEqual(report["pair_states"]["match"],1)
        self.assertEqual(report["run_state"],"running")

    def test_immutable_snapshot_idempotent_and_new_on_changed_source(self):
        db=FakeDB()
        first=record_sre_live_diagnostic_checkpoint(
            db,PID,RUN,actor_id="owner:77",actor_authorizer=allowed)
        second=record_sre_live_diagnostic_checkpoint(
            db,PID,RUN,actor_id="owner:77",actor_authorizer=allowed)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["checkpoint_id"],second["checkpoint_id"])
        self.assertEqual(first["snapshot_sha256"],second["snapshot_sha256"])
        db.mode="paid"
        third=record_sre_live_diagnostic_checkpoint(
            db,PID,RUN,actor_id="owner:77",actor_authorizer=allowed)
        self.assertTrue(third["created"])
        self.assertNotEqual(first["snapshot_sha256"],third["snapshot_sha256"])
        self.assertEqual(len(db.saved),2)
        self.assertEqual(third["alert_code"],"cost_without_complete_evidence")
        for v in (first,third):
            self.assertFalse(v["has_raw_source_or_secrets"])
            self.assertFalse(v["paid_post_allowed"])

    def test_foreign_actor_and_category_pair_denied(self):
        db=FakeDB()
        with self.assertRaises(SREContractError) as denied:
            read_sre_live_diagnostics(
                db,PID,RUN,actor_id="stranger",actor_authorizer=allowed)
        self.assertEqual(denied.exception.code,"actor_not_authorized")
        with self.assertRaises(SREContractError):
            record_sre_live_diagnostic_checkpoint(
                db,PID,RUN,actor_id="stranger",actor_authorizer=allowed)
        with self.assertRaises(SREContractError) as denied:
            read_sre_live_pair_trace(
                db,PID,RUN,ITEM,"44444444-4444-4444-8444-444444444444",
                actor_id="owner:77",actor_authorizer=allowed)
        self.assertEqual(denied.exception.code,"rights_denied")
        trace=read_sre_live_pair_trace(
            db,PID,RUN,ITEM,KEY,actor_id="owner:77",
            actor_authorizer=allowed)
        self.assertEqual(trace["pair_state"],"not_evaluated")
        self.assertIsNone(trace["provider_inference_usd"])
        self.assertFalse(trace["source_text_or_key_in_report"])
        self.assertEqual(trace["model_id"],"anthropic/claude-opus-5.5")
        paid=read_sre_live_pair_trace(
            FakeDB("paid"),PID,RUN,ITEM,KEY,actor_id="owner:77",
            actor_authorizer=allowed)
        self.assertEqual(paid["pair_state"],"not_evaluated")
        self.assertEqual(paid["provider_inference_usd"],"0.250000000")
        self.assertTrue(
            paid["provider_generation_accounted_but_classification_missing"])
        self.assertFalse(paid["verified_d1_evidence"])
        self.assertEqual(
            paid["provider_generation_settled_at"],
            "2026-10-28T09:30:00.000000Z")

    def test_mutated_or_float_money_and_forged_result_are_fail_closed(self):
        db=FakeDB("paid")
        original=db.execute
        def broken(sql,params=()):
            if sql==SQL_RECEIPTS:return Cursor((1,0.25))
            return original(sql,params)
        db.execute=broken
        with self.assertRaises(SREContractError):
            read_sre_live_diagnostics(
                db,PID,RUN,actor_id="owner:77",actor_authorizer=allowed)
        db=FakeDB("paid")
        original=db.execute
        def forged(sql,params=()):
            if sql==SQL_PROVEN:return Cursor((1,0))
            return original(sql,params)
        db.execute=forged
        with self.assertRaises(SREContractError):
            read_sre_live_diagnostics(
                db,PID,RUN,actor_id="owner:77",actor_authorizer=allowed)


if __name__=="__main__":
    unittest.main()
