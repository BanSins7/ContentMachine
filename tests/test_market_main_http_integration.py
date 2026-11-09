"""Real localhost Handler routing smoke: no provider calls, no live database.

The test starts the actual app.Handler over a disposable loopback port;
it checks that Marketing and Carousel Studio are reachable while retaining
existing Host/CSRF restrictions and path-safe independent React assets.
"""
from __future__ import annotations

from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

import app


class MarketMainHttpSmoke(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.site = Path(self.temp.name)
        (self.site / "carousel-studio" / "assets").mkdir(parents=True)
        (self.site / "carousel-studio" / "index.html").write_text(
            '<!doctype html><title>Carousel Studio</title>', encoding="utf-8")
        (self.site / "carousel-studio" / "assets" / "index-test.js").write_text(
            'export const ready = true;', encoding="utf-8")
        (self.site / "market-intelligence.html").write_text(
            '<!doctype html><title>Market Intelligence</title>', encoding="utf-8")
        self.site_patch = patch.object(app, "SITE", self.site)
        self.site_patch.start()
        self.addCleanup(self.site_patch.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.thread.join, 2)

    def request(self, method, path, data=None, authorized=True):
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=8)
        headers = {
            "Host": "127.0.0.1:8765" if authorized else "example.org",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=json.dumps(data) if data is not None else None,
                         headers=headers)
            response = conn.getresponse()
            return response.status, response.getheader("Content-Type"), response.read()
        finally:
            conn.close()

    def test_native_react_entry_and_asset_served_but_traversal_denied(self):
        status, mime, data = self.request("GET", "/carousel-studio/")
        self.assertEqual(status, 200)
        self.assertIn(b"Carousel Studio", data)
        status, mime, data = self.request("GET", "/carousel-studio/assets/index-test.js")
        self.assertEqual(status, 200)
        self.assertTrue(mime.startswith("text/javascript"))
        self.assertIn(b"ready", data)
        status, mime, data = self.request("GET", "/market-intelligence.html")
        self.assertEqual(status, 200)
        self.assertIn(b"Market Intelligence", data)
        status, _, _ = self.request("GET", "/carousel-studio/assets/../../secrets.js")
        self.assertEqual(status, 404)

    def test_studio_readiness_and_unauthorized_paid_dispatch(self):
        status, _, raw = self.request("GET", "/api/studio/readiness")
        self.assertEqual(status, 200)
        response = json.loads(raw)
        self.assertTrue(response["max_carousels_supported"])
        self.assertTrue(response["account_master_supported"])
        self.assertTrue(response["retry_failed_supported"])
        status, _, _ = self.request("POST", "/api/studio/runs", {"kind":"post","value":"x"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("GET", "/api/studio/readiness", authorized=False)
        self.assertEqual(status, 403)

    def test_project_market_endpoint_is_wired_to_the_real_backend(self):
        from intelligence.market_intelligence import http_api
        def stub(handler, url):
            handler.json_response(200, {"market_main_integration": url.path})
            return True
        target = "/api/projects/" + "a"*32 + "/market-intelligence/preview"
        with patch.object(http_api, "handle_get", side_effect=stub) as handle:
            status, _, raw = self.request("GET", target)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)["market_main_integration"],target)
            handle.assert_called_once()


if __name__ == "__main__":
    unittest.main()
