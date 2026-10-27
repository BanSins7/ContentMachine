"""Small offline-only FIX-10/11 handoff unit tests; never call providers."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from intelligence.taxonomy_builder import local_release_handoff as handoff


class RTBLocalHandoffTests(unittest.TestCase):
    def good(self, **overrides):
        kwargs = dict(
            git_check=True,
            fingerprints=({"example.txt": "ab" * 32}, []),
            qa_report={"case_count": 36, "mapped": 34,
                       "reference_check_passed": True,
                       "partial": [], "external_gaps": ["T28", "T29"]},
            migration_report={"static_valid": True},
            boundary_report={"source_isolation_passed": True,
                             "head_sha": "a" * 40, "base_sha": "b" * 40},
            tests=[],
        )
        kwargs.update(overrides)
        return handoff.snapshot(**kwargs)

    def test_complete_source_handoff_never_claims_release_or_qa_pass(self):
        report = self.good()
        self.assertTrue(report["code_handoff_valid"])
        self.assertFalse(report["release_ready"])
        self.assertFalse(report["merge_authorized"])
        self.assertEqual(report["local_pg17_qa"], "DEFERRED_TO_OWNER_PC")
        self.assertEqual(report["external_search_planner_t28_t29"],
                         "EXTERNAL_NOT_VERIFIED")

    def test_modified_file_changes_fingerprint(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            path = root / "example.txt"
            path.write_text("before", encoding="utf-8")
            initial, issues = handoff.source_fingerprints(root, ("example.txt",))
            self.assertFalse(issues)
            self.assertEqual(initial["example.txt"],
                             hashlib.sha256(b"before").hexdigest())
            path.write_text("after", encoding="utf-8")
            changed, issues = handoff.source_fingerprints(root, ("example.txt",))
            self.assertNotEqual(initial, changed)

    def test_missing_and_escaped_paths_fail_closed(self):
        with tempfile.TemporaryDirectory() as work:
            hashes, issues = handoff.source_fingerprints(
                Path(work), ("missing.txt", "../outside.txt"))
            self.assertFalse(hashes)
            self.assertIn("missing_source:missing.txt", issues)
            self.assertIn("outside_repository:../outside.txt", issues)

    def test_no_git_means_only_source_audit_not_merge_ready(self):
        report = self.good(git_check=False, boundary_report=None)
        self.assertFalse(report["code_handoff_valid"])
        self.assertTrue(report["source_only_valid"])
        self.assertEqual(report["source_isolation"], "NOT_CHECKED")

    def test_missing_boundary_blocks_handoff(self):
        report = self.good(boundary_report={
            "source_isolation_passed": False,
            "violations": ["foreign_module_or_unreviewed_file:abc"]})
        self.assertFalse(report["code_handoff_valid"])
        self.assertIn("release_boundary_invalid", report["issues"])

    def test_partial_case_or_sp_scope_drift_fails_closed(self):
        report = self.good(qa_report={
            "case_count": 36, "mapped": 35,
            "reference_check_passed": True,
            "partial": ["T23"], "external_gaps": ["T28"]})
        self.assertFalse(report["code_handoff_valid"])
        self.assertTrue(any("partial_rtb_test_coverage" in x
                            for x in report["issues"]))
        self.assertIn("unexpected_downstream_scope", report["issues"])

    def test_basic_local_failure_is_not_masked(self):
        report = self.good(tests=[{"suite": "source-only", "exit_code": 1}])
        self.assertFalse(report["code_handoff_valid"])
        self.assertIn("basic_local_tests_failed", report["issues"])

    def test_static_sql_migration_invalid_blocks(self):
        report = self.good(migration_report={"static_valid": False})
        self.assertFalse(report["code_handoff_valid"])
        self.assertIn("rtb_migration_source_invalid", report["issues"])


if __name__ == "__main__":
    unittest.main()
