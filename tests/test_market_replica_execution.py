"""Real MI media pipeline smoke: original A samples + B codebook validations."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from intelligence.market_intelligence.replica_vision import classify_grounded
from intelligence.market_intelligence._copied.group_b.blocks.b1_hook import MECHANISMS
from intelligence.market_intelligence._copied.group_b.blocks.b2_format import VIDEO_FORMATS
from intelligence.market_intelligence._copied.group_b.blocks.b3_story import ROLES
from intelligence.market_intelligence.replica_media import analyze_media


class ReplicatedLiveExecution(unittest.TestCase):
    def test_verified_model_return_only_real_frame_refs(self):
        frame=b"PNG BYTES TEST MOCK"
        sha=hashlib.sha256(frame).hexdigest()
        name="f001-"+sha+".png"
        ref="ga-frame:f001-"+sha
        one={"frame_ref":ref,"png_sha256":sha,
             "sample_index":1,"observed_sec":"0.125"}
        evidence={"format_family":VIDEO_FORMATS[0],
          "hook_mechanisms":[MECHANISMS[0]],"hook_text":None,
          "hook_visual_description":"A person looks at camera",
          "story_roles":[ROLES[0]],"cta_type":None,"cta_text":None,
          "evidence_frame_refs":[ref],"visual_subjects":["person"]}
        def fake_request(body):
            parsed=json.loads(body)
            self.assertIn("data:image/png;base64,",
                 parsed["messages"][1]["content"][1]["image_url"]["url"])
            return {"id":"mock_response_1","choices":[
                {"message":{"content":json.dumps(evidence)}}]}
        env={"CM_MARKET_VISION_ENABLED":"1",
          "CM_MARKET_MEDIA_TO_PROVIDER_ALLOWED":"1",
          "CM_MARKET_OPENROUTER_API_KEY":"mock-token",
          "CM_MARKET_VISION_MODEL":"test/mock"}
        with patch.dict(os.environ,env):
            data=classify_grounded(frames=[one],png_blobs={name:frame},
               transcript=[],duration_sec=5,requester=fake_request)
        self.assertEqual(data["state"],"model_observed_partial")
        self.assertEqual(data["format_family"],VIDEO_FORMATS[0])

        evidence["evidence_frame_refs"]=["invented-frame"]
        with patch.dict(os.environ,env):
            with self.assertRaises(ValueError):
                classify_grounded(frames=[one],png_blobs={name:frame},
                  transcript=[],duration_sec=5,requester=fake_request)

    def test_no_paid_model_if_disabled(self):
        with patch.dict(os.environ,{
            "CM_MARKET_VISION_ENABLED":"0",
            "CM_MARKET_OPENROUTER_API_KEY":""}):
            self.assertEqual(classify_grounded(
                frames=[{"frame_ref":"untrusted","png_sha256":"bad"}],
                png_blobs={},transcript=[],duration_sec=5)["state"],"unavailable")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg binaries unavailable on host")
    def test_copied_A_processes_real_local_video(self):
        with tempfile.TemporaryDirectory(prefix="mi-original-a-") as d:
            root=Path(d)
            media=root/"small.mp4"
            command=["ffmpeg","-hide_banner","-loglevel","error","-y",
              "-f","lavfi","-i","color=c=black:s=320x240:r=15:d=3",
              "-an","-c:v","mpeg4","-qscale:v","4",str(media)]
            subprocess.run(command,check=True,capture_output=True,timeout=25)
            sha=hashlib.sha256(media.read_bytes()).hexdigest()
            with patch.dict(os.environ,{
                 "CM_MARKET_VISION_ENABLED":"0",
                 "CM_MARKET_TESSERACT_PIN":"",
                 "CM_MARKET_OPENROUTER_API_KEY":""}):
                technical,visual=analyze_media(media,sha,roots=[d],
                   project_id="b"*32,srt_rows=[])
            self.assertEqual(visual["source_media_sha256"],sha)
            self.assertGreaterEqual(visual["frame_count"],1)
            self.assertEqual(visual["visual_classification_state"],"unavailable")
            self.assertTrue(technical.measurement.duration_sec>0)


if __name__=="__main__":
    unittest.main()
