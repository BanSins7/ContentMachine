"""M36 no-QDP, no-network result/invalidation and lease regression."""
from __future__ import annotations

import unittest
from contextlib import nullcontext
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from intelligence.relevance import live_result_outbox as outbox
from intelligence.relevance.ports.qdp_reference import (
    apply_qdp_reference_event,empty_qdp_reference_state,make_invalidation,
    validate_invalidation_event,
)
from intelligence.relevance.v1_contracts import SREContractError,fingerprint
from test_sre_module_ports import live_event

PID="a"*32
RUN="11111111-1111-4111-8111-111111111111"
PUB="22222222-2222-4222-8222-222222222222"
K1="33333333-3333-4333-8333-333333333333"
K2="44444444-4444-4444-8444-444444444444"
SHA="f"*64
TIME=datetime(2026,10,28,tzinfo=timezone.utc)


def allowed(pid,actor,action):
    return pid==PID and actor=="reviewed:host" and action in {
        "publish_sre_live_result","materialize_sre_invalidation",
        "deliver_sre_result_event","ack_sre_result_event",
        "invalidate_sre_live_result",
    }


def src():
    return {
        "publication_id":PUB,"taxonomy_version_id":9,"taxonomy_sha256":SHA,
        "video_id":29,"content_version_id":51,"content_sha256":SHA,
        "source_policy_version_id":14,"leaves":[K1,K2],
        "decisions":{"match":[K1],"no_match":[K2],"uncertain":[]},
    }


class Cursor:
    def __init__(self,row=None,rows=None):
        self.row=row
        self.rows=[] if rows is None else rows
    def fetchone(self):return self.row
    def fetchall(self):return self.rows


class DB:
    autocommit=True
    info=SimpleNamespace(transaction_status=SimpleNamespace(name="IDLE"))
    def __init__(self):
        self.steps=[]
        self.saved_ready=None
        self.ready_count=0
        self.intent=None
        self.leased=None
    def transaction(self):return nullcontext()
    def execute(self,sql,args=None):
        self.steps.append((sql,args))
        if sql==outbox.WORKSPACE:return Cursor((1,))
        if sql==outbox.RUN_ITEM:
            return Cursor(("live","completed",False,PUB,9,SHA,1,1,0,
                            "completed",29,51,SHA,14,[K1,K2]))
        if sql==outbox.PENDING:return Cursor((0,))
        if sql==outbox.VERIFIED:
            return Cursor(rows=[
                (K1,"match",str(uuid4()),"match",str(uuid4()),SHA),
                (K2,"no_match",str(uuid4()),"no_match",str(uuid4()),SHA),
            ])
        if sql==outbox.PAIR_COUNT:return Cursor((2,))
        if sql=="SELECT clock_timestamp()":return Cursor((TIME,))
        if sql==outbox.READ_READY:return Cursor(self.saved_ready)
        if sql==outbox.INSERT_READY:
            import json
            e=json.loads(args[5])
            self.saved_ready=(args[0],args[6],e,"pending")
            self.ready_count+=1
            return Cursor((args[0],))
        if sql==outbox.READY_FOR_INVALIDATION:
            if self.saved_ready is None:return Cursor()
            e=self.saved_ready
            return Cursor((e[0],RUN,7,51,e[2],e[1]))
        if sql==outbox.INTENT_INSERT:
            if self.intent:return Cursor()
            self.intent=args[2]
            return Cursor((args[1],))
        if sql==outbox.INTENT_READ:return Cursor((self.intent,) if self.intent else None)
        if sql==outbox.INTENTS:
            return Cursor(rows=[(self.saved_ready[0],self.intent)] if self.intent else [])
        if sql==outbox.INSERT_INVALID:
            return Cursor((args[0],))
        if sql==outbox.SELECT_CLAIM:return Cursor(self.leased)
        if sql==outbox.READY_CURRENT:return Cursor((1,))
        if sql==outbox.LEASE:return Cursor((self.leased[7]+1,TIME+timedelta(seconds=60)))
        if sql==outbox.ACK:return Cursor((args[1],))
        if "SELECT 1 FROM sre_live_result_invalidation_intents" in sql:
            return Cursor((1,) if self.intent else None)
        if "SELECT 1 FROM sre_live_human_reviews" in sql:return Cursor((1,))
        raise AssertionError(sql[:120])


