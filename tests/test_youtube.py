"""YouTube Shorts adapter tests. They never access YouTube or OpenRouter."""
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import youtube_worker as youtube

GOOD = "https://www.youtube.com/shorts/AbCdEf12345"


class TestYouTubeURL(unittest.TestCase):
    def test_short(self):
        self.assertEqual(youtube.parse_link(GOOD+"?si=tracked"), ("video", GOOD, "AbCdEf12345"))

    def test_watch(self):
        self.assertEqual(youtube.parse_link("https://youtube.com/watch?v=AbCdEf12345"), ("video", GOOD, "AbCdEf12345"))

    def test_short_domain(self):
        self.assertEqual(youtube.parse_link("https://youtu.be/AbCdEf12345"), ("video", GOOD, "AbCdEf12345"))

    def test_at_channel(self):
        self.assertEqual(youtube.parse_link("https://youtube.com/@samplecreator"),
                         ("channel", "https://www.youtube.com/@samplecreator/shorts", None))

    def test_at_channel_short_tab(self):
        self.assertEqual(youtube.parse_link("https://www.youtube.com/@samplecreator/shorts"),
                         ("channel", "https://www.youtube.com/@samplecreator/shorts", None))

    def test_no_other_host(self):
        with self.assertRaises(ValueError):
            youtube.parse_link("https://youtube.com.evil.com/shorts/AbCdEf12345")

    def test_no_insecure(self):
        with self.assertRaises(ValueError):
            youtube.parse_link("http://www.youtube.com/shorts/AbCdEf12345")

    def test_no_channel_videos(self):
        with self.assertRaises(ValueError):
            youtube.parse_link("https://youtube.com/@samplecreator/videos")

    def test_cookies_only_explicit(self):
        self.assertEqual(youtube.cookie_opts(), {})
        self.assertEqual(youtube.cookie_opts("firefox"), {"cookiesfrombrowser": ("firefox",)})
        with self.assertRaises(ValueError):
            youtube.cookie_opts("../../secrets")

    def test_separate_metrics(self):
        m = app.platform_stats([{}], [{"openrouter_cost_usd": 0.0004}],
                               [{"openrouter_cost_usd": 0.0002}], 0.001)
        self.assertEqual(m["youtube"]["count"], 1)
        self.assertAlmostEqual(m["youtube"]["spent_usd"], 0.0002)
        self.assertAlmostEqual(m["instagram"]["spent_usd"], 0.0004)

    def test_discovery_filter(self):
        class FakeYdl:
            def __init__(self, options):
                self.options = options
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def extract_info(self, url, download=False):
                return {"entries": [
                    {"id": "AbCdEf12345"}, {"id": "AbCdEf12345"},
                    {"id": "Wrong"}, {"id": "XyZabc67890"},
                ]}
        updates = []
        fake = SimpleNamespace(YoutubeDL=FakeYdl)
        with mock.patch.dict(sys.modules, {"yt_dlp": fake}):
            youtube.discover_channel("job", "https://www.youtube.com/@samplecreator",
                                     lambda _id, **kw: updates.append(kw))
        self.assertEqual(updates[-1]["urls"], [GOOD, "https://www.youtube.com/shorts/XyZabc67890"])


class TestYouTubeAPI(unittest.TestCase):
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
        req = Request("http://127.0.0.1:"+str(self.server.server_port)+route,
                      data=json.dumps(data).encode(), method="POST",
                      headers={"Host": "localhost:8765", "X-ContentMachine": "1",
                               "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=5) as res:
                return res.status, json.load(res)
        except HTTPError as e:
            return e.code, json.loads(e.read())

    def test_find_video(self):
        status, result = self.post("/api/find", {"platform": "youtube", "target": GOOD})
        self.assertEqual(status, 200)
        self.assertEqual(result["urls"], [GOOD])

    def test_find_channel(self):
        with mock.patch.object(app.discover_pool, "submit") as submit:
            status, result = self.post("/api/find", {"platform": "youtube",
                                  "target": "https://www.youtube.com/@samplecreator",
                                  "cookie_browser": "firefox"})
        self.assertEqual(status, 202)
        self.assertEqual(result["kind"], "profile")
        self.assertEqual(submit.call_args.args[-1], "firefox")

    def test_download_queues_id(self):
        with mock.patch.object(app.worker.pool, "submit") as submit:
            status, result = self.post("/api/download", {
                "platform": "youtube", "urls": [GOOD], "cookie_browser": "firefox", "engine": "local"
            })
        self.assertEqual(status, 202)
        self.assertEqual(result["jobs"][0]["code"], "AbCdEf12345")
        req = submit.call_args.args[-1]
        self.assertEqual(req["cookie_browser"], "firefox")
        with app.worker.job_lock:
            app.worker.jobs.pop(result["jobs"][0]["id"], None)

    def test_refuse_other_platform_url(self):
        status, _ = self.post("/api/download", {"platform": "youtube",
                  "urls": ["https://www.instagram.com/reel/DeffteyB6If/"], "engine": "local"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
