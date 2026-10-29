"""GB-MOD-24 optional disposable PG17 atomic invalidation & refresh tests.

Every current QDP/GA/media receipt and reviewer identity below is a synthetic
TEST DOUBLE, never live approval for Analytics, paid execution or production.
"""
from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4
import os
import unittest

from intelligence.migrate import apply_migrations
from intelligence.group_b.review import make_decision,append_review,gold_sample_key
from intelligence.group_b.integration import (
    InvalidationError,INVALIDATED,REFRESH_REQUESTED,REFRESH_DRAFT_CREATED,
    FreshnessState,make_current_lineage,read_pinned_lineage,
    invalidate_run,request_refresh,create_refresh_draft,assess_current_run,
    read_verified_current_result,read_pending_outbox,
    acknowledge_outbox_delivery,
)
from intelligence.group_b.persistence import read_group_b_result
from intelligence.group_b.orchestrator import enqueue_jobs,claim_local
from intelligence.group_b.dependency_rules import (
    BLOCKS,ReadinessReport,BlockReadiness,ReadinessState,
)
from intelligence.group_b.errors import DependencyUnavailable
import test_group_b_atomic_db as fixture


def test_dsn():
    from psycopg.conninfo import conninfo_to_dict
    dsn=os.environ["CM_TEST_PG_DSN"]
    db=conninfo_to_dict(dsn).get("dbname","").lower()
    if "test" not in db and "_ci" not in db:
        raise RuntimeError("GB24 refuses non-disposable PostgreSQL")
    return dsn