class QDPReferenceTests(unittest.TestCase):
    def test_invalidation_before_ready_and_duplicates_never_resurrect(self):
        ready=live_event()
        gone=make_invalidation(project=ready["project_id"],prior_ready=ready,
            reason="source_revoked",event_id=str(uuid4()),
            occurred_at="2026-10-28T10:00:00Z")
        state=empty_qdp_reference_state()
        state=apply_qdp_reference_event(gone,state)
        state=apply_qdp_reference_event(ready,state)
        state=apply_qdp_reference_event(ready,state)
        self.assertEqual(state["ready"],{})
        self.assertIn(ready["event_id"],state["invalidated"])
        self.assertEqual(len(state["seen"]),2)

    def test_ready_first_then_revoked_clears_consumer(self):
        ready=live_event()
        state=apply_qdp_reference_event(ready,empty_qdp_reference_state())
        self.assertEqual(len(state["ready"]),1)
        gone=make_invalidation(project=ready["project_id"],prior_ready=ready,
            reason="review_superseded",event_id=str(uuid4()),
            occurred_at="2026-10-28T10:00:00Z")
        new=apply_qdp_reference_event(gone,state)
        self.assertEqual(new["ready"],{})
        self.assertEqual(len(state["ready"]),1)

    def test_invalidations_reject_forged_source_sha_and_unknown_reason(self):
        ready=live_event()
        gone=make_invalidation(project=ready["project_id"],prior_ready=ready,
            reason="rights_lost",event_id=str(uuid4()),
            occurred_at="2026-10-28T10:00:00Z")
        for mutation in (
            lambda x:x.update(prior_ready_event_sha256="0"*64),
            lambda x:x.update(reason_code="approved"),
            lambda x:x.update(project_id="0"*32),
            lambda x:x.update(content_version_id=0),
        ):
            wrong=dict(gone);mutation(wrong)
            with self.assertRaises(SREContractError):
                validate_invalidation_event(wrong)
        wrong=dict(gone)
        wrong["event_id"]=str(uuid4())
        with self.assertRaises(SREContractError):
            apply_qdp_reference_event(wrong,empty_qdp_reference_state())

    def test_invalidation_first_rejects_late_ready_with_changed_sha(self):
        ready=live_event()
        gone=make_invalidation(project=ready["project_id"],prior_ready=ready,
            reason="source_revoked",event_id=str(uuid4()),
            occurred_at="2026-10-28T10:00:00Z")
        state=apply_qdp_reference_event(gone,empty_qdp_reference_state())
        tampered=dict(ready)
        tampered["rights_verified_at"]="2026-10-28T10:00:00Z"
        tampered["event_sha256"]=fingerprint({
            k:v for k,v in tampered.items() if k!="event_sha256"
        })
        with self.assertRaises(SREContractError):
            apply_qdp_reference_event(tampered,state)

    def test_synthetic_complete_never_accepted_by_reference(self):
        ready=live_event()
        ready["execution_mode"]="offline_fixture"
        ready["event_sha256"]=fingerprint({
            k:v for k,v in ready.items() if k!="event_sha256"})
        with self.assertRaises(SREContractError):
            apply_qdp_reference_event(ready,empty_qdp_reference_state())


class OutboxHostTests(unittest.TestCase):
    def test_publish_is_idempotent_only_after_exact_current_source(self):
        db=DB()
        with patch.object(outbox,"_source_ready",return_value=src()):
            x=outbox.publish_live_sre_result(
                db,PID,RUN,7,actor_id="reviewed:host",
                actor_authorizer=allowed)
            y=outbox.publish_live_sre_result(
                db,PID,RUN,7,actor_id="reviewed:host",
                actor_authorizer=allowed)
        self.assertFalse(x["replayed"])
        self.assertTrue(y["replayed"])
        self.assertEqual(x["event_id"],y["event_id"])
        self.assertEqual(db.ready_count,1)
        self.assertEqual(db.saved_ready[2]["decisions"],src()["decisions"])

    def test_denied_actor_no_db_access(self):
        db=DB()
        with self.assertRaises(SREContractError):
            outbox.publish_live_sre_result(
                db,PID,RUN,7,actor_id="browser:forged",
                actor_authorizer=allowed)
        self.assertEqual(db.steps,[])

    def test_fake_run_partial_or_unreviewed_denied_before_event_write(self):
        db=DB()
        with patch.object(outbox,"read_authorized_taxonomy",return_value={}):
            with patch.object(outbox,"read_live_content_gate",return_value={
                "content":{"source_policy_version_id":14},"content_sha256":SHA
            }):
                actual=outbox._source_ready(db,PID,RUN,7)
                self.assertEqual(actual["leaves"],[K1,K2])
                self.assertEqual(actual["decisions"],src()["decisions"])
        db2=DB()
        badrow=("offline_fixture","completed",False,PUB,9,SHA,1,1,0,
                "completed",29,51,SHA,14,[K1,K2])
        with patch.object(db2,"execute",return_value=Cursor(badrow)):
            with self.assertRaises(SREContractError):
                outbox._source_ready(db2,PID,RUN,7)

    def test_request_invalidation_materializes_later_without_qdp(self):
        db=DB()
        with patch.object(outbox,"_source_ready",return_value=src()):
            created=outbox.publish_live_sre_result(
                db,PID,RUN,7,actor_id="reviewed:host",
                actor_authorizer=allowed)
        self.assertEqual(created["state"],"pending")
        requested=outbox.request_sre_result_invalidation(
            db,PID,created["event_id"],"operator_revoked",
            actor_id="reviewed:host",actor_authorizer=allowed)
        self.assertTrue(requested["intent_created"])
        self.assertTrue(requested["ready_delivery_suppressed"])
        materialized=outbox.materialize_sre_invalidation_intents(
            db,PID,actor_id="reviewed:host",actor_authorizer=allowed)
        self.assertEqual(materialized["materialized"],1)

    def test_cas_ack_stale_lease_cannot_claim_success(self):
        db=DB()
        old=outbox.ACK
        with patch.object(db,"execute",side_effect=lambda sql,args=None:
                          Cursor(None) if sql==old else Cursor((1,))):
            with self.assertRaises(SREContractError):
                outbox.acknowledge_sre_result_event(
                    db,PID,str(uuid4()),str(uuid4()),1,SHA,
                    actor_id="reviewed:host",actor_authorizer=allowed)


if __name__=="__main__":
    unittest.main()
