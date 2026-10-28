"""M34 SRE-only offline revocation contracts; not production PG17 acceptance."""
from __future__ import annotations
from contextlib import nullcontext
from datetime import datetime,timezone
from unittest.mock import patch
import unittest

from intelligence.relevance.live_source_revocation import (
    record_sre_source_revocation,record_sre_source_policy_revocation,
    process_sre_source_revocation_outbox,inspect_sre_source_revocation,
    audit_sre_source_revocation_remnants,
    record_expired_sre_content_revocations,
    SQL_WORKSPACE,SQL_WORKSPACE_READ,SQL_CONTENT,SQL_INSERT,SQL_EXISTS,
    SQL_CACHE_LEFT,SQL_COUNTS,SQL_PENDING,SQL_ACK,SQL_READ_OUTBOX,SQL_SCOPE,
    SQL_AUDIT,SQL_POLICY_CLOSED,SQL_EXPIRED,
)
from intelligence.relevance.v1_contracts import SREContractError

PID="a"*32
CID=12
POLICY=20
SHA="f"*64
RUN="11111111-1111-4111-8111-111111111111"
WHEN=datetime(2026,10,28,8,0,tzinfo=timezone.utc)
PERMS=frozenset((
    "revoke_sre_source","process_sre_source_revocation",
    "read_sre_source_revocation",
))

def allowed(pid,actor,action):
    return pid==PID and actor=="owner:77" and action in PERMS

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
    def __init__(self):
        self.tombstones={}
        self.caches={CID:7}
        self.outbox=[dict(event=1,cid=CID,run=RUN,state="pending",
                          run_state="running",cancel=False,who=None)]
        self.commands=[]
        self.policy_rows=[(CID,SHA)]
        self.policy_closed=True
        self.expired_rows=[(CID,SHA)]
    def transaction(self):return nullcontext()
    def execute(self,sql,params=()):
        self.commands.append(sql)
        if sql=="SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY":
            return Cursor()
        if sql in (SQL_WORKSPACE,SQL_WORKSPACE_READ):
            return Cursor((1,))
        if sql==SQL_CONTENT:
            return Cursor((333,SHA,POLICY) if params[1]==CID else None)
        if sql==SQL_INSERT:
            pid,cid,video,sha,policy,reason,actor=params
            if cid in self.tombstones:
                return Cursor(None)
            self.tombstones[cid]=(video,sha,policy,reason,WHEN)
            self.caches[cid]=0
            return Cursor((cid,))
        if sql==SQL_EXISTS:
            return Cursor(self.tombstones.get(params[1]))
        if sql==SQL_CACHE_LEFT:
            return Cursor((self.caches.get(params[1],0),))
        if sql==SQL_COUNTS:
            counts={}
            for e in self.outbox:
                if e["cid"]==params[1]:
                    counts[e["state"]]=counts.get(e["state"],0)+1
            return Cursor(rows=sorted(counts.items()))
        if sql==SQL_PENDING:
            cid=params[1]
            return Cursor(rows=[
                (e["event"],e["run"],e["run_state"],e["cancel"])
                for e in self.outbox if e["cid"]==cid and e["state"]=="pending"
            ][:params[2]])
        if sql==SQL_ACK:
            _,_,cid,event=params
            for e in self.outbox:
                if e["cid"]==cid and e["event"]==event and e["state"]=="pending":
                    safe_terminal=e["run_state"] in (
                        "completed","partial","cancelled","failed")
                    safe_unknown=(e["run_state"]=="unknown" and e["cancel"])
                    if not safe_terminal and not safe_unknown:
                        raise AssertionError("SQL guard refused active run ACK")
                    e["state"]="acknowledged"
                    return Cursor((event,))
            return Cursor(None)
        if sql==SQL_READ_OUTBOX:
            return Cursor(rows=[
                (e["run"],e["state"],WHEN,
                 WHEN if e["state"]=="acknowledged" else None)
                for e in self.outbox if e["cid"]==params[1]
            ])
        if sql==SQL_EXPIRED:
            return Cursor(rows=[
                (cid,digest) for cid,digest in self.expired_rows
                if cid not in self.tombstones
            ][:params[1]])
        if sql==SQL_POLICY_CLOSED:
            return Cursor((1,) if self.policy_closed else None)
        if sql==SQL_SCOPE:
            return Cursor(rows=self.policy_rows)
        if sql==SQL_AUDIT:
            return Cursor((
                self.caches.get(CID,0),
                getattr(self,"remaining_offline_items",0),
                getattr(self,"remaining_fixture_results",0),
                getattr(self,"remaining_stage_executions",0),
                getattr(self,"readable_d1",0),
                getattr(self,"visible_live_results",0),
                getattr(self,"original_payload_count",1),1,
                getattr(self,"unrevoked_pre_dispatch_consents",0),1,
                getattr(self,"releasable_pre_dispatch_holds",0),
                getattr(self,"protected_paid_or_unknown_holds",0),
                0,0,0,
                sum(x["state"]=="pending" for x in self.outbox),
            ))
        raise AssertionError("Unexpected SQL "+sql[:150])

