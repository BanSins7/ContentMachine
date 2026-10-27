"""GB-MOD-12 source-first local B5: measured shots, SQL thresholds, exact SHA.

No invented cuts from narrative scenes; no paid model, FFmpeg or network.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4
from unittest.mock import Mock
import unittest

from intelligence.group_b.blocks.b5_editing import (
    B5MeasurementUnavailable, B5EditingExecutor, B5EditingVerifier,
    analyze_b5, read_shot_timeline, VERSION, SHOT_TIMELINE,
)
from intelligence.group_b.context.evidence import MediaByteProof, VerifiedObservation
from intelligence.group_b.context.manifest import MediaEvidenceManifest
from intelligence.group_b.orchestrator import JobClaim
from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.worker import LocalResult

PROJECT="a"*32
RUN=str(uuid4())
GA_RUN=str(uuid4())
QDP_RUN=str(uuid4())
MEDIA_SHA="b"*64
GA_SHA="c"*64
GA_MEDIA_SHA="d"*64


def scene(n,start,end):
    return VerifiedObservation("scene",f"{n+1:064x}","e"*64,
                               MEDIA_SHA,42,"video_absolute",start,end)


def make_manifest(shots,*,duration_ms=60_000,kind="video",extra=()):
    raw=MediaEvidenceManifest(
        schema_version="group-b-media-evidence/v1",project_id=PROJECT,
        qdp_run_id=QDP_RUN,b_run_id=RUN,ga_run_id=GA_RUN,ga_result_id=8,
        ga_result_sha256=GA_SHA,qdp_input_sha256="f"*64,
        media_manifest_sha256=GA_MEDIA_SHA,video_id=21,source_id="trusted-one",
        source_policy_version_id=7,content_kind=kind,
        assets=(MediaByteProof(42,MEDIA_SHA,700,"video",1,duration_ms),),
        observations=tuple(shots)+tuple(extra),manifest_sha256="0"*64
    )
    return replace(raw,manifest_sha256=canonical_hash(raw.payload()))


def intervals(cuts,*,duration_ms=60_000):
    """Exact, contiguous intervals from explicit measured boundaries."""
    return tuple(scene(i,left,right) for i,(left,right) in enumerate(
        zip((0,*cuts),(*cuts,duration_ms))
    ))


def attestation(m,*,status="complete",kind="shot_boundaries"):
    obj={
        "schema_version":SHOT_TIMELINE,"manifest_sha256":m.manifest_sha256,
        "ga_result_sha256":m.ga_result_sha256,
        "media_asset_id":42,"media_sha256":MEDIA_SHA,
        "duration_ms":m.assets[0].duration_ms,
        "segmentation_kind":kind,"status":status,
        "shot_refs":[x.ref_sha256 for x in m.observations if x.kind=="scene"],
    }
    obj["timeline_sha256"]=canonical_hash(obj)
    return obj


class Catalog:
    def __init__(self,row):
        self.row=row
        self.calls=0
    def resolve_shot_timeline(self,m):
        self.calls+=1
        return self.row


def claim(m):
    return JobClaim(str(uuid4()),PROJECT,RUN,"B5","local",
                    m.manifest_sha256,str(uuid4()),1,1,3)


class TestEditingMath(unittest.TestCase):
    def calculate(self,cuts,*,duration_ms=60_000):
        m=make_manifest(intervals(cuts,duration_ms=duration_ms),
                        duration_ms=duration_ms)
        timeline=read_shot_timeline(m,Catalog(attestation(m)))
        return analyze_b5(timeline)

    def test_one_measured_shot_single_take_not_missing_zero(self):
        x=self.calculate(())
        self.assertEqual(x.editing_type,"single_take")
        self.assertEqual(x.cut_count,0)
        self.assertEqual(x.cuts_per_minute,Decimal("0.0000"))
        self.assertEqual(x.avg_shot_duration,Decimal("60.0000"))
        self.assertEqual(x.cuts_first_5_sec,0)
        self.assertEqual(set(x.db_fields()),{
            "editing_type","cut_count","cuts_per_minute",
            "avg_shot_duration","cuts_first_5_sec"
        })

    def test_slow_rate_threshold_is_per_minute_not_absolute_count(self):
        x=self.calculate((30_000,))
        self.assertEqual(x.editing_type,"slow_cuts")
        self.assertEqual(x.cuts_per_minute,Decimal("1.0000"))
        # Eleven total cuts over two minutes is still *slow*, not medium.
        cuts=tuple(10_000*i for i in range(1,12))
        slower=self.calculate(cuts,duration_ms=120_000)
        self.assertEqual(slower.cut_count,11)
        self.assertEqual(slower.editing_type,"slow_cuts")
        self.assertEqual(slower.cuts_per_minute,Decimal("5.5000"))

    def test_exact_10_and_30_cuts_per_minute_boundaries(self):
        m10=self.calculate(tuple(6000*i for i in range(1,11)))
        self.assertEqual(m10.cut_count,10)
        self.assertEqual(m10.editing_type,"medium_cuts")
        self.assertEqual(m10.cuts_per_minute,Decimal("10.0000"))
        m30=self.calculate(tuple(2000*i for i in range(1,31)))
        self.assertEqual(m30.cut_count,30)
        self.assertEqual(m30.editing_type,"fast_cuts")
        self.assertEqual(m30.cuts_per_minute,Decimal("30.0000"))

    def test_4999_in_first_five_but_5000_is_excluded(self):
        x=self.calculate((4999,5000,5001,10000))
        self.assertEqual(x.cut_count,4)
        self.assertEqual(x.cuts_first_5_sec,1)

    def test_four_decimal_half_up_and_repeatability(self):
        x=self.calculate((1000,10000,17000),duration_ms=31_000)
        self.assertEqual(x.cuts_per_minute,Decimal("5.8065"))
        self.assertEqual(x.avg_shot_duration,Decimal("7.7500"))
        self.assertEqual(x,self.calculate((1000,10000,17000),
                                          duration_ms=31_000))
        self.assertEqual(x.wire()["cuts_per_minute"],"5.8065")

    def test_quantized_type_respects_original_005_sql_check(self):
        x=self.calculate(tuple(i*100 for i in range(1,30)),duration_ms=6000)
        self.assertEqual(x.editing_type,"fast_cuts")
        self.assertGreaterEqual(x.cuts_per_minute,30)
        self.assertGreater(x.avg_shot_duration,0)


class TestEditingFailClosed(unittest.TestCase):
    def setUp(self):
        self.m=make_manifest(intervals((10_000,25_000)))
        self.raw=attestation(self.m)

    def rejected(self,manifest=None,catalog=None):
        with self.assertRaises(B5MeasurementUnavailable):
            read_shot_timeline(manifest or self.m,catalog or Catalog(self.raw))

    def test_absent_catalog_or_partial_measurement_must_not_mean_zero_cuts(self):
        self.rejected(catalog=object())
        self.rejected(catalog=Catalog(dict(self.raw,status="partial")))
        self.rejected(catalog=Catalog(dict(self.raw,status="not_run")))

    def test_narrative_chapters_not_allowed_as_cut_boundaries(self):
        self.rejected(catalog=Catalog(attestation(self.m,kind="semantic_chapters")))

    def test_missing_first_last_gap_overlap_duplicates(self):
        cases=(
            (scene(0,100,10_000),scene(1,10_000,60_000)),
            (scene(0,0,10_000),scene(1,10_001,60_000)),
            (scene(0,0,10_000),scene(1,9_999,60_000)),
            (scene(0,0,10_000),scene(1,10_000,50_000)),
            (scene(0,0,10_000),scene(0,10_000,60_000)),
        )
        for shots in cases:
            m=make_manifest(shots)
            with self.subTest(shots=shots),self.assertRaises(B5MeasurementUnavailable):
                read_shot_timeline(m,Catalog(attestation(m)))

    def test_malformed_timeline_sha_wrong_version_video_or_duration(self):
        for change in (
            {"timeline_sha256":"0"*64},
            {"manifest_sha256":"0"*64},
            {"ga_result_sha256":"0"*64},
            {"segmentation_kind":"semantic_chapters"},
            {"duration_ms":59_000},
            {"media_asset_id":99},
            {"shot_refs":[]},
        ):
            with self.subTest(change=change),self.assertRaises(B5MeasurementUnavailable):
                read_shot_timeline(self.m,Catalog(dict(self.raw,**change)))
        zero=make_manifest(intervals((10_000,)),duration_ms=0)
        self.rejected(manifest=zero)
        mutated=replace(self.m,manifest_sha256="0"*64)
        self.rejected(manifest=mutated)
        photo=make_manifest(intervals((10_000,)),kind="photo_carousel")
        self.rejected(manifest=photo)

    def test_unknown_scene_count_empty_and_missing_evidence(self):
        empty=make_manifest(())
        self.rejected(manifest=empty,catalog=Catalog(attestation(empty)))
        m=make_manifest((scene(1,0,60_000),))
        att=attestation(m)
        att["shot_refs"].append("f"*64)
        att["timeline_sha256"]=canonical_hash(
            {k:v for k,v in att.items() if k!="timeline_sha256"}
        )
        self.rejected(manifest=m,catalog=Catalog(att))

    def test_output_executor_and_independent_verifier(self):
        cat=Catalog(self.raw)
        claimed=claim(self.m)
        result=B5EditingExecutor().execute_local(claimed,self.m,cat)
        self.assertTrue(result.verify_digest())
        self.assertEqual(result.output["metrics"]["cut_count"],2)
        self.assertEqual(result.output["schema_version"],VERSION)
        self.assertTrue(B5EditingVerifier().verify_local_result(
            claimed,result,self.m,cat))
        altered={**result.output,"metrics":{
            **result.output["metrics"],"cuts_first_5_sec":99}}
        forged=LocalResult("B5",self.m.manifest_sha256,altered,
                           canonical_hash(altered))
        self.assertTrue(forged.verify_digest())
        self.assertFalse(B5EditingVerifier().verify_local_result(
            claimed,forged,self.m,cat))
        self.assertFalse(B5EditingVerifier().verify_local_result(
            replace(claimed,run_id=str(uuid4())),result,self.m,cat))
        self.assertFalse(B5EditingVerifier().verify_local_result(
            claimed,result,replace(self.m,manifest_sha256="0"*64),cat))
        self.assertEqual(cat.calls,3)

    def test_no_llm_or_model_provider_modules_imported(self):
        import inspect
        import intelligence.group_b.blocks.b5_editing as module
        text=inspect.getsource(module)
        self.assertNotIn("post_json(",text)
        self.assertNotIn("OpenRouterProvider",text)
        self.assertNotIn("model_api_key",text)


if __name__=="__main__":
    unittest.main()
