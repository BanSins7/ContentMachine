"""GC-MOD-22: strict incremental C7 revision + pending Analytics outbox QA.

Pure tests are independent of PostgreSQL. PG17 tests require disposable DB and
reuse GC11/12 signed HUMAN and GC21 source fixtures with test-only IAM.
"""
from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError
from uuid import uuid4

from intelligence.group_c.contracts import ContractError, canonical_hash
from intelligence.group_c.incremental_recompute import (
    RECOMPUTE_VERSION, OUTBOX_VERSION, RevisionPlan, RevisionOutbox,
    PostgreSQLIncrementalRecompute, determine_revision_kind, _version_key,
)


def before():
    return dict(
        project_id="a"*32,video_id=7,source_id="source-A",
        snapshot_id=12,previous_snapshot_id=11,metadata_id=20,
        config_id=4,creator_cohort_id=5,prior_cohort_id=6,
        niche_cohort_id=None,
    )


class TestIncrementalPure(unittest.TestCase):
    def test_new_raw_snapshot_is_authoritative_cause(self):
        old=before()
        new={**old,"previous_snapshot_id":12,"snapshot_id":13,
             "config_id":15,"creator_cohort_id":16}
        self.assertEqual(determine_revision_kind(old,new),"new_snapshot")

    def test_new_snapshot_must_continue_previous_score_raw(self):
        old=before()
        with self.assertRaises(ContractError) as e:
            determine_revision_kind(old,{**old,"snapshot_id":999,"previous_snapshot_id":11})
        self.assertEqual(e.exception.code,"revision_history_mismatch")

    def test_config_version_change_requires_new_config_key(self):
        old=before()
        new={**old,"config_id":9}
        self.assertEqual(determine_revision_kind(old,new),"config_changed")
        self.assertNotEqual(_version_key(old),_version_key(new))

    def test_cohort_change_priority_even_if_config_also_changes(self):
        old=before()
        self.assertEqual(determine_revision_kind(
            old,{**old,"config_id":9,"creator_cohort_id":99}),"cohort_changed")
        self.assertEqual(determine_revision_kind(
            old,{**old,"prior_cohort_id":99}),"cohort_changed")

    def test_publication_version_priority_without_invented_score_key(self):
        old=before()
        changed={**old,"metadata_id":21}
        self.assertEqual(determine_revision_kind(old,changed),"publication_correction")
        # The old 003 unique key EXCLUDES published_metadata_id.
        self.assertEqual(_version_key(old),_version_key(changed))
        # A separate reviewed config or cohort version is required to persist.
        changed["config_id"]=15
        self.assertNotEqual(_version_key(old),_version_key(changed))

    def test_same_evidence_rejected_no_version_fabrication(self):
        with self.assertRaises(ContractError) as e:
            determine_revision_kind(before(),before())
        self.assertEqual(e.exception.code,"revision_version_required")

    def test_source_project_or_video_mismatch_fails_closed(self):
        for change in ({"project_id":"b"*32},{"video_id":8},{"source_id":"B"}):
            with self.subTest(change=change),self.assertRaises(ContractError) as err:
                determine_revision_kind(before(),{**before(),**change})
            self.assertEqual(err.exception.code,"revision_identity_mismatch")

    def test_no_silent_previous_history_replacement(self):
        with self.assertRaises(ContractError) as e:
            determine_revision_kind(before(),{**before(),"previous_snapshot_id":10})
        self.assertEqual(e.exception.code,"revision_history_mismatch")

    def test_malformed_missing_or_unstructured_evidence(self):
        for old,new in ((None,before()),(before(),None),
                        ({},{"foo":5}),
                        ({k:v for k,v in before().items() if k!="niche_cohort_id"},before())):
            with self.subTest(case=(old,new)),self.assertRaises(ContractError):
                determine_revision_kind(old,new)

    def test_receipts_are_immutable_and_non_delivery(self):
        plan=RevisionPlan(
            revision_id=str(uuid4()),project_id="a"*32,
            run_id=str(uuid4()),video_id=7,previous_calculated_metric_id=15,
            revision_kind="config_changed",evidence_sha256="b"*64,
            score_job_id=200,
        )
        self.assertEqual(plan.schema_version,RECOMPUTE_VERSION)
        self.assertFalse(plan.is_score_complete)
        with self.assertRaises(FrozenInstanceError):
            plan.status="complete"
        event=RevisionOutbox(
            revision_id=plan.revision_id,run_id=plan.run_id,
            calculated_metric_id=17,previous_calculated_metric_id=15,
            outbox_id=str(uuid4()),event_key="c7-revision:"+plan.revision_id,
            payload_sha256="c"*64,inserted=True,
        )
        self.assertEqual(event.state,"pending_analytics_delivery")
        self.assertEqual(OUTBOX_VERSION,"group-c.result_ready/v1")
        with self.assertRaises(FrozenInstanceError):
            event.inserted=False

    def test_schema_version_and_no_implicit_analytics_delivery(self):
        recompute=PostgreSQLIncrementalRecompute()
        self.assertFalse(hasattr(recompute,"send_to_analytics"))
        self.assertFalse(hasattr(recompute,"ack_qdp"))
        self.assertEqual(RECOMPUTE_VERSION,"group-c-incremental-recompute/v1")


