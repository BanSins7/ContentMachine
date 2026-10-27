"""UA-MOD-16 source-backed revision inbox/outbox, replay and out-of-order QA.

Fake DB simulates only SQL outcomes and transactional rollback, never a
signed/approved QDP/SRE/A/B/C producer. PG17 integration tests live in
test_unified_analytics_transactional_persistence.py and use disposable DB.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError, canonical_hash
from intelligence.unified_analytics.revision_events import (
    RevisionEvent, RevisionOutcome, RevisionEventProcessor,
    EVENT_WIRE, SOURCE_RIGHTS_SQL, QDP_PROOF_SQL,
    SRE_PROOF_SQL, A_PROOF_SQL, B_PROOF_SQL, C_PROOF_SQL,
    INBOX_FIND_SQL, INBOX_INSERT_SQL, PROVENANCE_FIND_SQL,
    QUARANTINE_SQL, SNAPSHOTS_SQL, INVALIDATE_SQL, OUTBOX_INSERT_SQL,
)

NOW=datetime(2026,10,27,12,0,tzinfo=timezone.utc)
PROJECT="a"*32
VIDEO=17
SOURCE="local-fixture-source"
POLICY=5
RUN="00112233-4455-4677-8899-aabbccddeeff"
UUID_A="11112222-3333-4444-8555-666677778888"
SHA="b"*64


def src_row(*,revoked=True,days=30):
    return dict(
        source_id=SOURCE,source_status="revoked" if revoked else "enabled",
        policy_id=POLICY,policy_source_id=SOURCE,policy_active=True,
        can_store_metadata=True,can_analyze_media=True,
        can_store_metrics=True,can_derive_scores=True,
        permissions={k:True for k in (
            "can_store_metadata","can_analyze_media",
            "can_store_metrics","can_derive_scores"
        )},
        retention_days=days,
        retention_policy={"derived_retention_days":days},
        project_link=True,
    )


def source_digest(r):
    return canonical_hash({
        "source":SOURCE,"policy":POLICY,
        "status":r["source_status"],"active":r["policy_active"],
        "rights":[r[x] for x in (
            "can_store_metadata","can_analyze_media",
            "can_store_metrics","can_derive_scores"
        )],"permissions":r["permissions"],
        "retention_days":r["retention_days"],
        "retention_policy":r["retention_policy"],
    })


def event(*,kind="source_policy_revoked",record_id=None,revision=0,
          sha=None,when=NOW,event_id=UUID_A):
    rid=str(POLICY) if record_id is None else record_id
    return RevisionEvent(
        PROJECT,VIDEO,SOURCE,POLICY,RUN,event_id,kind,rid,
        revision,sha if sha is not None else source_digest(src_row()),
        when,
    )


class Cursor:
    def __init__(self,rows): self.rows=rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows


class FakeDB:
    def __init__(self):
        self.source=src_row()
        self.proofs={}
        self.snapshots=[
            dict(id=1,status="partial",qdp_run_id=RUN,
                 verified_as_of=NOW-timedelta(days=2),
                 sre_run_id=RUN,group_a_analysis_id=100,
                 group_b_analysis_id=200,calculated_metric_id=300,
                 payload={"versions":{"sre_taxonomy_version_id":3,
                     "group_a_result_sha256":"1"*64}}),
            dict(id=2,status="blocked",qdp_run_id=RUN,
                 verified_as_of=NOW+timedelta(minutes=1),
                 sre_run_id=RUN,group_a_analysis_id=101,
                 group_b_analysis_id=201,calculated_metric_id=301,
                 payload={"versions":{"sre_taxonomy_version_id":5,
                     "group_a_result_sha256":"2"*64}}),
        ]
        self.inbox={}
        self.quarantine=[]
        self.invalidations={}
        self.outbox=[]
        self.info=SimpleNamespace(transaction_status=0)
        self.fault=None
        self.queries=[]
    @contextmanager
    def transaction(self):
        prior=copy.deepcopy((
            self.inbox,self.quarantine,self.invalidations,self.outbox
        ))
        try: yield
        except Exception:
            self.inbox,self.quarantine,self.invalidations,self.outbox=prior
            raise
    def execute(self,sql,args=()):
        self.queries.append((sql,args))
        if self.fault==sql:
            raise RuntimeError("database password=secret-never-leak")
        if sql=="SET TRANSACTION ISOLATION LEVEL READ COMMITTED":
            return Cursor([])
        if sql==SOURCE_RIGHTS_SQL:
            return Cursor([self.source] if self.source else [])
        if sql in (QDP_PROOF_SQL,SRE_PROOF_SQL,A_PROOF_SQL,B_PROOF_SQL,C_PROOF_SQL):
            r=self.proofs.get(sql)
            return Cursor([r] if r else [])
        if sql==INBOX_FIND_SQL:
            row=self.inbox.get((args[0],args[1],args[2]))
            return Cursor([row] if row else [])
        if sql==PROVENANCE_FIND_SQL:
            for x in self.inbox.values():
                if all(x[k]==v for k,v in zip((
                    "project_id","producer","event_kind","producer_record_id",
                    "producer_revision","producer_sha256",
                ),args)):
                    return Cursor([x])
            return Cursor([])
        if sql==INBOX_INSERT_SQL:
            project,eid,vid,source,policy,producer,kind,rid,seq,sha,hash_,at=args
            key=(project,producer,eid)
            if key in self.inbox:
                return Cursor([])
            for x in self.inbox.values():
                if (x["project_id"],x["producer"],x["event_kind"],
                    x["producer_record_id"],x["producer_revision"],
                    x["producer_sha256"]) == (project,producer,kind,rid,seq,sha):
                    return Cursor([])
            self.inbox[key]=dict(
                project_id=project,producer=producer,event_kind=kind,
                event_id=eid,producer_record_id=rid,producer_revision=seq,
                producer_sha256=sha,event_sha256=hash_,
            )
            return Cursor([{"event_sha256":hash_}])
        if sql==QUARANTINE_SQL:
            self.quarantine.append(args)
            return Cursor([])
        if sql==SNAPSHOTS_SQL:
            return Cursor(self.snapshots)
        if sql==INVALIDATE_SQL:
            project,sid,reason,digest=args
            key=(project,sid,digest)
            if key in self.invalidations:
                return Cursor([])
            ident=str(uuid4())
            self.invalidations[key]=ident
            return Cursor([{"id":ident}])
        if sql==OUTBOX_INSERT_SQL:
            self.outbox.append(args)
            return Cursor([])
        raise AssertionError("Unexpected SQL route")


def qdp_proof(*,revision=8,when=NOW):
    return dict(record_id=str(revision),project_id=PROJECT,run_id=RUN,
                video_id=VIDEO,analysis_group="C",
                evidence_sha256=SHA,revision=revision,recorded_at=when)


class RevisionUnitTests(unittest.TestCase):
    def test_source_revocation_invalidation_is_atomic_and_replay_safe(self):
        db=FakeDB()
        processor=RevisionEventProcessor(db,as_of=NOW)
        original=event()
        first=processor.consume(original)
        self.assertEqual(first.state,"accepted")
        self.assertEqual(first.invalidated_snapshot_ids,(1,2))
        self.assertEqual(first.outbox_created,2)
        self.assertEqual(len(db.inbox),1)
        self.assertEqual(len(db.invalidations),2)
        self.assertEqual(len(db.outbox),2)
        again=processor.consume(original)
        self.assertEqual((again.state,again.outbox_created),("replayed",0))
        self.assertEqual(len(db.outbox),2)
        self.assertEqual(db.snapshots[0]["status"],"partial")

    def test_delivery_new_event_uuid_for_same_producer_fact_is_idempotent(self):
        db=FakeDB()
        processor=RevisionEventProcessor(db,as_of=NOW)
        self.assertEqual(processor.consume(event()).state,"accepted")
        alternate=event(event_id="22223333-4444-4555-8666-777788889999")
        second=processor.consume(alternate)
        self.assertEqual(second.state,"replayed")
        self.assertEqual(len(db.inbox),1)
        self.assertEqual(len(db.outbox),2)

    def test_same_event_id_different_producer_sha_is_quarantined(self):
        db=FakeDB()
        processor=RevisionEventProcessor(db,as_of=NOW)
        processor.consume(event())
        wrong=event(sha="f"*64)
        out=processor.consume(wrong)
        self.assertEqual(out.state,"quarantined")
        self.assertEqual(out.reason_code,"event_identity_collision")
        self.assertEqual(len(db.quarantine),1)
        self.assertEqual(len(db.outbox),2)

    def test_unrevoked_source_fails_to_authenticate(self):
        db=FakeDB()
        db.source=src_row(revoked=False)
        e=event(sha=source_digest(db.source))
        result=RevisionEventProcessor(db,as_of=NOW).consume(e)
        self.assertEqual(result.state,"unverified")
        self.assertEqual(len(db.inbox),0)
        self.assertEqual(len(db.outbox),0)

    def test_source_scope_membership_and_policy_are_db_verified(self):
        for changed in ("project_link","source"):
            db=FakeDB()
            if changed=="project_link": db.source["project_link"]=False
            else: db.source=None
            e=event()
            result=RevisionEventProcessor(db,as_of=NOW).consume(e)
            self.assertEqual(result.state,"unverified")
            self.assertEqual(len(db.invalidations),0)

    def test_retention_only_hits_expired_snapshot_with_reviewed_limit(self):
        db=FakeDB()
        db.source=src_row(revoked=False,days=1)
        e=event(kind="source_retention_expired",
                sha=source_digest(db.source))
        out=RevisionEventProcessor(db,as_of=NOW).consume(e)
        self.assertEqual(out.state,"accepted")
        self.assertEqual(out.invalidated_snapshot_ids,(1,))
        self.assertEqual(len(db.outbox),1)
        self.assertEqual(db.snapshots[1]["status"],"blocked")

    def test_unapproved_missing_retention_cannot_be_guessed(self):
        db=FakeDB()
        db.source=src_row(days=0)
        result=RevisionEventProcessor(db,as_of=NOW).consume(
            event(kind="source_retention_expired",sha=source_digest(db.source))
        )
        self.assertEqual(result.state,"unverified")
        self.assertFalse(db.inbox)

    def test_qdp_invalidated_rechecks_exact_run_sha_and_out_of_order(self):
        db=FakeDB()
        db.proofs[QDP_PROOF_SQL]=qdp_proof()
        e=event(kind="qdp_invalidated",record_id="8",
                revision=8,sha=SHA)
        result=RevisionEventProcessor(db,as_of=NOW).consume(e)
        self.assertEqual(result.invalidated_snapshot_ids,(1,))
        self.assertEqual(result.outbox_created,1)
        self.assertEqual(len(db.outbox),1)

    def test_qdp_wrong_provenance_or_run_does_not_invalidate(self):
        for attr,val in (("evidence_sha256","c"*64),("run_id",UUID_A),
                         ("project_id","b"*32)):
            db=FakeDB()
            db.proofs[QDP_PROOF_SQL]=qdp_proof()
            db.proofs[QDP_PROOF_SQL][attr]=val
            out=RevisionEventProcessor(db,as_of=NOW).consume(
                event(kind="qdp_invalidated",record_id="8",
                      revision=8,sha=SHA)
            )
            self.assertEqual(out.state,"unverified")
            self.assertFalse(db.invalidations)

    def test_missing_peer_table_fails_closed_unavailable(self):
        db=FakeDB()
        db.fault=C_PROOF_SQL
        e=event(kind="group_c_revised",record_id=UUID_A,
                revision=3,sha=SHA)
        out=RevisionEventProcessor(db,as_of=NOW).consume(e)
        self.assertEqual(out.state,"unavailable")
        self.assertEqual(out.reason_code,"revision_producer_unavailable")
        self.assertFalse(db.inbox)

    def test_group_c_cohort_change_invalidates_pinned_previous_score(self):
        db=FakeDB()
        db.proofs[C_PROOF_SQL]=dict(
            record_id=UUID_A,project_id=PROJECT,video_id=VIDEO,source_id=SOURCE,
            source_policy_version_id=POLICY,revision=3,
            evidence_sha256=SHA,recorded_at=NOW,
            previous_calculated_metric_id=300,c_run_id=RUN,
        )
        e=event(kind="group_c_revised",record_id=UUID_A,
                revision=3,sha=SHA)
        out=RevisionEventProcessor(db,as_of=NOW).consume(e)
        self.assertEqual(out.invalidated_snapshot_ids,(1,))
        self.assertEqual(len(db.outbox),1)

    def test_group_c_new_revision_rerun_never_rewrites_newer_snapshots(self):
        db=FakeDB()
        db.proofs[C_PROOF_SQL]=dict(
            record_id=UUID_A,project_id=PROJECT,video_id=VIDEO,source_id=SOURCE,
            source_policy_version_id=POLICY,revision=9,
            evidence_sha256=SHA,recorded_at=NOW,
            previous_calculated_metric_id=None,c_run_id=RUN,
        )
        out=RevisionEventProcessor(db,as_of=NOW).consume(event(
            kind="group_c_revised",record_id=UUID_A,
            revision=9,sha=SHA,
        ))
        self.assertEqual(out.invalidated_snapshot_ids,(1,))
        self.assertEqual(db.snapshots[1]["status"],"blocked")

    def test_group_b_review_newer_than_old_output_only(self):
        db=FakeDB()
        db.proofs[B_PROOF_SQL]=dict(
            record_id=UUID_A,project_id=PROJECT,video_id=VIDEO,source_id=SOURCE,
            source_policy_version_id=POLICY,revision=5,
            evidence_sha256=SHA,recorded_at=NOW,result_analysis_id=200,
        )
        out=RevisionEventProcessor(db,as_of=NOW).consume(event(
            kind="group_b_reviewed",record_id=UUID_A,revision=5,sha=SHA,
        ))
        self.assertEqual(out.invalidated_snapshot_ids,(1,))

    def test_group_a_different_result_sha_revokes_only_older_A(self):
        db=FakeDB()
        db.proofs[A_PROOF_SQL]=dict(
            record_id="8",project_id=PROJECT,video_id=VIDEO,source_id=SOURCE,
            source_policy_version_id=POLICY,revision=8,
            evidence_sha256=SHA,recorded_at=NOW,result_sha256="a"*64,
        )
        out=RevisionEventProcessor(db,as_of=NOW).consume(event(
            kind="group_a_result_ready",record_id="8",revision=8,sha=SHA,
        ))
        self.assertEqual(out.invalidated_snapshot_ids,(1,))

    def test_sre_new_approved_taxonomy_revokes_old_pinned_taxonomy(self):
        db=FakeDB()
        db.proofs[SRE_PROOF_SQL]=dict(
            record_id="12",project_id=PROJECT,revision=5,
            evidence_sha256=SHA,recorded_at=NOW,
        )
        out=RevisionEventProcessor(db,as_of=NOW).consume(event(
            kind="sre_taxonomy_approved",record_id="12",revision=5,sha=SHA,
        ))
        self.assertEqual(out.invalidated_snapshot_ids,(1,))

    def test_bad_source_times_and_ids_rejected(self):
        with self.assertRaises(AnalyticsBoundaryError):
            replace(event(),video_id=0)
        with self.assertRaises(AnalyticsBoundaryError):
            replace(event(),observed_at=datetime(2026,10,27))
        with self.assertRaises(AnalyticsBoundaryError):
            replace(event(),producer_sha256="bad")
        with self.assertRaises(AnalyticsBoundaryError):
            replace(event(),kind="unknown")
        with self.assertRaises(AnalyticsBoundaryError):
            replace(event(kind="group_c_revised",record_id=UUID_A),
                    producer_record_id="bad")

    def test_future_event_is_not_applied(self):
        db=FakeDB()
        out=RevisionEventProcessor(db,as_of=NOW).consume(event(
            when=NOW+timedelta(seconds=1)
        ))
        self.assertEqual(out.state,"unverified")
        self.assertFalse(db.inbox)

    def test_mid_outbox_failure_rolls_back_inbox_and_invalidations(self):
        db=FakeDB()
        db.fault=OUTBOX_INSERT_SQL
        with self.assertRaises(AnalyticsBoundaryError) as err:
            RevisionEventProcessor(db,as_of=NOW).consume(event())
        self.assertEqual(err.exception.code,"revision_transaction_failed")
        self.assertNotIn("password",str(err.exception))
        self.assertFalse(db.inbox)
        self.assertFalse(db.invalidations)
        self.assertFalse(db.outbox)

    def test_nonidle_transaction_denied(self):
        db=FakeDB()
        db.info.transaction_status=2
        with self.assertRaises(AnalyticsBoundaryError) as err:
            RevisionEventProcessor(db,as_of=NOW).consume(event())
        self.assertEqual(err.exception.code,"revision_fresh_transaction_required")

    def test_historical_snapshot_is_never_updated(self):
        db=FakeDB()
        historical=copy.deepcopy(db.snapshots)
        RevisionEventProcessor(db,as_of=NOW).consume(event())
        self.assertEqual(historical,db.snapshots)
        self.assertTrue(all(sql.find("UPDATE uae_unified_snapshots")==-1
                            for sql,_ in db.queries))

    def test_no_silent_db_schema_fallback_to_unverified_peer(self):
        db=FakeDB()
        db.fault=SRE_PROOF_SQL
        res=RevisionEventProcessor(db,as_of=NOW).consume(event(
            kind="sre_taxonomy_approved",record_id="12",
            revision=5,sha=SHA,
        ))
        self.assertEqual(res.state,"unavailable")
        self.assertFalse(db.inbox)


class RevisionMigrationStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=(Path(__file__).resolve().parents[1]/
                 "intelligence/migrations/087_unified_analytics_revision_events.sql"
                 ).read_text(encoding="utf-8").lower()

    def test_durable_scope_scoped_event_and_revision_uniques(self):
        self.assertIn("primary key(project_id,producer,event_id)",self.sql)
        self.assertIn("unique(project_id,producer,event_kind,producer_record_id,",self.sql)
        self.assertIn("references sre_project_videos(project_id,video_id)",self.sql)
        self.assertIn("unique(project_id,invalidation_id)",self.sql)

    def test_inbox_and_quarantine_are_immutable(self):
        self.assertIn("uae_revision_inbox_immutable",self.sql)
        self.assertIn("uae_revision_quarantine_immutable",self.sql)
        self.assertIn("uae_revision_outbox_identity_guard",self.sql)

    def test_outbox_scoped_to_real_immutable_invalidation(self):
        self.assertIn("uae_revision_outbox_invalidation_guard",self.sql)
        self.assertIn("i.snapshot_id=new.snapshot_id",self.sql)
        self.assertIn("i.project_id=new.project_id",self.sql)
        self.assertNotIn("grant select",self.sql)
        self.assertNotIn("create table calculated_metrics",self.sql)


if __name__=="__main__":
    unittest.main()
