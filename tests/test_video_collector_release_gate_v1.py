"""VC-MOD-15 release gate contract: never mistake skipped PG17/live as PASS."""
from __future__ import annotations
from unittest import TestCase
from unittest.mock import patch
from scripts import collector_release_gate as release
from pathlib import Path


class CollectorReleaseGateTests(TestCase):
    def test_unit_only_records_skipped_pg_and_live_are_unverified(self):
        with patch.object(release,"_run",return_value=(True,"ok")),patch(
             "shutil.which",return_value="/usr/bin/node"):
            report=release.gate(unit_only=True)
        self.assertTrue(report["passed"])
        self.assertTrue(all(not x["passed"]
                    for x in report["postgres_integration"]))
        self.assertFalse(report["live_dtk_verified"])
        self.assertFalse(report["authenticated_sre_inbox_verified"])
        self.assertFalse(report["complete_pipeline_e2e_verified"])
        self.assertEqual(report["provider_requests"],0)

    def test_strict_pg_absent_is_failure_even_when_every_offline_test_passes(self):
        with patch.object(release,"_run",return_value=(True,"ok")),patch(
             "shutil.which",return_value="/usr/bin/node"),patch.object(
             release,"_pg17",return_value={
                 "configured":False,"server_version_num":None,"ready":False}):
            report=release.gate(strict_ci=True)
        self.assertFalse(report["passed"])
        self.assertEqual(report["postgres_integration"][0]["reason"],
                         "pg17_missing_or_wrong_version")

    def test_strict_pg16_is_refused_before_db_integration(self):
        with patch.object(release,"_run",return_value=(True,"ok")),patch(
             "shutil.which",return_value="/usr/bin/node"),patch.object(
             release,"_pg17",return_value={
                 "configured":True,"server_version_num":160009,"ready":False}):
            report=release.gate(strict_ci=True)
        self.assertFalse(report["passed"])

    def test_strict_pg17_and_all_suites_pass_even_if_live_not_verified(self):
        with patch.object(release,"_run",return_value=(True,"ok")),patch(
             "shutil.which",return_value="/usr/bin/node"),patch.object(
             release,"_pg17",return_value={
                 "configured":True,"server_version_num":170005,"ready":True}):
            report=release.gate(strict_ci=True)
        self.assertTrue(report["passed"])
        self.assertTrue(report["postgres_integration"][0]["passed"])
        self.assertFalse(report["complete_pipeline_e2e_verified"])
        self.assertFalse(report["authenticated_sre_inbox_verified"])

    def test_single_failed_suite_blocks_pass_without_secret_test_traces(self):
        with patch.object(release,"_run",return_value=(False,"test_failed")),patch(
             "shutil.which",return_value=None):
            report=release.gate(unit_only=True)
        self.assertFalse(report["passed"])
        self.assertNotIn("postgresql://",str(report))
        self.assertNotIn("Traceback",str(report))

    def test_pg_missing_configuration_never_probes_network(self):
        with patch.dict("os.environ",{"CM_TEST_PG_DSN":""}):
            self.assertEqual(release._pg17(),{
                "configured":False,"server_version_num":None,"ready":False,
            })

    def test_suites_remain_versioned_and_require_core_scopes(self):
        expected={"ports_qv","planner_owner","worker_dtk",
                  "d1_completion","outbox_recovery","migration_contracts"}
        self.assertEqual(set(release.SUITES),expected)
        unique={f for files in release.SUITES.values() for f in files}
        self.assertGreaterEqual(len(unique),20)
        self.assertIn("test_video_collector_pg15_integration.py",release.PG_TESTS)
        self.assertIn("site/collector.js",release.JS_FILES)

if __name__=="__main__":
    import unittest
    unittest.main()
