"""UA-MOD-30: full seven-port *structural* E2E for video/carousel.

Uses actual JoinedContextAssembler, QualificationOrchestrator and
ImmutableSnapshotAssembler classes, with explicitly synthetic source/peer
adapters from earlier UAE tests. Complete pinned claims NEVER represent
effective SRE/QDP/A/B/C approval: UA13 + UA85 must remain fail-closed.
No paid models, external providers, real producer database or live data.
"""
from __future__ import annotations

from dataclasses import replace
import copy
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics.contracts import canonical_hash
from intelligence.unified_analytics.joined_context import REQUIRED_PORTS
from intelligence.unified_analytics.qualification_orchestrator import (
    QualificationOrchestrator, QualificationStatus,
)
from intelligence.unified_analytics.snapshot_assembly import (
    ImmutableSnapshotAssembler,
)
from test_unified_analytics_joined_context import (
    SCOPE, NOW, MockAssembler, FakeDB, bundle,
)


def candidate(kind="video"):
    """Exact seven-port pins; new scope for carousel, never copy old video IDs."""
    if kind not in ("video","photo_carousel","mixed_carousel","slideshow_video"):
        raise ValueError("fixture kind unsupported")
    original=bundle()
    scope=replace(SCOPE,content_kind=kind)
    n=1 if kind in ("video","slideshow_video") else 2
    lineage=replace(original.lineage,scope=scope)
    return replace(
        original,
        lineage=lineage,
        rights=replace(
            original.rights,scope=scope,
            media_asset_ids=tuple(100+i for i in range(1,n+1)),
        ),
        sre=replace(original.sre,lineage=lineage),
        qdp=replace(original.qdp,scope=scope),
        group_a=replace(original.group_a,scope=scope,expected_media_items=n),
        group_b=replace(original.group_b,scope=scope),
        group_c=replace(original.group_c,scope=scope),
    )


def assemble(kind="video",*,pins=None,flags=None,media=None):
    c=candidate(kind) if pins is None else pins
    db=FakeDB()
    if media is not None:
        db.media=media
    else:
        db.media=c.rights.media_asset_ids
    join=MockAssembler(db,c.scope,(c,),as_of=NOW,flags=flags)
    draft=ImmutableSnapshotAssembler(join).assemble()
    return draft,join,db


