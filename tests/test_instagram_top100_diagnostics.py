"""Bright Data connectivity preflight regression tests; strictly offline."""
from __future__ import annotations

import socket
import ssl
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from intelligence.instagram_top100 import diagnostics, service


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class BrightDataDiagnosticsTests(unittest.TestCase):
    def check(self, error):
        def opener(_request, timeout):
            self.assertEqual(timeout, 12)
            raise error
        return diagnostics.probe_brightdata(opener=opener, token_reader=lambda: "fake-secret")

    def test_404_from_absent_snapshot_means_reachable_not_verified(self):
        result = self.check(urllib.error.HTTPError(
            diagnostics.TEST_URL, 404, "Not Found", {}, None))
        self.assertTrue(result["ok"])
        self.assertEqual(result["http_status"], 404)
        self.assertFalse(result["key_verified"])
        self.assertFalse(result["paid"])

    def test_200_response(self):
        result = diagnostics.probe_brightdata(opener=lambda _r, timeout: _Response(),
                                             token_reader=lambda: "fake")
        self.assertTrue(result["ok"])

    def test_unauthorized_and_forbidden(self):
        for status, expected in ((401, "unauthorized"), (403, "forbidden"), (407, "proxy")):
            with self.subTest(status=status):
                result = self.check(urllib.error.HTTPError(
                    diagnostics.TEST_URL, status, "Error", {}, None))
                self.assertFalse(result["ok"])
                self.assertEqual(result["code"], expected)

    def test_dns_failure_without_leaking_network_details(self):
        result = self.check(urllib.error.URLError(
            socket.gaierror(-2, "invalid proxy password secret")))
        self.assertEqual(result["code"], "dns")
        self.assertNotIn("secret", str(result))
        self.assertFalse(result["paid"])

    def test_tls_failure(self):
        result = self.check(urllib.error.URLError(
            ssl.SSLCertVerificationError("certificate verify failed")))
        self.assertEqual(result["code"], "tls")

    def test_timeout(self):
        result = self.check(urllib.error.URLError(TimeoutError()))
        self.assertEqual(result["code"], "timeout")

    def test_missing_key_no_http_request(self):
        def absent():
            raise ValueError("brightdata_not_configured")
        def cannot_open(*_args, **_kwargs):
            raise AssertionError("GET must not occur without token")
        result = diagnostics.probe_brightdata(opener=cannot_open,
                                             token_reader=absent)
        self.assertEqual(result["code"], "key_missing")

    def test_preflight_prevents_all_paid_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = service.RunManager(Path(temp))
            with patch.object(service.bd, "readiness", return_value={"configured": True}):
                with patch.object(service, "probe_brightdata", return_value={
                    "ok": False, "code": "dns", "message": "Нет DNS"}) as probe:
                    with patch.object(manager, "_launch") as launch:
                        with self.assertRaisesRegex(ValueError, "до оплаты"):
                            manager.start("@first, @second", 500,
                                          paid_confirmed=True, rights_confirmed=True)
                        launch.assert_not_called()
                        probe.assert_called_once_with()

    def test_first_ambiguous_trigger_stops_other_accounts(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = service.RunManager(Path(temp))
            entries = [
                {"username": handle,
                 "profile_url": "https://www.instagram.com/" + handle + "/",
                 "status": "queued", "snapshot_id": None, "posts": [], "error": None}
                for handle in ("first", "second")
            ]
            run = {"id": "a" * 32, "since": "2026-11-01",
                   "until": "2026-11-13", "scan_limit_per_account": 100,
                   "accounts": entries, "status": "running"}
            with patch.object(service, "provider_trigger", side_effect=OSError("connection failed")) as trigger:
                manager._account(run, entries[0])
                self.assertEqual(trigger.call_count, 1)
            self.assertTrue(manager.stop.is_set())
            self.assertEqual(entries[0]["status"], "trigger_unknown")
            self.assertEqual(entries[1]["status"], "queued")
            self.assertIsNone(entries[0]["snapshot_id"])


if __name__ == "__main__":
    unittest.main()
