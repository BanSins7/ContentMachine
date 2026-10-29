"""UA-MOD-30 QDP/SRE/A/B/C review revisions in the same fixture matrix.

This tests actual UAE RevisionEventProcessor with its existing deterministic
mock database, NOT a live trusted upstream peer or a qualified production
snapshot. Accepted rejected-review events invalidate old lineage, never
rewrite immutable snapshots or authorize old score caches.
"""
from __future__ import annotations

import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics.revision_events import (
    RevisionEventProcessor,C_PROOF_SQL,C_REJECT_PROOF_SQL,
    B_PROOF_SQL,SRE_PROOF_SQL,OUTBOX_INSERT_SQL,
)
from test_unified_analytics_revision_events import (
    PROJECT,VIDEO,SOURCE,POLICY,RUN,UUID_A,SHA,NOW,
    FakeDB,event,
)


class FullFixtureReviewRegressions(unittest.TestCase):
    def test_human_reject_c_config_invalidates_only_old_snapshot(self):
        db=FakeDB()
        db.snapshots[0]["payload"]["versions"]["calculation_config_id"]=42
        db.snapshots[1]["payload"]["versions"]["calculation_config_id"]=99
        db.proofs[C_REJECT_PROOF_SQL]=dict(
            record_id=UUID_A,project_id=PROJECT,video_id=VIDEO,
            source_id=SOURCE,source_policy_version_id=POLICY,
            revision=42,evidence_sha256=SHA,recorded_at=NOW,
            creator_freeze_id=1,niche_freeze_id=None,prior_freeze_id=3,
        )
        out=RevisionEventProcessor(db,as_of=NOW).consume(
            event(kind="group_c_review_rejected",record_id=UUID_A,
                  revision=42,sha=SHA)
        )
        self.assertEqual(out.state,"accepted")
        self.assertEqual(out.invalidated_snapshot_ids,(1,))
        self.assertEqual(len(db.outbox),1)
        self.assertEqual(db.snapshots[0]["status"],"partial")
        self.assertEqual(db.snapshots[1]["status"],"blocked")

    def test_group_c_new_cohort_does_not_requalify_historical(self):
        db=FakeDB()
        db.proofs[C_PROOF_SQL]=dict(
            record_id=UUID_A,project_id=PROJECT,video_id=VIDEO,
            source_id=SOURCE,source_policy_version_id=POLICY,
            revision=9,evidence_sha256=SHA,recorded_at=NOW,
            previous_calculated_metric_id=None,c_run_id=RUN,
        )
        out=RevisionEventProcessor(db,as_of=NOW).consume(
            event(kind="group_c_revised",record_id=UUID_A,
                  revision=9,sha=SHA)
        )
        self.assertEqual(out.invalidated_snapshot_ids,(1,))
        self.assertEqual(len(db.outbox),1)
        self.assertNotIn("qualified",[s["status"] for s in db.snapshots])

    def test_group_b_review_versions_make_earlier_row_stale(self):
        db=FakeDB()
        db.proofs[B_PROOF_SQL]=dict(
            record_id=UUID_A,project_id=PROJECT,video_id=VIDEO,
            source_id=SOURCE,source_policy_version_id=POLICY,
            revision=5,evidence_sha256=SHA,recorded_at=NOW,
            result_analysis_id=200,
        )
        out=RevisionEventProcessor(db,as_of=NOW).consume(
            event(kind="group_b_reviewed",record_id=UUID_A,
                  revision=5,sha=SHA)
        )
        self.assertEqual(out.invalidated_snapshot_ids,(1,))
        self.assertEqual(len(db.outbox),1)

    def test_sre_taxonomy_revision_invalidates_pinned_group_lineage(self):
        db=FakeDB()
        db.proofs[SRE_PROOF_SQL]=dict(
            record_id="12",project_id=PROJECT,revision=5,
            evidence_sha256=SHA,recorded_at=NOW,
        )
        out=RevisionEventProcessor(db,as_of=NOW).consume(
            event(kind="sre_taxonomy_approved",record_id="12",
                  revision=5,sha=SHA)
        )
        self.assertEqual(out.invalidated_snapshot_ids,(1,))
        self.assertEqual(len(db.outbox),1)

    def test_rejected_review_requires_true_producer_proof(self):
        db=FakeDB()
        out=RevisionEventProcessor(db,as_of=NOW).consume(
            event(kind="group_c_review_rejected",record_id=UUID_A,
                  revision=42,sha=SHA)
        )
        self.assertNotEqual(out.state,"accepted")
        self.assertEqual(len(db.outbox),0)
        self.assertEqual(len(db.invalidations),0)

    def test_outbox_failure_rolls_back_review_and_invalidation(self):
        db=FakeDB()
        db.proofs[C_REJECT_PROOF_SQL]=dict(
            record_id=UUID_A,project_id=PROJECT,video_id=VIDEO,
            source_id=SOURCE,source_policy_version_id=POLICY,
            revision=42,evidence_sha256=SHA,recorded_at=NOW,
            creator_freeze_id=1,niche_freeze_id=None,prior_freeze_id=3,
        )
        db.snapshots[0]["payload"]["versions"]["calculation_config_id"]=42
        db.fault=OUTBOX_INSERT_SQL
        with self.assertRaises(AnalyticsBoundaryError):
            RevisionEventProcessor(db,as_of=NOW).consume(
                event(kind="group_c_review_rejected",record_id=UUID_A,
                      revision=42,sha=SHA)
            )
        self.assertFalse(db.outbox)
        self.assertFalse(db.invalidations)
        self.assertFalse(db.inbox)


if __name__=="__main__":
    unittest.main()
