"""Offline provider settings API contracts. No paid calls or live credentials."""
from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

from intelligence import provider_credentials as credentials
from intelligence import provider_settings_http as http


class FakeHandler:
    def __init__(self, body=None, content_type="application/json"):
        raw = json.dumps(body or {}).encode("utf-8")
        self.rfile = io.BytesIO(raw)
        self.headers = {"Content-Length": str(len(raw)), "Content-Type": content_type}
        self.result = None

    def json_response(self, code, body):
        self.result = (code, body)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        p1 = patch.object(credentials, "ROOT", Path(temporary.name))
        p2 = patch.object(credentials, "FILE", Path(temporary.name) / "studio-credentials.dpapi")
        p3 = patch.object(credentials, "_dpapi", side_effect=lambda value, protect: value)
        p1.start(); p2.start(); p3.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop); self.addCleanup(p3.stop)

    def test_store_and_status_never_return_secrets(self):
        secret1 = "rusgpt-" + "a" * 64
        secret2 = "11111111-2222-3333-4444-555555555555"
        h = FakeHandler({
            "rusgpt_api_key": secret1,
            "brightdata_api_token": secret2,
            "preferred_provider": "rusgpt",
            "brightdata_authorized": False,
        })
        self.assertTrue(http.handle_post(h, http.ENDPOINT))
        self.assertEqual(h.result[0], 200)
        data = h.result[1]
        self.assertEqual(data["active_model"], "anthropic/claude-haiku-5.5")
        self.assertTrue(data["rusgpt_configured"])
        self.assertTrue(data["brightdata_configured"])
        self.assertFalse(data["brightdata_ready"])
        self.assertNotIn(secret1, json.dumps(data))
        self.assertNotIn(secret2, json.dumps(data))
        self.assertEqual(credentials.token("rusgpt_api_key"), secret1)
        self.assertEqual(credentials.token("brightdata_api_token"), secret2)
        self.assertTrue(credentials.FILE.name == "studio-credentials.dpapi")

        update = FakeHandler({"preferred_provider": "openrouter"})
        http.handle_post(update, http.ENDPOINT)
        self.assertEqual(update.result[0], 200)
        self.assertEqual(credentials.token("brightdata_api_token"), secret2)

        getter = FakeHandler()
        self.assertTrue(http.handle_get(getter, urlparse(http.ENDPOINT)))
        self.assertEqual(getter.result[0], 200)
        self.assertNotIn(secret1, json.dumps(getter.result))
        self.assertNotIn(secret2, json.dumps(getter.result))

    def test_invalid_entries_do_not_overwrite_existing_token(self):
        http.handle_post(FakeHandler({"rusgpt_api_key": "existing-value"}), http.ENDPOINT)
        for body in (
            {"unknown": "value"},
            {"preferred_provider": "unknown"},
            {"rusgpt_api_key": "contains spaces"},
            {"brightdata_authorized": "yes"},
        ):
            h = FakeHandler(body)
            http.handle_post(h, http.ENDPOINT)
            self.assertEqual(h.result[0], 400)
            self.assertEqual(credentials.token("rusgpt_api_key"), "existing-value")

    def test_content_type_size_and_route_guards(self):
        wrong = FakeHandler({"rusgpt_api_key": "valid"}, "text/plain")
        self.assertTrue(http.handle_post(wrong, http.ENDPOINT))
        self.assertEqual(wrong.result[0], 415)
        huge = FakeHandler({"rusgpt_api_key": "z" * 14000})
        http.handle_post(huge, http.ENDPOINT)
        self.assertEqual(huge.result[0], 413)
        self.assertFalse(http.handle_get(FakeHandler(), urlparse("/api/projects")))
        self.assertFalse(http.handle_post(FakeHandler(), "/api/projects"))


if __name__ == "__main__":
    unittest.main()
