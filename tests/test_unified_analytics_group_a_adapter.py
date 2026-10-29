"""UA-MOD-08 Group A source-backed exact fact adapter test vectors.

Mock-complete data is a FUTURE hypothetical integration, not genuine deployed
QDP and GA peer verification. Test no fabricated carousel swipe_time.
"""
from __future__ import annotations

import copy
import os
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from intelligence.unified_analytics import AnalyticsBoundaryError, InputScope
from intelligence.unified_analytics.dto import EvidenceRead, EvidenceRef, ReadState
from intelligence.unified_analytics.ports import (
    UAEPorts, PortCapability, CapabilityState, PORT_SCHEMAS,
)
from intelligence.unified_analytics.qdp_adapter import QDPQualifiedSetReader
from intelligence.unified_analytics.group_a_adapter import (
    GA_PEER_WIRE, GroupAVerifiedPins, GroupAVerifiedFactsReader,
    REQUIRED_RELATIONS, PROBE_SQL, GA_RESULT_SQL, A_VIDEO_SQL,
    A_CAROUSEL_SQL, MEDIA_SQL, STEPS_SQL, SCENES_SQL, SLIDES_SQL,
    _hash_json,
)

NOW=datetime(2026,10,26,12,tzinfo=timezone.utc)
PROJECT="a"*32
QDP="00112233-4455-4677-8899-aabbccddeeff"
GA="11112222-3333-4444-8555-666677778888"
SOURCE="licensed_source"
SCOPE=InputScope(PROJECT,17,SOURCE,9,"video",QDP,"c"*64)
KIND_STREAMS={
    "video":("technical","speech","screen_text","audio","subtitles"),
    "photo_carousel":("slides","screen_text"),
    "mixed_carousel":("slides","technical","speech","screen_text","audio","subtitles"),
}
EVIDENCE_KIND={
    "technical":"scene","speech":"transcript","screen_text":"screen_text",
    "audio":"audio","subtitles":"screen_text","slides":"slide",
}


def sample(kind="video"):
    scope=replace(SCOPE,content_kind=kind)
    video=kind in ("video","slideshow_video")
    mixed=kind=="mixed_carousel"
    n=1 if video else 2
    media=[]
    for i in range(1,n+1):
        k="video" if video or (mixed and i==2) else "image"
        media.append(dict(
            slide_index=i,media_asset_id=100+i,media_kind=k,asset_sha256=str(i)*64,
            byte_size=1000*i,verified_at=NOW-timedelta(hours=4),
            actual_asset_id=100+i,asset_video_id=scope.video_id,
            asset_source_id=scope.source_id,asset_kind=k,actual_sha256=str(i)*64,
            asset_permission="local_verified",asset_expires_at=NOW+timedelta(days=2),
        ))
    ev=[]
    streams={}
    for stream in KIND_STREAMS[kind]:
        label=EVIDENCE_KIND[stream]
        count=2 if stream=="slides" else 1
        streams[stream]=dict(status="complete",evidence_count=count,
                             verified_absence=False,reason_code=None)
        # A screen-text evidence may also prove subtitles; do not duplicate.
        for j in range(count):
            if stream=="subtitles":
                continue
            proof_asset = media[-1] if mixed and label in (
                "scene", "transcript", "audio", "frame"
            ) else media[0]
            ev.append(dict(kind=label,ref_sha256="d"*64,
                           asset_sha256=proof_asset["asset_sha256"]))
    if not video:
        # Existing slide evidence must come from corresponding media assets.
        for i,row in enumerate([e for e in ev if e["kind"]=="slide"]):
            row["asset_sha256"]=media[i]["asset_sha256"]
    if video:
        ana=dict(id=31,video_id=17,source_id=SOURCE,source_policy_version_id=9,
                 media_asset_id=101,analysis_version="a-v3",state="completed",
                 scene_status="complete",speech_status="complete",
                 screen_text_status="complete",audio_status="complete",
                 measured_at=NOW-timedelta(hours=1),
                 duration_sec=Decimal("8"),has_subtitles="false",scene_count=1)
    else:
        ana=dict(id=31,video_id=17,source_id=SOURCE,source_policy_version_id=9,
                 analysis_version="a-v3",content_kind=kind,state="completed",
                 slide_status="complete",text_status="complete",
                 audio_status="complete" if mixed else "not_run",
                 measured_at=NOW-timedelta(hours=1),
                 has_audio="true" if mixed else "unknown",slide_count=n)
    snap=dict(schema_version=GA_PEER_WIRE,project_id=PROJECT,
              qdp_run_id=QDP,video_id=17,qdp_input_sha256="b"*64,
              manifest_sha256="f"*64,content_kind=kind,
              analysis_state="completed",
              video_a_analysis_id=31 if video else None,
              carousel_a_analysis_id=None if video else 31,
              method_version="ga-method-v3",streams=streams,evidence=ev)
    result_hash=_hash_json(snap)
    result=dict(
        ga_run_id=GA,project_id=PROJECT,qdp_run_id=QDP,
        video_id=17,source_id=SOURCE,source_policy_version_id=9,
        qdp_input_sha256="b"*64,manifest_sha256="f"*64,content_kind=kind,
        stage_version="group-a-assembly/v1",ga_state="completed",
        expected_media_items=n,invalidated_at=None,
        result_id=21,visible_result_id=21,result_state="completed",
        result_schema=GA_PEER_WIRE,result_sha256=result_hash,
        method_version="ga-method-v3",snapshot=snap,evidence=ev,
        video_a_analysis_id=31 if video else None,
        carousel_a_analysis_id=None if video else 31,
    )
    steps=[dict(step_key="main",step_kind="assembly",state="completed",
                input_sha256="e"*64,output_sha256="0"*64,
                finished_at=NOW-timedelta(minutes=2))]
    scenes=[dict(scene_index=1,start_sec=Decimal("0"),end_sec=Decimal("8"))]
    slides=[]
    if not video:
        for i,m in enumerate(media,1):
            slides.append(dict(slide_index=i,item_type=m["media_kind"],
                media_asset_id=m["media_asset_id"],video_a_analysis_id=None,
                text_status="complete",on_screen_text="" if i==1 else "Hello!",
                screen_word_count=0 if i==1 else 1,
                duration_sec=Decimal("4.0") if m["media_kind"]=="video" else None))
    pins=GroupAVerifiedPins(
        scope=scope,ga_run_id=GA,ga_result_id=21,analysis_id=31,
        qdp_a_preparation_sha256="b"*64,media_manifest_sha256="f"*64,
        result_sha256=result_hash,analysis_version="a-v3",
        method_version="ga-method-v3",expected_media_items=n,
    )
    return dict(pins=pins,result=result,analysis=ana,media=media,
                steps=steps,scenes=scenes,slides=slides)


