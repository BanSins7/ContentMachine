"""Central OpenRouter key settings regression tests: no real secrets or network."""
import json
import os
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
import local_worker
import tiktok_worker
import youtube_worker
from openrouter_transcribe import get_key


class TestGlobalKeySettings(unittest.TestCase):
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

    def request(self, route, payload=None, origin=None):
        headers = {"Host": "localhost:8765"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
            headers["X-ContentMachine"] = "1"
        if origin is not None:
            headers["Origin"] = origin
        req = Request("http://127.0.0.1:" + str(self.server.server_port) + route,
                      method="POST" if payload is not None else "GET",
                      headers=headers,
                      data=json.dumps(payload).encode("utf-8") if payload is not None else None)
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except HTTPError as exc:
            return exc.code, exc.read()

    def test_replace_key_for_three_platforms_without_leaking(self):
        first = "sk-or-unit-test-FIRST123"
        second = "sk-or-unit-test-SECOND456"
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with (mock.patch.object(app, "WORKER", folder),
                  mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-ENV-VALUE999"})):
                status, body = self.request("/api/state")
                self.assertEqual(status, 200)
                self.assertFalse(json.loads(body)["configuration"]["openrouter_configured"])

                for value in (first, second):
                    status, body = self.request("/api/key", {"key": value})
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(body), {"ok": True, "configured": True})
                    self.assertNotIn(value.encode("utf-8"), body)
                    self.assertEqual((folder / "openrouter_key.txt").read_text(), value)
                    for downloader in (local_worker, tiktok_worker, youtube_worker):
                        self.assertEqual(downloader.get_key(folder), value)
                    self.assertEqual(get_key(folder), value)
                    self.assertEqual(list(folder.glob("*.tmp")), [])

                status, state = self.request("/api/state")
                self.assertEqual(status, 200)
                self.assertTrue(json.loads(state)["configuration"]["openrouter_configured"])
                self.assertNotIn(first.encode("utf-8"), state)
                self.assertNotIn(second.encode("utf-8"), state)

    def test_existing_key_stays_unchanged_when_input_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "openrouter_key.txt").write_text("sk-or-keep-this-123")
            with mock.patch.object(app, "WORKER", folder):
                for attempted in ("abc", "", "sk-or-invalid key", "sk-or-bad\nvalue"):
                    status, body = self.request("/api/key", {"key": attempted})
                    self.assertEqual(status, 400)
                    self.assertIn(b"error", body)
                    self.assertEqual(get_key(folder), "sk-or-keep-this-123")

    def test_cross_site_cannot_change_key(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(app, "WORKER", Path(directory)):
                status, _ = self.request("/api/key", {"key": "sk-or-unsafe-change"}, "https://evil.example")
                self.assertEqual(status, 403)
                self.assertFalse((Path(directory) / "openrouter_key.txt").exists())

    def test_local_view_opens_file_without_returning_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            saved = folder / "openrouter_key.txt"
            saved.write_text("sk-or-test-secret-123", encoding="utf-8")
            with (mock.patch.object(app, "WORKER", folder),
                  mock.patch.object(app.sys, "platform", "win32"),
                  mock.patch.object(app.subprocess, "Popen") as popen):
                code, body = self.request("/api/open-key-file", {})
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(body), {"ok": True})
                popen.assert_called_once_with(["notepad.exe", str(saved)])
                self.assertNotIn(b"sk-or-test-secret-123", body)

    def test_local_view_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(app, "WORKER", Path(directory)):
                code, body = self.request("/api/open-key-file", {})
                self.assertEqual(code, 400)
                self.assertIn("error", json.loads(body))

    def test_local_view_rejects_cross_site(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory) / "openrouter_key.txt"
            saved.write_text("sk-or-secret-123")
            with mock.patch.object(app, "WORKER", Path(directory)):
                code, _ = self.request("/api/open-key-file", {}, "https://evil.example")
                self.assertEqual(code, 403)

    def test_settings_section_appears_and_key_is_never_prefilled(self):
        status, data = self.request("/")
        self.assertEqual(status, 200)
        html = data.decode("utf-8")
        self.assertIn('id="settingsNav"', html)
        self.assertIn('id="settingsView"', html)
        self.assertIn('id="openrouterInput"', html)
        self.assertIn('type="password"', html)
        self.assertIn('autocomplete="new-password"', html)
        self.assertNotIn('value="sk-or-', html)


if __name__ == "__main__":
    unittest.main()
