"""Regression that a prepared staging tree must not claim real PIE success."""
import unittest
from pathlib import Path
from scripts.pipeline_to_pie_preflight import static_report


class TestPipelineReadinessSource(unittest.TestCase):
    def test_source_modules_are_parsable(self):
        report = static_report()
        self.assertFalse(report["unavailable_or_invalid"],
                         report["unavailable_or_invalid"])

    def test_preflight_explicitly_reports_unresolved_modern_and_approval_gates(self):
        codes = set(static_report()["blocking_contracts"])
        self.assertIn("uae_sre_reader_requires_legacy_run_for_modern_sre", codes)
        self.assertIn("uae_qdp_reader_requires_legacy_sre_foreign_key", codes)
        self.assertIn("uae_positive_qualification_operator_release_blocked", codes)
        self.assertIn("pie_live_attestation_default_denied", codes)

    def test_preflight_never_fabricates_live_success(self):
        self.assertIs(static_report()["positive_pipeline_to_pie_verified"], False)


if __name__ == "__main__":
    unittest.main()
