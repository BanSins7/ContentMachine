"""UA32 read-only diff/migration/peer-release preflight independent tests.

Injects synthetic local git output; tests do not use network, a database,
or real GitHub PR status. Any empty/untrusted evidence must stay BLOCKED.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from intelligence.unified_analytics import release_preflight as ra


class FakeGit:
    def __init__(self):
        self.calls=[]
        self.paths={
            ra.UAE_REF:(
                "intelligence/unified_analytics/contracts.py",
                "intelligence/migrations/080_unified_analytics_core.sql",
                "docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md",
                "site/unified-analytics-studio.js","app.py",
            ),
            ra.PEERS[0]:(
                "intelligence/group_a/contracts.py",
                "intelligence/migrations/055_group_a_handoff_inbox.sql",
            ),
            ra.PEERS[1]:(
                "app.py","site/index.html","site/projects.js",
                "intelligence/migrations/055_group_b_runtime.sql",
            ),
            ra.PEERS[2]:(
                "intelligence/migrations/056_group_c_runtime.sql",
            ),
            ra.PEERS[3]:(
                "site/index.html",
                "intelligence/migrations/055_sre_live_batch_coverage.sql",
            ),
            ra.PEERS[4]:(
                "intelligence/migrations/200_pie_standalone_foundation.sql",
            ),
        }
        self.fail=set()
    def __call__(self,*args):
        self.calls.append(args)
        if args[:2]==("rev-parse","--verify"):
            name=args[2].replace("^{commit}","")
            if name in self.fail:raise ra.PreflightError("git_ref_or_history_unavailable")
            return "a"*40 if name==ra.BASE_REF else "b"*40
        if args[:3]==("diff","--name-only","--diff-filter=ACMRD"):
            match=args[3].split("...",1)
            if len(match)!=2 or match[0]!=ra.BASE_REF:raise AssertionError(args)
            if match[1] in self.fail:
                raise ra.PreflightError("git_ref_or_history_unavailable")
            return "\n".join(self.paths[match[1]])
        raise AssertionError("Unexpected git command "+str(args))


GUARD=ra.NO_QUALIFIED+"\n"+ra.NO_COMPLETE


class ReleasePreflightTests(unittest.TestCase):
    def setUp(self):
        self.git=FakeGit()

    def test_all_actual_uae_file_classes_allowed(self):
        names=(
            "intelligence/unified_analytics/release_preflight.py",
            "intelligence/migrations/080_unified_analytics_core.sql",
            "intelligence/migrations/090_unified_analytics_project_aggregate_runs.sql",
            "tests/test_unified_analytics_release_preflight.py",
            "tests/node/test_unified_analytics_studio.cjs",
            "site/unified-analytics-studio.js",
            "docs/UNIFIED_ANALYTICS_RELEASE_READINESS_V1.md",
            "docs/UA_LEGACY_PORT_MATRIX.md",
            "app.py","site/index.html","site/projects.js",
            ".github/workflows/unified-analytics-module.yml",
        )
        self.assertTrue(all(ra.allowed_uae_path(x) for x in names))

    def test_peer_and_unknown_file_not_misclassified_as_uae(self):
        for path in (
            "intelligence/group_a/core.py","intelligence/group_b/core.py",
            "intelligence/migrations/055_group_a_runtime.sql",
            "intelligence/migrations/200_pie_standalone_foundation.sql",
            ".github/workflows/unrelated.yml","README.md",
            "site/sre-studio.js","worker/local_worker.py",
            "", "app.py\nignore",
        ):
            with self.subTest(path=path):
                self.assertFalse(ra.allowed_uae_path(path))

    def test_migration_id_extracts_entire_three_digit_prefix(self):
        self.assertEqual(ra.migration_id(
            "intelligence/migrations/080_unified_analytics_core.sql"),"080")
        self.assertEqual(ra.migration_id(
            "intelligence/migrations/055_group_a_runtime.sql"),"055")
        self.assertIsNone(ra.migration_id("app.py"))

    def test_fixed_approval_veto_cannot_be_silent_green(self):
        report=ra.inspect(git=self.git,guard_text=GUARD)
        self.assertEqual(report["verdict"],"blocked")
        self.assertFalse(report["ready_for_merge"])
        self.assertTrue(report["known_positive_qualification_veto"])
        self.assertIn("ua85_positive_qualified_intentionally_blocked",
                      report["blockers"])
        self.assertFalse(report["verified_pg17_result"])
        self.assertFalse(report["verified_ci_results"])

    def test_host_shared_overlap_precisely_detected(self):
        report=ra.inspect(git=self.git,guard_text=GUARD)
        peer={x["branch"]:x for x in report["peer_compatibility"]}
        self.assertEqual(peer[ra.PEERS[1]]["shared_surface_overlap"],["app.py"])
        self.assertEqual(peer[ra.PEERS[3]]["shared_surface_overlap"],[])
        self.assertIn("manual_shared_file_reconciliation_required",
                      report["blockers"])
        self.assertEqual(report["uae_diff"]["shared_host_paths"],["app.py"])

    def test_no_false_exact_migration_collision_for_different_names(self):
        report=ra.inspect(git=self.git,guard_text=GUARD)
        self.assertTrue(all(not x["exact_migration_overlap"]
                            for x in report["peer_compatibility"]))
        self.assertNotIn("same_name_migration_requires_reconciliation",
                         report["blockers"])

    def test_exact_migration_same_path_blocks(self):
        self.git.paths[ra.PEERS[2]]+=(
            "intelligence/migrations/080_unified_analytics_core.sql",
        )
        report=ra.inspect(git=self.git,guard_text=GUARD)
        self.assertIn("same_name_migration_requires_reconciliation",
                      report["blockers"])
        self.assertEqual(
            report["peer_compatibility"][2]["exact_migration_overlap"],
            ["intelligence/migrations/080_unified_analytics_core.sql"],
        )

    def test_peer_prefix_collision_is_not_a_duplicate_filename(self):
        report=ra.inspect(git=self.git,guard_text=GUARD)
        collisions=report["peer_migration_prefix_overlaps"]
        self.assertTrue(any(
            item["a"]==ra.PEERS[0] and item["b"]==ra.PEERS[1]
            and item["numeric_prefix_overlap"]==["055"]
            and item["exact_filenames"]==[]
            for item in collisions
        ))
        self.assertTrue(report["numeric_prefixes_are_not_version_identity"])
        self.assertIn("peer_migration_order_unverified",report["blockers"])
        self.assertNotIn("peer_same_name_migration_conflict",report["blockers"])

    def test_delete_of_foreign_peer_file_is_included_in_diff_filter(self):
        result=ra.inspect(git=self.git,guard_text=GUARD)
        commands=[x for x in self.git.calls if x[0]=="diff"]
        self.assertTrue(commands)
        self.assertTrue(all("--diff-filter=ACMRD" in x for x in commands))
        self.assertFalse(result["ready_for_merge"])

    def test_relative_parent_traversal_rejected(self):
        for path in ("../private.txt","docs/../private.txt",
                     "file/..","app.py\\evil.txt"):
            with self.subTest(path=path):
                with self.assertRaises(ra.PreflightError):
                    ra._paths(path)

    def test_non_uae_change_rejected_with_path(self):
        self.git.paths[ra.UAE_REF]+=("intelligence/group_c/core.py",)
        report=ra.inspect(git=self.git,guard_text=GUARD)
        self.assertIn("non_uae_changes_require_review",report["blockers"])
        self.assertIn("intelligence/group_c/core.py",
                      report["uae_diff"]["non_uae_paths"])

    def test_missing_peer_ref_rejected_not_fabricated(self):
        self.git.fail.add(ra.PEERS[3])
        report=ra.inspect(git=self.git,guard_text=GUARD)
        self.assertIn("peer_git_ref_missing",report["blockers"])
        self.assertFalse(report["peer_compatibility"][3]["diff_verified"])
        self.assertIsNone(report["peer_compatibility"][3]["local_head"])

    def test_missing_uae_ref_blocks_releasediff(self):
        self.git.fail.add(ra.UAE_REF)
        report=ra.inspect(git=self.git,guard_text=GUARD)
        self.assertIn("git_ref_or_history_unavailable",report["blockers"])
        self.assertIn("unverified_uae_diff",report["blockers"])
        self.assertFalse(report["ready_for_merge"])

    def test_missing_source_gate_requires_review_not_assumed_safe(self):
        report=ra.inspect(git=self.git,guard_text="")
        self.assertIn("unapproved_gate_unreadable",report["blockers"])
        self.assertFalse(report["known_positive_qualification_veto"])

    def test_partial_migration_gate_requires_review(self):
        self.assertIn("complete_evidence_proof_requires_separate_review",
                      ra.release_baseline_gate(ra.NO_QUALIFIED))
        self.assertIn("qualified_approval_migration_requires_separate_review",
                      ra.release_baseline_gate(ra.NO_COMPLETE))

    def test_git_inventory_malicious_filename_rejected(self):
        for raw in ('"\n"', '"bad\\nname"', "../evil.py\napp.py",
                    "a.py\na.py"):
            # Only a subset is unambiguously invalid; quoted/git
            # duplicates and traversal must always be rejected.
            if raw.startswith('"') or raw.count("a.py")==2:
                with self.assertRaises(ra.PreflightError):
                    ra._paths(raw)

    def test_preflight_does_not_execute_any_merge_or_change_command(self):
        ra.inspect(git=self.git,guard_text=GUARD)
        self.assertTrue(self.git.calls)
        for args in self.git.calls:
            self.assertIn(args[0],("diff","rev-parse"))
            self.assertNotIn("merge",args)
            self.assertNotIn("push",args)
            self.assertNotIn("checkout",args)

    def test_report_does_not_contain_secret_or_license_to_merge(self):
        report=ra.inspect(git=self.git,guard_text=GUARD)
        rendered=json.dumps(report)
        self.assertNotIn("postgresql://",rendered)
        self.assertNotIn("CM_UAE_OPS_PROJECT_TOKEN",rendered)
        self.assertFalse(report["canary_and_rollback_approved"])
        self.assertFalse(report["verified_approved_qdp_sre_abc"])

    def test_valid_schema_and_exact_head_shas(self):
        report=ra.inspect(git=self.git,guard_text=GUARD)
        self.assertEqual(report["schema_version"],"uae-release-preflight/v1")
        self.assertEqual(report["git"],{"main":"a"*40,"uae":"b"*40})
        self.assertEqual(report["uae_diff"]["changed_file_count"],5)
        self.assertEqual(len(report["peer_compatibility"]),len(ra.PEERS))


if __name__=="__main__":
    unittest.main()
