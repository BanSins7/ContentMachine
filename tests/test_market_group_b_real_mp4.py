"""Actual MP4 → copied A → native B1–B8 → SHA-pinned MI receipt smoke."""
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from intelligence.market_intelligence.replica_media import analyze_media
from intelligence.market_intelligence.extended_b import assemble_b1_b8
from intelligence.market_intelligence.group_b_report import (
    build_group_b_report, verify_group_b_report,verify_group_b_payload)
from intelligence.market_intelligence.native_evidence import (
    make_receipt,verify_receipt)
from intelligence.market_intelligence.media_worker import METHOD


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Local FFmpeg not installed")
class RealMarketingAB(unittest.TestCase):
    def test_real_mp4_a_through_all_b_stage_reports_and_receipt(self):
        with tempfile.TemporaryDirectory(prefix="mi-ab-video-") as d:
            file=Path(d)/"video.mp4"
            subprocess.run(["ffmpeg","-hide_banner","-nostdin","-loglevel","error",
              "-y","-f","lavfi","-i","color=c=black:s=320x240:r=15:d=2",
              "-an","-c:v","mpeg4","-qscale:v","4",str(file)],
              check=True,capture_output=True,timeout=30)
            sha=hashlib.sha256(file.read_bytes()).hexdigest()
            with patch.dict(os.environ,{
                "CM_MARKET_VISION_ENABLED":"0",
                "CM_MARKET_TESSERACT_PIN":"",
                "CM_MARKET_ASR_ENABLED":"0",
                "CM_MARKET_VAD_ENABLED":"0",
                "CM_MARKET_AUDIO_EVENTS_ENABLED":"0",
            }):
                technical,visual=analyze_media(file,sha,
                    roots=[d],project_id="b"*32,srt_rows=[],
                    allow_provider=False)
            a=visual["group_a_report"]
            b=assemble_b1_b8(visual=visual,
                inferred=visual["model_result"],
                measurements=technical,transcript=[],
                extras=visual.get("b_extra_result"))
            self.assertEqual(set(b["blocks"]),
                {"B"+str(i) for i in range(1,9)})
            report=build_group_b_report(
                source_sha256=sha,group_a=a,visual=visual,
                group_b=b,duration_sec=technical.measurement.duration_sec)
            self.assertTrue(verify_group_b_report(
                report,sha,a["report_sha256"]))
            self.assertTrue(verify_group_b_payload(report,b))
            self.assertFalse(report["original_full_group_b_certified"])
            b["native_report"]=report
            receipt=make_receipt(project_id="b"*32,video_id=77,
                source_id="source-local",source_version_id="version-local",
                media_asset_id="asset-local",media_sha256=sha,
                duration_sec=technical.measurement.duration_sec,
                visual=visual,group_b=b,method=METHOD)
            self.assertTrue(verify_receipt(receipt,media_sha256=sha))
            self.assertEqual(receipt["group_b_report_sha256"],
                             report["report_sha256"])
            self.assertEqual(receipt["group_a_report_sha256"],
                             a["report_sha256"])
            self.assertEqual(b["blocks"]["B8"]["approved_topic_id"],None)
            self.assertIn(b["blocks"]["B5"]["state"],
                ("measured_ffmpeg_scene_change_proxy","unavailable"))


if __name__=="__main__":
    unittest.main()
