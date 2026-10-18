"""Offline integration smoke tests for YouTube exact download + SRT/TXT."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import youtube_worker as y
import local_worker as w

VIDEO = "https://www.youtube.com/shorts/AbCdEf12345"


class TestYouTubeDownload(unittest.TestCase):
    def test_download_writes_matching_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            def downloader(opts):
                class YDL:
                    def __enter__(self): return self
                    def __exit__(self, *args): return False
                    def extract_info(self, link, download=False):
                        self.testcase.assertFalse(download)
                        return {"id": "AbCdEf12345", "title": "Example", "channel": "Creator"}
                    def process_ie_result(self, info, download=True):
                        self.testcase.assertTrue(download)
                        out = Path(opts["outtmpl"].replace("%(ext)s", "mp4"))
                        out.parent.mkdir(parents=True, exist_ok=True)
                        out.write_bytes(b"fake video with audio")
                        return info
                result = YDL()
                result.testcase = self
                return result
            fake_ydl = SimpleNamespace(YoutubeDL=downloader)
            fake_ffmpeg = SimpleNamespace(get_ffmpeg_exe=lambda: "ffmpeg")
            with (mock.patch.object(y, "ARCHIVE", Path(tmp)),
                  mock.patch.dict(sys.modules, {"yt_dlp": fake_ydl, "imageio_ffmpeg": fake_ffmpeg}),
                  mock.patch.object(w, "inspect_media", return_value={"video", "audio"}),
                  mock.patch.object(y, "get_key", return_value="test-key"),
                  mock.patch.object(y, "transcribe_video", return_value=(
                      [(0.0,1.2,"Привет")], "Привет", 0.0001, 1))):
                job_id = "fakeyoutubejob"
                with w.job_lock:
                    w.jobs[job_id] = {"status": "queued", "phase": "queued", "progress": None}
                try:
                    y.download_job(job_id, {
                        "source_url": VIDEO, "video_id": "AbCdEf12345",
                        "engine": "openrouter", "cookie_browser": "none",
                    })
                    root = Path(tmp) / "single" / "AbCdEf12345"
                    self.assertTrue((root / "video.mp4").exists())
                    self.assertIn("Привет", (root / "subtitles.srt").read_text(encoding="utf-8-sig"))
                    self.assertEqual((root / "transcript.txt").read_text(encoding="utf-8"), "Привет")
                    metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
                    self.assertEqual(metadata["id"], "AbCdEf12345")
                    self.assertEqual(metadata["source_url"], VIDEO)
                    self.assertEqual(w.jobs[job_id]["status"], "complete")
                finally:
                    with w.job_lock:
                        w.jobs.pop(job_id, None)

    def test_wrong_video_stopped_before_media_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            flag = []
            class YDL:
                def __init__(self, opts): pass
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def extract_info(self, link, download=False):
                    return {"id": "XyZabc67890"}
                def process_ie_result(self, *args, **kwargs):
                    flag.append("downloaded")
            with (mock.patch.object(y, "ARCHIVE", Path(tmp)),
                  mock.patch.dict(sys.modules, {"yt_dlp": SimpleNamespace(YoutubeDL=YDL),
                                                "imageio_ffmpeg": SimpleNamespace(get_ffmpeg_exe=lambda: "ffmpeg")})):
                job_id = "wrongid"
                with w.job_lock:
                    w.jobs[job_id] = {"status": "queued"}
                try:
                    y.download_job(job_id, {"source_url": VIDEO, "video_id": "AbCdEf12345",
                                            "engine": "local", "cookie_browser": "none"})
                    self.assertEqual(w.jobs[job_id]["status"], "failed")
                    self.assertIn("другое видео", w.jobs[job_id]["error"])
                    self.assertEqual(flag, [])
                finally:
                    with w.job_lock:
                        w.jobs.pop(job_id, None)


if __name__ == "__main__":
    unittest.main()