class Cursor:
    def __init__(self,value): self.value=value
    def fetchone(self): return self.value[0] if self.value else None
    def fetchall(self): return self.value


class Conn:
    def __init__(self,state):
        self.s=state
        self.calls=[]
        self.schema=True
        self.fail=None
    def execute(self,sql,args=()):
        self.calls.append((sql,args))
        if self.fail==sql:
            raise RuntimeError("DB password=do-not-leak")
        if sql==PROBE_SQL:
            return Cursor([{name:name if self.schema else None for name in REQUIRED_RELATIONS}])
        if sql==GA_RESULT_SQL:
            return Cursor([self.s["result"]] if self.s["result"] else [])
        if sql in (A_VIDEO_SQL,A_CAROUSEL_SQL):
            return Cursor([self.s["analysis"]] if self.s["analysis"] else [])
        for key,statement in (
            ("media",MEDIA_SQL),("steps",STEPS_SQL),("scenes",SCENES_SQL),
            ("slides",SLIDES_SQL),
        ):
            if sql==statement:
                return Cursor(self.s[key])
        raise AssertionError("Unexpected SQL")


class QDPStub(QDPQualifiedSetReader):
    """Pretend QDP adapter for unit tests only; no SQL or LIVE authorization."""

    def __init__(self,conn,pins):
        self._conn=conn
        self._pins=SimpleNamespace(
            scope=pins.scope,
            preparation_shas=(pins.qdp_a_preparation_sha256,"1"*64,"2"*64),
        )
        self.located=True
        self.declared=True

    def capability(self):
        if not self.declared:
            return PortCapability("qdp",CapabilityState.UNAVAILABLE,
                                  reason_code="peer_missing")
        return PortCapability("qdp",CapabilityState.DECLARED,
                              PORT_SCHEMAS["qdp"],"qdp-handoff/v1")

    def read_qualified_membership(self,scope):
        if not self.located:
            return EvidenceRead("qdp",scope,ReadState.MISSING,
                                reason_code="qdp_not_qualified")
        return EvidenceRead("qdp",scope,ReadState.LOCATED,reference=EvidenceRef(
            scope=scope,port="qdp",record_key="qdp:mock:17",
            producer_schema="qdp-handoff/v1",producer_version="mock-v1",
            payload_sha256="a"*64,
        ))