class FullE2ESyntheticContracts(unittest.TestCase):
    def check_unapproved_complete(self,kind):
        draft,join,db=assemble(kind)
        picked=join.select()
        self.assertEqual(picked.status,"complete_claims_unapproved")
        self.assertIsNotNone(picked.selected)
        self.assertEqual(draft.scope.content_kind,kind)
        self.assertEqual(draft.decision.status,QualificationStatus.PARTIAL)
        self.assertFalse(draft.qualified)
        self.assertFalse(draft.decision.qualified)
        self.assertEqual(draft.payload["status"],"partial")
        self.assertEqual(draft.payload["qualified"] if "qualified" in draft.payload else False,False)
        self.assertEqual(draft.payload["scope_sha256"],draft.scope.scope_sha256)
        self.assertEqual(draft.payload["input_sha256"],draft.decision.input_sha256)
        self.assertEqual(draft.payload["decision_sha256"],draft.decision.decision_sha256)
        self.assertEqual(draft.payload_sha256,canonical_hash(draft.payload))
        self.assertEqual(tuple(x.port for x in draft.evidence),REQUIRED_PORTS)
        self.assertTrue(all(x.state=="partial" for x in draft.evidence))
        self.assertEqual(tuple(draft.payload["readiness"]),REQUIRED_PORTS)
        self.assertTrue(all(state=="located_unapproved"
                            for state in draft.payload["readiness"].values()))
        self.assertEqual([x.code for x in draft.decision.blockers],
                         ["qualification_effective_approval_unavailable"])
        self.assertEqual(
            len(draft.payload["versions"]["qdp_preparation_shas"]),3
        )
        self.assertEqual(draft.payload["versions"]["group_b_analysis_id"],
                         candidate(kind).group_b.b_analysis_id)
        self.assertEqual(draft.payload["versions"]["calculated_metric_id"],
                         candidate(kind).group_c.calculated_metric_id)
        self.assertFalse(picked.qualified)
        return draft,join

    def test_complete_structural_video_never_promoted(self):
        self.check_unapproved_complete("video")

    def test_complete_structural_slideshow_video_never_promoted(self):
        self.check_unapproved_complete("slideshow_video")

    def test_complete_structural_photo_carousel_never_promoted(self):
        draft,join=self.check_unapproved_complete("photo_carousel")
        self.assertEqual(len(join._candidates[0].rights.media_asset_ids),2)
        self.assertEqual(draft.payload["content_kind"],"photo_carousel")

    def test_complete_structural_mixed_carousel_never_promoted(self):
        draft,join=self.check_unapproved_complete("mixed_carousel")
        self.assertEqual(len(join._candidates[0].rights.media_asset_ids),2)

    def test_video_and_carousel_distinct_hashes_no_cross_type_replay(self):
        video=assemble("video")[0]
        photo=assemble("photo_carousel")[0]
        self.assertNotEqual(video.scope.scope_sha256,photo.scope.scope_sha256)
        self.assertNotEqual(video.decision.input_sha256,
                            photo.decision.input_sha256)
        self.assertNotEqual(video.payload_sha256,photo.payload_sha256)

    def test_canonical_video_two_repeated_runs_deterministic(self):
        a=assemble("video")[0]
        b=assemble("video")[0]
        self.assertEqual(a.payload_sha256,b.payload_sha256)
        self.assertEqual(a.evidence_set_sha256,b.evidence_set_sha256)

    def test_canonical_carousel_two_repeated_runs_deterministic(self):
        a=assemble("photo_carousel")[0]
        b=assemble("photo_carousel")[0]
        self.assertEqual(a.payload_sha256,b.payload_sha256)

    def test_exact_qdp_a_prep_hash_wrong_deny(self):
        original=candidate()
        bad=replace(original,group_a=replace(
            original.group_a,qdp_a_preparation_sha256="f"*64))
        self.assert_blocked(bad,"join_abc_preparation_sha_mismatch")

    def test_exact_qdp_b_prep_hash_wrong_deny(self):
        original=candidate("photo_carousel")
        bad=replace(original,group_b=replace(
            original.group_b,qdp_b_preparation_sha256="f"*64))
        self.assert_blocked(bad,"join_abc_preparation_sha_mismatch")

    def test_exact_qdp_c_prep_hash_wrong_deny(self):
        original=candidate()
        bad=replace(original,group_c=replace(
            original.group_c,qdp_c_preparation_sha256="f"*64))
        self.assert_blocked(bad,"join_abc_preparation_sha_mismatch")

    def test_wrong_qdp_run_against_sre_deny(self):
        original=candidate()
        bad=replace(original,qdp=replace(
            original.qdp,sre_run_id="33334444-5555-4666-8777-88889999aaaa"))
        self.assert_blocked(bad,"join_sre_qdp_version_mismatch")

    def test_wrong_qdp_taxonomy_sha_deny(self):
        original=candidate()
        bad=replace(original,qdp=replace(
            original.qdp,taxonomy_sha256="f"*64))
        self.assert_blocked(bad,"join_sre_qdp_version_mismatch")

    def test_wrong_group_b_parent_result_deny(self):
        original=candidate()
        bad=replace(original,group_b=replace(
            original.group_b,group_a_result_sha256="f"*64))
        self.assert_blocked(bad,"join_b_wrong_group_a_parent")

    def test_wrong_group_b_taxonomy_version_deny(self):
        original=candidate("mixed_carousel")
        bad=replace(original,group_b=replace(
            original.group_b,taxonomy_version="taxonomy-wrong"))
        self.assert_blocked(bad,"join_b_taxonomy_version_mismatch")

    def test_wrong_group_c_config_deny(self):
        original=candidate()
        bad=replace(original,group_c=replace(original.group_c,config_id=99))
        self.assert_blocked(bad,"join_c_config_or_snapshot_changed")

    def test_wrong_metric_snapshot_pair_deny(self):
        original=candidate()
        bad=replace(original,rights=replace(
            original.rights,metric_snapshot_ids=(200,202)))
        self.assert_blocked(bad,"join_metric_snapshot_pair_mismatch")

    def test_wrong_media_order_carousel_deny(self):
        original=candidate("photo_carousel")
        draft,join,db=assemble("photo_carousel",pins=original,
                               media=(102,101))
        self.assertEqual(draft.payload["status"],"blocked")
        self.assertEqual(draft.decision.blockers[0].code,
                         "join_ga_source_media_mismatch")
        self.assertFalse(draft.decision.qualified)

    def test_missing_slide_carousel_deny(self):
        draft,join,_=assemble("photo_carousel",media=(101,))
        self.assertEqual(draft.decision.status,QualificationStatus.BLOCKED)
        self.assertEqual(draft.decision.blockers[0].code,
                         "join_ga_source_media_mismatch")

    def test_missing_qdp_peer_deny(self):
        draft,_,_=assemble("video",flags={"missing":"qdp"})
        self.assertEqual(draft.decision.status,QualificationStatus.BLOCKED)
        self.assertFalse(draft.qualified)
        self.assertIn("join_qdp_not_current",
                      [x.code for x in draft.decision.blockers])

    def test_missing_group_c_peer_deny(self):
        draft,_,_=assemble("video",flags={"missing":"group_c"})
        self.assertFalse(draft.qualified)
        self.assertEqual(draft.decision.status,QualificationStatus.PARTIAL)
        self.assertIn("join_group_c_not_current",
                      [x.code for x in draft.decision.blockers])

    def test_unavailable_group_b_peer_deny(self):
        draft,_,_=assemble("photo_carousel",flags={"unavailable":"group_b"})
        self.assertFalse(draft.qualified)
        self.assertIn("join_group_b_not_current",
                      [x.code for x in draft.decision.blockers])

    def test_source_revoked_during_group_a_cannot_qualify(self):
        draft,_,_=assemble("video",flags={"revoked_during":"group_a"})
        self.assertFalse(draft.qualified)
        self.assertIsNone(draft.payload["versions"])
        self.assertIn("join_group_a_not_current",
                      [x.code for x in draft.decision.blockers])

    def test_source_retention_expired_deny(self):
        draft,_,_=assemble("video",flags={"expiry":True})
        self.assertFalse(draft.qualified)
        self.assertIn("source_retention_expired",
                      [x.code for x in draft.decision.blockers])

    def test_current_rights_revision_mid_stream_deny(self):
        draft,_,_=assemble("photo_carousel",
                            flags={"right_change_during":"group_c"})
        self.assertFalse(draft.qualified)
        self.assertIn("join_group_c_not_current",
                      [x.code for x in draft.decision.blockers])

    def test_wrong_d1_canonical_version_deny(self):
        draft,_,_=assemble("video",flags={"canonical_stale":True})
        self.assertFalse(draft.qualified)
        self.assertIn("join_canonical_version_changed",
                      [x.code for x in draft.decision.blockers])

    def test_no_raw_group_c_score_or_fake_zero(self):
        draft=assemble("video")[0]
        fields=draft.payload
        self.assertNotIn("breakout_score",fields)
        self.assertNotIn("normalized_score",fields)
        self.assertNotIn("statistically_significant",fields)
        self.assertNotIn("p_value",fields)

    def test_no_raw_group_a_media_transcript_exported(self):
        for kind in ("video","photo_carousel"):
            payload=assemble(kind)[0].payload
            self.assertNotIn("raw_video",payload)
            self.assertNotIn("transcript",payload)
            self.assertNotIn("media_bytes",payload)
            self.assertNotIn("url",payload)

    def test_missing_qualifier_does_not_become_checked_true(self):
        for kind in ("video","photo_carousel"):
            for flags in ({},{"missing":"group_c"},{"revoked":True}):
                draft=assemble(kind,flags=flags)[0]
                self.assertFalse(draft.qualified)
                self.assertTrue(all(e.state!="complete" for e in draft.evidence))
                self.assertTrue(all(
                    x["checked"] is False and x["is_current"] is False
                    for x in draft.payload["evidence"]
                ))

    def assert_blocked(self,pins,code):
        draft,_,_=assemble(pins.scope.content_kind,pins=pins)
        self.assertFalse(draft.qualified)
        self.assertIn(code,[b.code for b in draft.decision.blockers])
        self.assertFalse(any(x["checked"] for x in draft.payload["evidence"]))


if __name__=="__main__":
    unittest.main()
