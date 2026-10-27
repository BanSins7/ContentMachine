"""Regression for FIX-18: missing QA evidence must never become green release."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.qdp_release_gate import assess

ROOT = Path(__file__).resolve().parents[1]
MATRIX = json.loads((ROOT / "docs/QDP_QA_001_120.json").read_text(encoding="utf-8"))


def evidence(*, sha="a" * 40, pg=True, skips=0, tests=None):
    return {
        "git_sha": sha,
        "overall": "PASS" if pg else "PARTIAL",
        "unit_status": "PASS",
        "pg17_status": "PASS" if pg else "NOT_RUN",
        "python_syntax": "PASS",
        "js_syntax": "PASS",
        "tests_failed": 0,
        "tests_errored": 0,
        "tests_skipped": skips,
        "test_results": tests or {},
    }


class QDPReleaseGateTests(unittest.TestCase):
    def test_original_matrix_has_120_nonempty_scenarios(self):
        cases = MATRIX["cases"]
        self.assertEqual(len(cases), 120)
        self.assertEqual([item["id"] for item in cases],
                         [f"QA-{n:03d}" for n in range(1, 121)])
        self.assertTrue(all(item["scenario"] for item in cases))
        self.assertEqual(cases[115]["tier"], "external")
        self.assertEqual(cases[117]["tier"], "external")
        self.assertEqual(cases[118]["tier"], "external")

    def test_gate_blocks_incomplete_evidence(self):
        result = assess(MATRIX, None, None)
        self.assertEqual(result["release_status"], "NO_GO")
        self.assertEqual(result["qa_not_closed"], 120)
        self.assertIn("linux_pg17_evidence_missing", result["blockers"])
        self.assertIn("windows_unit_evidence_missing", result["blockers"])

    def test_actual_unit_name_requires_exact_unique_passing_result(self):
        cases = MATRIX["cases"]
        method = cases[0]["tests"][0]
        test = "tests.test_qdp_live_sre_intake.LiveSRE.test_" + method[5:]
        result = assess(MATRIX, evidence(tests={test: "PASS"}),
                        evidence(pg=False))
        self.assertEqual(result["cases"][0]["status"], "PASS")
        self.assertEqual(result["cases"][1]["status"], "UNVERIFIED")
        # Ambiguous matches must not certify a scenario.
        result = assess(MATRIX, evidence(tests={test:"PASS", "other.X." + method:"PASS"}),
                        evidence(pg=False))
        self.assertEqual(result["cases"][0]["status"], "UNVERIFIED")

    def test_skips_and_cross_platform_sha_mismatch_cannot_be_ready(self):
        result = assess(MATRIX, evidence(skips=1), evidence(pg=False))
        self.assertIn("linux_pg17_not_verified", result["blockers"])
        other = assess(MATRIX, evidence(), evidence(sha="b"*40,pg=False))
        self.assertIn("linux_windows_commit_mismatch", other["blockers"])
        self.assertEqual(other["release_status"], "NO_GO")

    def test_even_passing_unit_tests_do_not_approve_live_pipeline(self):
        methods = {f"tests.test_suite.Case.{t}": "PASS"
                   for item in MATRIX["cases"]
                   for t in item["tests"]}
        result = assess(MATRIX, evidence(tests=methods), evidence(pg=False))
        self.assertEqual(result["release_status"], "NO_GO")
        self.assertEqual(result["cases"][115]["status"], "BLOCKED_EXTERNAL")
        self.assertEqual(result["cases"][117]["status"], "BLOCKED_EXTERNAL")
        self.assertEqual(result["cases"][118]["status"], "BLOCKED_EXTERNAL")
        self.assertGreater(result["qa_not_closed"], 0)

    def test_duplicate_or_missing_original_case_is_rejected(self):
        clone = dict(MATRIX)
        clone["cases"] = MATRIX["cases"][:-1]
        with self.assertRaises(ValueError):
            assess(clone, None, None)
        clone["cases"] = MATRIX["cases"][:]
        clone["cases"][0] = clone["cases"][1]
        with self.assertRaises(ValueError):
            assess(clone, None, None)


if __name__ == "__main__":
    unittest.main()
