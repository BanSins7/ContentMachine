"""GB-MOD-07: pure block dependencies and fresh QDP/A/media preflight tests.

Never runs paid providers, writes DB block states, or claims a mock is a live
cross-branch producer. Disabled/missing rights always fail closed.
"""
from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4
import unittest

from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.context.evidence import MediaByteProof, VerifiedObservation, EvidenceFailure
from intelligence.group_b.context.manifest import MediaEvidenceManifest
from intelligence.group_b.dependency_rules import (
    BLOCKS, ReadinessState, ReadinessReport, evaluate_block_dependencies,
)
from intelligence.group_b.readiness import assess_current_run, require_block_ready
from intelligence.group_b.adapters.qdp import VerifiedPreparation
from intelligence.group_b.errors import DependencyUnavailable

PROJECT="a"*32
RUN=str(uuid4())
QDP=str(uuid4())
EVENT=str(uuid4())
GA_RUN=str(uuid4())
SOURCE="source-t"
VIDEO=94
MEDIA_SHA="d"*64
PREP_SHA="b"*64
GA_MANIFEST="c"*64

def stream(*,status="complete",count=1,absence=False,reason=None):
    return {"status":status,"evidence_count":count,"verified_absence":absence,
            "reason_code":reason}

def streams(kind="video"):
    names = ("technical","speech","screen_text","audio","subtitles") if kind=="video" else (
        "slides","screen_text")
    return {name:stream() for name in names}

def observation(kind,ref, *,asset=11,slide=None,start=0,end=10):
    return VerifiedObservation(kind,ref,"e"*64,MEDIA_SHA,asset,
                               "none" if kind=="slide_ref" else "video_absolute",
                               None if kind=="slide_ref" else start,
                               None if kind=="slide_ref" else end,
                               slide)

def manifest(kind="video", observations=None):
    if observations is None:
        observations=(
            observation("scene","1"*64),
            observation("sampled_frame","2"*64,start=1,end=1),
            observation("transcript_segment","3"*64),
            observation("ocr_span","4"*64),
            observation("audio_event","5"*64),
        ) if kind=="video" else (
            observation("slide_ref","1"*64,asset=11,slide=1),
            observation("slide_ref","2"*64,asset=12,slide=2),
        )
    assets=(MediaByteProof(11,MEDIA_SHA,100,"video",1,10000),) if kind=="video" else (
        MediaByteProof(11,MEDIA_SHA,100,"image",1,None),
        MediaByteProof(12,MEDIA_SHA,100,"image",2,None),
    )
    data={
        "schema_version":"group-b-media-evidence/v1",
        "project_id":PROJECT, "qdp_run_id":QDP,"b_run_id":RUN,"ga_run_id":GA_RUN,
        "ga_result_id":72,"ga_result_sha256":"f"*64,
        "qdp_input_sha256":PREP_SHA,
        "media_manifest_sha256":GA_MANIFEST,
        "video_id":VIDEO,"source_id":SOURCE,"source_policy_version_id":15,
        "content_kind":kind if kind=="video" else "photo_carousel",
        "assets":assets,"observations":tuple(observations),
    }
    pre=MediaEvidenceManifest(**data,manifest_sha256="0"*64)
    return replace(pre,manifest_sha256=canonical_hash(pre.payload()))

