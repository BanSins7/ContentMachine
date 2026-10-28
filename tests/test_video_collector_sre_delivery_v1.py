"""VC-MOD-11: single durable SRE outbox state machine and SRE M02 port shape QA.

Offline PostgreSQL facsimile with explicit generation and project scopes.
A mock ACK is not evidence of installed production SRE M35 inbox.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime,timezone,timedelta
from uuid import UUID
from unittest import TestCase
from unittest.mock import patch

from intelligence.collector.contracts import CollectorError
from intelligence.collector.sre_delivery import (
    OUTBOX_VERSION,SRE_INBOUND_VERSION,ACK_VERSION,
    _strict_event,claim_one,ack_delivery,fail_delivery,recover_expired,
    dispatch_one,
)
from intelligence.taxonomy_builder.pipeline_contracts import sha256

PID="a"*32
FOREIGN="b"*32
EVENT="11111111-1111-4111-8111-111111111111"
Q1="22222222-2222-4222-8222-222222222222"
Q2="33333333-3333-4333-8333-333333333333"
PUB="44444444-4444-4444-8444-444444444444"
GEN="55555555-5555-4555-8555-555555555555"
AT=datetime(2026,10,26,12,0,tzinfo=timezone.utc)
OWNER="collector-sre-test"
PAYLOAD={
    "schema_version":OUTBOX_VERSION,"project_id":PID,
    "delivery_key":"d"*64,"source_id":"dtk_tiktok",
    "relevance_state":"not_evaluated","evidence_scope":"metadata_only",
    "page_sha256":"f"*64,"source_request_id":"33","run_id":"22",
    "page_id":"77","video":{
        "video_id":"88","discovery_id":"99","source_metadata_id":"111"},
    "candidate_ids":[Q1,Q2],"hypothesis_ids":["hyp_example"],
    "content_kind":"video","quality":"valid",
}
QUERYSET={
    "schema_version":"validated_query_set/v1","project_id":PID,
    "generation_id":GEN,"taxonomy_publication_id":PUB,
    "taxonomy_version":1,"taxonomy_sha256":"b"*64,
    "validation_version":"qv-test",
    "queries":[]
}


class Cursor:
    def __init__(self,one=None,all=None):
        self.one=one;self.all=all
    def fetchone(self):return self.one
    def fetchall(self):return self.all or []


class DB:
    def __init__(self):
        self.calls=[]
        self.data=deepcopy(PAYLOAD)
        self.state="pending"
        self.attempts=0
        self.generation=0
        self.leased_by=None
        self.lease_valid=True
        self.permission=True
        self.valid_links=True
        self.invalid_d1=False
        self.created=AT+timedelta(seconds=5)
        self.persisted=AT
        self.fail_ack=False
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def transaction(self):
        class T:
            def __init__(self,db):self.db=db
            def __enter__(self):
                self.old=(self.db.state,self.db.attempts,
                          self.db.generation,self.db.leased_by)
                return self.db
            def __exit__(self,t,*args):
                if t is not None:
                    (self.db.state,self.db.attempts,self.db.generation,
                     self.db.leased_by)=self.old
                return False
        return T(self)
    def execute(self,sql,params=None):
        self.calls.append((sql,params))
        if "SELECT id,delivery_key,event_payload,attempts,lease_generation" in sql:
            if self.state!="pending" or params[0]!=PID:return Cursor()
            return Cursor((UUID(EVENT),self.data["delivery_key"],self.data,
                           self.attempts,self.generation))
        if "UPDATE collector_sre_outbox" in sql and "SET state='leased'" in sql:
            if self.state!="pending":return Cursor()
            self.state="leased";self.leased_by=params[0]
            self.attempts+=1;self.generation+=1
            return Cursor((self.attempts,self.generation))
        if "SELECT event_payload,delivery_key,attempts,lease_generation" in sql:
            if self.state!="leased" or not self.lease_valid:return Cursor()
            return Cursor((self.data,self.data["delivery_key"],
                           self.attempts,self.generation))
        if "SELECT e.project_id,e.video_id" in sql:
            if self.invalid_d1 or params[1]!=PID:return Cursor()
            return Cursor((PID,88,99,111,77,self.created,self.data,
                           self.data["delivery_key"],self.persisted,"f"*64,
                           33,22,QUERYSET,sha256(QUERYSET),12,"dtk_tiktok",
                           True,AT if self.permission else None,
                           "enabled" if self.permission else "disabled",True))
        if "SELECT l.candidate_id,t.hypothesis_id" in sql:
            if not self.valid_links:return Cursor(all=[(Q1,"different")])
            return Cursor(all=[(Q1,"hyp_example"),(Q2,"hyp_example")])
        if "SELECT event_payload,state,attempts" in sql:
            if (self.state!="leased" or self.leased_by!=params[2]
                or self.generation!=params[3] or not self.lease_valid):
                return Cursor()
            return Cursor((self.data,"leased",self.attempts))
        if "SELECT attempts FROM collector_sre_outbox" in sql:
            return Cursor((self.attempts,))
        if "UPDATE collector_sre_outbox SET state='delivered'" in sql:
            if self.fail_ack:raise RuntimeError("lost ACK PostgreSQL connection")
            if self.state!="leased" or not self.lease_valid:return Cursor()
            self.state="delivered";self.leased_by=None
            return Cursor((UUID(EVENT),))
        if "UPDATE collector_sre_outbox" in sql and "SET state=%s" in sql:
            if self.state!="leased":return Cursor()
            self.state=params[0];self.leased_by=None
            return Cursor((UUID(EVENT),))
        if "UPDATE collector_sre_outbox SET" in sql and "lease_expired" in sql:
            if self.state!="leased" or self.lease_valid:return Cursor(all=[])
            self.state="blocked" if self.attempts>=8 else "pending"
            self.leased_by=None
            return Cursor(all=[(UUID(EVENT),self.state)])
        raise AssertionError("Unexpected SQL "+sql[:160])


class SRE:
    def __init__(self,mode="ok"):
        self.mode=mode
        self.calls=[]
    def capabilities(self):
        return {"schema_version":SRE_INBOUND_VERSION,
                "authenticated":True,"durable_inbox":True,"ready":True}
    def deliver(self,message):
        self.calls.append(message)
        if self.mode=="down":raise TimeoutError("SRE unavailable")
        if self.mode=="false_ack":return {"persisted":False}
        return ack(message)


def ack(event):
    return {"schema_version":ACK_VERSION,"event_id":event["event_id"],
            "project_id":event["project_id"],
            "event_sha256":event["event_sha256"],"persisted":True,
            "durable_receipt_id":"ack-m35-123"}


class OutboxTests(TestCase):
    def test_default_disabled_or_missing_sre_never_claims(self):
        db=DB()
        self.assertEqual(dispatch_one(PID,OWNER,connect=lambda:db)["status"],"disabled")
        self.assertEqual(db.calls,[])
        self.assertEqual(dispatch_one(PID,OWNER,adapter=None,
            env={"CM_COLLECTOR_SRE_DELIVERY_ENABLED":"1"},
            connect=lambda:db)["status"],"sre_unavailable")
        self.assertEqual(db.calls,[])

    def test_project_scoped_claim_increments_attempt_and_generation_once(self):
        db=DB()
        self.assertIsNone(claim_one(FOREIGN,OWNER,connect=lambda:db))
        claimed=claim_one(PID,OWNER,connect=lambda:db)
        self.assertEqual(claimed["collector_outbox_id"],EVENT)
        self.assertEqual(claimed["attempts"],1)
        self.assertEqual(claimed["lease_generation"],1)
        self.assertEqual(db.state,"leased")
        self.assertIsNone(claim_one(PID,OWNER,connect=lambda:db))
        sql=str(db.calls)
        self.assertIn("FOR UPDATE SKIP LOCKED",sql)
        self.assertIn("project_id=%s",sql)

    def test_reconstruction_reads_real_d1_and_emits_exact_sre_m02_event(self):
        db=DB()
        message=_strict_event(db,PID,(EVENT,PAYLOAD["delivery_key"],
                                      PAYLOAD,1,1))
        evt=message["sre_event"]
        self.assertEqual(evt["schema_version"],SRE_INBOUND_VERSION)
        self.assertEqual(evt["producer"],"video_collector")
        self.assertEqual(evt["event_id"],EVENT)
        self.assertEqual(evt["video_id"],88)
        self.assertEqual(evt["discovery_id"],99)
        self.assertEqual(evt["source_request_id"],33)
        self.assertEqual(evt["source_policy_version_id"],12)
        self.assertEqual(evt["collector_query_ids"],[Q1,Q2])
        self.assertEqual(evt["event_sha256"],sha256(
            {k:v for k,v in evt.items() if k!="event_sha256"}))
        self.assertEqual(message["collector_provenance"]["hypothesis_ids"],
                         ["hyp_example"])
        self.assertEqual(message["collector_provenance"]["taxonomy_sha256"],
                         "b"*64)
        self.assertEqual(message["collector_provenance"]["relevance_state"],
                         "not_evaluated")
        self.assertNotIn("relevance_state",evt)

    def test_mock_connected_sre_requires_ack_before_delivered(self):
        db=DB();service=SRE()
        result=dispatch_one(PID,OWNER,adapter=service,connect=lambda:db,
                            env={"CM_COLLECTOR_SRE_DELIVERY_ENABLED":"1"})
        self.assertEqual(result["status"],"delivered")
        self.assertEqual(result["event_id"],EVENT)
        self.assertEqual(result["external_calls"],1)
        self.assertEqual(db.state,"delivered")
        self.assertEqual(service.calls[0]["event_id"],EVENT)
        self.assertIsNone(claim_one(PID,OWNER,connect=lambda:db))

    def test_sre_down_defers_without_ack_or_fabricated_success(self):
        db=DB();service=SRE("down")
        result=dispatch_one(PID,OWNER,adapter=service,connect=lambda:db,
                            env={"CM_COLLECTOR_SRE_DELIVERY_ENABLED":"1"})
        self.assertEqual(result["status"],"pending")
        self.assertFalse(result["delivered"])
        self.assertEqual(result["external_calls"],1)
        self.assertEqual(db.state,"pending")
        self.assertEqual(db.attempts,1)
        self.assertEqual(result["retry_seconds"],5)

    def test_bad_ack_is_not_delivered(self):
        db=DB()
        result=dispatch_one(PID,OWNER,adapter=SRE("false_ack"),
                            env={"CM_COLLECTOR_SRE_DELIVERY_ENABLED":"1"},
                            connect=lambda:db)
        self.assertEqual(result["status"],"pending")
        self.assertFalse(result["delivered"])
        self.assertNotEqual(db.state,"delivered")

    def test_claimed_project_lease_is_fenced_on_ack(self):
        db=DB();claim_one(PID,OWNER,connect=lambda:db)
        evt={**_strict_event(db,PID,(EVENT,PAYLOAD["delivery_key"],
                                    db.data,1,1))["sre_event"],
             "delivery_key":PAYLOAD["delivery_key"]}
        for owner,gen,project in (
            ("other_worker",1,PID),(OWNER,2,PID),(OWNER,1,FOREIGN),
        ):
            with self.subTest(owner=owner,gen=gen,project=project):
                with self.assertRaises(CollectorError):
                    ack_delivery(project,owner,gen,evt,ack(evt),connect=lambda:db)
                self.assertEqual(db.state,"leased")
        db.lease_valid=False
        with self.assertRaises(CollectorError):
            ack_delivery(PID,OWNER,1,evt,ack(evt),connect=lambda:db)
        self.assertEqual(db.state,"leased")

    def test_replayed_or_forged_ack_cannot_change_delivery_state(self):
        db=DB();claim_one(PID,OWNER,connect=lambda:db)
        evt={**_strict_event(db,PID,(EVENT,PAYLOAD["delivery_key"],
                                    db.data,1,1))["sre_event"],
             "delivery_key":PAYLOAD["delivery_key"]}
        with self.assertRaises(CollectorError):
            ack_delivery(PID,OWNER,1,evt,{**ack(evt),"event_sha256":"a"*64},
                         connect=lambda:db)
        self.assertEqual(db.state,"leased")
        good=ack_delivery(PID,OWNER,1,evt,ack(evt),connect=lambda:db)
        self.assertEqual(good["status"],"delivered")
        with self.assertRaises(CollectorError):
            ack_delivery(PID,OWNER,1,evt,ack(evt),connect=lambda:db)

    def test_last_attempt_blocks_instead_of_infinite_retry(self):
        db=DB();db.attempts=7
        claim=claim_one(PID,OWNER,connect=lambda:db)
        value=fail_delivery(PID,OWNER,EVENT,claim["lease_generation"],
                            error_code="sre_unavailable",connect=lambda:db)
        self.assertEqual(value["status"],"blocked")
        self.assertEqual(db.state,"blocked")
        self.assertEqual(db.attempts,8)

    def test_retry_backoff_is_bounded_and_permanent_poison_is_blocked(self):
        db=DB();claim_one(PID,OWNER,connect=lambda:db)
        value=fail_delivery(PID,OWNER,EVENT,1,error_code="sre_event_invalid",
                            permanent=True,connect=lambda:db)
        self.assertEqual(value["status"],"blocked")
        self.assertIsNone(value["retry_seconds"])
        with self.assertRaises(CollectorError):
            fail_delivery(PID,OWNER,EVENT,1,error_code="raw traceback string",
                          connect=lambda:db)

    def test_expired_lease_recovers_only_project_pending_rows(self):
        db=DB();claim_one(PID,OWNER,connect=lambda:db)
        db.lease_valid=False
        res=recover_expired(PID,connect=lambda:db)
        self.assertEqual(res["recovered"],1)
        self.assertEqual(res["pending"],1)
        self.assertEqual(db.state,"pending")
        self.assertEqual(recover_expired(PID,connect=lambda:db)["recovered"],0)

    def test_invalid_d1_or_qv_blocks_poison_event_before_sre_call(self):
        for field in ("permission","links","missing","payload","timestamp"):
            with self.subTest(field=field):
                db=DB();srv=SRE()
                if field=="permission":db.permission=False
                elif field=="links":db.valid_links=False
                elif field=="missing":db.invalid_d1=True
                elif field=="payload":db.data["relevance_state"]="match"
                else:db.created=AT-timedelta(minutes=1)
                out=dispatch_one(PID,OWNER,adapter=srv,
                        env={"CM_COLLECTOR_SRE_DELIVERY_ENABLED":"1"},
                        connect=lambda:db)
                self.assertEqual(out["status"],"blocked")
                self.assertEqual(db.state,"blocked")
                self.assertEqual(len(srv.calls),0)
                self.assertEqual(out["external_calls"],0)

    def test_lost_ack_returns_pending_without_second_provider_or_relabel(self):
        db=DB();db.fail_ack=True;srv=SRE()
        result=dispatch_one(PID,OWNER,adapter=srv,connect=lambda:db,
                            env={"CM_COLLECTOR_SRE_DELIVERY_ENABLED":"1"})
        self.assertEqual(result["status"],"ack_pending")
        self.assertFalse(result["delivered"])
        self.assertEqual(len(srv.calls),1)
        self.assertEqual(db.state,"leased")
        self.assertEqual(srv.calls[0]["event_id"],EVENT)
        db.lease_valid=False
        recover_expired(PID,connect=lambda:db)
        self.assertEqual(db.state,"pending")
        # A real SRE durable inbox dedupes the same immutable event_id.
        self.assertEqual(srv.calls[0]["event_id"],EVENT)

    def test_wrong_or_unready_adapter_does_not_claim(self):
        for adapter in (object(),SRE()):
            with self.subTest(adapter=type(adapter).__name__):
                db=DB()
                if isinstance(adapter,SRE):
                    adapter.capabilities=lambda:{"schema_version":SRE_INBOUND_VERSION,
                      "authenticated":True,"durable_inbox":True,"ready":False}
                    outcome=dispatch_one(PID,OWNER,adapter=adapter,connect=lambda:db,
                        env={"CM_COLLECTOR_SRE_DELIVERY_ENABLED":"1"})
                    self.assertEqual(outcome["status"],"sre_unavailable")
                else:
                    with self.assertRaises(CollectorError):
                        dispatch_one(PID,OWNER,adapter=adapter,connect=lambda:db,
                            env={"CM_COLLECTOR_SRE_DELIVERY_ENABLED":"1"})
                self.assertEqual(db.calls,[])

if __name__=="__main__":
    import unittest
    unittest.main()