class _TestOnlyIAM:
    def verify_c7_persistence(self,conn,**kwargs):
        return True


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "Explicit disposable PostgreSQL17 only: CM_TEST_PG_DSN+GC_TEST_DISPOSABLE_DB=YES",
)
class TestIncrementalPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        TestDurableWorkerPG17.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        TestDurableWorkerPG17.tearDownClass.__func__(cls)

    def setUp(self):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        self.base=TestDurableWorkerPG17()
        self.base.conn=self.conn
        self.base.psycopg=self.psycopg
        self.base.setUp()
        self.driver=PostgreSQLIncrementalRecompute()
        self.project=self.base.project
        self.video=self.base.target[0]

    def tearDown(self):
        self.base.tearDown()

    def baseline(self):
        req=self.base.request()
        lease=self.base.claim()
        result=self.base.worker.execute_score(
            self.conn,lease=lease,review_request_id=req.request_id,
            approval_verifier=_TestOnlyIAM(),
        )
        self.assertTrue(result.score.inserted)
        self.assertIsNone(result.revision_outbox_id)
        return result

    def next_config(self):
        old_config=self.base.config
        new_config=self.base.fixture.new_config()
        new_review=self.base.fixture.signed(config_id=new_config)
        self.base.fixture.activate(
            new_review,config_id=new_config,replace=old_config,
        )
        self.base.config=new_config
        return new_review

    def prepare(self, old,kind,review):
        job_id,run_id=self.base.make_job()
        plan=self.driver.plan(
            self.conn,project_id=self.project,run_id=run_id,
            previous_calculated_metric_id=old.score.calculated_metric_id,
            revision_kind=kind,review_request_id=review.request_id,
        )
        self.assertEqual(plan.score_job_id,job_id)
        return plan

    def claim_finish(self, plan, review):
        lease=self.base.claim("node-002")
        self.assertEqual(lease.job_id,plan.score_job_id)
        return self.base.worker.execute_score(
            self.conn,lease=lease,review_request_id=review.request_id,
            approval_verifier=_TestOnlyIAM(),
        )

    def test_config_revision_new_score_old_immutable_and_outbox(self):
        old=self.baseline()
        previous=self.conn.execute(
            "SELECT breakout_score FROM calculated_metrics WHERE id=%s",
            (old.score.calculated_metric_id,),
        ).fetchone()[0]
        new_review=self.next_config()
        plan=self.prepare(old,"config_changed",new_review)
        result=self.claim_finish(plan,new_review)
        self.assertTrue(result.score.inserted)
        self.assertNotEqual(result.score.calculated_metric_id,old.score.calculated_metric_id)
        self.assertIsNotNone(result.revision_outbox_id)
        event=self.conn.execute(
            """SELECT id,calculated_metric_id,state,payload_sha256,payload
               FROM group_c_outbox WHERE id=%s""",
            (result.revision_outbox_id,),
        ).fetchone()
        self.assertEqual(event[1],result.score.calculated_metric_id)
        self.assertEqual(event[2],"pending")
        self.assertEqual(event[4]["previous_calculated_metric_id"],
                         old.score.calculated_metric_id)
        self.assertEqual(event[4]["revision_id"],plan.revision_id)
        self.assertEqual(event[3],canonical_hash(event[4]))
        self.assertEqual(self.conn.execute(
            "SELECT breakout_score FROM calculated_metrics WHERE id=%s",
            (old.score.calculated_metric_id,),
        ).fetchone()[0],previous)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM calculated_metrics WHERE post_id=%s",
            (self.video,),
        ).fetchone()[0],2)

    def test_idempotent_plan_and_outbox_no_duplicate(self):
        old=self.baseline()
        review=self.next_config()
        plan=self.prepare(old,"config_changed",review)
        repeated=self.driver.plan(
            self.conn,project_id=self.project,run_id=plan.run_id,
            previous_calculated_metric_id=old.score.calculated_metric_id,
            revision_kind="config_changed",review_request_id=review.request_id,
        )
        self.assertTrue(repeated.replayed)
        self.assertEqual(repeated.revision_id,plan.revision_id)
        saved=self.claim_finish(plan,review)
        replayed=self.driver.enqueue_ready(
            self.conn,project_id=self.project,run_id=plan.run_id,
        )
        self.assertIsNotNone(replayed)
        self.assertFalse(replayed.inserted)
        self.assertEqual(replayed.outbox_id,saved.revision_outbox_id)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM group_c_outbox WHERE run_id=%s",
            (plan.run_id,),
        ).fetchone()[0],1)

    def test_new_snapshot_revision_is_pinned_to_real_new_history(self):
        old=self.baseline()
        old_snapshot=self.base.target[1]
        new_snapshot=self.conn.execute(
            """INSERT INTO metric_snapshots (
                 video_id,source_id,source_policy_version_id,
                 observed_at,collected_at,views,likes,comments,shares,
                 source_metric_definition,source_metric_version,
                 precision,data_quality_status,observation_time_assumed)
               VALUES(%s,%s,%s,now()-interval '1 hour',now()-interval '30 minute',
                      150,15,4,3,'platform_post_views','v1','exact','ok',FALSE)
               RETURNING id""",
            (self.video,self.base.source,self.base.policy),
        ).fetchone()[0]
        self.base.previous=old_snapshot
        self.base.target=(self.video,new_snapshot,self.base.target[2])
        self.base.fixture.target=self.base.target
        review=self.next_config()
        plan=self.prepare(old,"new_snapshot",review)
        self.assertEqual(plan.revision_kind,"new_snapshot")
        result=self.claim_finish(plan,review)
        self.assertEqual(result.score.snapshot_id,new_snapshot)
        self.assertNotEqual(result.score.calculated_metric_id,old.score.calculated_metric_id)

    def test_publication_correction_with_new_approved_version(self):
        old=self.baseline()
        previous_md=self.base.target[2]
        self.conn.execute(
            "UPDATE source_video_metadata SET is_current=FALSE WHERE id=%s",
            (previous_md,),
        )
        new_md=self.conn.execute(
            """INSERT INTO source_video_metadata
               (video_id,source_id,source_policy_version_id,
                fetched_at,published_at)
               VALUES (%s,%s,%s,now()-interval '3 hours',now()-interval '6 days')
               RETURNING id""",
            (self.video,self.base.source,self.base.policy),
        ).fetchone()[0]
        self.base.target=(self.video,self.base.target[1],new_md)
        self.base.fixture.target=self.base.target
        review=self.next_config()
        plan=self.prepare(old,"publication_correction",review)
        saved=self.claim_finish(plan,review)
        self.assertEqual(saved.score.published_metadata_id,new_md)
        self.assertIsNotNone(saved.revision_outbox_id)

    def test_publication_correction_without_new_version_fails_closed(self):
        old=self.baseline()
        # In-place metadata editing is NOT a new calculation version and
        # cannot overwrite an already completed original score.
        review_id=old.score.review_request_id
        job,run=self.base.make_job()
        self.conn.execute(
            "UPDATE source_video_metadata SET published_at=published_at-interval '1 hour' "
            "WHERE id=%s",(self.base.target[2],),
        )
        with self.assertRaises(ContractError) as error:
            self.driver.plan(
                self.conn,project_id=self.project,run_id=run,
                previous_calculated_metric_id=old.score.calculated_metric_id,
                revision_kind="publication_correction",
                review_request_id=review_id,
            )
        self.assertIn(error.exception.code,("revision_kind_mismatch",
                                             "revision_version_required"))
        self.assertFalse(self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM group_c_revisions WHERE run_id=%s)",
            (run,),
        ).fetchone()[0])

    def test_new_run_without_canonical_approved_review_denied(self):
        old=self.baseline()
        job,run=self.base.make_job()
        with self.assertRaises(ContractError):
            self.driver.plan(
                self.conn,project_id=self.project,run_id=run,
                previous_calculated_metric_id=old.score.calculated_metric_id,
                revision_kind="config_changed",
                review_request_id=str(uuid4()),
            )

    def test_existing_run_cannot_claim_different_revision_kind(self):
        old=self.baseline()
        review=self.next_config()
        plan=self.prepare(old,"config_changed",review)
        with self.assertRaises(ContractError) as error:
            self.driver.plan(
                self.conn,project_id=self.project,run_id=plan.run_id,
                previous_calculated_metric_id=old.score.calculated_metric_id,
                revision_kind="cohort_changed",review_request_id=review.request_id,
            )
        self.assertEqual(error.exception.code,"revision_conflict")

    def test_old_completed_run_not_reusable_as_new_revision(self):
        old=self.baseline()
        with self.assertRaises(ContractError):
            self.driver.plan(
                self.conn,project_id=self.project,run_id=old.lease.run_id,
                previous_calculated_metric_id=old.score.calculated_metric_id,
                revision_kind="config_changed",
                review_request_id=old.score.review_request_id,
            )

    def test_revision_review_bound_to_worker_and_cannot_be_swapped(self):
        old=self.baseline()
        review=self.next_config()
        plan=self.prepare(old,"config_changed",review)
        lease=self.base.claim("node-002")
        with self.assertRaises(ContractError) as error:
            self.base.worker.execute_score(
                self.conn,lease=lease,review_request_id=str(uuid4()),
                approval_verifier=_TestOnlyIAM(),
            )
        self.assertEqual(error.exception.code,"revision_conflict")
        self.assertFalse(self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM group_c_outbox WHERE run_id=%s)",
            (plan.run_id,),
        ).fetchone()[0])

    def test_revoked_source_after_plan_blocks_new_score_and_outbox(self):
        old=self.baseline()
        review=self.next_config()
        plan=self.prepare(old,"config_changed",review)
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.base.source,),
        )
        lease=self.base.claim("node-002")
        with self.assertRaises(ContractError):
            self.base.worker.execute_score(
                self.conn,lease=lease,review_request_id=review.request_id,
                approval_verifier=_TestOnlyIAM(),
            )
        self.assertFalse(self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM group_c_outbox WHERE run_id=%s)",
            (plan.run_id,),
        ).fetchone()[0])
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM calculated_metrics WHERE post_id=%s",
            (self.video,),
        ).fetchone()[0],1)

    def test_new_raw_retraction_after_plan_blocks_stale_score(self):
        old=self.baseline()
        review=self.next_config()
        plan=self.prepare(old,"config_changed",review)
        self.conn.execute(
            """INSERT INTO metric_snapshots(
                 video_id,source_id,source_policy_version_id,
                 observed_at,collected_at,views,likes,comments,shares,
                 source_metric_definition,source_metric_version,
                 precision,data_quality_status,observation_time_assumed)
               VALUES(%s,%s,%s,now()-interval '10 minutes',now(),999,10,3,2,
                      'platform_post_views','v2','exact','retracted',FALSE)""",
            (self.video,self.base.source,self.base.policy),
        )
        lease=self.base.claim("node-002")
        with self.assertRaises(ContractError):
            self.base.worker.execute_score(
                self.conn,lease=lease,review_request_id=review.request_id,
                approval_verifier=_TestOnlyIAM(),
            )
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM calculated_metrics WHERE post_id=%s",
            (self.video,),
        ).fetchone()[0],1)

    def test_outbox_cannot_enqueue_before_score_complete(self):
        old=self.baseline()
        review=self.next_config()
        plan=self.prepare(old,"config_changed",review)
        with self.assertRaises(ContractError) as error:
            self.driver.enqueue_ready(self.conn,project_id=self.project,run_id=plan.run_id)
        self.assertEqual(error.exception.code,"revision_not_complete")

    def test_unrelated_nonrevision_run_has_no_incremental_outbox(self):
        old=self.baseline()
        other_job,run=self.base.make_job()
        self.assertIsNone(
            self.driver.enqueue_ready(self.conn,project_id=self.project,run_id=run),
        )

    def test_cross_project_and_previous_metric_refused(self):
        old=self.baseline()
        review=self.next_config()
        job,run=self.base.make_job()
        for kwargs in (
            dict(project_id=uuid4().hex,run_id=run,
                 previous_calculated_metric_id=old.score.calculated_metric_id),
            dict(project_id=self.project,run_id=run,
                 previous_calculated_metric_id=2**62),
        ):
            with self.subTest(kwargs=kwargs),self.assertRaises(ContractError):
                self.driver.plan(
                    self.conn,revision_kind="config_changed",
                    review_request_id=review.request_id,**kwargs,
                )


if __name__=="__main__":
    unittest.main()
