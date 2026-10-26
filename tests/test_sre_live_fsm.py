"""M10: LIVE-only strict DTOs, state transitions and unknown-charge fencing.

Pure tests, no DB, network, paid model, HTTP or filesystem side effects.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import math
import subprocess
import sys
import unittest
from uuid import uuid4

from intelligence.relevance.live_fsm import (
    VERSION, MODE, EDGES, RUN_STATES, ITEM_STATES, BATCH_STATES,
    ATTEMPT_STATES, OUTCOME_CODES, OUTCOME_CATEGORIES,
    validate_live_run, validate_live_item, validate_live_batch,
    validate_live_attempt, transition_live, classify_technical_outcome,
)
from intelligence.relevance.v1_contracts import SREContractError

PID = "a"*32
RID = "11111111-1111-4111-8111-111111111111"
PUB = "22222222-2222-4222-8222-222222222222"
BID = "33333333-3333-4333-8333-333333333333"
AID = "44444444-4444-4444-8444-444444444444"
H1 = "55555555-5555-4555-8555-555555555555"
H2 = "66666666-6666-4666-8666-666666666666"


def run(state="pending_consent"):
    return {
        "schema_version":VERSION,"execution_mode":MODE,"project_id":PID,
        "run_id":RID,"publication_id":PUB,"taxonomy_sha256":"a"*64,
        "pipeline_sha256":"b"*64,"request_sha256":"c"*64,
        "state":state,"expected_items":2,"finished_items":0,"blocked_items":0,
    }


def outcomes(*,complete=False,partial=False):
    return {
        "match":[H1] if (complete or partial) else [],
        "no_match":[H2] if complete else [],
        "uncertain":[],
        "not_evaluated":[] if complete else ([H2] if partial else [H1,H2]),
    }


def item(state="queued"):
    return {
        "schema_version":VERSION,"execution_mode":MODE,"project_id":PID,
        "run_id":RID,"video_id":103,"content_version_id":201,
        "content_sha256":"d"*64,"expected_hypothesis_keys":[H1,H2],
        "outcomes":outcomes(),"state":state,"lease_generation":0,
    }


def batch(state="queued"):
    return {
        "schema_version":VERSION,"execution_mode":MODE,"project_id":PID,
        "run_id":RID,"batch_id":BID,"item_ids":[1,2,3],
        "input_sha256":"e"*64,"state":state,"lease_generation":0,
    }


def attempt(state="reserved"):
    return {
        "schema_version":VERSION,"execution_mode":MODE,"project_id":PID,
        "run_id":RID,"batch_id":BID,"attempt_id":AID,
        "request_sha256":"f"*64,"state":state,"lease_generation":0,
        "dispatch_intent":state not in ("reserved","abandoned_before_dispatch"),
        "provider_request_id":None,
        "outcome_code":{
            "unknown":"deadline_exceeded","completed":"success",
            "failed_known":"known_provider_rejection",
        }.get(state),
    }


def rejected(test, function, data, code=None):
    with test.assertRaises(SREContractError) as ctx:
        function(data)
    if code:
        test.assertEqual(ctx.exception.code, code)


class M10TypedLiveFSMUnit(unittest.TestCase):
    def test_all_four_live_dtos_are_frozen_and_typed(self):
        a=validate_live_run(run())
        b=validate_live_item(item())
        c=validate_live_batch(batch())
        d=validate_live_attempt(attempt())
        self.assertEqual((a.state,b.state,c.state,d.state),
                         ("pending_consent","queued","queued","reserved"))
        self.assertEqual(a.expected_items,2)
        self.assertEqual(b.expected_hypothesis_keys,(H1,H2))
        with self.assertRaises(TypeError):
            b.outcomes["match"] = (H2,)
        self.assertEqual(b.outcomes["match"],())
        self.assertEqual(c.item_ids,(1,2,3))
        self.assertEqual(d.provider_request_id,None)
        for dto in (a,b,c,d):
            self.assertEqual(dto.execution_mode,"live")
            self.assertEqual(dto.schema_version,VERSION)
            with self.assertRaises(FrozenInstanceError):
                dto.state = "completed"

    def test_live_only_never_accepts_offline_fixture_or_self_signed_fake(self):
        for factory,validator in (
            (run,validate_live_run),(item,validate_live_item),
            (batch,validate_live_batch),(attempt,validate_live_attempt),
        ):
            for mode in ("offline_fixture","offline_fixture_s4","legacy",
                         "LIVE",None,0,True,{},float("nan")):
                with self.subTest(name=factory.__name__,mode=str(mode)):
                    p=factory(); p["execution_mode"]=mode
                    rejected(self,validator,p,"synthetic_result_denied")

    def test_exact_schema_no_extra_or_missing_fields(self):
        for factory,validator in (
            (run,validate_live_run),(item,validate_live_item),
            (batch,validate_live_batch),(attempt,validate_live_attempt),
        ):
            p=factory();p["admin_override"]=True
            rejected(self,validator,p)
            p=factory();p.pop("project_id")
            rejected(self,validator,p)
            p=factory();p["schema_version"]="sre-live-fsm/v2"
            rejected(self,validator,p,"unsupported_schema_version")

    def test_uuid_boolean_int_nan_inf_and_sha_negative_matrix(self):
        for factory,validator in (
            (run,validate_live_run),(item,validate_live_item),
            (batch,validate_live_batch),(attempt,validate_live_attempt),
        ):
            for name,value in (
                ("project_id","A"*32),("project_id",True),
                ("run_id","not-uuid"),("run_id",str(uuid4()).upper()),
            ):
                with self.subTest(dto=factory.__name__,field=name,value=str(value)):
                    p=factory();p[name]=value
                    rejected(self,validator,p)
        for field,broken in (
            ("expected_items",True),("expected_items",-1),
            ("expected_items",0),("finished_items",float("nan")),
            ("blocked_items",float("inf")),("finished_items","1"),
            ("taxonomy_sha256","A"*64),
        ):
            p=run();p[field]=broken
            rejected(self,validate_live_run,p)
        for field,broken in (
            ("video_id",True),("video_id",0),
            ("video_id",2**63),("content_version_id",float("nan")),
            ("lease_generation",True),("lease_generation",-1),
            ("content_sha256","Z"*64),
        ):
            p=item();p[field]=broken
            rejected(self,validate_live_item,p)
        for itemids in ([True],[1,1],[3,1],[],"1,2",[2**63]):
            p=batch();p["item_ids"]=itemids
            rejected(self,validate_live_batch,p)
        for value in (True,"true",0,None,math.nan):
            p=attempt("dispatch_intent");p["dispatch_intent"]=value
            rejected(self,validate_live_attempt,p)

    def test_run_coverage_never_false_completed_or_partial(self):
        p=run("completed");p["finished_items"]=2
        self.assertEqual(validate_live_run(p).state,"completed")
        for done,blocked in ((1,0),(0,2),(2,1)):
            x=run("completed");x["finished_items"]=done;x["blocked_items"]=blocked
            rejected(self,validate_live_run,x)
        p=run("partial");p["finished_items"]=1;p["blocked_items"]=1
        self.assertEqual(validate_live_run(p).state,"partial")
        for done,blocked in ((0,1),(2,0),(0,0),(2,1)):
            x=run("partial");x["finished_items"]=done;x["blocked_items"]=blocked
            rejected(self,validate_live_run,x)
        p=run("planned");p["finished_items"]=1
        rejected(self,validate_live_run,p)

    def test_item_exact_all_leaf_coverage_and_partial_not_evaluated(self):
        full=item("completed");full["outcomes"]=outcomes(complete=True)
        self.assertEqual(validate_live_item(full).outcomes["no_match"],(H2,))
        partial=item("partial");partial["outcomes"]=outcomes(partial=True)
        self.assertEqual(validate_live_item(partial).outcomes["not_evaluated"],(H2,))
        for field,value in (
            ("expected_hypothesis_keys",[H2,H1]),
            ("expected_hypothesis_keys",[H1,H1]),
            ("expected_hypothesis_keys",[True]),
            ("expected_hypothesis_keys",[]),
        ):
            p=item();p[field]=value
            rejected(self,validate_live_item,p)
        for change in (
            lambda x:x["outcomes"]["not_evaluated"].pop(),
            lambda x:x["outcomes"]["not_evaluated"].append(H1),
            lambda x:x["outcomes"].update(no_match=[H1]),
            lambda x:x["outcomes"].update(no_match=["bad"]),
            lambda x:x["outcomes"].update(fake=[]),
        ):
            p=item();change(p)
            rejected(self,validate_live_item,p)
        full["outcomes"]=outcomes(partial=True)
        rejected(self,validate_live_item,full,"incomplete_coverage")
        partial["outcomes"]=outcomes(complete=True)
        rejected(self,validate_live_item,partial,"incomplete_coverage")
        queued=item("queued");queued["outcomes"]=outcomes(complete=True)
        rejected(self,validate_live_item,queued)

    def test_attempt_reserved_dispatched_unknown_and_terminal_guards(self):
        for state in ATTEMPT_STATES:
            p=attempt(state)
            self.assertEqual(validate_live_attempt(p).state,state)
        for state in ("dispatch_intent","unknown","completed","failed_known"):
            p=attempt(state);p["dispatch_intent"]=False
            rejected(self,validate_live_attempt,p)
        p=attempt("unknown");p["outcome_code"]="known_provider_rejection"
        rejected(self,validate_live_attempt,p)
        p=attempt("failed_known");p["outcome_code"]="deadline_exceeded"
        rejected(self,validate_live_attempt,p)
        p=attempt("completed");p["outcome_code"]="unknown_charge"
        rejected(self,validate_live_attempt,p)
        p=attempt("reserved");p["outcome_code"]="success"
        rejected(self,validate_live_attempt,p)
        p=attempt("unknown");p["provider_request_id"]=42
        rejected(self,validate_live_attempt,p)
        for state in ("reserved","abandoned_before_dispatch"):
            p=attempt(state);p["provider_request_id"]="provider_123"
            rejected(self,validate_live_attempt,p)
        p=attempt("completed");p["provider_request_id"]="provider_123"
        self.assertEqual(validate_live_attempt(p).provider_request_id,"provider_123")

    def test_fsm_all_domains_every_edge_and_illegal_transitions(self):
        for name,edges in EDGES.items():
            states=set(edges)
            self.assertTrue(states)
            for source,targets in edges.items():
                for target in targets:
                    with self.subTest(domain=name,source=source,target=target):
                        kw={"reconciled":True} if source=="unknown" else {}
                        if name!="attempt" and target in ("completed","partial"):
                            kw["coverage"]=(
                                {"expected":2,"evaluated":2,"blocked":0}
                                if target=="completed" else
                                {"expected":2,"evaluated":1,"blocked":1}
                            )
                        self.assertEqual(
                            transition_live(name,source,target,**kw),target
                        )
                for target in states-set(targets):
                    with self.subTest(forbidden=(name,source,target)):
                        with self.assertRaises(SREContractError) as err:
                            transition_live(name,source,target)
                        self.assertEqual(err.exception.code,"invalid_transition")

    def test_ambiguous_charge_requires_reconciliation_and_never_auto_retry(self):
        with self.assertRaises(SREContractError) as err:
            transition_live("attempt","unknown","completed")
        self.assertEqual(err.exception.code,"ambiguous_charge")
        self.assertEqual(transition_live("attempt","unknown","completed",
                                         reconciled=True),"completed")
        for parent in ("run","item","batch"):
            for target in ("running","completed","partial","cancelled"):
                if target not in EDGES[parent]["unknown"]:
                    continue
                kw={}
                if target in ("completed","partial"):
                    kw["coverage"]=(
                        {"expected":2,"evaluated":2,"blocked":0}
                        if target=="completed" else
                        {"expected":2,"evaluated":1,"blocked":1}
                    )
                with self.subTest(parent=parent,target=target):
                    with self.assertRaises(SREContractError) as denied:
                        transition_live(parent,"unknown",target,**kw)
                    self.assertEqual(denied.exception.code,"ambiguous_charge")
                    self.assertEqual(
                        transition_live(parent,"unknown",target,reconciled=True,**kw),
                        target,
                    )
        for to in ("reserved","dispatch_intent","unknown"):
            with self.assertRaises(SREContractError):
                transition_live("attempt","unknown",to,reconciled=True)
        with self.assertRaises(SREContractError):
            transition_live("attempt","completed","reserved")
        with self.assertRaises(SREContractError):
            transition_live("run","partial","queued")
        with self.assertRaises(SREContractError):
            transition_live("run","completed","running")
        with self.assertRaises(SREContractError):
            transition_live("none","queued","running")

    def test_coverage_transition_requires_exact_all(self):
        for kind,previous in (
            ("run","running"),("item","running"),("batch","running"),
        ):
            with self.subTest(kind=kind):
                for payload in (
                    None,{},{ "expected":3,"evaluated":2,"blocked":0},
                    { "expected":2,"evaluated":2,"blocked":1},
                    { "expected":2,"evaluated":True,"blocked":0},
                    { "expected":float("nan"),"evaluated":1,"blocked":0},
                ):
                    with self.assertRaises(SREContractError):
                        transition_live(kind,previous,"completed",coverage=payload)
                self.assertEqual(
                    transition_live(kind,previous,"completed",coverage={
                        "expected":2,"evaluated":2,"blocked":0,
                    }),"completed"
                )
                with self.assertRaises(SREContractError):
                    transition_live(kind,previous,"partial",coverage={
                        "expected":2,"evaluated":0,"blocked":2,
                    })

    def test_outcome_taxonomy_no_silent_paid_retry_after_possible_send(self):
        for code in OUTCOME_CODES:
            if code=="success":
                result=classify_technical_outcome(code,dispatch_intent=True)
                self.assertEqual((result.category,result.retry_policy),("success","never"))
            else:
                result=classify_technical_outcome(code,dispatch_intent=True)
                self.assertIn(result.category,OUTCOME_CATEGORIES)
                if code in ("deadline_exceeded","rate_limited","transport_error","unknown_charge"):
                    self.assertEqual((result.category,result.retry_policy),
                                     ("unknown","reconcile_only"))
        for code in ("deadline_exceeded","transport_error","rate_limited"):
            self.assertEqual(
                classify_technical_outcome(code,dispatch_intent=False).retry_policy,
                "safe_after_new_reservation",
            )
        for wrong in ("not_a_code",0,None,False,{},math.nan):
            with self.assertRaises(SREContractError):
                classify_technical_outcome(wrong,dispatch_intent=False)
        for flag in (None,1,"true",math.nan):
            with self.assertRaises(SREContractError):
                classify_technical_outcome("deadline_exceeded",dispatch_intent=flag)
        with self.assertRaises(SREContractError):
            classify_technical_outcome("unknown_charge",dispatch_intent=False)
        with self.assertRaises(SREContractError):
            classify_technical_outcome("success",dispatch_intent=False)

    def test_import_without_optional_provider_db_or_live_connectors(self):
        code=(
            "import sys;from intelligence.relevance.live_fsm import MODE;"
            "assert MODE=='live';"
            "assert 'openai' not in sys.modules;"
            "assert 'psycopg' not in sys.modules;"
            "assert 'openrouter' not in sys.modules;"
            "assert 'intelligence.collector' not in sys.modules;"
        )
        proc=subprocess.run([sys.executable,"-c",code],
                            capture_output=True,text=True,timeout=15)
        self.assertEqual(proc.returncode,0,proc.stderr)


if __name__=="__main__":
    unittest.main()