class M34SourceRevocation(unittest.TestCase):
    def test_tombstone_vector_purge_is_idempotent_and_permanent(self):
        db=FakeDB()
        first=record_sre_source_revocation(
            db,PID,CID,expected_content_sha256=SHA,
            reason_code="rights_revoked",actor_id="owner:77",
            actor_authorizer=allowed)
        self.assertTrue(first["tombstone_created"])
        self.assertEqual(first["local_embedding_vectors_remaining"],0)
        self.assertEqual(first["cancellation_outbox_pending"],1)
        self.assertFalse(first["source_content_physically_erased"])
        self.assertFalse(first["financial_audit_physically_erased"])
        self.assertEqual(first["paid_post_calls"],0)
        second=record_sre_source_revocation(
            db,PID,CID,expected_content_sha256=SHA,
            reason_code="privacy_delete",actor_id="owner:77",
            actor_authorizer=allowed)
        self.assertFalse(second["tombstone_created"])
        self.assertEqual(second["reason_code"],"rights_revoked")
        self.assertEqual(len(db.tombstones),1)
        self.assertEqual(db.caches[CID],0)

    def test_cancel_and_ack_only_when_existing_M15_run_is_safe(self):
        db=FakeDB()
        record_sre_source_revocation(
            db,PID,CID,expected_content_sha256=SHA,
            reason_code="rights_revoked",actor_id="owner:77",
            actor_authorizer=allowed)
        def stop(conn,pid,run):
            self.assertEqual((pid,run),(PID,RUN))
            db.outbox[0]["run_state"]="cancelled"
            db.outbox[0]["cancel"]=True
            return {"status":"cancelled"}
        with patch("intelligence.relevance.live_source_revocation.cancel_live_run",
                   side_effect=stop) as cancel:
            result=process_sre_source_revocation_outbox(
                db,PID,CID,actor_id="owner:77",actor_authorizer=allowed)
            self.assertEqual(cancel.call_count,1)
        self.assertEqual(result["pending"],0)
        self.assertEqual(result["acknowledged_in_this_call"],1)
        self.assertFalse(result["full_physical_deletion_complete"])
        again=process_sre_source_revocation_outbox(
            db,PID,CID,actor_id="owner:77",actor_authorizer=allowed)
        self.assertEqual(again["acknowledged_in_this_call"],0)
        self.assertTrue(again["outbox_fully_acknowledged"])
        shown=inspect_sre_source_revocation(
            db,PID,CID,actor_id="owner:77",actor_authorizer=allowed)
        self.assertEqual(shown["run_outbox"][0]["state"],"acknowledged")
        self.assertFalse(shown["source_text_exposed"])
        self.assertFalse(shown["source_content_physically_erased"])

    def test_immediate_stop_flag_cannot_ack_as_cancelled_without_M15(self):
        db=FakeDB()
        record_sre_source_revocation(
            db,PID,CID,expected_content_sha256=SHA,
            reason_code="rights_revoked",actor_id="owner:77",
            actor_authorizer=allowed)
        db.outbox[0]["cancel"]=True  # M34 immediate stop, still running
        with self.assertRaisesRegex(AssertionError,"active run ACK"):
            db.execute(SQL_ACK,("owner:77",PID,CID,1))
        self.assertEqual(db.outbox[0]["state"],"pending")
        db.outbox[0]["run_state"]="unknown"  # M15 fenced UNKNOWN
        self.assertEqual(db.execute(
            SQL_ACK,("owner:77",PID,CID,1)).fetchone(),(1,))
        self.assertEqual(db.outbox[0]["state"],"acknowledged")

    def test_unknown_no_cancel_never_reposted_or_acknowledged(self):
        db=FakeDB()
        record_sre_source_revocation(
            db,PID,CID,expected_content_sha256=SHA,
            reason_code="source_deleted",actor_id="owner:77",
            actor_authorizer=allowed)
        db.outbox[0]["run_state"]="unknown"
        with patch("intelligence.relevance.live_source_revocation.cancel_live_run",
                   side_effect=AssertionError("Paid retry forbidden")):
            result=process_sre_source_revocation_outbox(
                db,PID,CID,actor_id="owner:77",actor_authorizer=allowed)
        self.assertEqual(result["pending"],1)
        self.assertEqual(result["blocked_runs"][0]["reason_code"],
                         "possibly_paid_unknown_needs_operator")
        self.assertFalse(result["paid_retry_allowed"])

    def test_crash_during_cancellation_replay_without_data_recreation(self):
        db=FakeDB()
        record_sre_source_revocation(
            db,PID,CID,expected_content_sha256=SHA,
            reason_code="retention_expired",actor_id="owner:77",
            actor_authorizer=allowed)
        with patch("intelligence.relevance.live_source_revocation.cancel_live_run",
                   side_effect=SREContractError(
                       "invalid_transition","run_id","M15 unresolved")):
            res=process_sre_source_revocation_outbox(
                db,PID,CID,actor_id="owner:77",actor_authorizer=allowed)
        self.assertEqual(res["pending"],1)
        self.assertEqual(res["acknowledged_in_this_call"],0)
        db.outbox[0]["run_state"]="cancelled"
        replay=process_sre_source_revocation_outbox(
            db,PID,CID,actor_id="owner:77",actor_authorizer=allowed)
        self.assertEqual(replay["pending"],0)

    def test_exact_aggregate_audit_reports_retained_payload_and_paid_audit(self):
        db=FakeDB()
        record_sre_source_revocation(
            db,PID,CID,expected_content_sha256=SHA,
            reason_code="privacy_delete",actor_id="owner:77",
            actor_authorizer=allowed)
        report=audit_sre_source_revocation_remnants(
            db,PID,CID,actor_id="owner:77",actor_authorizer=allowed)
        self.assertEqual(report["counts"]["local_embedding_vectors"],0)
        self.assertEqual(report["counts"]["offline_fixture_stage_executions"],0)
        self.assertEqual(report["counts"]["original_s3_content_payloads"],1)
        self.assertEqual(report["counts"]["immutable_provider_receipts"],1)
        self.assertEqual(report["counts"]["unrevoked_pre_dispatch_consents"],0)
        self.assertEqual(report["counts"]["releasable_pre_dispatch_holds"],0)
        self.assertEqual(report["counts"]["protected_paid_or_unknown_holds"],0)
        self.assertFalse(report["paid_unknown_hold_protected"])
        self.assertTrue(report["disposable_purge_verified"])
        self.assertTrue(report["post_revocation_sre_views_hidden"])
        self.assertEqual(report["remaining_protective_invariant_violations"],[])
        self.assertFalse(report["source_content_physically_erased"])
        self.assertTrue(report["original_s3_payload_detected"])
        self.assertFalse(report["source_content_erasure_verified"])
        # Absence of a source payload is NOT proof that this service
        # physically erased it; the original S3 row may have been empty.
        db.original_payload_count=0
        empty=audit_sre_source_revocation_remnants(
            db,PID,CID,actor_id="owner:77",actor_authorizer=allowed)
        self.assertFalse(empty["original_s3_payload_detected"])
        self.assertFalse(empty["source_content_physically_erased"])
        self.assertFalse(empty["source_content_erasure_verified"])
        self.assertFalse(report["backup_and_log_retention_verified"])
        self.assertFalse(report["full_physical_deletion_complete"])
        self.assertEqual(report["paid_post_calls"],0)
        self.assertEqual(db.commands[-2],SQL_EXISTS)
        self.assertEqual(db.commands[-1],SQL_AUDIT)
        db.unrevoked_pre_dispatch_consents=2
        db.releasable_pre_dispatch_holds=1
        db.protected_paid_or_unknown_holds=2
        db.readable_d1=1
        db.remaining_stage_executions=1
        db.remaining_offline_items=2
        failure=audit_sre_source_revocation_remnants(
            db,PID,CID,actor_id="owner:77",actor_authorizer=allowed)
        self.assertEqual(set(failure["remaining_protective_invariant_violations"]),
                         {"offline_item_contexts","offline_fixture_stage_executions",
                          "readable_d1_versions","releasable_pre_dispatch_holds",
                          "unrevoked_pre_dispatch_consents"})
        self.assertFalse(failure["disposable_purge_verified"])
        self.assertFalse(failure["post_revocation_sre_views_hidden"])
        self.assertTrue(failure["paid_unknown_hold_protected"])
        with self.assertRaises(SREContractError):
            audit_sre_source_revocation_remnants(
                db,PID,CID,actor_id="worker:1",actor_authorizer=allowed)
        with self.assertRaises(SREContractError):
            audit_sre_source_revocation_remnants(
                db,PID,CID+1,actor_id="owner:77",actor_authorizer=allowed)

    def test_expired_version_sweeper_bounded_replay_never_automatic(self):
        db=FakeDB()
        first=record_expired_sre_content_revocations(
            db,PID,actor_id="owner:77",actor_authorizer=allowed,
            max_versions=1)
        self.assertEqual(first["newly_tombstoned_versions"],1)
        self.assertEqual(db.tombstones[CID][3],"retention_expired")
        self.assertTrue(first["explicit_host_invocation_required"])
        self.assertFalse(first["all_downstream_run_cancellations_processed"])
        second=record_expired_sre_content_revocations(
            db,PID,actor_id="owner:77",actor_authorizer=allowed,
            max_versions=1)
        self.assertEqual(second["newly_tombstoned_versions"],0)
        self.assertEqual(second["scanned_expired_unrevoked"],0)
        self.assertFalse(second["more_expired_versions_possible"])
        with self.assertRaises(SREContractError):
            record_expired_sre_content_revocations(
                db,PID,actor_id="owner:77",actor_authorizer=allowed,
                max_versions=101)
        with self.assertRaises(SREContractError):
            record_expired_sre_content_revocations(
                db,PID,actor_id="bot:1",actor_authorizer=allowed)

    def test_owner_hash_policy_scope_and_explicit_reason(self):
        db=FakeDB()
        with self.assertRaises(SREContractError):
            record_sre_source_revocation(
                db,PID,CID,expected_content_sha256="0"*64,
                reason_code="rights_revoked",actor_id="owner:77",
                actor_authorizer=allowed)
        with self.assertRaises(SREContractError):
            record_sre_source_revocation(
                db,PID,CID,expected_content_sha256=SHA,
                reason_code="arbitrary user note",actor_id="owner:77",
                actor_authorizer=allowed)
        with self.assertRaises(SREContractError):
            record_sre_source_revocation(
                db,PID,CID,expected_content_sha256=SHA,
                reason_code="rights_revoked",actor_id="worker:1",
                actor_authorizer=allowed)
        batch=record_sre_source_policy_revocation(
            db,PID,POLICY,reason_code="rights_revoked",
            actor_id="owner:77",actor_authorizer=allowed)
        self.assertEqual(batch["tombstones_created"],1)
        self.assertEqual(batch["source_versions_processed"],1)
        replay=record_sre_source_policy_revocation(
            db,PID,POLICY,reason_code="rights_revoked",
            actor_id="owner:77",actor_authorizer=allowed)
        self.assertEqual(replay["tombstones_created"],0)
        db.policy_closed=False
        with self.assertRaises(SREContractError) as still_active:
            record_sre_source_policy_revocation(
                db,PID,POLICY,reason_code="rights_revoked",
                actor_id="owner:77",actor_authorizer=allowed)
        self.assertEqual(still_active.exception.code,"rights_denied")
        db.policy_closed=True
        db.policy_rows=[(CID,SHA)]*1001
        with self.assertRaises(SREContractError):
            record_sre_source_policy_revocation(
                db,PID,POLICY,reason_code="rights_revoked",
                actor_id="owner:77",actor_authorizer=allowed)

if __name__=="__main__":
    unittest.main()
