"""Check read-only audit never mistakes unavailable A/B/C for passed live E2E."""
import unittest
from unittest.mock import patch
from scripts.check_market_live_project import audit_snapshot

PROJECT="a"*32

class LiveAuditContracts(unittest.TestCase):
    def test_empty_project_cannot_pass_hundred_video_e2e(self):
        report=audit_snapshot({"project_id":PROJECT,"video_evidence":[]},
                              {"batch_id":3,"selected":100,
                               "videos":[]},expect=100)
        self.assertFalse(report["all_expected_source_scoped_receipts_present"])
        self.assertEqual(report["market_metrics_coverage"]["c0_exact"],0)
        self.assertFalse(report["official_original_group_abc_certified"])
        self.assertEqual(report["provider_requests"],0)
        self.assertEqual(report["writes_to_project_database"],0)

    def test_unverifiable_semantics_are_reported_not_guessed(self):
        items=[{
            "video_id":17,"url":"https://private-user.test/video",
            "group_a":{"source_media_sha256":"a"*64,"report_sha256":"fake"},
            "group_b":{"blocks":{}},
            "group_c":{"c0":"unavailable","c5":None,
                       "c7_breakout_preview":None},
        }]
        result=audit_snapshot({"project_id":PROJECT,"video_evidence":items},
             {"batch_id":4,"selected":1,"videos":[{"video_id":17}]},expect=1)
        video=result["videos"][0]
        self.assertFalse(video["a_verified"])
        self.assertFalse(video["b_verified"])
        self.assertFalse(video["c_verified"])
        self.assertEqual(len(video["issues"]),4)
        self.assertNotIn("https://private-user.test",str(result))
        self.assertFalse(result["all_expected_source_scoped_receipts_present"])


if __name__=="__main__":
    unittest.main()
