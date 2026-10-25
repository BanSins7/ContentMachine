"""Stage14 fixture B: 3 real downloader adapters verify local mock media ->
real SRT normalizer -> pinned/mock Vision preparation -> Fusion JSON sanity.

No HTTP downloads, ASR/FFmpeg/OCR calls, or paid multimodal API requests.
"""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from intelligence.project_sources.video_adapters import (
    AcquisitionProblem, TikTokAdapter, YouTubeAdapter, InstagramAdapter)
from intelligence.project_sources.transcript_normalizer import normalize,parse_srt
from intelligence.project_sources.timeline_fusion import fuse,canonical,sha,FusionProblem

MP4=bytes((0,0,0,24))+b"ftypisom"+b"\0"*512
SRT=(b"1\n00:00:00,000 --> 00:00:01,000\nReal speech in sidecar\n\n"
     b"2\n00:00:01,200 --> 00:00:02,300\nLink in bio\n")
SOURCES=[
    (TikTokAdapter(),"https://www.tiktok.com/@stage14/video/7234567890123456789",
     "shortcode"),
    (InstagramAdapter(),"https://www.instagram.com/reel/ABCd123/",
     "verified_shortcode"),
    (YouTubeAdapter(),"https://www.youtube.com/shorts/AbCdEf12345","id"),
]


class MultiPlatformOfflineMedia(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
    def _verified(self,adapter,url,identifier):
        identity=adapter.resolve_identity(url)
        folder=self.root/adapter.platform/identity.native_id
        folder.mkdir(parents=True)
        mp4=folder/"asset.mp4"
        meta=folder/"metadata.json"
        mp4.write_bytes(MP4)
        meta.write_text(json.dumps({
            identifier:identity.native_id,
            "source_url":identity.canonical_url,
            "video_sha256":hashlib.sha256(MP4).hexdigest(),
            "duration":3,"subtitle_status":"completed",
            "language":"en"}),encoding="utf-8")
        manifest=adapter.verify_asset(identity,self.root,mp4,meta)
        self.assertEqual(manifest["platform"],adapter.platform)
        self.assertEqual(manifest["video_sha256"],hashlib.sha256(MP4).hexdigest())
        self.assertTrue(manifest["acquisition_verified"])
        return manifest
    def _prepared(self,media,version,acquisition):
        return {"schema_version":"vision-prepare/v1",
            "status":"completed","video_sha256":media["video_sha256"],
            "video_source_version_id":version,"acquisition_job_id":acquisition,
            "video_duration_ms":3000,
            "scenes":[{"scene_id":"sc001","start_ms":0,"end_ms":3000,
                       "frame_ids":["f001"],"boundary_kind":"sampling_bin"}],
            "frames":[{"frame_id":"f001","scene_id":"sc001","time_ms":1000,
                "sha256":hashlib.sha256(b"mock JPEG frame").hexdigest(),
                "ocr":{"status":"ok","text":"SALE","start_ms":1000,"end_ms":1040}}],
            "warnings":[]}
    def test_three_platforms_complete_data_chain_with_no_fabrication(self):
        for adapter,url,key in SOURCES:
            with self.subTest(platform=adapter.platform):
                manifest=self._verified(adapter,url,key)
                transcript=normalize(manifest,srt=SRT)
                self.assertEqual(transcript["speech_status"],"speech_detected")
                self.assertEqual(transcript["processing_status"],"completed")
                self.assertEqual(len(transcript["segments"]),2)
                pid=uuid4().hex;sid=str(uuid4());version=str(uuid4())
                acquisition=str(uuid4());tjob=str(uuid4())
                prepjob=str(uuid4());visionjob=str(uuid4())
                prepared=self._prepared(manifest,version,acquisition)
                visual={
                    "schema_version":"visual-observations/v1",
                    "status":"completed","video_sha256":manifest["video_sha256"],
                    "video_source_version_id":version,
                    "prepare_job_id":prepjob,
                    "scenes":[{
                        "scene_id":"sc001","start_ms":0,"end_ms":3000,"frame_ids":["f001"],
                        "visual_description":"Mocked visible product on a table",
                        "observed_visible":["product"],"camera_editing":[],
                        "product_logo_visibility":"uncertain",
                        "inferred_interpretation":"hypothesis only",
                        "uncertainty":"One sampled frame"}]}
                data=fuse(acquisition=manifest,project_id=pid,source_id=sid,
                    source_version_id=version,acquisition_job_id=acquisition,
                    transcript=transcript,transcript_job_id=tjob,
                    prepared=prepared,prepare_job_id=prepjob,
                    observations=visual,vision_job_id=visionjob)
                self.assertEqual(data["schema_version"],"video-intelligence/v1")
                self.assertEqual(data["platform"],adapter.platform)
                self.assertEqual(data["video_sha256"],manifest["video_sha256"])
                self.assertEqual(data["review"]["status"],"needs_review")
                self.assertFalse(data["coverage"]["all_frames_analyzed"])
                self.assertFalse(data["review"]["group_a_published"])
                self.assertFalse(data["review"]["group_b_published"])
                self.assertEqual(data["transcript"]["segments"][0]["text"],
                                 "Real speech in sidecar")
                self.assertEqual(data["ocr_segments"][0]["text"],"SALE")
                self.assertTrue(all(a["start_ms"]<a["end_ms"]<=3000
                    for a in data["timeline"]))
                self.assertEqual([a["start_ms"] for a in data["timeline"]],
                    sorted(a["start_ms"] for a in data["timeline"]))
                self.assertTrue(all(eid in data["evidence"]
                    for row in data["timeline"]
                    for eid in row["speech_evidence_ids"]+
                         row["ocr_evidence_ids"]+row["vision_evidence_ids"]))
                self.assertFalse(any(e["kind"]=="model_visual_description" and
                    e["trust"]!="model_reported_unverified"
                    for e in data["evidence"].values()))
                self.assertIsNone(data["group_b_candidates_unpublished"]["topics"])
                self.assertEqual(sha(canonical(data)),sha(canonical(copy.deepcopy(data))))
                # An invalid source-version reference is never silently fused.
                invalid=copy.deepcopy(prepared)
                invalid["video_source_version_id"]=str(uuid4())
                with self.assertRaises(FusionProblem):
                    fuse(acquisition=manifest,project_id=pid,source_id=sid,
                        source_version_id=version,acquisition_job_id=acquisition,
                        transcript=transcript,transcript_job_id=tjob,
                        prepared=invalid,prepare_job_id=prepjob)
    def test_no_fabricated_speech_and_corrupted_assets_rejected(self):
        adapter,url,key=SOURCES[0]
        manifest=self._verified(adapter,url,key)
        no_speech=normalize({**manifest,
            "metadata":{**manifest["metadata"],"speech_status":"no_speech",
                        "subtitle_status":"no_speech"}},srt=None)
        self.assertEqual(no_speech["speech_status"],"no_speech")
        self.assertEqual(no_speech["segments"],[])
        self.assertNotEqual(no_speech["speech_status"],"speech_detected")
        ident=adapter.resolve_identity(url)
        folder=self.root/adapter.platform/ident.native_id
        mp4=folder/"asset.mp4";metadata=folder/"metadata.json"
        mp4.write_bytes(MP4+b"tampered")
        with self.assertRaises(AcquisitionProblem):
            adapter.verify_asset(ident,self.root,mp4,metadata)


if __name__=="__main__":
    unittest.main()
