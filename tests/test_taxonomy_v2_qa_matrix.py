"""FIX-10: audit all T01–T36 test links and explicit integration gaps.

This suite uses only Python AST and never imports or invokes the referenced
tests. The official CI still needs to execute the referenced suites.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import unittest

from intelligence.taxonomy_builder import qa_matrix


class RTBT01T36QAAudit(unittest.TestCase):

    def test_exactly_36_distinct_spec_requirements(self):
        ids = [case["id"] for case in qa_matrix.CASES]
        self.assertEqual(ids, [f"T{i:02d}" for i in range(1, 37)])
        self.assertEqual(len(set(ids)), 36)

    def test_every_evidence_reference_is_actual_python_unittest_method(self):
        summary = qa_matrix.audit()
        self.assertTrue(summary["reference_check_passed"],
                        "\n".join(summary["reference_errors"]))
        self.assertEqual(summary["case_count"], 36)

    def test_only_external_sp_v2_integration_gaps_remain_explicit(self):
        summary = qa_matrix.audit()
        self.assertEqual(summary["partial"], [])
        self.assertEqual(set(summary["external_gaps"]), {"T28", "T29"})
        self.assertEqual(summary["mapped"], 34)
        self.assertFalse(summary["runtime_tests_executed"])
        self.assertFalse(summary["ci_success_verified"])

    def test_release_strict_mode_fails_until_actual_integrations_exist(self):
        with redirect_stdout(StringIO()):
            code = qa_matrix.main(["--strict"])
        self.assertEqual(code, 2)
        with redirect_stdout(StringIO()):
            self.assertEqual(qa_matrix.main(["--check"]), 0)

    def test_curated_offline_suite_has_no_live_e2e_entrypoint(self):
        all_entries = (*qa_matrix.SAFE_OFFLINE,
                       *qa_matrix.SAFE_NODE, *qa_matrix.SAFE_PG)
        self.assertEqual(len(all_entries), len(set(all_entries)))
        for name in all_entries:
            self.assertTrue(name.startswith("test_"))
            self.assertTrue(name.endswith((".py", ".cjs")))
            self.assertNotIn("live", name)
            self.assertNotIn("edema", name)
            self.assertNotIn("five_e2e", name)
            self.assertTrue((qa_matrix.TESTS / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
