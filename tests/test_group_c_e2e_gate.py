"""GC31 local E2E gate status truth and secret-safe report, pure tests."""
from __future__ import annotations

import unittest

from intelligence.group_c.e2e_gate import (
    VERSION,MIN_E2E_TESTS,_report,git_sha,
)


class TestCrossPlatformGatePure(unittest.TestCase):
    def fake(self,*,run=13,skips=0,errors=0,failures=0):
        r=unittest.TestResult()
        r.testsRun=run
        for _ in range(skips):
            r.skipped.append((None,"test not executed"))
        for _ in range(errors):
            r.errors.append((None,"error"))
        for _ in range(failures):
            r.failures.append((None,"failure"))
        return r

    def test_report_prohibits_production_peer_claim_from_synthetic_e2e(self):
        summary=_report(self.fake(),170006,"abcdef0123456789","a"*40)
        self.assertTrue(summary["local_synthetic_e2e_passed"])
        self.assertFalse(summary["production_qdp_verified"])
        self.assertFalse(summary["production_analytics_verified"])
        self.assertFalse(summary["production_iam_verified"])
        self.assertEqual(summary["schema_version"],VERSION)
        self.assertEqual(summary["pg_major"],17)

    def test_skips_and_failing_or_errored_tests_never_pass(self):
        for kw in ({"skips":1},{"errors":1},{"failures":1},
                   {"run":MIN_E2E_TESTS-1}):
            with self.subTest(kw=kw):
                report=_report(self.fake(**kw),170006,"abcdef0123456789","a"*40)
                self.assertFalse(report["local_synthetic_e2e_passed"])

    def test_missing_sha_cannot_claim_exact_head_validation(self):
        report=_report(self.fake(),170006,"abcdef0123456789",None)
        self.assertFalse(report["git_sha_verified"])
        self.assertFalse(report["local_synthetic_e2e_passed"])

    def test_report_does_not_expose_postgresql_connection_or_numeric_score(self):
        report=_report(self.fake(),170006,"abcdef0123456789","a"*40)
        self.assertNotIn("dsn",report)
        self.assertNotIn("password",report)
        self.assertNotIn("breakout_score",report)
        self.assertNotIn("source_metric_values",report)
        self.assertEqual(len(report["disposable_db_fingerprint"]),16)


if __name__=="__main__":
    unittest.main()
