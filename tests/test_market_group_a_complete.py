"""Marketing Group A real MP4 / FFmpeg and QDP-free proof tests."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from intelligence.market_intelligence.replica_media import analyze_media
from intelligence.market_intelligence.group_a_report import verify_group_a_report
from intelligence.market_intelligence.group_a_audio import inspect_audio
from intelligence.market_intelligence._copied.group_a.media_manifest import verify_file
from intelligence.market_intelligence._copied.group_a.extractors.technical import analyze_technical


class MarketingGroupA(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Local FFmpeg tools unavailable")
    def test_audit_on_real_silent_video(self):
        with tempfile.TemporaryDirectory() as d:
            video=Path(d)/"sample.mp4"
            subprocess.run(["ffmpeg","-nostdin","-loglevel","error","-y",
                "-f","lavfi","-i","color=c=black:s=320x240:r=15:d=2",
                "-an","-c:v","mpeg4","-qscale:v","4",str(video)],
                check=True,capture_output=True,timeout=30)
            sha=hashlib.sha256(video.read_bytes()).hexdigest()
            with patch.dict(os.environ,{
                "CM_MARKET_ASR_ENABLED":"0",
                "CM_MARKET_VAD_ENABLED":"0",
                "CM_MARKET_AUDIO_EVENTS_ENABLED":"0",
                "CM_MARKET_VISION_ENABLED":"0",
                "CM_MARKET_TESSERACT_PIN":""}):
                tech,observed=analyze_media(video,sha,roots=[d],
                      project_id="b"*32,srt_rows=[])
            group_a=observed["group_a_report"]
            self.assertTrue(verify_group_a_report(group_a,sha))
            self.assertEqual(set(group_a["stages"]),{
                "technical","frame_sampling","ocr","text_tracking",
                "audio_decode","vad","asr","speech_metrics","timed_captions",
                "subtitle_probe","subtitles","audio_events"})
            self.assertEqual(group_a["stages"]["technical"]["state"],"complete")
            self.assertGreater(group_a["stages"]["frame_sampling"]["details"]["observed_frames"],0)
            self.assertTrue(group_a["no_qdp_dependencies"])
            self.assertFalse(group_a["original_full_group_a_certified"])
            self.assertFalse(group_a["visual_sampling_is_continuous"])
            self.assertTrue(any(event["kind"]=="technical_scene" for event in group_a["timeline"]))
            self.assertNotIn("pcm",str(group_a))

            bad=dict(group_a)
            bad["duration_sec"]="999.000"
            self.assertFalse(verify_group_a_report(bad,sha))
            self.assertFalse(verify_group_a_report(group_a,"0"*64))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Local FFmpeg tools unavailable")
    def test_real_audio_decodes_without_faking_asr_or_music(self):
        with tempfile.TemporaryDirectory() as d:
            video=Path(d)/"audio.mp4"
            cmd=["ffmpeg","-nostdin","-loglevel","error","-y",
                 "-f","lavfi","-i","color=c=black:s=320x240:r=15:d=2",
                 "-f","lavfi","-i","sine=frequency=440:sample_rate=16000:duration=2",
                 "-c:v","mpeg4","-qscale:v","4","-c:a","aac",
                 "-shortest",str(video)]
            subprocess.run(cmd,check=True,capture_output=True,timeout=35)
            sha=hashlib.sha256(video.read_bytes()).hexdigest()
            src=verify_file(str(video),media_kind="video",sha256=sha,roots=[d])
            technical=analyze_technical(video,strict=True,roots=[d])
            with patch.dict(os.environ,{
                "CM_MARKET_ASR_ENABLED":"0",
                "CM_MARKET_VAD_ENABLED":"0",
                "CM_MARKET_AUDIO_EVENTS_ENABLED":"0"}):
                audio=inspect_audio(src,roots=[d],
                    video_probe=technical.probe,
                    verify_current=lambda ctx:ctx.get("source_media_sha256")==sha)
            self.assertEqual(audio["source_media_sha256"],sha)
            self.assertEqual(audio["decode"]["audio_status"],"decoded")
            self.assertEqual(audio["asr"]["status"],"unavailable")
            self.assertEqual(audio["audio_events"]["music_presence"],"unknown")
            self.assertEqual(audio["audio_events"]["sound_effect_presence"],"unknown")
            self.assertEqual(audio["transcript_segments"],[])
            self.assertFalse("pcm" in audio)

            with self.assertRaises(ValueError):
                inspect_audio(src,roots=[d],video_probe=technical.probe,
                    verify_current=lambda ctx:False)


if __name__=="__main__":
    unittest.main()
