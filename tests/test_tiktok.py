"""ContentMachine TikTok link/API tests; no TikTok network requests."""
import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import tiktok_worker


GOOD = "https://www.tiktok.com/@sample_user/video/7380123456789012345"


class TestTikTokURL(unittest.TestCase):
    def test_video_with_tracking_params(self):
        self.assertEqual(tiktok_worker.parse_link(GOOD + "?is_from_webapp=1"),
                         ("video", "sample_user", "7380123456789012345", GOOD))

    def test_profile_url(self):
        self.assertEqual(tiktok_worker.parse_link("https://www.tiktok.com/@sample_user"),
                         ("profile", "sample_user", None, "https://www.tiktok.com/@sample_user"))

    def test_wrong_hostname(self):
        with self.assertRaises(ValueError):
            tiktok_worker.parse_link("https://www.tiktok.com.attacker.test/@sample_user/video/7380123456789012345")

    def test_reject_insecure_link(self):
        with self.assertRaises(ValueError):
            tiktok_worker.parse_link(GOOD.replace("https://", "http://"))

    def test_reject_path_traversal(self):
        with self.assertRaises(ValueError):
            tiktok_worker.parse_link("https://www.tiktok.com/@../video/7380123456789012345")

    def test_reject_arbitrary_stream(self):
        with self.assertRaises(ValueError):
            tiktok_worker.parse_link("https://www.tiktok.com/@sample_user/live")

    def test_stats_separate(self):
        x = app.platform_stats([{}], [{"openrouter_cost_usd": 0.0004}], 0.001)
        self.assertEqual(x["instagram"]["count"], 1)
        self.assertEqual(x["tiktok"]["count"], 1)
        self.assertAlmostEqual(x["tiktok"]["spent_usd"], 0.0004)


class TestTikTokAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def call(self, path, data):
        req = Request(
            "http://127.0.0.1:" + str(self.server.server_port) + path,
            data=json.dumps(data).encode("utf-8"), method="POST",
            headers={"Host": "localhost:8765", "X-ContentMachine": "1", "Content-Type": "application/json"},
        )
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_find_exact_video(self):
        status, body = self.call("/api/find", {"platform": "tiktok", "target": GOOD})
        self.assertEqual(status, 200)
        self.assertEqual(body["urls"], [GOOD])

    def test_find_account_enqueues_discovery(self):
        with mock.patch.object(app.discover_pool, "submit") as submit:
            status, body = self.call("/api/find", {
                "platform": "tiktok", "target": "https://www.tiktok.com/@sample_user"
            })
        self.assertEqual(status, 202)
        self.assertEqual(body["kind"], "profile")
        submit.assert_called_once()

    def test_download_queues_exact_id(self):
        with mock.patch.object(app.worker.pool, "submit") as submit:
            status, body = self.call("/api/download", {
                "platform": "tiktok", "urls": [GOOD], "engine": "local"
            })
        self.assertEqual(status, 202)
        self.assertEqual(body["jobs"][0]["code"], "7380123456789012345")
        submit.assert_called_once()
        with app.worker.job_lock:
            app.worker.jobs.pop(body["jobs"][0]["id"], None)

    def test_reject_mixed_platforms(self):
        status, body = self.call("/api/download", {
            "platform": "tiktok", "urls": ["https://www.instagram.com/reel/DeffteyB6If/"],
            "engine": "local"
        })
        self.assertEqual(status, 400)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
