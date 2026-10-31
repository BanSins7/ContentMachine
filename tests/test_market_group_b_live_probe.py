"""No-network, no-charge contract tests for opt-in live B1-B8 probe."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from scripts.probe_market_group_b_live import (
    BoundedLiveRequester, LiveRequestError, preflight, ENDPOINT,
)


class Response:
    status=200
    def __init__(self, data=b'{"id":"response-123","choices":[]}'):
        self.data=data
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self, limit):
        return self.data[:limit]


class MarketingLiveBProbe(unittest.TestCase):
    def test_upload_requires_explicit_flag_before_any_other_checks(self):
        with patch("scripts.probe_market_group_b_live.urllib.request.urlopen") as network:
            with self.assertRaisesRegex(ValueError, "allow-media-upload"):
                preflight(Path("does-not-exist.mp4"), authorized=False)
            network.assert_not_called()

    def test_preflight_checks_model_and_credentials_without_leaking_key(self):
        with tempfile.TemporaryDirectory() as folder:
            video=Path(folder)/"movie.mp4"
            video.write_bytes(b"sample bytes")
            names={"CM_MARKET_VISION_MODEL":"model/vision-test",
                   "CM_MARKET_OPENROUTER_API_KEY":"not-a-real-token"}
            with patch("scripts.probe_market_group_b_live.config.setting",
                       side_effect=lambda name, default="": names.get(name,default)):
                self.assertEqual(preflight(video,authorized=True),"model/vision-test")
                names["CM_MARKET_OPENROUTER_API_KEY"]=""
                with self.assertRaisesRegex(ValueError,"missing in .env") as exc:
                    preflight(video,authorized=True)
                self.assertNotIn("not-a-real-token",str(exc.exception))

    def test_one_request_budget_applies_to_both_provider_windows(self):
        client=BoundedLiveRequester(max_requests=1)
        with patch("scripts.probe_market_group_b_live.config.setting",
                   return_value="dummy-token"), patch(
                   "scripts.probe_market_group_b_live.urllib.request.urlopen",
                   return_value=Response()) as network:
            got=client(b'{"model":"test"}')
            self.assertEqual(got["id"],"response-123")
            with self.assertRaisesRegex(LiveRequestError,"request_budget_exceeded"):
                client(b'{"model":"test"}')
            network.assert_called_once()
        self.assertEqual(client.attempted,1)
        self.assertEqual(client.completed,1)

    def test_http_failure_disables_all_further_uploads(self):
        client=BoundedLiveRequester(max_requests=12)
        failure=urllib.error.HTTPError(ENDPOINT,429,"rate-limited",{},None)
        with patch("scripts.probe_market_group_b_live.config.setting",
                   return_value="a-private-token"), patch(
                   "scripts.probe_market_group_b_live.urllib.request.urlopen",
                   side_effect=failure) as network:
            with self.assertRaisesRegex(LiveRequestError,"http_429") as exc:
                client(b'{}')
            self.assertNotIn("a-private-token",str(exc.exception))
            with self.assertRaisesRegex(LiveRequestError,"prior_transport_failure"):
                client(b'{}')
            network.assert_called_once()
        self.assertEqual(client.attempted,1)
        self.assertEqual(client.completed,0)

    def test_invalid_request_budget_rejected(self):
        for budget in (0,-1,13,True):
            with self.subTest(budget=budget):
                with self.assertRaises(ValueError):
                    BoundedLiveRequester(max_requests=budget)

    def test_production_model_transmit_flags_not_required_for_offline_probes(self):
        # This probe is separate from the safe offline runner.
        from scripts.probe_market_video import run
        self.assertIsNotNone(run)
        self.assertNotEqual(
            run.__module__,
            "scripts.probe_market_group_b_live")


if __name__=="__main__":
    unittest.main()
