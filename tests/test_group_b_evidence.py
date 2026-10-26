"""GB-MOD-06: deterministic byte, provenance, timebase, and manifest tests.

Tests use temporary local artifacts and explicit injected catalog; no FFmpeg,
PG server, peer imports, remote fetch, raw text export, or paid providers.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import hashlib
import unittest

from intelligence.group_b.adapters.group_a import GroupAExactPin
from intelligence.group_b.context.evidence import (
    EvidenceFailure,MediaByteProof,validate_observations,verify_local_bytes,
)
from intelligence.group_b.context.manifest import build_media_evidence_manifest
from intelligence.group_b.errors import DependencyUnavailable

PROJECT="a"*32
SOURCE="official-test"
QDP=str(uuid4())
BRUN=str(uuid4())
ARUN=str(uuid4())
GA_RESULT=42
VIDEO=55
GA_HASH="c"*64
MEDIA_HASH="d"*64
REF_SCENE="e"*64
REF_FRAME="f"*64
REF_OCR="1"*64

class Catalog:
    def __init__(self,rows):
        self.rows=rows
    def resolve_a_proof(self,kind,ref):
        return self.rows[(kind,ref)]

class QueryResult:
    def __init__(self,row=None,rows=None):
        self.row,self.rows=row,rows
    def fetchone(self):
        return self.row
    def fetchall(self):
        return self.rows if self.rows is not None else []

class FakeConn:
    def __init__(self,current,rows):
        self.current,self.rows=current,rows
    def execute(self,query,args=()):
        if "SELECT b.project_id" in query:
            return QueryResult(row=self.current)
        if "FROM ga_media_refs mr" in query:
            return QueryResult(rows=self.rows)
        raise AssertionError("Unexpected query (no peer writes permitted)")

class TestByteTruth(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.asset=self.root/"media.mp4"
        self.asset.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"x"*500)
        self.sha=hashlib.sha256(self.asset.read_bytes()).hexdigest()

    def test_real_sha_and_size(self):
        self.assertEqual(verify_local_bytes(str(self.asset),self.sha,[self.root]),
                         (self.sha,len(self.asset.read_bytes())))

    def test_corrupted_hash_missing_file_relative_path_and_size(self):
        with self.assertRaises(EvidenceFailure):
            verify_local_bytes(str(self.asset),"0"*64,[self.root])
        with self.assertRaises(EvidenceFailure):
            verify_local_bytes(str(self.root/"phantom.mp4"),self.sha,[self.root])
        with self.assertRaises(EvidenceFailure):
            verify_local_bytes("media.mp4",self.sha,[self.root])
        with self.assertRaises(EvidenceFailure):
            verify_local_bytes(str(self.asset),self.sha,[self.root],expected_size=999)
        with self.assertRaises(EvidenceFailure):
            verify_local_bytes(str(self.asset),self.sha,[self.root],limit_bytes=1)

    def test_magic_signature_cannot_be_fake_image_or_video(self):
        text_file=self.root/"faux.png"
        text_file.write_bytes(b"not really a PNG")
        digest=hashlib.sha256(text_file.read_bytes()).hexdigest()
        with self.assertRaises(EvidenceFailure):
            verify_local_bytes(str(text_file),digest,[self.root],media_kind="image")
        with self.assertRaises(EvidenceFailure):
            verify_local_bytes(str(text_file),digest,[self.root],media_kind="video")
        real=self.root/"valid.png"
        real.write_bytes(b"\\x89PNG\\r\\n\\x1a\\n" + b"image-payload")
        real_sha=hashlib.sha256(real.read_bytes()).hexdigest()
        self.assertEqual(verify_local_bytes(str(real),real_sha,[self.root],
                                            media_kind="image")[0],real_sha)
        with self.assertRaises(EvidenceFailure):
            verify_local_bytes(str(real),real_sha,[self.root],media_kind="video")

    def test_escape_from_root_and_final_symlink_denied(self):
        with TemporaryDirectory() as outsider:
            outside=Path(outsider)/"outside.mp4"
            outside.write_bytes(b"unchanged")
            checksum=hashlib.sha256(outside.read_bytes()).hexdigest()
            with self.assertRaises(EvidenceFailure):
                verify_local_bytes(str(outside),checksum,[self.root])
            shortcut=self.root/"shortcut.mp4"
            try:
                shortcut.symlink_to(outside)
            except (OSError,NotImplementedError):
                return
            with self.assertRaises(EvidenceFailure):
                verify_local_bytes(str(shortcut),checksum,[self.root])


class TestObservationTruth(TestByteTruth):
    def setUp(self):
        super().setUp()
        self.proof=self.root/"evidence.json"
        self.proof.write_bytes(b'{"source":"observed"}')
        self.content_sha=hashlib.sha256(self.proof.read_bytes()).hexdigest()
        self.media=MediaByteProof(7,self.sha,len(self.asset.read_bytes()),"video",1,10000)

    def record(self,kind="scene",ref=REF_SCENE,**overrides):
        data={
            "reference_sha256":ref,"kind":kind,"project_id":PROJECT,
            "video_id":VIDEO,"source_id":SOURCE,"media_asset_id":7,
            "asset_sha256":self.sha,"content_sha256":self.content_sha,
            "local_path":str(self.proof),"timebase":"video_absolute",
            "start_ms":100,"end_ms":500,
        }
        data.update(overrides)
        return data

    def validate(self,items,rows,media=None):
        return validate_observations(
            items,Catalog(rows),project_id=PROJECT,video_id=VIDEO,
            source_id=SOURCE,media=(media or self.media,),roots=[self.root],
        )

    def test_video_absolute_vs_clip_relative_offsets(self):
        ref=self.record(timebase="clip_relative",start_ms=100,end_ms=200,
                        clip_origin_ms=900,clip_duration_ms=500)
        obs=self.validate([("scene",REF_SCENE,self.sha)],{("scene",REF_SCENE):ref})
        self.assertEqual((obs[0].start_ms,obs[0].end_ms),(1000,1100))
        self.assertEqual(obs[0].timebase,"video_absolute")
        ref.pop("clip_origin_ms")
        with self.assertRaises(EvidenceFailure):
            self.validate([("scene",REF_SCENE,self.sha)],{("scene",REF_SCENE):ref})
        invalid=self.record(start_ms=500,end_ms=100)
        with self.assertRaises(EvidenceFailure):
            self.validate([("scene",REF_SCENE,self.sha)],{("scene",REF_SCENE):invalid})
        invalid=self.record(start_ms=9900,end_ms=10100)
        with self.assertRaises(EvidenceFailure):
            self.validate([("scene",REF_SCENE,self.sha)],{("scene",REF_SCENE):invalid})
        invalid=self.record(timebase="video_absolute",clip_origin_ms=100)
        with self.assertRaises(EvidenceFailure):
            self.validate([("scene",REF_SCENE,self.sha)],{("scene",REF_SCENE):invalid})

    def test_duplicate_frame_and_ocr_must_reference_real_frame(self):
        frame=self.record("sampled_frame",REF_FRAME,start_ms=250,end_ms=250)
        ocr=self.record("ocr_span",REF_OCR,frame_ref_sha256=REF_FRAME)
        rows={("frame",REF_FRAME):frame,("screen_text",REF_OCR):ocr}
        obs=self.validate(
            [("frame",REF_FRAME,self.sha),("screen_text",REF_OCR,self.sha)],rows)
        self.assertEqual([x.kind for x in obs],["sampled_frame","ocr_span"])
        with self.assertRaises(EvidenceFailure):
            self.validate([("frame",REF_FRAME,self.sha),("frame",REF_FRAME,self.sha)],rows)
        with self.assertRaises(EvidenceFailure):
            self.validate([("screen_text",REF_OCR,self.sha)],rows)
        ocr["frame_ref_sha256"]="4"*64
        with self.assertRaises(EvidenceFailure):
            self.validate([("frame",REF_FRAME,self.sha),("screen_text",REF_OCR,self.sha)],rows)

    def test_invalid_source_project_media_and_missing_observation(self):
        raw=self.record()
        for change in ({"project_id":"b"*32},{"video_id":VIDEO+1},
                       {"source_id":"other"},{"media_asset_id":8},
                       {"asset_sha256":"b"*64},{"content_sha256":"f"*64}):
            with self.subTest(change=change),self.assertRaises(EvidenceFailure):
                self.validate([("scene",REF_SCENE,self.sha)],
                              {("scene",REF_SCENE):dict(raw,**change)})
        with self.assertRaises(EvidenceFailure):
            self.validate([("scene",REF_SCENE,self.sha)],{})
        with self.assertRaises(EvidenceFailure):
            self.validate([("scene",REF_SCENE,"b"*64)],
                          {("scene",REF_SCENE):raw})

    def test_slide_index_without_invented_timestamp(self):
        image=MediaByteProof(13,self.sha,100,"image",2,None)
        record=self.record("slide_ref",timebase="none",start_ms=None,end_ms=None,
                           slide_index=2,media_asset_id=13)
        result=self.validate([("slide",REF_SCENE,self.sha)],
                             {("slide",REF_SCENE):record},media=image)
        self.assertIsNone(result[0].start_ms)
        self.assertEqual(result[0].slide_index,2)
        for broken in ({"start_ms":0},{"slide_index":1},
                       {"timebase":"clip_relative","clip_origin_ms":1}):
            with self.subTest(broken=broken),self.assertRaises(EvidenceFailure):
                self.validate([("slide",REF_SCENE,self.sha)],
                              {("slide",REF_SCENE):dict(record,**broken)},media=image)

    def test_no_timed_video_evidence_with_unknown_duration(self):
        without=replace(self.media,duration_ms=None)
        with self.assertRaises(EvidenceFailure):
            self.validate([("scene",REF_SCENE,self.sha)],
                          {("scene",REF_SCENE):self.record()},media=without)


class TestManifestAssembly(TestObservationTruth):
    def setUp(self):
        super().setUp()
        self.now=datetime.now(timezone.utc)
        self.ga=GroupAExactPin(
            PROJECT,BRUN,ARUN,GA_RESULT,301,"video",11,
            GA_HASH,MEDIA_HASH,1,(7,),(self.sha,),
            (("scene",REF_SCENE,self.sha),)
        )
        self.current={
            "project_id":PROJECT,"video_id":VIDEO,"source_id":SOURCE,
            "qdp_run_id":QDP,"qdp_input_sha256":"b"*64,
            "content_kind":"video","manifest_sha256":MEDIA_HASH,
            "source_policy_version_id":11,"expected_media_items":1
        }
        self.medias=[(1,7,"video",self.sha,len(self.asset.read_bytes()),
                      str(self.asset),self.sha,"analysis",None,
                      VIDEO,SOURCE,"video","enabled",True,True,
                      {"can_analyze_media":True},10)]
        self.catalog=Catalog({("scene",REF_SCENE):self.record()})

    def build(self):
        with patch("intelligence.group_b.context.manifest.read_exact_a",
                   return_value=self.ga):
            return build_media_evidence_manifest(
                FakeConn(self.current,self.medias),b_run_id=BRUN,
                ga_result_id=GA_RESULT,ga_result_sha256=GA_HASH,
                media_roots=[self.root],observation_catalog=self.catalog,
            )

    def test_no_raw_media_paths_or_transcripts_exported(self):
        payload=self.build()
        wire=payload.wire()
        self.assertTrue(payload.verify_digest())
        self.assertEqual(wire["schema_version"],"group-b-media-evidence/v1")
        self.assertEqual(wire["observed_counts"]["scene"],1)
        self.assertEqual(wire["ga_result_id"],GA_RESULT)
        self.assertNotIn(str(self.root),str(wire))
        self.assertNotIn("local_path",str(wire))
        self.assertNotIn("observed",str(wire["observations"][0].values()))
        self.assertEqual(self.build().manifest_sha256,payload.manifest_sha256)

    def test_corrupted_media_byte_hash_fails_closed(self):
        self.asset.write_bytes(b"corrupted"+self.asset.read_bytes()[9:])
        with self.assertRaises(EvidenceFailure):
            self.build()

    def test_missing_asset_and_invalid_rights_expiry(self):
        self.asset.unlink()
        with self.assertRaises(EvidenceFailure):
            self.build()
        self.asset.write_bytes(b"\x00\x00\x00\x18ftypisom"+b"x"*500)
        original=self.medias[0]
        for index,value in (
            (13,False),(14,False),(12,"disabled"),
            (15,{"can_analyze_media":False}),
            (8,self.now-timedelta(days=1)),
            (9,VIDEO+1),
        ):
            self.medias=[tuple(value if i==index else x for i,x in enumerate(original))]
            with self.subTest(column=index),self.assertRaises(EvidenceFailure):
                self.build()
        self.medias=[original]

    def test_ga_manifest_lineage_wrong_policy_or_no_proofs(self):
        self.current["source_policy_version_id"]=100
        with self.assertRaises(EvidenceFailure):
            self.build()
        self.current["source_policy_version_id"]=11
        self.ga=replace(self.ga,evidence_refs=())
        with self.assertRaises(EvidenceFailure):
            self.build()


if __name__=="__main__":
    unittest.main()
