"""M32 offline tests of immutable human review/supersession and D1 privacy.

No model POST, no implicit reviewer role, no invented C9 provider evidence.
Original result remains a separate immutable M30 row.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from intelligence.relevance.live_human_review import (
    VERSION,human_review_confirmation,record_live_sre_human_review,
    read_effective_live_sre_decision,read_sre_human_review_history,
    read_live_sre_human_review_queue,
    SQL_SOURCE,SQL_HEAD,SQL_HISTORY,SQL_INSERT,SQL_QUEUE,SQL_RUN,
)
from intelligence.relevance.v1_contracts import SREContractError

PID="a"*32
RID="11111111-1111-4111-8111-111111111111"
KEY="22222222-2222-4222-8222-222222222222"
BASE="33333333-3333-4333-8333-333333333333"
RECEIPT="44444444-4444-4444-8444-444444444444"
REV1="55555555-5555-4555-8555-555555555555"
REV2="66666666-6666-4666-8666-666666666666"
SHA="f"*64
WHEN=datetime(2026,10,28,7,30,tzinfo=timezone.utc)
ACTIONS={
    "human_review_sre_live_pair",
    "read_sre_human_review","read_sre_human_review_queue",
}

def grant(pid,actor,action):
    return pid==PID and actor=="human:7" and action in ACTIONS

class Result:
    def __init__(self,row=None,rows=None):
        self.row=row;self.rows=[] if rows is None else rows
    def fetchone(self):return self.row
    def fetchall(self):return self.rows

class DB:
    autocommit=True
    class Info:
        class Status:
            name="IDLE"
        transaction_status=Status()
    info=Info()

    def __init__(self):
        self.history=[]
        self.executed=[]
        self.unauthorized_run=False
        self.missing_base=False
        self.rights=True
    def transaction(self):return nullcontext()
    def execute(self,sql,params=()):
        self.executed.append(sql)
        if "FROM sre_workspaces" in sql:return Result((1,))
        if sql==SQL_SOURCE:
            if self.missing_base:return Result(None)
            return Result((BASE,RECEIPT,SHA,SHA,SHA,SHA,"match",100,101,SHA,
                           "cancelled" if self.unauthorized_run else "running",
                           self.unauthorized_run,True,
                           "validated_untrusted_output","match"))
        if sql==SQL_HEAD:
            if not self.history:return Result(None)
            x=self.history[-1]
            return Result((x["id"],x["rev"],x["decision"],x["action"],
                           x["reason"],x["actor"],WHEN,"match",BASE))
        if sql==SQL_INSERT:
            (identity,pid,rid,iid,key,base,receipt,req,evidence,quote,catalog,
             orig,decision,action,reason,actor,rev,prior)=params
            assert quote==SHA and catalog==SHA
            assert identity not in [x["id"] for x in self.history]
            assert orig=="match" and base==BASE and receipt==RECEIPT
            assert prior==(self.history[-1]["id"] if self.history else None)
            self.history.append({"id":identity,"rev":rev,
                                 "decision":decision,"action":action,
                                 "reason":reason,"actor":actor,"prior":prior})
            return Result((identity,))
        if sql==SQL_HISTORY:
            return Result(rows=[
                (x["id"],x["rev"],x["prior"],x["decision"],x["action"],
                 x["reason"],x["actor"],WHEN,"match",BASE,RECEIPT,SHA,SHA,
                 SHA,SHA)
                for x in self.history])
        if sql==SQL_QUEUE:
            if not self.history:
                rid=None;rev=None;dec=None;action=None;who=None
            else:
                x=self.history[-1]
                rid=x["id"];rev=x["rev"];dec=x["decision"]
                action=x["action"];who=x["actor"]
            return Result(rows=[(
                9,KEY,BASE,"match",RECEIPT,SHA,SHA,SHA,
                100,101,SHA,rid,rev,dec,action,who,
            )])
        if sql==SQL_RUN:return Result(("running",False,"live"))
        if sql.startswith("SET TRANSACTION"):return Result()
        raise AssertionError("unexpected SQL "+sql[:140])

def confirmation(decision,reason,prior=None):
    return human_review_confirmation(
        PID,RID,9,KEY,BASE,"match",decision,reason,prior,
    )

def record(db,decision,reason,prior=None,confirm=None):
    return record_live_sre_human_review(
        db,PID,RID,9,KEY,reviewer_actor="human:7",
        actor_authorizer=grant,human_decision=decision,reason_code=reason,
        expected_prior_review_id=prior,
        explicit_confirmation=(confirm if confirm is not None
                               else confirmation(decision,reason,prior)),
    )

class M32HumanReviewContracts(unittest.TestCase):
    def setUp(self):
        self.db=DB()
        self.gate=patch(
            "intelligence.relevance.live_human_review._verify_current_d1"
        )
        self.verify=self.gate.start()
    def tearDown(self):self.gate.stop()

    def test_initial_confirmation_preserves_model(self):
        result=record(self.db,"match","verified_model_decision")
        self.assertEqual(result["review_action"],"confirm")
        self.assertEqual(result["revision"],1)
        self.assertFalse(result["model_result_mutated"])
        self.assertEqual(result["provider_post_calls"],0)
        effective=read_effective_live_sre_decision(
            self.db,PID,RID,9,KEY,actor_id="human:7",
            actor_authorizer=grant)
        self.assertEqual(effective["effective_decision"],"match")
        self.assertEqual(effective["original_model_decision"],"match")
        self.assertEqual(effective["quote_sha256"],SHA)
        self.assertTrue(effective["human_reviewed"])
        self.assertEqual(effective["human_revision"],1)
        self.assertTrue(effective["source_rights_rechecked"])

    def test_correct_then_supersede_then_history(self):
        first=record(self.db,"no_match","incorrect_positive")
        self.assertEqual(first["review_action"],"correct")
        second=record(self.db,"uncertain","ambiguous_content",first["review_id"])
        self.assertEqual(second["review_action"],"supersede")
        self.assertEqual(second["prior_review_id"],first["review_id"])
        self.assertEqual(second["revision"],2)
        effective=read_effective_live_sre_decision(
            self.db,PID,RID,9,KEY,actor_id="human:7",actor_authorizer=grant)
        self.assertEqual(effective["effective_decision"],"uncertain")
        self.assertEqual(effective["original_model_decision"],"match")
        history=read_sre_human_review_history(
            self.db,PID,RID,9,KEY,actor_id="human:7",actor_authorizer=grant)
        self.assertEqual([x["revision"] for x in history["reviews"]],[1,2])
        self.assertEqual([x["human_decision"] for x in history["reviews"]],
                         ["no_match","uncertain"])
        self.assertEqual(history["effective_decision"],"uncertain")
        self.assertTrue(history["immutable_model_decision_preserved"])

    def test_stale_fork_duplicate_and_same_effective_decision_denied(self):
        first=record(self.db,"no_match","incorrect_positive")
        with self.assertRaises(SREContractError) as replay:
            record(self.db,"no_match","incorrect_positive")
        self.assertEqual(replay.exception.code,"stale_review")
        with self.assertRaises(SREContractError) as stale:
            record(self.db,"uncertain","ambiguous_content",REV2)
        self.assertEqual(stale.exception.code,"stale_review")
        with self.assertRaises(SREContractError):
            record(self.db,"no_match","review_correction",first["review_id"])
        with self.assertRaises(SREContractError):
            record(self.db,"match","verified_model_decision",
                   first["review_id"])
        self.assertEqual(len(self.db.history),1)

    def test_role_and_exact_confirmations_block_implicit_review(self):
        with self.assertRaises(SREContractError) as wrong_actor:
            record_live_sre_human_review(
                self.db,PID,RID,9,KEY,reviewer_actor="worker:1",
                actor_authorizer=grant,human_decision="match",
                reason_code="verified_model_decision",
                explicit_confirmation=confirmation("match",
                                                    "verified_model_decision"),
            )
        self.assertEqual(wrong_actor.exception.code,"actor_not_authorized")
        with self.assertRaises(SREContractError) as forged:
            record(self.db,"no_match","incorrect_positive",
                   confirm=confirmation("match","verified_model_decision"))
        self.assertEqual(forged.exception.code,"review_not_explicit")
        with self.assertRaises(SREContractError):
            record(self.db,"match","incorrect_positive")
        with self.assertRaises(SREContractError):
            record(self.db,"no_match","verified_model_decision")
        self.assertEqual(len(self.db.history),0)

    def test_revocation_cancel_and_foreign_original_result_fail_closed(self):
        self.db.unauthorized_run=True
        with self.assertRaises(SREContractError):
            record(self.db,"no_match","incorrect_positive")
        self.db.unauthorized_run=False
        self.db.missing_base=True
        with self.assertRaises(SREContractError) as missing:
            record(self.db,"no_match","incorrect_positive")
        self.assertEqual(missing.exception.code,"rights_denied")
        self.db.missing_base=False
        self.verify.side_effect=SREContractError(
            "rights_denied","source","rights revoked")
        with self.assertRaises(SREContractError):
            record(self.db,"no_match","incorrect_positive")
        self.assertEqual(len(self.db.history),0)

    def test_queue_is_bounded_and_never_infers_unverified_c9(self):
        before=read_live_sre_human_review_queue(
            self.db,PID,RID,actor_id="human:7",actor_authorizer=grant)
        self.assertEqual(len(before["rows"]),1)
        self.assertEqual(before["rows"][0]["review_state"],"awaiting_review")
        record(self.db,"no_match","incorrect_positive")
        after=read_live_sre_human_review_queue(
            self.db,PID,RID,actor_id="human:7",actor_authorizer=grant)
        self.assertEqual(after["rows"][0]["effective_decision"],"no_match")
        self.assertEqual(after["rows"][0]["original_model_decision"],"match")
        self.assertFalse(after["model_results_mutated"])
        self.assertFalse(after["paid_post_allowed"])
        with self.assertRaises(SREContractError):
            read_live_sre_human_review_queue(
                self.db,PID,RID,actor_id="human:7",
                actor_authorizer=grant,limit=101)

    def test_history_corruption_fails_closed(self):
        record(self.db,"no_match","incorrect_positive")
        original=self.db.execute
        def tampered(sql,params=()):
            if sql==SQL_HISTORY:
                return Result(rows=[(
                    REV1,2,None,"no_match","correct","incorrect_positive",
                    "human:7",WHEN,"match",BASE,RECEIPT,SHA,SHA,SHA,SHA,
                )])
            return original(sql,params)
        self.db.execute=tampered
        with self.assertRaises(SREContractError):
            read_sre_human_review_history(
                self.db,PID,RID,9,KEY,actor_id="human:7",
                actor_authorizer=grant)


if __name__=="__main__":
    unittest.main()
