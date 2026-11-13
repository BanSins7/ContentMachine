"""Regression tests: no videos must not be shown as a successful download."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from intelligence.instagram_top100 import service


class EmptyResultsTests(unittest.TestCase):
    def account(self, raw_records):
        with tempfile.TemporaryDirectory() as tmp:
            manager = service.RunManager(Path(tmp))
            item = {
                "username": "demo.account", "profile_url": "https://www.instagram.com/demo.account/",
                "snapshot_id": "s_AAAAAAAAAAAAAAAA", "status": "waiting_snapshot",
                "posts": [], "error": None, "rejected": {}, "observed": 0,
                "eligible": 0, "selected": 0, "downloaded": 0,
                "failed": 0, "cached": 0,
            }
            run = {"since": "2026-11-01", "until": "2026-11-13", "scan_limit_per_account": 100}
            with patch.object(service.bd, "progress", return_value="ready"):
                with patch.object(service, "provider_records", return_value=raw_records):
                    with patch.object(manager, "_write"):
                        with patch.object(service, "existing_downloader", side_effect=AssertionError("No videos")):
                            manager._account(run, item)
            return item

    def test_brightdata_zero_records_not_completed(self):
        result = self.account([])
        self.assertEqual(result["status"], "no_results")
        self.assertEqual(result["observed"], 0)
        self.assertEqual(result["selected"], 0)
        self.assertIn("0 записей", result["error"])

    def test_out_of_period_is_visible(self):
        result = self.account([{
            "url": "https://www.instagram.com/reel/ABCDE123/",
            "user_posted": "demo.account",
            "date_posted": "2026-10-20",
            "views": 1234,
        }])
        self.assertEqual(result["status"], "no_results")
        self.assertEqual(result["observed"], 1)
        self.assertEqual(result["rejected"]["outside_period"], 1)
        self.assertEqual(result["eligible"], 0)

    def test_missing_views_reported(self):
        result = self.account([{
            "url": "https://www.instagram.com/reel/ABCDE123/",
            "user_posted": "demo.account",
            "date_posted": "2026-11-11",
        }])
        self.assertEqual(result["status"], "no_results")
        self.assertEqual(result["rejected"]["missing_views"], 1)

    def test_provider_error_record_is_failure(self):
        result = self.account([{"error": "dataset error"}])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["rejected"]["invalid_record"], 1)
        self.assertIn("ошибку", result["error"])


if __name__ == "__main__":
    unittest.main()