class GroupATests(unittest.TestCase):
    def make(self,kind="video",state=None):
        data=copy.deepcopy(sample(kind) if state is None else state)
        conn=Conn(data)
        qdp=QDPStub(conn,data["pins"])
        reader=GroupAVerifiedFactsReader(conn,data["pins"],qdp,as_of=NOW)
        return reader,conn,qdp,data

    def reject(self,data,reason):
        reader,_,_,_=self.make(state=data)
        res=reader.read_completed_analysis(data["pins"].scope)
        self.assertIs(res.state,ReadState.MISSING)
        self.assertEqual(res.reason_code,reason)
        self.assertIsNone(res.reference)

    def test_pinned_completed_video_conditional_future_peer_positive(self):
        reader,conn,_,d=self.make()
        cap=reader.capability()
        self.assertIs(cap.state,CapabilityState.DECLARED)
        res=reader.read_completed_analysis(d["pins"].scope)
        self.assertIs(res.state,ReadState.LOCATED)
        self.assertEqual(res.reference.producer_schema,GA_PEER_WIRE)
        self.assertEqual(len(res.reference.lineage_sha256),64)
        self.assertEqual(res,reader.read_completed_analysis(d["pins"].scope))
        self.assertIn(SCENES_SQL,[x[0] for x in conn.calls])
        self.assertIs(UAEPorts(group_a=reader).read("group_a",d["pins"].scope).state,
                      ReadState.LOCATED)
        self.assertFalse(UAEPorts(group_a=reader).status("group_a").qualified)

    def test_slideshow_video_has_real_video_stream_contract(self):
        r,conn,_,d=self.make("slideshow_video")
        self.assertIs(r.read_completed_analysis(d["pins"].scope).state,ReadState.LOCATED)
        self.assertIn(SCENES_SQL,[sql for sql,_ in conn.calls])

    def test_stream_proofs_must_bind_exact_verified_media_sha(self):
        d=sample()
        d["result"]["evidence"][0]["asset_sha256"]="0"*64
        # Changed wire proof also requires new result SHA, but neither proves
        # an asset not present in GA media refs.
        snap=d["result"]["snapshot"]
        digest=_hash_json(snap)
        d["result"]["result_sha256"]=digest
        d["pins"]=replace(d["pins"],result_sha256=digest)
        self.reject(d,"ga_evidence_media_unverified")

    def test_carousel_slide_proofs_cannot_repeat_first_asset(self):
        d=sample("photo_carousel")
        slides=[e for e in d["result"]["evidence"] if e["kind"]=="slide"]
        slides[1]["asset_sha256"]=slides[0]["asset_sha256"]
        digest=_hash_json(d["result"]["snapshot"])
        d["result"]["result_sha256"]=digest
        d["pins"]=replace(d["pins"],result_sha256=digest)
        self.reject(d,"ga_slide_evidence_incomplete")

    def test_mixed_video_proofs_cannot_use_image_asset(self):
        d=sample("mixed_carousel")
        proof=next(e for e in d["result"]["evidence"] if e["kind"]=="scene")
        proof["asset_sha256"]=d["media"][0]["asset_sha256"]
        digest=_hash_json(d["result"]["snapshot"])
        d["result"]["result_sha256"]=digest
        d["pins"]=replace(d["pins"],result_sha256=digest)
        self.reject(d,"ga_mixed_video_evidence_wrong_asset")

    def test_missing_peer_and_qdp_fail_closed(self):
        reader,conn,qdp,d=self.make()
        conn.schema=False
        self.assertIs(reader.capability().state,CapabilityState.UNAVAILABLE)
        self.assertEqual(reader.read_completed_analysis(d["pins"].scope).reason_code,
                         "ga_schema_missing")
        conn.schema=True
        qdp.declared=False
        self.assertEqual(reader.capability().reason_code,"ga_qdp_unavailable")
        qdp.declared=True
        qdp.located=False
        self.assertEqual(reader.read_completed_analysis(d["pins"].scope).reason_code,
                         "ga_qdp_not_current")

    def test_video_wrong_project_source_version_and_qdp_run(self):
        for field,val in (
            ("project_id","f"*32),("qdp_run_id",GA),
            ("video_id",77),("source_id","other"),
            ("source_policy_version_id",999),
            ("qdp_input_sha256","0"*64),
            ("manifest_sha256","0"*64),
            ("stage_version","group-a-assembly/v2"),
            ("result_schema","group-a-to-b/v2"),
            ("result_id",999),("result_sha256","0"*64),
            ("video_a_analysis_id",999),
            ("carousel_a_analysis_id",31),
        ):
            with self.subTest(field=field):
                d=sample()
                d["result"][field]=val
                self.reject(d,"ga_exact_run_or_result_mismatch")

    def test_missing_partials_revocations_never_success(self):
        for field,val in (
            ("ga_state","partial"),("ga_state","invalidated"),
            ("result_state","partial"),("invalidated_at",NOW),
            ("visible_result_id",None),
        ):
            with self.subTest(field=field):
                d=sample()
                d["result"][field]=val
                self.reject(d,"ga_exact_run_or_result_mismatch")
        d=sample()
        d["result"]=None
        self.reject(d,"ga_result_missing_or_revoked")

    def test_all_streams_required_with_explicit_verified_absence(self):
        d=sample()
        d["result"]["snapshot"]["streams"]["speech"]["status"]="partial"
        self.reject(d,"ga_stream_or_snapshot_incomplete")
        d=sample()
        d["result"]["snapshot"]["streams"]["audio"]["verified_absence"]=True
        self.reject(d,"ga_stream_or_snapshot_incomplete")
        d=sample()
        d["result"]["snapshot"]["streams"]["audio"]["evidence_count"]=None
        self.reject(d,"ga_stream_or_snapshot_incomplete")
        d=sample()
        d["result"]["snapshot"]["evidence"]=[]
        self.reject(d,"ga_stream_or_snapshot_incomplete")

    def test_wrong_a003_row_or_missing_measured_at(self):
        for field,val in (
            ("id",99),("video_id",77),("source_id","other"),
            ("source_policy_version_id",3),
            ("analysis_version","old-v1"),
            ("state","partial"),
            ("measured_at",None),
        ):
            with self.subTest(field=field):
                d=sample()
                d["analysis"][field]=val
                self.reject(d,"ga_a003_a004_version_missing")

    def test_video_unmeasured_speech_ocr_audio_are_not_fake_zero(self):
        for field,val in (
            ("scene_status","not_run"),("speech_status","partial"),
            ("screen_text_status","unavailable"),
            ("audio_status","not_run"),("has_subtitles","unknown"),
            ("duration_sec",None),("scene_count",0),
        ):
            with self.subTest(field=field):
                d=sample()
                d["analysis"][field]=val
                self.reject(d,"ga_video_streams_incomplete")

    def test_scene_positions_are_real_covered_ordered_intervals(self):
        for values in (
            [dict(scene_index=2,start_sec=0,end_sec=8)],
            [dict(scene_index=1,start_sec=0,end_sec=9)],
            [dict(scene_index=1,start_sec=3,end_sec=2)],
            [],
        ):
            with self.subTest(scenes=values):
                d=sample()
                d["scenes"]=values
                self.reject(d,"ga_scenes_incomplete")

    def test_verified_media_assets_cannot_be_swapped(self):
        for field,val in (
            ("asset_video_id",99),("asset_source_id","other"),
            ("actual_sha256","0"*64),("asset_permission","metadata_only"),
            ("asset_expires_at",NOW),
            ("byte_size",0),("verified_at",NOW+timedelta(hours=2)),
        ):
            with self.subTest(field=field):
                d=sample()
                d["media"][0][field]=val
                self.reject(d,"ga_media_manifest_mismatch")
        d=sample()
        d["analysis"]["media_asset_id"]=456
        self.reject(d,"ga_a003_media_mismatch")

    def test_ga_steps_cannot_hide_failed_or_unfinished_worker(self):
        for value in ("partial","running","failed","cancelled"):
            d=sample()
            d["steps"][0]["state"]=value
            self.reject(d,"ga_steps_unfinished")
        d=sample()
        d["steps"]=[]
        self.reject(d,"ga_steps_unfinished")
        d=sample()
        d["steps"][0]["output_sha256"]=None
        self.reject(d,"ga_steps_unfinished")

    def test_photo_carousel_complete_ordered_slides_without_fake_swipe_time(self):
        reader,conn,_,d=self.make("photo_carousel")
        got=reader.read_completed_analysis(d["pins"].scope)
        self.assertIs(got.state,ReadState.LOCATED)
        self.assertIn(SLIDES_SQL,[sql for sql,_ in conn.calls])
        self.assertEqual(d["analysis"]["has_audio"],"unknown")
        self.assertTrue(all("swipe_time" not in r for r in d["slides"]))

    def test_mixed_carousel_with_video_slide_and_no_fabricated_swipe(self):
        reader,conn,_,d=self.make("mixed_carousel")
        got=reader.read_completed_analysis(d["pins"].scope)
        self.assertIs(got.state,ReadState.LOCATED)
        self.assertEqual([m["media_kind"] for m in d["media"]],["image","video"])

    def test_carousel_duplicate_missing_or_wrong_slide_position(self):
        for mutate in ("missing","index","text","kind","duration"):
            d=sample("photo_carousel")
            if mutate=="missing":
                d["slides"].pop()
            if mutate=="index":
                d["slides"][1]["slide_index"]=1
            if mutate=="text":
                d["slides"][0]["text_status"]="not_run"
            if mutate=="kind":
                d["slides"][0]["item_type"]="video"
            if mutate=="duration":
                d["slides"][0]["duration_sec"]=Decimal(1)
            with self.subTest(mutate=mutate):
                self.reject(d,"ga_slide_positions_incomplete")

    def test_carousel_missing_ocr_coverage_not_coerced_to_zero(self):
        for field,val in (
            ("slide_status","partial"),("text_status","unavailable"),
            ("slide_count",0),("measured_at",None),
        ):
            d=sample("photo_carousel")
            d["analysis"][field]=val
            reason=("ga_a003_a004_version_missing" if field=="measured_at"
                    else "ga_carousel_streams_incomplete")
            with self.subTest(field=field):
                self.reject(d,reason)
        d=sample("photo_carousel")
        d["analysis"]["slide_count"]=3
        self.reject(d,"ga_slide_positions_incomplete")
        d=sample("photo_carousel")
        d["slides"][1]["screen_word_count"]=None
        self.reject(d,"ga_slide_positions_incomplete")

    def test_photos_with_wrong_media_kind_cannot_qualify(self):
        d=sample("photo_carousel")
        d["media"][1]["media_kind"]="video"
        d["media"][1]["asset_kind"]="video"
        self.reject(d,"ga_media_manifest_mismatch")
        d=sample("mixed_carousel")
        d["media"][1]["media_kind"]="image"
        d["media"][1]["asset_kind"]="image"
        self.reject(d,"ga_media_manifest_mismatch")

    def test_unsupported_image_post_and_malformed_strict_pins(self):
        with self.assertRaises(AnalyticsBoundaryError):
            replace(SCOPE,content_kind="image_post")
        p=sample()["pins"]
        for kw in (
            {"ga_run_id":"X"}, {"ga_result_id":True},{"analysis_id":0},
            {"expected_media_items":2},{"result_sha256":"A"*64},
            {"analysis_version":""},
        ):
            with self.subTest(kw=kw),self.assertRaises(AnalyticsBoundaryError):
                replace(p,**kw)
        reader,conn,_,d=self.make()
        with self.assertRaises(AnalyticsBoundaryError):
            reader.read_completed_analysis(replace(SCOPE,project_id="f"*32))
        conn.fail=GA_RESULT_SQL
        with self.assertRaises(AnalyticsBoundaryError) as e:
            reader.read_completed_analysis(SCOPE)
        self.assertEqual(e.exception.code,"ga_read_failed")
        self.assertNotIn("password",str(e.exception))


@unittest.skipUnless(os.environ.get("CM_TEST_PG_DSN"),"Disposable PostgreSQL 17 only")
class GroupAStandalonePG17(unittest.TestCase):
    def test_undeployed_ga054_does_not_claim_completed_output(self):
        import psycopg
        from intelligence import migrate
        if os.environ.get("CM_TEST_PG_DSN")==os.environ.get("CM_DB_DSN"):
            self.fail("Refuse production DB")
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True) as conn:
            v=int(conn.execute("SHOW server_version_num").fetchone()[0])
            self.assertTrue(170000<=v<180000)
            migrate.apply_migrations(conn)
            from psycopg.rows import dict_row
            conn.row_factory=dict_row
            d=sample()
            q=QDPStub(conn,d["pins"])
            reader=GroupAVerifiedFactsReader(conn,d["pins"],q,as_of=NOW)
            self.assertIs(reader.capability().state,CapabilityState.UNAVAILABLE)
            self.assertEqual(reader.capability().reason_code,"ga_schema_missing")
            self.assertIs(reader.read_completed_analysis(SCOPE).state,ReadState.UNAVAILABLE)


if __name__=="__main__":
    unittest.main()