class TestDependencyRules(unittest.TestCase):
    def test_video_all_evidence_covers_eight_blocks(self):
        m=manifest()
        report=evaluate_block_dependencies(m,a_streams=streams())
        self.assertEqual(tuple(x.block for x in report.blocks),BLOCKS)
        self.assertTrue(report.all_ready)
        self.assertTrue(all(x.evidence_sha256==m.manifest_sha256 for x in report.blocks))
        self.assertEqual(require_block_ready(report,"B5",
                                            expected_manifest_sha256=m.manifest_sha256).block,"B5")
        with self.assertRaises(DependencyUnavailable):
            require_block_ready(report,"B5",expected_manifest_sha256="0"*64)

    def test_silent_video_missing_audio_event_is_not_false(self):
        m=manifest(observations=(
            observation("scene","1"*64),
            observation("sampled_frame","2"*64,start=1,end=1),
            observation("transcript_segment","3"*64),
            observation("ocr_span","4"*64),
        ))
        truth=streams()
        truth["speech"]=stream(count=0,absence=True)
        truth["audio"]=stream(count=0,absence=True)
        report=evaluate_block_dependencies(m,a_streams=truth)
        self.assertTrue(report.for_block("B1").can_attempt)
        self.assertTrue(report.for_block("B7").can_attempt)
        self.assertTrue(report.for_block("B8").can_attempt)

    def test_unknown_speech_is_partial_never_no_cta(self):
        truth=streams()
        truth["speech"]=stream(status="not_run",count=None,reason="not_measured")
        report=evaluate_block_dependencies(manifest(),a_streams=truth)
        for block in ("B1","B7","B8"):
            self.assertEqual(report.for_block(block).state,ReadinessState.PARTIAL)
            self.assertIn("a_speech_not_measured",report.for_block(block).reason_codes)
        self.assertFalse(report.all_ready)

    def test_subtitles_zero_ocr_not_inferred_none(self):
        m=manifest(observations=(
            observation("scene","1"*64),
            observation("sampled_frame","2"*64,start=1,end=1),
            observation("transcript_segment","3"*64),
        ))
        truth=streams()
        self.assertEqual(
            evaluate_block_dependencies(m,a_streams=truth).for_block("B6").state,
            ReadinessState.PARTIAL)
        truth["subtitles"]=stream(count=0,absence=True)
        r=evaluate_block_dependencies(m,a_streams=truth)
        self.assertIn("subtitle_absence_proof_missing",r.for_block("B6").reason_codes)

    def test_missing_samples_and_scenes_partial_not_zero_cuts(self):
        m=manifest(observations=(
            observation("transcript_segment","3"*64),
            observation("ocr_span","4"*64),
        ))
        report=evaluate_block_dependencies(m,a_streams=streams())
        self.assertEqual(report.for_block("B3").state,ReadinessState.PARTIAL)
        self.assertEqual(report.for_block("B5").state,ReadinessState.PARTIAL)
        self.assertIn("scene_measurement_missing",report.for_block("B5").reason_codes)

    def test_carousel_b5_b6_not_applicable_and_slide_coverage(self):
        m=manifest("carousel")
        report=evaluate_block_dependencies(m,a_streams=streams("carousel"))
        for b in ("B5","B6"):
            self.assertEqual(report.for_block(b).state,ReadinessState.BLOCKED)
        self.assertTrue(report.for_block("B3").can_attempt)
        self.assertTrue(report.for_block("B2").can_attempt)
        incomplete=replace(m,observations=(m.observations[0],))
        incomplete=replace(incomplete,manifest_sha256=canonical_hash(incomplete.payload()))
        r=evaluate_block_dependencies(incomplete,a_streams=streams("carousel"))
        self.assertIn("carousel_slide_coverage_incomplete",r.for_block("B3").reason_codes)

    def test_image_post_is_not_supported_as_b2(self):
        m=manifest("carousel")
        bad=replace(m,content_kind="image_post")
        bad=replace(bad,manifest_sha256=canonical_hash(bad.payload()))
        report=evaluate_block_dependencies(bad,a_streams=streams("carousel"))
        self.assertEqual(report.for_block("B2").state,ReadinessState.BLOCKED)

    def test_mutated_manifest_hash_or_a_truth_never_grants_ready(self):
        m=replace(manifest(),manifest_sha256="0"*64)
        with self.assertRaises(ValueError):
            evaluate_block_dependencies(m,a_streams=streams())
        wrong=streams()
        wrong.pop("speech")
        report=evaluate_block_dependencies(manifest(),a_streams=wrong)
        self.assertFalse(report.all_ready)
        self.assertIn("a_stream_matrix_invalid",report.global_reason_codes)
        with self.assertRaises(ValueError):
            require_block_ready(report,"B0",expected_manifest_sha256="0"*64)


class Result:
    def __init__(self,value=None):
        self.value=value
    def fetchone(self):
        return self.value


class MockConn:
    def __init__(self,rows):
        self.rows=rows
        self.calls=[]
    def execute(self,sql,args=()):
        self.calls.append((sql,args))
        if "SELECT b.id::text AS b_run_id" in sql:
            return Result(self.rows["run"])
        if "FROM group_b_inbox_receipts r" in sql:
            return Result(self.rows["receipt"])
        if "FROM ga_results g" in sql:
            return Result(self.rows["ga_result"])
        raise AssertionError("Unexpected SQL: "+sql[:90])


def fixture():
    truth=streams()
    snapshot={
        "analysis_state":"completed","streams":truth,
        "manifest_sha256":GA_MANIFEST,"content_kind":"video",
    }
    sha=canonical_hash(snapshot)
    m=replace(manifest(),ga_result_sha256=sha)
    m=replace(m,manifest_sha256=canonical_hash(m.payload()))
    row={
        "b_run_id":RUN,"project_id":PROJECT,"video_id":VIDEO,"source_id":SOURCE,
        "content_type":"video","b_state":"draft",
        "video_a_analysis_id":351,"carousel_a_analysis_id":None,
        "a_snapshot_sha256":sha,"media_manifest_sha256":GA_MANIFEST,
        "source_policy_version_id":15,
        "inbox_state":"accepted","event_key":EVENT,"event_sha256":"e"*64,
        "qdp_run_id":QDP,"preparation_sha256":PREP_SHA,
    }
    receipt={"event_id":EVENT,"event_sha256":"e"*64,
             "preparation_sha256":PREP_SHA,"run_id":RUN}
    prep=VerifiedPreparation(
        PROJECT,EVENT,QDP,VIDEO,SOURCE,"video","video",
        PREP_SHA,"e"*64,15,11,MEDIA_SHA
    )
    ga={"state":"completed","result_schema":"group-a-to-b/v1",
        "result_sha256":sha,"snapshot":snapshot,
        "video_a_analysis_id":351,"carousel_a_analysis_id":None}
    return m,prep,{"run":row,"receipt":receipt,"ga_result":ga}


