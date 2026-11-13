"""Top100 API-key settings tests; never call live Bright Data or Windows DPAPI."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from intelligence.instagram_top100 import server, service


class BrightDataUiSettingsTests(unittest.TestCase):
    def test_save_only_token_into_existing_encrypted_vault(self):
        secret = "a" * 45
        with patch.object(server.studio_credentials, "store", return_value={"saved": True}) as store:
            with patch.object(server.bd, "_api", side_effect=AssertionError("No provider calls")):
                result = server.save_brightdata_key({"key": "  " + secret + "  "})
        store.assert_called_once_with({"brightdata_api_token": secret})
        self.assertEqual(result["configured"], True)
        self.assertNotIn(secret, str(result))
        self.assertNotIn("brightdata_authorized", store.call_args.args[0])

    def test_bad_key_cannot_be_saved(self):
        bad = ({"key": ""}, {"key": "abc def"}, {"key": "abc\ndef"},
               {"key": 12345}, {"key": "a", "brightdata_authorized": True},
               {"key": "a" * 4097})
        with patch.object(server.studio_credentials, "store") as store:
            for payload in bad:
                with self.subTest(payload=repr(payload)[:50]):
                    with self.assertRaises(ValueError):
                        server.save_brightdata_key(payload)
            store.assert_not_called()

    def test_stored_key_without_global_flag_allows_explicit_optin(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = service.RunManager(Path(temp))
            with patch.object(service.bd, "readiness", return_value={
                    "configured": True, "enabled": False, "rights_verified": False, "ready": False}):
                with patch.object(service, "probe_brightdata", return_value={
                    "ok": True, "code": "reachable", "message": "test read-only probe"}):
                    with patch.object(manager, "_launch") as launch:
                        with self.assertRaisesRegex(ValueError, "Подтверди"):
                            manager.start("@test.creator", 100, paid_confirmed=False, rights_confirmed=True)
                        result = manager.start("@test.creator", 100,
                                               paid_confirmed=True, rights_confirmed=True)
            self.assertEqual(result["accounts"], 1)
            launch.assert_called_once()

    def test_provider_uses_token_but_never_global_authorization(self):
        with patch.object(service.bd, "_read_only_token", return_value="not-a-real-secret") as token:
            with patch.object(service.bd, "_token", side_effect=AssertionError("Global authorization")):
                with patch.object(service.bd, "dataset_id", return_value="gd_lyclm20il4r5helnj"):
                    with patch.object(service.bd, "_api", return_value={"snapshot_id": "s_AAAAAAAAAAAAAAAA"}) as api:
                        ident = service.provider_trigger("https://www.instagram.com/test.creator/", 200)
        self.assertEqual(ident, "s_AAAAAAAAAAAAAAAA")
        self.assertEqual(token.call_count, 1)
        kwargs = api.call_args.kwargs
        self.assertEqual(kwargs["payload"], {
            "input": [{"url": "https://www.instagram.com/test.creator",
                       "num_of_posts": 200}],
            "limit_per_input": None,
        })
        self.assertEqual(kwargs["query"]["include_errors"], "true")
        self.assertEqual(kwargs["query"]["discover_by"], "url_all_reels")
        self.assertEqual(kwargs["query"]["dataset_id"], "gd_lyclm20il4r5helnj")
        self.assertNotIn("start_date", str(kwargs["payload"]))
        self.assertNotIn("end_date", str(kwargs["payload"]))


if __name__ == "__main__":
    unittest.main()
