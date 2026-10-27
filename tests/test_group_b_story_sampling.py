"""GB-MOD-15: offline deterministic B3 2fps+1fps source-proof scheduling.

Tests enforce absence != observed negative; no model, video decode, fake
story role, paid request, background task or SQL write is performed.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4
import unittest

from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.context.evidence import MediaByteProof,VerifiedObservation
from intelligence.group_b.context.manifest import MediaEvidenceManifest
from intelligence.group_b.blocks.b3_sampling import (
    B3SamplingError,B3SamplingPlan,SamplingPolicy,build_b3_sampling_plan,
    _farthest_select,
)

PROJECT="a"*32
RUN=str(uuid4())
QDP=str(uuid4())
GA_RUN=str(uuid4())
GA_SHA="d"*64
VIDEO_SHA="b"*64


def frame(n,t,*,asset=11,asset_sha=VIDEO_SHA,slide=None):
    return VerifiedObservation(
        "sampled_frame",f"{n+1:064x}","f"*64,asset_sha,asset,
        "video_absolute",t,t,slide,None,
    )


def hint(kind,n,start,end,*,asset=11,frame_index=None):
    return VerifiedObservation(
        kind,f"{n+1:064x}","f"*64,VIDEO_SHA,asset,
        "video_absolute",start,end,None,
        f"{frame_index+1:064x}" if frame_index is not None else None,
    )


def video_manifest(*,duration=60000,times=(),hints=(),kind="video"):
    assets=(MediaByteProof(11,VIDEO_SHA,8000,"video",1,duration),)
    obs=tuple(frame(i,t) for i,t in enumerate(times))+tuple(hints)
    return manifest(assets=assets,observations=obs,kind=kind)


def manifest(*,assets,observations,kind):
    params=dict(
        schema_version="group-b-media-evidence/v1",
        project_id=PROJECT,qdp_run_id=QDP,b_run_id=RUN,
        ga_run_id=GA_RUN,ga_result_id=18,ga_result_sha256=GA_SHA,
        qdp_input_sha256="c"*64,media_manifest_sha256="e"*64,
        video_id=111,source_id="verified-source",source_policy_version_id=9,
        content_kind=kind,assets=tuple(assets),observations=tuple(observations),
    )
    unverified=MediaEvidenceManifest(**params,manifest_sha256="0"*64)
    return replace(unverified,manifest_sha256=canonical_hash(unverified.payload()))


def image_slide(n,*,index=None,asset_id=None,asset_sha=None):
    i=n if index is None else index
    a=10+n if asset_id is None else asset_id
    digest=f"{n+200:064x}" if asset_sha is None else asset_sha
    asset=MediaByteProof(a,digest,4000,"image",i,None)
    observation=VerifiedObservation(
        "slide_ref",f"{n+500:064x}","7"*64,digest,a,
        "none",None,None,i,None,
    )
    return asset,observation


class TestVideoScheduling(unittest.TestCase):
    def test_under_five_seconds_exact_grid_and_individual_verified_frames(self):
        m=video_manifest(duration=2300,times=(0,500,1000,1500,2000))
        p=build_b3_sampling_plan(m)
        self.assertTrue(p.verify_digest())
        self.assertEqual([s["target_ms"] for s in p.payload["slots"]],
                         [0,500,1000,1500,2000])
        self.assertTrue(p.payload["coverage"]["complete"])
        self.assertEqual(p.payload["coverage"]["matched_original_refs"],5)
        self.assertEqual(p.payload["coverage"]["missing_scheduled_refs"],0)
        self.assertEqual(p.payload["analysis_state"],"planned_not_inferred")
        self.assertFalse(p.payload["role_labels_assigned"])
        self.assertTrue(all(s["model_delivery_state"]=="not_sent"
                            for s in p.payload["slots"]))
        self.assertEqual(len(p.matched_refs),5)
        db=p.sampling_db_fields()
        self.assertEqual(db["first_5_sec_fps"],Decimal("2"))
        self.assertEqual(db["remaining_fps"],Decimal("1"))
        self.assertEqual(db["max_sampled_frames_target"],60)
        self.assertTrue(db["sampling_notes"]["source_data_not_model_delivered"])

    def test_5_second_edge_2fps_before_5_and_1fps_after(self):
        m=video_manifest(duration=7200,times=tuple(
            list(range(0,5000,500))+[5000,6000,7000]
        ))
        p=build_b3_sampling_plan(m)
        t=[s["target_ms"] for s in p.payload["slots"]]
        self.assertEqual(t,[*range(0,5000,500),5000,6000,7000])
        self.assertTrue(p.payload["coverage"]["complete"])
        self.assertEqual(p.payload["coverage"]["regular_grid_target_count"],13)

    def test_200_second_cap_60_slots_never_claim_long_video_complete(self):
        observations=tuple(range(0,230000,500))
        m=video_manifest(duration=230000,times=observations)
        p=build_b3_sampling_plan(m)
        self.assertEqual(len(p.payload["slots"]),60)
        self.assertEqual(p.payload["coverage"]["scheduled_duration_ms"],200000)
        self.assertTrue(p.payload["coverage"]["duration_truncated"])
        self.assertFalse(p.payload["coverage"]["complete"])
        self.assertEqual(p.payload["coverage"]["regular_grid_target_count"],205)
        self.assertEqual([s["target_ms"] for s in p.payload["slots"][:10]],
                         list(range(0,5000,500)))
        self.assertEqual(p.payload["slots"][-1]["target_ms"],199000)
        self.assertTrue(all(s["target_ms"]<200000 for s in p.payload["slots"]))
        self.assertEqual(p.payload["coverage"]["matched_original_refs"],60)

    def test_exact_200_second_video_not_marked_duration_truncated(self):
        m=video_manifest(duration=200000,times=tuple(range(0,200000,100)))
        p=build_b3_sampling_plan(m)
        self.assertEqual(len(p.payload["slots"]),60)
        self.assertFalse(p.payload["coverage"]["duration_truncated"])
        self.assertFalse(p.payload["coverage"]["complete"])
        self.assertGreater(p.payload["coverage"]["omitted_regular_grid_targets"],0)

    def test_missing_frame_and_position_offset_are_explicit_not_invented(self):
        m=video_manifest(duration=1800,times=(250,1030,1550))
        p=build_b3_sampling_plan(m)
        slots=p.payload["slots"]
        self.assertEqual([s["target_ms"] for s in slots],[0,500,1000,1500])
        self.assertEqual(slots[0]["observed_ms"],250)
        self.assertEqual(slots[0]["matched_ref_sha256"],m.observations[0].ref_sha256)
        self.assertEqual(slots[0]["status"],"matched")
        self.assertEqual(slots[1]["status"],"missing") # cannot reuse frame at 250
        self.assertIsNone(slots[1]["matched_ref_sha256"])
        self.assertEqual(slots[2]["observed_ms"],1030)
        self.assertEqual(slots[3]["observed_ms"],1550)
        self.assertEqual(p.payload["coverage"]["missing_scheduled_refs"],1)
        self.assertFalse(p.payload["coverage"]["complete"])
        self.assertEqual(len(set(p.matched_refs)),len(p.matched_refs))

    def test_outside_early_tolerance_must_not_fake_a_sampled_frame(self):
        m=video_manifest(duration=1200,times=(300,800))
        p=build_b3_sampling_plan(m)
        self.assertEqual([x["status"] for x in p.payload["slots"]],
                         ["missing","matched","matched"])

    def test_sparse_verified_ga_frames_are_not_generated(self):
        m=video_manifest(duration=60000,times=(0,29999,59800))
        p=build_b3_sampling_plan(m)
        self.assertEqual(p.payload["coverage"]["matched_original_refs"],3)
        self.assertGreater(p.payload["coverage"]["missing_scheduled_refs"],40)
        self.assertEqual(len(p.matched_refs),3)
        self.assertFalse(p.payload["coverage"]["complete"])

    def test_scene_and_ocr_are_hints_not_claimed_cuts_or_roles(self):
        notes=(hint("scene",1900,20000,26000),
               hint("ocr_span",1901,33250,33600,frame_index=133))
        m=video_manifest(duration=60000,times=tuple(range(0,60000,250)),
                         hints=notes)
        p=build_b3_sampling_plan(m)
        self.assertLessEqual(len(p.payload["slots"]),60)
        adaptive=[x for x in p.payload["slots"]
                  if x["target_source"]=="adaptive_hint"]
        self.assertTrue(any(x["target_ms"]==20000 for x in p.payload["slots"]) or
                        any(x["target_ms"]==33250 for x in adaptive))
        self.assertTrue(all(x["matched_kind"] in ("sampled_frame",None)
                            for x in p.payload["slots"]))
        self.assertTrue(all("role" not in slot for slot in p.payload["slots"]))
        self.assertFalse(p.payload["role_labels_assigned"])
        self.assertNotIn("cut_count",str(p.payload))
        self.assertFalse(p.payload["coverage"]["delivered_to_model"])

    def test_all_chosen_verified_frames_does_not_hide_grid_budget_omissions(self):
        m=video_manifest(duration=60000,times=tuple(range(0,60000,500)))
        plan=build_b3_sampling_plan(m)
        self.assertEqual(plan.payload["coverage"]["matched_original_refs"],60)
        self.assertEqual(plan.payload["coverage"]["missing_scheduled_refs"],0)
        self.assertGreater(plan.payload["coverage"]["omitted_regular_grid_targets"],0)
        self.assertFalse(plan.payload["coverage"]["complete"])
        self.assertEqual(plan.payload["coverage"]["regular_grid_target_count"],65)

    def test_adaptive_plan_is_deterministic_under_observation_shuffle(self):
        hints=(hint("scene",1900,22250,22800),
               hint("ocr_span",1901,33250,33750,frame_index=67))
        m=video_manifest(duration=90000,times=tuple(range(0,90000,500)),
                         hints=hints)
        reversed_manifest=replace(
            m,observations=tuple(reversed(m.observations))
        )
        reversed_manifest=replace(
            reversed_manifest,
            manifest_sha256=canonical_hash(reversed_manifest.payload())
        )
        a=build_b3_sampling_plan(m)
        b=build_b3_sampling_plan(reversed_manifest)
        self.assertEqual(
            [s["target_ms"] for s in a.payload["slots"]],
            [s["target_ms"] for s in b.payload["slots"]]
        )
        self.assertEqual(
            [s["matched_ref_sha256"] for s in a.payload["slots"]],
            [s["matched_ref_sha256"] for s in b.payload["slots"]]
        )
        self.assertNotEqual(a.plan_sha256,b.plan_sha256) # manifest order pinned
        self.assertEqual(a.plan_sha256,build_b3_sampling_plan(m).plan_sha256)

    def test_small_cap_invalid_and_hints_can_be_disabled(self):
        with self.assertRaises(B3SamplingError):
            SamplingPolicy(max_sampled_frames_target=9)
        with self.assertRaises(B3SamplingError):
            SamplingPolicy(first_5_sec_fps=3)
        with self.assertRaises(B3SamplingError):
            SamplingPolicy(max_video_window_ms=300000)
        notes=(hint("ocr_span",1900,7750,8000,frame_index=31),)
        m=video_manifest(duration=18000,times=tuple(range(0,18000,250)),
                         hints=notes)
        p=build_b3_sampling_plan(m,policy=SamplingPolicy(
            extra_at_text_changes=False
        ))
        self.assertTrue(all(s["target_source"]!="adaptive_hint"
                            for s in p.payload["slots"]))

    def test_bad_or_negative_or_out_of_range_original_video_times(self):
        malformed=(
            (frame(0,-100),),
            (frame(0,60001),),
            (replace(frame(0,100),timebase="clip_relative"),),
            (replace(frame(0,200),asset_sha256="d"*64),),
        )
        for observations in malformed:
            m=video_manifest(duration=60000)
            m=replace(m,observations=observations)
            m=replace(m,manifest_sha256=canonical_hash(m.payload()))
            with self.subTest(observations=observations),self.assertRaises(B3SamplingError):
                build_b3_sampling_plan(m)
        for duration in (None,0,86_400_001):
            m=video_manifest(duration=duration,times=())
            with self.assertRaises(B3SamplingError):
                build_b3_sampling_plan(m)

    def test_ocr_adaptive_hint_requires_original_matching_frame_reference(self):
        missing=video_manifest(duration=10000,times=tuple(range(0,10000,500)),
                               hints=(hint("ocr_span",1900,2500,3000),))
        with self.assertRaises(B3SamplingError):
            build_b3_sampling_plan(missing)
        wrong=video_manifest(duration=10000,times=tuple(range(0,10000,500)),
                             hints=(hint("ocr_span",1900,2500,3000,frame_index=14),))
        with self.assertRaises(B3SamplingError):
            build_b3_sampling_plan(wrong)

    def test_unverified_manifest_or_duplicate_refs_rejected(self):
        m=video_manifest(duration=2000,times=(0,))
        with self.assertRaises(B3SamplingError):
            build_b3_sampling_plan(replace(m,manifest_sha256="0"*64))
        m=video_manifest(duration=2000,times=(0,500))
        wrong=replace(m,observations=(m.observations[0],m.observations[0]))
        wrong=replace(wrong,manifest_sha256=canonical_hash(wrong.payload()))
        with self.assertRaises(B3SamplingError):
            build_b3_sampling_plan(wrong)

    def test_zero_observed_frames_means_all_missing_and_no_claims(self):
        m=video_manifest(duration=1800,times=())
        p=build_b3_sampling_plan(m)
        self.assertEqual(p.matched_refs,())
        self.assertEqual(p.payload["coverage"]["missing_scheduled_refs"],4)
        self.assertFalse(p.payload["coverage"]["complete"])


class TestCarouselScheduling(unittest.TestCase):
    def test_all_three_photo_slides_keep_ordinal_without_video_timestamps(self):
        pairs=[image_slide(i) for i in range(1,4)]
        m=manifest(assets=[a for a,_ in pairs],
                   observations=[o for _,o in pairs],kind="photo_carousel")
        p=build_b3_sampling_plan(m)
        self.assertTrue(p.verify_digest())
        self.assertEqual([s["slide_index"] for s in p.payload["slots"]],[1,2,3])
        self.assertEqual([s["matched_kind"] for s in p.payload["slots"]],
                         ["slide_ref"]*3)
        self.assertTrue(all(s["target_ms"] is None and s["observed_ms"] is None
                            for s in p.payload["slots"]))
        self.assertTrue(p.payload["coverage"]["complete"])
        self.assertEqual(len(p.matched_refs),3)

    def test_missing_middle_slide_never_promoted_to_observed(self):
        pairs=[image_slide(i) for i in range(1,4)]
        m=manifest(assets=[a for a,_ in pairs],
                   observations=[pairs[0][1],pairs[2][1]],
                   kind="photo_carousel")
        p=build_b3_sampling_plan(m)
        self.assertEqual([s["status"] for s in p.payload["slots"]],
                         ["matched","missing","matched"])
        self.assertFalse(p.payload["coverage"]["complete"])

    def test_mixed_carousel_video_slide_uses_its_own_asset_frame_only(self):
        image,slide=image_slide(1)
        clip=MediaByteProof(30,"9"*64,3333,"video",2,2000)
        clip_frame=frame(2200,750,asset=30,asset_sha="9"*64,slide=2)
        m=manifest(assets=(image,clip),
                   observations=(slide,clip_frame),kind="mixed_carousel")
        p=build_b3_sampling_plan(m)
        self.assertTrue(p.payload["coverage"]["complete"])
        self.assertEqual([s["matched_kind"] for s in p.payload["slots"]],
                         ["slide_ref","sampled_frame"])
        self.assertEqual(p.payload["slots"][1]["slide_index"],2)
        self.assertEqual(p.payload["slots"][1]["observed_ms"],750)
        self.assertIsNone(p.payload["slots"][1]["target_ms"])
        self.assertEqual(p.payload["slots"][1]["asset_sha256"],clip.source_sha256)

    def test_mixed_carousel_without_video_slide_frame_missing_not_inferred(self):
        image,slide=image_slide(1)
        clip=MediaByteProof(30,"9"*64,3333,"video",2,2000)
        m=manifest(assets=(image,clip),observations=(slide,),
                   kind="mixed_carousel")
        p=build_b3_sampling_plan(m)
        self.assertEqual(p.payload["slots"][1]["status"],"missing")
        self.assertFalse(p.payload["coverage"]["complete"])

    def test_over_60_carousel_slides_record_explicit_budget_omission(self):
        pairs=[image_slide(i) for i in range(1,62)]
        m=manifest(assets=[a for a,_ in pairs],
                   observations=[o for _,o in pairs],kind="photo_carousel")
        p=build_b3_sampling_plan(m)
        self.assertEqual(p.payload["coverage"]["scheduled_target_count"],60)
        self.assertEqual(p.payload["coverage"]["omitted_budget_slots"],1)
        self.assertEqual(len(p.payload["slots"]),61)
        self.assertFalse(p.payload["coverage"]["complete"])
        self.assertEqual(p.payload["slots"][0]["status"],"matched")
        self.assertEqual(p.payload["slots"][-1]["status"],"matched")

    def test_carousel_gap_duplicate_or_fake_slide_ordinal_rejected(self):
        pairs=[image_slide(i) for i in range(1,4)]
        for assets,obs in (
            ([pairs[0][0],pairs[2][0]],[pairs[0][1],pairs[2][1]]),
            ([pairs[0][0],pairs[0][0]],[pairs[0][1]]),
            ([pairs[0][0],pairs[1][0]],
             [replace(pairs[0][1],slide_index=2),pairs[1][1]]),
        ):
            m=manifest(assets=assets,observations=obs,kind="photo_carousel")
            with self.subTest(assets=assets),self.assertRaises(B3SamplingError):
                build_b3_sampling_plan(m)

    def test_mixed_requires_both_video_and_image_assets(self):
        pairs=[image_slide(i) for i in (1,2)]
        m=manifest(assets=[a for a,_ in pairs],
                   observations=[o for _,o in pairs],kind="mixed_carousel")
        with self.assertRaises(B3SamplingError):
            build_b3_sampling_plan(m)

    def test_005_sampling_metadata_contains_no_fabricated_story_roles(self):
        a,b=image_slide(1),image_slide(2)
        m=manifest(assets=(a[0],b[0]),observations=(a[1],b[1]),
                   kind="photo_carousel")
        p=build_b3_sampling_plan(m)
        fields=p.sampling_db_fields()
        self.assertEqual(set(fields),{
            "first_5_sec_fps","remaining_fps","max_sampled_frames_target",
            "extra_at_scene_cuts","extra_at_text_changes","sampling_notes",
        })
        self.assertNotIn("role",fields)
        self.assertEqual(fields["sampling_notes"]["plan_sha256"],p.plan_sha256)


if __name__=="__main__":
    unittest.main()
