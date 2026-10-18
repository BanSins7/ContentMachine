"""Settings imported from the YouTube Shorts v0.1.10 Chrome popup.

Offline regression tests; no YouTube or OpenRouter network calls.
"""
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import youtube_worker as y
import local_worker as w

SHORT = "https://www.youtube.com/shorts/AbCdEf12345"
CHANNEL = "https://www.youtube.com/@testchannel"


class YoutubeSettingsAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def post(self, route, data):
        req = Request(
            "http://127.0.0.1:" + str(self.server.server_port) + route,
            data=json.dumps(data).encode(), method="POST",
            headers={"Host": "localhost:8765", "X-ContentMachine": "1",
                     "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_channel_limit_is_sent_to_worker(self):
        with mock.patch.object(app.discover_pool, "submit") as submit:
            status, body = self.post("/api/find", {
                "platform": "youtube", "target": CHANNEL, "max_count": 5,
                "cookie_browser": "firefox",
            })
        self.assertEqual(status, 202)
        self.assertEqual(body["kind"], "profile")
        self.assertIs(submit.call_args.args[0], y.discover_channel)
        self.assertEqual(submit.call_args.args[-2:], ("firefox", 5))

    def test_invalid_limit_rejected(self):
        status, _ = self.post("/api/find", {
            "platform": "youtube", "target": CHANNEL, "max_count": 99999
        })
        self.assertEqual(status, 400)

    def test_off_mode_never_requires_key(self):
        with mock.patch.object(app, "get_key", return_value=""), \
             mock.patch.object(app.worker.pool, "submit") as submit:
            status, body = self.post("/api/download", {
                "platform": "youtube", "urls": [SHORT],
                "transcription_mode": "off", "language": "en"
            })
        self.assertEqual(status, 202)
        self.assertEqual(submit.call_args.args[-1]["engine"], "off")
        self.assertEqual(submit.call_args.args[-1]["language"], "en")
        with w.job_lock:
            w.jobs.pop(body["jobs"][0]["id"], None)

    def test_mai_requires_key(self):
        with mock.patch.object(app, "get_key", return_value=""):
            status, data = self.post("/api/download", {
                "platform": "youtube", "urls": [SHORT],
                "transcription_mode": "mai"
            })
        self.assertEqual(status, 400)
        self.assertIn("ключ", data["error"])

    def test_invalid_language_rejected(self):
        status, _ = self.post("/api/download", {
            "platform": "youtube", "urls": [SHORT],
            "transcription_mode": "off", "language": "other"
        })
        self.assertEqual(status, 400)

    def test_cancel_youtube_only(self):
        with w.job_lock:
            w.jobs["youtube-test"] = {"platform": "youtube", "status": "queued"}
            w.jobs["instagram-test"] = {"platform": "instagram", "status": "queued"}
        try:
            status, body = self.post("/api/cancel", {
                "platform": "youtube", "ids": ["youtube-test", "instagram-test"]
            })
            self.assertEqual(status, 200)
            self.assertEqual(body["requested"], 1)
            self.assertTrue(w.jobs["youtube-test"]["cancel_requested"])
            self.assertFalse(w.jobs["instagram-test"].get("cancel_requested", False))
        finally:
            with w.job_lock:
                w.jobs.pop("youtube-test", None)
                w.jobs.pop("instagram-test", None)

    def test_folder_points_to_youtube_archive(self):
        with tempfile.TemporaryDirectory() as root, \
             mock.patch.object(app.youtube, "ARCHIVE", Path(root)), \
             mock.patch.object(app.sys, "platform", "linux"), \
             mock.patch.object(app.subprocess, "Popen") as popen:
            status, _ = self.post("/api/open-folder", {"platform": "youtube"})
        self.assertEqual(status, 200)
        self.assertEqual(popen.call_args.args[0], ["xdg-open", root])


class YoutubeSettingsWorker(unittest.TestCase):
    def test_search_count(self):
        self.assertEqual(y.CAP, 500)
        urls = [{"id": "AbCdEf12345"}, {"id": "XyZabc67890"}]
        class FakeYdl:
            def __init__(self, opts):
                self.options = opts
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def extract_info(self, *_args, **_kwargs):
                self.testcase.assertEqual(self.options["playlistend"], 5)
                return {"entries": urls}
        fake = mock.Mock(YoutubeDL=FakeYdl)
        FakeYdl.testcase = self
        collected = []
        with mock.patch.dict(sys.modules, {"yt_dlp": fake}):
            y.discover_channel("id", CHANNEL, lambda _id, **data: collected.append(data),
                               browser="none", limit=5)
        self.assertEqual(collected[-1]["count"], 2)

    def test_off_creates_empty_subtitle_files_without_asr(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            video = folder / "video.mp4"
            video.write_bytes(b"test")
            meta = {}
            with mock.patch.object(y, "transcribe_video") as transcribe, \
                 mock.patch.object(w, "get_model") as local:
                changed = y._write_subtitles(video, folder, "off", SHORT,
                                             "hash", {}, meta, "auto")
            self.assertTrue(changed)
            self.assertEqual(meta["subtitle_source"], "off")
            self.assertEqual((folder / "transcript.txt").read_text(), "")
            self.assertEqual((folder / "subtitles.srt").read_text(), "")
            transcribe.assert_not_called()
            local.assert_not_called()

    def test_ru_is_forwarded_to_mai_and_reprocesses(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            video = folder / "video.mp4"
            video.write_bytes(b"test")
            meta = {}
            with mock.patch.object(y, "get_key", return_value="key"), \
                 mock.patch.object(y, "transcribe_video", return_value=(
                     [(0, 1.2, "Привет")], "Привет", 0.01, 1)) as transcribe:
                y._write_subtitles(video, folder, "openrouter", SHORT, "hash",
                                   {}, meta, "ru")
                self.assertEqual(transcribe.call_args.args[-1], "ru")
                self.assertEqual(meta["subtitle_language_requested"], "ru")
                # Same language and same video: no paid request again.
                self.assertFalse(y._write_subtitles(video, folder, "openrouter",
                                                    SHORT, "hash", dict(meta), meta, "ru"))
                self.assertEqual(transcribe.call_count, 1)
                # Changed mode: no need to download MP4 again.
                y._write_subtitles(video, folder, "off", SHORT, "hash", dict(meta), meta, "ru")
            self.assertEqual(meta["subtitle_source"], "off")
            self.assertEqual(transcribe.call_count, 1)

    def test_cancel_before_network(self):
        job_id = "stop-youtube-test"
        with w.job_lock:
            w.jobs[job_id] = {"status": "queued", "cancel_requested": True}
        try:
            y.download_job(job_id, {
                "source_url": SHORT, "video_id": "AbCdEf12345",
                "cookie_browser": "none", "engine": "off"
            })
            self.assertEqual(w.jobs[job_id]["status"], "cancelled")
        finally:
            with w.job_lock:
                w.jobs.pop(job_id, None)


if __name__ == "__main__":
    unittest.main()