class SyntheticHost:
    def __init__(self,allowed=True):
        self.allowed=allowed
        self.calls=[]
    def verify_current_lineage(self,project,run,pins,current,conn):
        self.calls.append("freshness")
        return (self.allowed and current.verify_digest() and
                current.project_id==project and pins.run_id==run and
                pins.video_id==current.video_id and
                pins.source_id==current.source_id)
    def verify_invalidation_admission(self,project,run,pins,current,verdict,conn):
        self.calls.append("invalidation")
        return self.allowed and verdict.reasons!=()
    def verify_refresh_request(self,project,run,pins,source_event,conn):
        self.calls.append("refresh")
        return self.allowed and source_event["qualified_for_downstream"] is False
    def verify_outbox_reader(self,project_id,conn):
        self.calls.append("outbox-read")
        return self.allowed and bool(project_id)
    def verify_consumer_delivery(self,project,event,receipt,conn):
        self.calls.append("outbox-ack")
        return self.allowed and receipt=="ack:"+event.payload_sha256
    def verify_refresh_draft(self,project,run,pins,new_qdp,config,event_digest,conn):
        self.calls.append("draft")
        return (self.allowed and new_qdp["status"]=="accepted" and
                new_qdp["project_id"]==project)
    def verify_human_review_admission(self,project,run,reviewer,decision,
                                      original,effective,history):
        self.calls.append("review")
        return self.allowed and reviewer=="synthetic-reviewer" and bool(original)


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"No disposable PostgreSQL17")
class TestB24PG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg=psycopg
        cls.dsn=test_dsn()
        with psycopg.connect(cls.dsn) as conn:
            apply_migrations(conn)

    def prepare(self):
        base=fixture.TestAtomicGroupBPG17("runTest")
        base.psycopg=self.psycopg
        base.dsn=self.dsn
        args=base.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            persisted=base.write(conn,args)
        return args[0],persisted

    def current(self,conn,manifest,*,changed_media=True,rights=True):
        pins,_=read_pinned_lineage(
            conn,project_id=manifest.project_id,run_id=manifest.b_run_id
        )
        return make_current_lineage(
            project_id=pins.project_id,video_id=pins.video_id,
            source_id=pins.source_id,content_type=pins.content_type,
            qdp_run_id=pins.qdp_run_id,
            qdp_input_sha256=pins.qdp_input_sha256,
            ga_result_id=pins.ga_result_id or 999,
            ga_result_sha256=pins.ga_result_sha256,
            media_manifest_sha256=("f"*64 if changed_media
                                   else pins.media_manifest_sha256),
            source_policy_version_id=pins.source_policy_version_id,
            source_rights_active=rights,
            source_media_bytes_current=rights,
            qdp_accepted_and_current=True,ga_completed_and_current=True,
        )

    def test_atomic_invalidate_idempotency_jobs_fencing_and_history(self):
        m,persisted=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                """INSERT INTO group_b_jobs(project_id,run_id,block,kind,
                     evidence_sha256,max_attempts)
                   VALUES (%s,%s::uuid,'B5','local',%s,3)""",
                (m.project_id,m.b_run_id,m.manifest_sha256)
            )
            now=self.current(conn,m)
            result=invalidate_run(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                current=now,verifier=SyntheticHost(),
            )
        self.assertFalse(result.replayed)
        self.assertEqual(result.fenced_jobs,1)
        self.assertEqual(result.state,"needs_review")
        self.assertIn("original_media_changed",result.reasons)
        with self.psycopg.connect(self.dsn) as conn:
            old=conn.execute(
                "SELECT state,result_analysis_id FROM group_b_runs WHERE id=%s::uuid",
                (m.b_run_id,)
            ).fetchone()
            self.assertEqual(old,("needs_review",persisted.analysis_id))
            self.assertEqual(conn.execute(
                "SELECT state FROM group_b_jobs WHERE project_id=%s AND run_id=%s::uuid",
                (m.project_id,m.b_run_id)
            ).fetchone()[0],"cancelled")
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM group_b_job_events WHERE project_id=%s AND run_id=%s::uuid AND event_type='cancelled'",
                (m.project_id,m.b_run_id)
            ).fetchone()[0],1)
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM group_b_outbox WHERE project_id=%s AND run_id=%s::uuid AND event_type=%s",
                (m.project_id,m.b_run_id,INVALIDATED)
            ).fetchone()[0],1)
            self.assertEqual(conn.execute(
                "SELECT state FROM content_b_analyses WHERE id=%s",
                (persisted.analysis_id,)
            ).fetchone()[0],"partial")
            replay=invalidate_run(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                current=now,verifier=SyntheticHost(),
            )
            self.assertTrue(replay.replayed)
            self.assertEqual(replay.event_id,result.event_id)
            checked=read_verified_current_result(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                current=now,verifier=SyntheticHost(),
            )
            self.assertFalse(checked["eligible_for_qualified_handoff"])
            self.assertEqual(checked["freshness_state"],"stale")
            self.assertFalse(read_group_b_result(
                conn,project_id=m.project_id,run_id=m.b_run_id,
            )["qualified_for_downstream"])

    def test_old_run_outbox_blocks_orchestrator_reenqueue_and_local_claim(self):
        m,_=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            now=self.current(conn,m)
            invalidate_run(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                current=now,verifier=SyntheticHost(),
            )
            report=ReadinessReport(
                m.project_id,m.b_run_id,m.manifest_sha256,
                tuple(BlockReadiness(
                    block,ReadinessState.READY if block=="B5" else
                    ReadinessState.PARTIAL,
                    () if block=="B5" else ("not_ready",),
                    m.manifest_sha256 if block=="B5" else None,
                ) for block in BLOCKS),(),
            )
            with self.assertRaises(DependencyUnavailable):
                enqueue_jobs(
                    conn,project_id=m.project_id,run_id=m.b_run_id,
                    readiness=report,selected_blocks=("B5",),
                )
            self.assertIsNone(claim_local(conn,project_id=m.project_id))
            self.assertEqual(conn.execute(
                """SELECT count(*) FROM group_b_jobs
                   WHERE project_id=%s AND run_id=%s::uuid""",
                (m.project_id,m.b_run_id)
            ).fetchone()[0],0)

    def test_missing_live_source_receipt_cannot_claim_fresh_or_invalidate(self):
        m,_=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            now=self.current(conn,m)
            verdict=assess_current_run(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                current=now,verifier=SyntheticHost(False),
            )
            self.assertIs(verdict.state,FreshnessState.UNVERIFIED)
            with self.assertRaises(InvalidationError):
                invalidate_run(conn,project_id=m.project_id,
                               run_id=m.b_run_id,current=now,
                               verifier=SyntheticHost(False))
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM group_b_outbox WHERE project_id=%s AND run_id=%s::uuid",
                (m.project_id,m.b_run_id)
            ).fetchone()[0],0)

    def test_revoke_media_rights_fails_new_refresh_draft(self):
        m,_=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            current=self.current(conn,m,rights=False)
            conn.execute("UPDATE sources SET can_analyze_media=FALSE WHERE id=%s",
                         (m.source_id,))
            result=invalidate_run(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                current=current,verifier=SyntheticHost(),
            )
            self.assertIn("source_revoked",result.reasons)
            self.assertEqual(conn.execute(
                "SELECT state FROM group_b_runs WHERE id=%s::uuid",
                (m.b_run_id,)
            ).fetchone()[0],"needs_review")

    def test_failure_after_fenced_jobs_rolls_back_all_and_outbox(self):
        m,_=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            proof=self.current(conn,m)
            with patch("intelligence.group_b.integration.invalidation._queue_event",
                       side_effect=InvalidationError("forced_after_state_update")):
                with self.assertRaises(InvalidationError):
                    invalidate_run(
                        conn,project_id=m.project_id,run_id=m.b_run_id,
                        current=proof,verifier=SyntheticHost(),
                    )
            self.assertEqual(conn.execute(
                "SELECT state FROM group_b_runs WHERE id=%s::uuid",
                (m.b_run_id,)
            ).fetchone()[0],"partial")
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM group_b_outbox WHERE project_id=%s AND run_id=%s::uuid",
                (m.project_id,m.b_run_id)
            ).fetchone()[0],0)

    def test_review_correction_and_outbox_one_commit_gold_not_invalidate(self):
        m,saved=self.prepare()
        d=make_decision(
            kind="correction",analysis_id=saved.analysis_id,
            analysis_key=saved.analysis_key,
            manifest_sha256=m.manifest_sha256,
            previous_revision=0,previous_sha256=None,
            block="B8",target="topic",index=None,
            field="subtopic_id",value=None,
            evidence_refs=[m.observations[0].ref_sha256],
            reason="Independently reviewed original evidence",
        )
        with self.psycopg.connect(self.dsn) as conn:
            append_review(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                reviewer_id="synthetic-reviewer",expected_revision=0,
                decision=d,admission_verifier=SyntheticHost(),
            )
            self.assertEqual(conn.execute(
                "SELECT state FROM group_b_runs WHERE id=%s::uuid",
                (m.b_run_id,)
            ).fetchone()[0],"needs_review")
            event=conn.execute(
                """SELECT payload FROM group_b_outbox
                   WHERE project_id=%s AND run_id=%s::uuid
                     AND event_type=%s""",
                (m.project_id,m.b_run_id,INVALIDATED)
            ).fetchone()[0]
            self.assertEqual(event["reasons"],["human_correction"])
            self.assertFalse(event["qualified_for_downstream"])
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM group_b_review_events WHERE project_id=%s AND run_id=%s::uuid",
                (m.project_id,m.b_run_id)
            ).fetchone()[0],1)

    def test_review_and_invalidation_outbox_fail_together_atomically(self):
        m,saved=self.prepare()
        d=make_decision(
            kind="correction",analysis_id=saved.analysis_id,
            analysis_key=saved.analysis_key,manifest_sha256=m.manifest_sha256,
            previous_revision=0,previous_sha256=None,
            block="B8",target="topic",index=None,field="subtopic_id",
            value=None,evidence_refs=[m.observations[0].ref_sha256],
            reason="Synthetic original-source review",
        )
        with self.psycopg.connect(self.dsn) as conn:
            with patch("intelligence.group_b.integration.invalidation._queue_event",
                       side_effect=InvalidationError("outbox_not_available")):
                with self.assertRaises(InvalidationError):
                    append_review(
                        conn,project_id=m.project_id,run_id=m.b_run_id,
                        reviewer_id="synthetic-reviewer",
                        expected_revision=0,decision=d,
                        admission_verifier=SyntheticHost(),
                    )
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM group_b_review_events WHERE project_id=%s AND run_id=%s::uuid",
                (m.project_id,m.b_run_id)
            ).fetchone()[0],0)
            self.assertEqual(conn.execute(
                "SELECT state FROM group_b_runs WHERE id=%s::uuid",
                (m.b_run_id,)
            ).fetchone()[0],"partial")

    def test_gold_only_annotation_does_not_revoke_original_b_run(self):
        m,saved=self.prepare()
        label=gold_sample_key(
            analysis_id=saved.analysis_id,block="B8",target="topic",
            index=None,field="subtopic_id",
        )
        gold=make_decision(
            kind="gold_annotation",analysis_id=saved.analysis_id,
            analysis_key=saved.analysis_key,manifest_sha256=m.manifest_sha256,
            previous_revision=0,previous_sha256=None,
            block="B8",target="topic",index=None,
            field="subtopic_id",value=None,
            evidence_refs=[m.observations[0].ref_sha256],
            reason="Synthetic gold annotation",
            gold_version="gold-2026-v1",sample_key=label,
        )
        with self.psycopg.connect(self.dsn) as conn:
            append_review(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                reviewer_id="synthetic-reviewer",
                expected_revision=0,decision=gold,
                admission_verifier=SyntheticHost(),
            )
            self.assertEqual(conn.execute(
                "SELECT state FROM group_b_runs WHERE id=%s::uuid",
                (m.b_run_id,)
            ).fetchone()[0],"partial")
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM group_b_outbox WHERE project_id=%s AND run_id=%s::uuid AND event_type=%s",
                (m.project_id,m.b_run_id,INVALIDATED)
            ).fetchone()[0],0)

    def test_refresh_outbox_and_verified_distinct_qdp_inbox_draft(self):
        m,_=self.prepare()
        host=SyntheticHost()
        with self.psycopg.connect(self.dsn) as conn:
            now=self.current(conn,m)
            invalidation=invalidate_run(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                current=now,verifier=host,
            )
            first_id,replayed=request_refresh(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                verifier=host,
            )
            second_id,repeated=request_refresh(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                verifier=host,
            )
            self.assertFalse(replayed)
            self.assertTrue(repeated)
            self.assertEqual(first_id,second_id)
            new_inbox=str(conn.execute(
                """INSERT INTO group_b_inbox(project_id,video_id,source_id,
                     event_key,qdp_run_id,producer_schema,event_sha256,
                     preparation_sha256,status)
                   VALUES(%s,%s,%s,%s,%s::uuid,'qdp-handoff/v1',%s,%s,'accepted')
                   RETURNING id::text""",
                (m.project_id,m.video_id,m.source_id,uuid4().hex,str(uuid4()),
                 "a"*64,"c"*64)
            ).fetchone()[0])
            created=create_refresh_draft(
                conn,project_id=m.project_id,old_run_id=m.b_run_id,
                new_inbox_id=new_inbox,config_sha256="d"*64,verifier=host,
            )
            self.assertFalse(created.replayed)
            repeat=create_refresh_draft(
                conn,project_id=m.project_id,old_run_id=m.b_run_id,
                new_inbox_id=new_inbox,config_sha256="d"*64,verifier=host,
            )
            self.assertTrue(repeat.replayed)
            self.assertEqual(created.new_run_id,repeat.new_run_id)
            row=conn.execute(
                """SELECT state,a_snapshot_sha256,result_analysis_id
                   FROM group_b_runs WHERE id=%s::uuid""",
                (created.new_run_id,)
            ).fetchone()
            self.assertEqual(row,("draft",None,None))
            self.assertEqual(conn.execute(
                """SELECT count(*) FROM group_b_block_states
                   WHERE project_id=%s AND run_id=%s::uuid AND status='not_run'""",
                (m.project_id,created.new_run_id)
            ).fetchone()[0],8)
            self.assertEqual(conn.execute(
                """SELECT count(*) FROM group_b_model_attempts
                   WHERE project_id=%s AND run_id=%s::uuid""",
                (m.project_id,created.new_run_id)
            ).fetchone()[0],0)
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM group_b_outbox WHERE project_id=%s AND run_id=%s::uuid AND event_type=%s",
                (m.project_id,m.b_run_id,REFRESH_DRAFT_CREATED)
            ).fetchone()[0],1)
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM group_b_outbox WHERE project_id=%s AND run_id=%s::uuid AND event_type=%s",
                (m.project_id,m.b_run_id,REFRESH_REQUESTED)
            ).fetchone()[0],1)

    def test_outbox_only_read_by_project_and_ack_after_consumer_receipt(self):
        m,_=self.prepare()
        host=SyntheticHost()
        with self.psycopg.connect(self.dsn) as conn:
            proof=self.current(conn,m)
            invalidate_run(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                current=proof,verifier=host,
            )
            events=read_pending_outbox(
                conn,project_id=m.project_id,limit=10,verifier=host,
            )
            self.assertEqual(len(events),1)
            event=events[0]
            self.assertEqual(event.event_type,INVALIDATED)
            self.assertEqual(event.payload["qualified_for_downstream"],False)
            self.assertEqual(read_pending_outbox(
                conn,project_id="different-project",verifier=host,
            ),())
            with self.assertRaises(InvalidationError):
                acknowledge_outbox_delivery(
                    conn,project_id=m.project_id,event_id=event.id,
                    consumer_receipt="unverifiable",verifier=host,
                )
            self.assertEqual(len(read_pending_outbox(
                conn,project_id=m.project_id,verifier=host,
            )),1)
            accepted=acknowledge_outbox_delivery(
                conn,project_id=m.project_id,event_id=event.id,
                consumer_receipt="ack:"+event.payload_sha256,verifier=host,
            )
            self.assertTrue(accepted)
            self.assertFalse(acknowledge_outbox_delivery(
                conn,project_id=m.project_id,event_id=event.id,
                consumer_receipt="ack:"+event.payload_sha256,verifier=host,
            ))
            self.assertEqual(read_pending_outbox(
                conn,project_id=m.project_id,verifier=host,
            ),())
            row=conn.execute(
                """SELECT status,delivered_at FROM group_b_outbox
                   WHERE id=%s::uuid""",(event.id,)
            ).fetchone()
            self.assertEqual(row[0],"delivered")
            self.assertIsNotNone(row[1])

    def test_cross_project_and_unaccepted_qdp_inbox_fail_without_new_run(self):
        m,_=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            current=self.current(conn,m)
            invalidate_run(conn,project_id=m.project_id,run_id=m.b_run_id,
                           current=current,verifier=SyntheticHost())
            rejected=str(conn.execute(
                """INSERT INTO group_b_inbox(project_id,video_id,source_id,
                     event_key,qdp_run_id,producer_schema,event_sha256,
                     preparation_sha256,status)
                   VALUES(%s,%s,%s,%s,%s::uuid,'qdp-handoff/v1',%s,%s,'received')
                   RETURNING id::text""",
                (m.project_id,m.video_id,m.source_id,uuid4().hex,str(uuid4()),
                 "b"*64,"c"*64)
            ).fetchone()[0])
            with self.assertRaises(InvalidationError):
                create_refresh_draft(conn,project_id=m.project_id,
                                     old_run_id=m.b_run_id,new_inbox_id=rejected,
                                     config_sha256="d"*64,verifier=SyntheticHost())
            with self.assertRaises(InvalidationError):
                create_refresh_draft(conn,project_id="foreign",
                                     old_run_id=m.b_run_id,new_inbox_id=rejected,
                                     config_sha256="d"*64,verifier=SyntheticHost())


if __name__=="__main__":
    unittest.main()
