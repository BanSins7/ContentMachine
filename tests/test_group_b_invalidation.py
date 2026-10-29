"""GB-MOD-24 pure QDP/GA/media/review source-lineage QA.

All current probes are synthetic; no signed GA/QDP proofs or real rights are
available. A checksum alone never grants a production freshness decision.
"""
from __future__ import annotations
from dataclasses import replace
from uuid import uuid4
import unittest

from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.integration import (
    LineagePins,FreshnessState,InvalidationError,
    make_current_lineage,pins_digest,compare_lineage,
)


def pair():
    p=LineagePins(
        "project",str(uuid4()),23,"original-source","video",
        str(uuid4()),"a"*64,51,"b"*64,"c"*64,77,0,None,
    )
    current=make_current_lineage(
        project_id=p.project_id,video_id=p.video_id,
        source_id=p.source_id,content_type=p.content_type,
        qdp_run_id=p.qdp_run_id,qdp_input_sha256=p.qdp_input_sha256,
        ga_result_id=p.ga_result_id,ga_result_sha256=p.ga_result_sha256,
        media_manifest_sha256=p.media_manifest_sha256,
        source_policy_version_id=p.source_policy_version_id,
        source_rights_active=True,source_media_bytes_current=True,
        qdp_accepted_and_current=True,ga_completed_and_current=True,
    )
    return p,current


def changed(current,**kwargs):
    new=replace(current,**kwargs,evidence_sha256="")
    return replace(new,evidence_sha256=canonical_hash(new.payload()))


class TestB24Freshness(unittest.TestCase):
    def test_same_source_verified_but_digest_alone_is_not_approval(self):
        p,c=pair()
        self.assertTrue(c.verify_digest())
        self.assertEqual(len(pins_digest(p)),64)
        not_authorized=compare_lineage(p,c,independently_verified=False)
        self.assertIs(not_authorized.state,FreshnessState.UNVERIFIED)
        self.assertFalse(not_authorized.qualified)
        admitted=compare_lineage(p,c,independently_verified=True)
        self.assertIs(admitted.state,FreshnessState.FRESH)
        self.assertTrue(admitted.qualified)
        self.assertEqual(admitted.reasons,())

    def test_changed_qdp_run_or_input_always_revokes_old_lineage(self):
        p,c=pair()
        for new in (
            changed(c,qdp_run_id=str(uuid4())),
            changed(c,qdp_input_sha256="9"*64),
            changed(c,qdp_accepted_and_current=False),
        ):
            with self.subTest(source=new):
                state=compare_lineage(p,new,independently_verified=True)
                self.assertIs(state.state,FreshnessState.STALE)
                self.assertTrue(any(x.startswith("qdp") for x in state.reasons))

    def test_group_a_result_id_or_sha_rerun_is_stale(self):
        p,c=pair()
        for new in (changed(c,ga_result_id=52),
                    changed(c,ga_result_sha256="9"*64),
                    changed(c,ga_completed_and_current=False)):
            state=compare_lineage(p,new,independently_verified=True)
            self.assertIs(state.state,FreshnessState.STALE)
            self.assertTrue(any(x.startswith("group_a") for x in state.reasons))

    def test_source_media_sha_rights_policy_and_revocation_are_stale(self):
        p,c=pair()
        variants=(
            changed(c,media_manifest_sha256="f"*64),
            changed(c,source_rights_active=False),
            changed(c,source_media_bytes_current=False),
            changed(c,source_policy_version_id=78),
        )
        for v in variants:
            with self.subTest(variant=v):
                report=compare_lineage(p,v,independently_verified=True)
                self.assertIs(report.state,FreshnessState.STALE)
                self.assertTrue(report.reasons)

    def test_only_immutable_human_correction_invalidates_not_gold_labels(self):
        p,c=pair()
        gold_annotations_only=p
        self.assertTrue(compare_lineage(
            gold_annotations_only,c,independently_verified=True).qualified)
        corrected=replace(p,review_correction_count=1,
                          review_correction_sha256="f"*64)
        verdict=compare_lineage(corrected,c,independently_verified=True)
        self.assertIn("human_correction",verdict.reasons)
        self.assertFalse(verdict.qualified)

    def test_cross_project_source_content_identity_is_error_not_refresh(self):
        p,c=pair()
        for v in (changed(c,project_id="another"),
                  changed(c,video_id=999),
                  changed(c,source_id="another"),
                  changed(c,content_type="carousel")):
            with self.assertRaises(InvalidationError):
                compare_lineage(p,v,independently_verified=True)

    def test_forged_sha_self_declared_ready_unverified(self):
        p,c=pair()
        incorrect=replace(c,ga_result_sha256="f"*64)
        report=compare_lineage(p,incorrect,independently_verified=True)
        self.assertIs(report.state,FreshnessState.UNVERIFIED)
        self.assertEqual(report.reasons,("upstream_unavailable",))
        invalid=replace(c,source_rights_active="true")
        self.assertFalse(invalid.verify_digest())
        self.assertFalse(compare_lineage(
            p,invalid,independently_verified=True).qualified)

    def test_invalid_historic_pin_and_missing_review_hash_rejected(self):
        p,c=pair()
        for bad in (replace(p,ga_result_sha256="not-a-digest"),
                    replace(p,review_correction_count=2,
                            review_correction_sha256=None)):
            with self.assertRaises(InvalidationError):
                compare_lineage(bad,c,independently_verified=True)


if __name__=="__main__":
    unittest.main()
