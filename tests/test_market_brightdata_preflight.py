"""Offline security/compatibility checks for the read-only Bright Data preflight."""
import unittest
from unittest.mock import patch
from intelligence.market_intelligence import brightdata_preflight as module


class BrightDataPreflight(unittest.TestCase):
    def test_missing_token_makes_no_request(self):
        with patch.object(module.config,"setting",return_value=""):
            response=module.check(fetcher=lambda token:self.fail("network call"))
        self.assertEqual(response["status"],"not_configured")

    def test_authenticated_catalog_lists_dataset_ids(self):
        sample=[{"id":module.DATASETS["tiktok"],"name":"TikTok - Posts"},
                {"id":module.DATASETS["youtube"],"name":"Youtube - Videos posts"},
                {"id":"gd_somethingelse12345","name":"Unrelated"}]
        with patch.object(module.config,"setting",side_effect=lambda key,default="": "fake-token" if key=="CM_BRIGHTDATA_API_TOKEN" else default):
            report=module.check(fetcher=lambda token:(200,sample))
        self.assertEqual(report["status"],"authenticated")
        self.assertTrue(report["targets"]["tiktok"]["listed"])
        self.assertFalse(report["targets"]["instagram"]["listed"])
        self.assertFalse(report["collection_started"])
        self.assertNotIn("fake-token",str(report))

    def test_unauthorized_sanitized(self):
        with patch.object(module.config,"setting",return_value="fake-token"):
            report=module.check(fetcher=lambda token:(401,None))
        self.assertEqual(report["status"],"unauthorized")
        self.assertNotIn("fake-token",str(report))

    def test_invalid_catalog_refused(self):
        with patch.object(module.config,"setting",return_value="fake-token"):
            report=module.check(fetcher=lambda token:(200,{"items":[]}))
        self.assertEqual(report["reason_code"],"unexpected_catalog_schema")
        self.assertFalse(report["collection_started"])


if __name__=="__main__":
    unittest.main()