class TestFreshPreflight(unittest.TestCase):
    def assess(self, *, data=None,prep=None,manifest_value=None,side_effect=None):
        expected_m,expected_p,rows=fixture()
        if data is not None:
            rows=data
        if prep is None:
            prep=expected_p
        if manifest_value is None:
            manifest_value=expected_m
        c=MockConn(rows)
        with patch("intelligence.group_b.readiness.require_runtime_schema"),patch(
            "intelligence.group_b.readiness.read_current_preparation",
            return_value=prep,side_effect=side_effect),patch(
            "intelligence.group_b.readiness.build_media_evidence_manifest",
            return_value=manifest_value):
            report=assess_current_run(
                c,project_id=PROJECT,b_run_id=RUN,ga_result_id=72,
                ga_result_sha256=expected_m.ga_result_sha256,
                media_roots=["/not-accessed"],observation_catalog=None
            )
        return report,c

    def test_fresh_rechecks_qdp_receipt_ga_snapshot_and_evidence(self):
        report,conn=self.assess()
        self.assertTrue(report.all_ready)
        self.assertEqual(len(conn.calls),3)
        self.assertEqual([x[0].split("FROM ")[1].split()[0] for x in conn.calls],
                         ["group_b_runs","group_b_inbox_receipts","ga_results"])

    def test_revoked_qdp_policy_blocks_every_block(self):
        report,_=self.assess(side_effect=DependencyUnavailable(
            "qdp","policy_media_rights_revoked"))
        self.assertFalse(report.all_ready)
        self.assertEqual(report.global_reason_codes,
                         ("qdp_preparation_or_rights_revoked",))
        self.assertTrue(all(v.state is ReadinessState.BLOCKED for v in report.blocks))

    def test_missing_peer_schema_is_partial_not_positive(self):
        report,_=self.assess(side_effect=DependencyUnavailable(
            "qdp","qdp_tables_missing"))
        self.assertEqual(report.for_block("B1").state,ReadinessState.PARTIAL)
        self.assertIn("qdp_producer_or_schema_unavailable",report.global_reason_codes)

    def test_receipt_tampering_rejected_before_qdp(self):
        m,prep,data=fixture()
        data["receipt"]={"event_id":str(uuid4()),"event_sha256":"e"*64,
                         "preparation_sha256":PREP_SHA,"run_id":RUN}
        report,conn=self.assess(data=data)
        self.assertEqual(report.global_reason_codes,("b_storage_or_identity_invalid",))
        self.assertEqual(len(conn.calls),2)

    def test_cross_video_or_policy_preparation_rejected(self):
        base_m,prep,data=fixture()
        for replacement in (
            replace(prep,video_id=VIDEO+1),
            replace(prep,source_id="another-source"),
            replace(prep,source_policy_version_id=33),
        ):
            with self.subTest(preparation=replacement):
                report,_=self.assess(prep=replacement)
                self.assertEqual(report.for_block("B5").state,ReadinessState.BLOCKED)

    def test_qdp_asset_must_be_in_ga_byte_verified_assets(self):
        m,prep,data=fixture()
        report,_=self.assess(prep=replace(prep,media_asset_id=999))
        self.assertEqual(report.for_block("B1").state,ReadinessState.BLOCKED)

    def test_ga_snapshot_swap_or_result_hash_rejected(self):
        m,prep,data=fixture()
        data["ga_result"]["result_sha256"]="0"*64
        report,_=self.assess(data=data)
        self.assertIn("group_a_snapshot_invalid",report.global_reason_codes)

    def test_media_byte_corruption_returns_blocked_no_run(self):
        report,_=self.assess(side_effect=DependencyUnavailable("qdp","media_expired"))
        self.assertEqual(report.for_block("B2").state,ReadinessState.BLOCKED)

    def test_project_and_run_selector_validation(self):
        with self.assertRaises(ValueError):
            assess_current_run(MockConn({}),project_id="bad",b_run_id=RUN,
                               ga_result_id=72,ga_result_sha256="f"*64,
                               media_roots=[],observation_catalog=None)


if __name__=="__main__":
    unittest.main()
