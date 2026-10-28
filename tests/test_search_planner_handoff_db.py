"""SP-12 durable inbox SQL state-machine regression with transactional DB doubles.

The double models rollback, 10x idempotent replay and lost-response recovery.
It is NOT a substitute for real PostgreSQL17 concurrent workers/migration QA;
those release gates stay open. No collector imports and zero paid calls.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import unittest

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.search_planner.handoff import (
    VerifiedHandoffProjector,ingest_qv_to_sp_inbox,get_sp_handoff_status,
    deliver_next_held_chunk,reconcile_uncertain_chunk,MockCollectorAdapter,
)
from test_search_planner_stage11_handoff import synthetic_trusted_source
from test_search_planner_stage10_task_planner import PID,QV,GEN,OUTBOX

EID="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
PLAN_ID="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ATTEMPT_ID="cccccccc-cccc-4ccc-8ccc-cccccccccccc"
STAMP=datetime(2026,10,26,tzinfo=timezone.utc)
OWNER=lambda action,pid,event: action=="search_planner.handoff.deliver" and pid==PID and event==EID


class Result:
    def __init__(self,one=None,rows=None):
        self.one=one
        self.rows=rows if rows is not None else []
    def fetchone(self):
        return self.one
    def fetchall(self):
        return self.rows


class FakeDB:
    def __init__(self,selected=117):
        self.input=synthetic_trusted_source(selected)
        self.projection=VerifiedHandoffProjector(
            planning_reader=lambda pid,run:deepcopy(self.input)
        )
        self.project=PID
        self.qvdelivered=False
        self.ack_count=0
        self.event=None
        self.chunks={}
        self.links={}
        self.attempts={}
        self.receipts={}
        self.approved=True
        self.transactions=0
        self.rollbacks=0
        self.sql=[]
    def conn(self):
        return Conn(self)
    def mutable(self):
        return deepcopy({
            "qvdelivered":self.qvdelivered,"ack_count":self.ack_count,
            "event":self.event,"chunks":self.chunks,"links":self.links,
            "attempts":self.attempts,"receipts":self.receipts,
        })
    def restore(self,old):
        for k,v in old.items():setattr(self,k,v)


class Txn:
    def __init__(self,db):
        self.db=db
    def __enter__(self):
        self.before=self.db.mutable()
        self.db.transactions+=1
        return self
    def __exit__(self,kind,exc,tb):
        if kind is not None:
            self.db.rollbacks+=1
            self.db.restore(self.before)
        return False


class Conn:
    def __init__(self,db):
        self.db=db
    def __enter__(self):return self
    def __exit__(self,*_):return False
    def transaction(self):return Txn(self.db)

    def execute(self,sql,args=None):
        db=self.db
        db.sql.append((sql,args))
        if sql.startswith("SET TRANSACTION"):
            return Result()
        if "FROM qv_outbox o JOIN qv_runs r" in sql:
            if args!=(OUTBOX,PID):
                return Result(None)
            return Result((OUTBOX,QV,STAMP if db.qvdelivered else None,
                           "validated_queries_ready/v1",{
                               "schema_version":"validated_queries/v1",
                               "validation_run_id":QV,
                               "generation_id":GEN,
                               "embedding_mode":"semantic_local",
                               "selected_count":len(db.input["validated_bundle"]["queries"]),
                           },GEN,"semantic_local",
                           len(db.input["validated_bundle"]["queries"])))
        if "SELECT id FROM sp_handoff_events" in sql:
            return Result((EID,) if db.event is not None else None)
        if "SELECT id,qv_run_id,projection_sha256,projection,selected_count" in sql:
            e=db.event
            return Result((EID,QV,e["projection_sha256"],deepcopy(e["projection"]),
                           e["selected_count"],e["chunk_count"],e["status"])
                          if e else None)
        if "INSERT INTO sp_handoff_events" in sql:
            if db.event is not None:raise AssertionError("Duplicate SP inbox event insert")
            db.event={
                "projection_sha256":args[3],
                "projection":deepcopy(__import__("json").loads(args[4])),
                "selected_count":args[5],"chunk_count":args[6],
                "status":args[7],
            }
            return Result((EID,))
        if "INSERT INTO sp_handoff_chunks" in sql:
            event_id,index,digest,n,payload=args
            if index in db.chunks:raise AssertionError("Duplicate SP chunk")
            db.chunks[index]=deepcopy(__import__("json").loads(payload))
            return Result()
        if "INSERT INTO sp_handoff_query_links" in sql:
            event_id,index,cid,hid,payload,digest=args
            if cid in db.links:raise AssertionError("Duplicate SP lineage")
            db.links[cid]=(index,cid,hid,__import__("json").loads(payload),digest)
            return Result()
        if "SELECT chunk_index,chunk_sha256,selected_count,chunk" in sql:
            return Result(rows=[
                (i,c["chunk_sha256"],c["chunk_size"],deepcopy(c))
                for i,c in sorted(db.chunks.items())
            ])
        if "SELECT chunk_index,candidate_id,qv_hypothesis_id,provenance" in sql:
            return Result(rows=[
                deepcopy(item) for item in sorted(db.links.values(),
                                                  key=lambda x:(x[0],x[1]))
            ])
        if "UPDATE qv_outbox SET delivered_at=now()" in sql:
            if db.qvdelivered:return Result(None)
            db.qvdelivered=True
            db.ack_count+=1
            return Result((OUTBOX,))
        if "FROM sp_handoff_events e" in sql and "JOIN qv_outbox o" in sql:
            if args!=(EID,PID) or db.event is None:
                return Result(None)
            return Result((EID,QV,db.event["status"],
                           db.event["chunk_count"],deepcopy(db.event["projection"]),
                           STAMP if db.qvdelivered else None,PID))
        if "FROM sp_held_plan_approvals a" in sql:
            return Result((PLAN_ID,) if db.approved else None)
        if "FROM sp_handoff_chunks c" in sql and "LEFT JOIN" in sql:
            rows=[]
            for idx,chunk in sorted(db.chunks.items()):
                attempt=db.attempts.get(idx)
                receipt=db.receipts.get(idx)
                rows.append((
                    idx,chunk["chunk_sha256"],deepcopy(chunk),
                    ATTEMPT_ID if attempt else None,
                    attempt["status"] if attempt else None,
                    attempt["key"] if attempt else None,
                    PLAN_ID if receipt else None,
                ))
            return Result(rows=rows)
        if "INSERT INTO sp_delivery_attempts" in sql:
            eid,idx,key,sha=args
            if idx in db.attempts:raise AssertionError("Duplicate reservation")
            db.attempts[idx]={"key":key,"status":"uncertain","sha":sha}
            return Result((ATTEMPT_ID,))
        if "UPDATE sp_handoff_events SET status" in sql:
            db.event["status"]=args[0]
            return Result()
        if "FROM sp_delivery_attempts" in sql and "FOR UPDATE" in sql:
            idx=args[1]
            a=db.attempts.get(idx)
            return Result((ATTEMPT_ID,a["status"],a["sha"]) if a else None)
        if "SELECT id FROM sp_delivery_receipts" in sql:
            idx=args[1]
            return Result((PLAN_ID,) if idx in db.receipts else None)
        if "INSERT INTO sp_delivery_receipts" in sql:
            aid,eid,idx,external_id,plan,contract,digest,receipt,stamp=args
            if idx in db.receipts:raise AssertionError("Duplicate receipt")
            db.receipts[idx]=__import__("json").loads(receipt)
            return Result()
        if "UPDATE sp_delivery_attempts SET status='accepted'" in sql:
            for a in db.attempts.values():
                if a["status"]=="uncertain":
                    a["status"]="accepted"
                    return Result()
            raise AssertionError("No uncertain attempt")
        if "SELECT COUNT(*) FROM sp_delivery_receipts" in sql:
            return Result((len(db.receipts),))
        if "SELECT a.id,a.idempotency_key,a.status,c.chunk" in sql:
            idx=args[1];a=db.attempts.get(idx)
            return Result((ATTEMPT_ID,a["key"],a["status"],
                           deepcopy(db.chunks[idx])) if a else None)
        if "SELECT e.qv_run_id,e.qv_outbox_id,e.projection_sha256" in sql:
            return Result((QV,OUTBOX,db.event["projection_sha256"],
                           db.event["selected_count"],db.event["chunk_count"],
                           db.event["status"]) if db.event and args==(EID,PID)
                          else None)
        if "SELECT r.chunk_index,r.external_query_set_id" in sql:
            return Result(rows=[(i,r["external_query_set_id"],
                    r["external_plan_id"],r["collector_contract"],
                    r["receipt_sha256"],STAMP)
                for i,r in sorted(db.receipts.items())])
        if "SELECT a.chunk_index,a.status,a.attempted_at" in sql:
            return Result(rows=[(i,a["status"],STAMP)
                for i,a in sorted(db.attempts.items())])
        raise AssertionError("Unknown query "+sql[:145])


class DurableSP12(unittest.TestCase):
    def fresh(self,selected=117):
        self.db=FakeDB(selected)
        return self.db

    def test_117_all_three_chunks_and_every_original_uuid_persist_atomically(self):
        db=self.fresh()
        receipt=ingest_qv_to_sp_inbox(PID,QV,projector=db.projection,connect=db.conn)
        self.assertEqual(receipt["schema_version"],"search-planner-inbox-receipt/v1")
        self.assertEqual(receipt["status"],"awaiting_collector")
        self.assertTrue(db.qvdelivered)
        self.assertEqual(db.ack_count,1)
        self.assertEqual([len(c["query_ids"]) for c in db.chunks.values()],
                         [50,50,17])
        self.assertEqual(len(db.links),117)
        self.assertEqual({c["candidate_id"] for c in
                          db.input["validated_bundle"]["queries"]},set(db.links))
        self.assertFalse(receipt["collector_accepted"])
        status=get_sp_handoff_status(PID,EID,connect=db.conn)
        self.assertEqual(status["status"],"awaiting_collector")
        self.assertEqual(status["collector_accepted_chunks"],[])
        self.assertEqual(status["delivery_attempts"],[])

    def test_ten_replays_produce_single_event_and_single_qv_ack(self):
        db=self.fresh(51)
        outcomes=[ingest_qv_to_sp_inbox(PID,QV,
                    projector=db.projection,connect=db.conn) for _ in range(10)]
        self.assertFalse(outcomes[0]["reused"])
        self.assertTrue(all(x["reused"] for x in outcomes[1:]))
        self.assertEqual(db.ack_count,1)
        self.assertEqual(len(db.chunks),2)
        self.assertEqual(len(db.links),51)
        self.assertEqual({x["event_id"] for x in outcomes},{EID})

    def test_before_and_after_qv_ack_failpoints_roll_back_entire_inbox(self):
        for phase in ("after_inbox_before_qv_ack","after_qv_ack_before_commit"):
            db=self.fresh(117)
            def crash(stage):
                if stage==phase:raise RuntimeError("QA crash injection")
            with self.subTest(stage=phase),self.assertRaises(RuntimeError):
                ingest_qv_to_sp_inbox(PID,QV,projector=db.projection,
                                       connect=db.conn,failpoint=crash)
            self.assertIsNone(db.event)
            self.assertEqual(db.chunks,{})
            self.assertEqual(db.links,{})
            self.assertFalse(db.qvdelivered)
            self.assertEqual(db.ack_count,0)
            self.assertEqual(db.rollbacks,1)
            receipt=ingest_qv_to_sp_inbox(PID,QV,projector=db.projection,
                                           connect=db.conn)
            self.assertEqual(receipt["status"],"awaiting_collector")
            self.assertEqual(db.ack_count,1)

    def test_zero_selected_is_durable_no_work_and_acks_once(self):
        db=self.fresh(0)
        result=ingest_qv_to_sp_inbox(PID,QV,projector=db.projection,connect=db.conn)
        self.assertEqual(result["status"],"no_work")
        self.assertEqual(result["chunk_count"],0)
        self.assertEqual(db.chunks,{})
        self.assertEqual(db.links,{})
        self.assertEqual(db.ack_count,1)
        self.assertTrue(ingest_qv_to_sp_inbox(PID,QV,projector=db.projection,
                                             connect=db.conn)["reused"])

    def test_delivered_qv_without_prior_durable_inbox_fails_closed(self):
        db=self.fresh(1)
        db.qvdelivered=True
        with self.assertRaisesRegex(PipelineError,"ACK exists without"):
            ingest_qv_to_sp_inbox(PID,QV,projector=db.projection,connect=db.conn)
        self.assertIsNone(db.event)

    def test_corrupt_duplicate_saved_chunk_makes_replay_refuse_ack(self):
        db=self.fresh(51)
        ingest_qv_to_sp_inbox(PID,QV,projector=db.projection,connect=db.conn)
        db.chunks[1]["query_ids"].pop()
        with self.assertRaisesRegex(PipelineError,"chunk SHA/replay conflict"):
            ingest_qv_to_sp_inbox(PID,QV,projector=db.projection,connect=db.conn)

    def test_unavailable_collector_does_not_create_attempt_or_fake_receipt(self):
        db=self.fresh(1)
        ingest_qv_to_sp_inbox(PID,QV,projector=db.projection,connect=db.conn)
        status=deliver_next_held_chunk(PID,EID,connect=db.conn)
        self.assertEqual(status["status"],"awaiting_collector")
        self.assertFalse(status["collector_available"])
        self.assertEqual(db.attempts,{})
        self.assertEqual(db.receipts,{})
        self.assertEqual(db.event["status"],"awaiting_collector")

    def test_without_owner_or_held_plan_cannot_call_port(self):
        db=self.fresh(1)
        ingest_qv_to_sp_inbox(PID,QV,projector=db.projection,connect=db.conn)
        port=MockCollectorAdapter()
        with self.assertRaises(PipelineError):
            deliver_next_held_chunk(PID,EID,collector_port=port,connect=db.conn)
        self.assertEqual(port.calls,0)
        db.approved=False
        with self.assertRaises(PipelineError):
            deliver_next_held_chunk(PID,EID,collector_port=port,
                                    owner_action_guard=OWNER,connect=db.conn)
        self.assertEqual(port.calls,0)

    def test_one_explicit_call_per_chunk_and_separate_real_receipts(self):
        db=self.fresh(117)
        ingest_qv_to_sp_inbox(PID,QV,projector=db.projection,connect=db.conn)
        port=MockCollectorAdapter()
        for index,expected in enumerate(("awaiting_collector",
                                         "awaiting_collector",
                                         "collector_accepted")):
            output=deliver_next_held_chunk(
                PID,EID,collector_port=port,
                owner_action_guard=OWNER,connect=db.conn)
            self.assertEqual(output["status"],expected)
            self.assertEqual(output["chunk_index"],index)
            self.assertEqual(len(db.receipts),index+1)
            self.assertFalse(output["provider_execution_authorized"])
        self.assertEqual(port.calls,3)
        self.assertEqual(len(db.attempts),3)
        self.assertTrue(all(x["status"]=="accepted" for x in db.attempts.values()))
        status=get_sp_handoff_status(PID,EID,connect=db.conn)
        self.assertEqual(status["status"],"collector_accepted")
        self.assertEqual(len(status["collector_accepted_chunks"]),3)
        retry=deliver_next_held_chunk(
            PID,EID,collector_port=port,
            owner_action_guard=OWNER,connect=db.conn)
        self.assertEqual(retry["status"],"collector_accepted")
        self.assertEqual(port.calls,3)
        self.assertEqual(db.ack_count,1)

    def test_lost_response_is_uncertain_and_never_automatically_resubmitted(self):
        db=self.fresh(51)
        ingest_qv_to_sp_inbox(PID,QV,projector=db.projection,connect=db.conn)
        port=MockCollectorAdapter()
        port.fail_after_accept=True
        first=deliver_next_held_chunk(
            PID,EID,collector_port=port,
            owner_action_guard=OWNER,connect=db.conn)
        self.assertEqual(first["status"],"needs_attention")
        self.assertEqual(db.attempts[0]["status"],"uncertain")
        self.assertEqual(port.calls,1)
        again=deliver_next_held_chunk(
            PID,EID,collector_port=port,
            owner_action_guard=OWNER,connect=db.conn)
        self.assertEqual(again["reason"],"previous_submit_result_unknown_no_auto_retry")
        self.assertEqual(port.calls,1)
        resolved=reconcile_uncertain_chunk(
            PID,EID,0,collector_port=port,
            owner_action_guard=OWNER,connect=db.conn)
        self.assertEqual(resolved["status"],"awaiting_collector")
        self.assertEqual(port.calls,1)
        self.assertEqual(db.attempts[0]["status"],"accepted")
        port.fail_after_accept=False
        final=deliver_next_held_chunk(
            PID,EID,collector_port=port,
            owner_action_guard=OWNER,connect=db.conn)
        self.assertEqual(final["status"],"collector_accepted")
        self.assertEqual(port.calls,2)

    def test_reconcile_without_receipt_never_calls_accept(self):
        db=self.fresh(1)
        ingest_qv_to_sp_inbox(PID,QV,projector=db.projection,connect=db.conn)
        port=MockCollectorAdapter()
        db.attempts[0]={"key":"sp12-"+EID+"-0",
                        "status":"uncertain",
                        "sha":db.chunks[0]["chunk_sha256"]}
        result=reconcile_uncertain_chunk(
            PID,EID,0,collector_port=port,
            owner_action_guard=OWNER,connect=db.conn)
        self.assertEqual(result["status"],"needs_attention")
        self.assertEqual(port.calls,0)
        self.assertEqual(db.receipts,{})

    def test_foreign_project_and_missing_event_cannot_fake_waiting_state(self):
        db=self.fresh(1)
        ingest_qv_to_sp_inbox(PID,QV,projector=db.projection,connect=db.conn)
        with self.assertRaises(PipelineError):
            get_sp_handoff_status("b"*32,EID,connect=db.conn)
        with self.assertRaises(PipelineError):
            deliver_next_held_chunk("b"*32,EID,connect=db.conn)
        with self.assertRaises(PipelineError):
            deliver_next_held_chunk(PID,"11111111-1111-4111-8111-111111111111",
                                    connect=db.conn)


if __name__=="__main__":
    unittest.main()
