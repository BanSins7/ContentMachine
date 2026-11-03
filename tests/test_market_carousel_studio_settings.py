"""Offline tests for localhost-only DPAPI-backed Studio settings.

No network calls and no real credentials. A reversible stand-in simulates
Windows DPAPI so these contract tests can run on Linux CI.
"""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

from intelligence.market_intelligence import studio_credentials as credentials
from intelligence.market_intelligence import carousel_studio_http as http
from intelligence.market_intelligence import brightdata_provider as bd
from intelligence.market_intelligence.openrouter_credentials import get_market_openrouter_key


class Handler:
    def __init__(self, body=None):
        raw=json.dumps(body).encode("utf-8") if body is not None else b""
        self.rfile=BytesIO(raw)
        self.headers={"Content-Type":"application/json","Content-Length":str(len(raw)),
                      "X-ContentMachine":"1"}
        self.results=[]
    def json_response(self,code,body):
        self.results.append((code,body))


class CredentialsSettingsTest(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base=Path(temp.name)
        patch_root=patch.object(credentials,"ROOT",base)
        patch_file=patch.object(credentials,"FILE",base/"studio-credentials.dpapi")
        patch_encryption=patch.object(credentials,"_dpapi",side_effect=lambda b,*,protect:
            b"ENCRYPTED"+b if protect else b.removeprefix(b"ENCRYPTED"))
        for item in (patch_root,patch_file,patch_encryption):
            item.start()
            self.addCleanup(item.stop)

    def test_save_load_masked_status_and_provider_resolution(self):
        secrets={"openrouter_api_key":"sk-or-test-only",
                 "brightdata_api_token":"brightdata-test-only",
                 "brightdata_authorized":True}
        credentials.store(secrets)
        raw=credentials.FILE.read_bytes()
        self.assertTrue(raw.startswith(b"ENCRYPTED"))
        self.assertNotIn(b"sk-or-test-only",raw[:10])
        self.assertEqual(credentials.token("openrouter_api_key"),"sk-or-test-only")
        self.assertEqual(credentials.token("brightdata_api_token"),"brightdata-test-only")
        self.assertTrue(credentials.authorized())
        with patch.object(bd.config,"setting",return_value=""):
            self.assertTrue(bd.readiness()["ready"])
            self.assertEqual(bd._token(),"brightdata-test-only")
        self.assertEqual(get_market_openrouter_key(),"sk-or-test-only")
        safe=json.dumps(credentials.status())
        self.assertNotIn("sk-or-test-only",safe)
        self.assertNotIn("brightdata-test-only",safe)
        self.assertTrue(credentials.status()["brightdata_saved"])


    def test_rusgpt_choice_is_encrypted_and_http_only_reports_status(self):
        settings={"preferred_provider":"rusgpt",
                  "rusgpt_api_key":"rusgpt-TEST-NOT-REAL",
                  "openrouter_api_key":"openrouter-TEST-NOT-REAL",
                  "brightdata_api_token":"brightdata-TEST-NOT-REAL",
                  "brightdata_authorized":True}
        handler=Handler(settings)
        self.assertTrue(http.handle_post(handler,"/api/studio/settings"))
        self.assertEqual(handler.results[0][0],200)
        self.assertEqual(credentials.provider(),"rusgpt")
        self.assertEqual(credentials.token("rusgpt_api_key"),"rusgpt-TEST-NOT-REAL")
        self.assertNotIn(b"rusgpt-TEST-NOT-REAL",credentials.FILE.read_bytes())
        got=Handler()
        self.assertTrue(http.handle_get(got,urlparse("/api/studio/settings")))
        self.assertEqual(got.results[0][1]["preferred_provider"],"rusgpt")
        self.assertEqual(got.results[0][1]["active_model"],"anthropic/claude-haiku-5.5")
        self.assertTrue(got.results[0][1]["rusgpt_configured"])
        ready=Handler()
        self.assertTrue(http.handle_get(ready,urlparse("/api/studio/readiness")))
        self.assertEqual(ready.results[0][1]["model"],"anthropic/claude-haiku-5.5")
        self.assertEqual(ready.results[0][1]["provider"],"rusgpt")
        self.assertTrue(ready.results[0][1]["gemini"])
        payload=json.dumps([handler.results,got.results,ready.results])
        for secret in ("rusgpt-TEST-NOT-REAL","openrouter-TEST-NOT-REAL",
                       "brightdata-TEST-NOT-REAL"):
            self.assertNotIn(secret,payload)
        # Switching back does not delete either secret.
        credentials.store({"preferred_provider":"openrouter",
                           "rusgpt_api_key":"","openrouter_api_key":""})
        self.assertEqual(credentials.provider(),"openrouter")
        self.assertEqual(credentials.token("rusgpt_api_key"),"rusgpt-TEST-NOT-REAL")
        self.assertEqual(credentials.token("openrouter_api_key"),"openrouter-TEST-NOT-REAL")

    def test_invalid_provider_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"провайдер"):
            credentials.store({"preferred_provider":"custom-url"})
        self.assertFalse(credentials.FILE.exists())

    def test_blank_fields_keep_previous_keys_and_consent_can_be_revoked(self):
        credentials.store({"openrouter_api_key":"old-key",
                           "brightdata_api_token":"old-bd",
                           "brightdata_authorized":True})
        credentials.store({"openrouter_api_key":"",
                           "brightdata_api_token":"",
                           "brightdata_authorized":False})
        self.assertEqual(credentials.token("openrouter_api_key"),"old-key")
        self.assertEqual(credentials.token("brightdata_api_token"),"old-bd")
        self.assertFalse(credentials.authorized())
        with patch.object(bd.config,"setting",return_value=""):
            self.assertFalse(bd.readiness()["ready"])

    def test_reject_injection_and_unknown_properties(self):
        for payload in ({"openrouter_api_key":"bad\nkey"},
                        {"brightdata_api_token":" key"},
                        {"openrouter_api_key":"x"*4097},
                        {"brightdata_authorized":"yes"},
                        {"unknown":"not-allowed"}):
            with self.assertRaises(ValueError):
                credentials.store(payload)
        self.assertFalse(credentials.FILE.exists())

    def test_local_api_save_and_get_never_returns_credentials(self):
        values={"openrouter_api_key":"sk-or-test-only",
                "brightdata_api_token":"brightdata-test-only",
                "brightdata_authorized":True}
        handler=Handler(values)
        self.assertTrue(http.handle_post(handler,"/api/studio/settings"))
        self.assertEqual(handler.results[0][0],200)
        read=Handler()
        self.assertTrue(http.handle_get(read,urlparse("/api/studio/settings")))
        self.assertEqual(read.results[0][0],200)
        self.assertTrue(read.results[0][1]["openrouter_configured"])
        self.assertTrue(read.results[0][1]["brightdata_ready"])
        combined=json.dumps([handler.results,read.results])
        self.assertNotIn("sk-or-test-only",combined)
        self.assertNotIn("brightdata-test-only",combined)

    def test_settings_rejects_wrong_type_and_content_type(self):
        request=Handler({"openrouter_api_key":"fake"})
        request.headers["Content-Type"]="text/plain"
        self.assertTrue(http.handle_post(request,"/api/studio/settings"))
        self.assertEqual(request.results[0][0],415)
        request2=Handler({"brightdata_api_token":"bad token"})
        self.assertTrue(http.handle_post(request2,"/api/studio/settings"))
        self.assertEqual(request2.results[0][0],400)


if __name__=="__main__":
    unittest.main()
