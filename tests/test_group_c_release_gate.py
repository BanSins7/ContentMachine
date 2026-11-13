"""GC-MOD-32 release-preflight: fail-closed pure tests + optional real PG17.

Never merge, never disable CI, never treat synthetic external ports as production.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from intelligence.group_c.release_gate import (
    VERSION,MIGRATIONS,BASELINE_PROTECTED,REQUIRED_RUNBOOKS,PEER_FILE,
    ReleaseAuditError,allowed_change,check_changed_paths,
    inspect_migration_inventory,inspect_repo,evaluate_release,
)

ROOT=Path(__file__).resolve().parents[1]
PEERS=json.loads((ROOT/PEER_FILE).read_text(encoding="utf-8"))
SHA="a"*40


def facts():
    return {
        "git_head_sha":SHA,
        "base_ref":"main",
        "changed_file_count":116,
        "preserved_baseline_migration_sha256":{
            "intelligence/migrations/003_performance_c.sql":"f"*64,
            "intelligence/migrations/004_performance_c_integrity.sql":"e"*64,
        },
        "allowed_scope_only":True,
    }


class TestGC32ReleaseGatePure(unittest.TestCase):
    def test_exact_migration_inventory_and_owned_paths(self):
        self.assertEqual(len(MIGRATIONS),9)
        self.assertTrue(MIGRATIONS[0].endswith("056_group_c_runtime.sql"))
        self.assertTrue(MIGRATIONS[-1].endswith("064_group_c_performance_indexes.sql"))
        self.assertTrue(all(allowed_change(path) for path in (
            *MIGRATIONS,"intelligence/group_c/release_gate.py",
            "docs/GROUP_C_STAGE32_PEER_AUDIT.json",
            "tests/test_group_c_release_gate.py",
            ".github/workflows/group-c-module.yml",
            "site/group-c-studio.js",
        )))

    def test_deny_peer_module_changes_and_existing_migrations(self):
        bad=(
            "main","intelligence/group_a/worker.py",
            "intelligence/group_b/worker.py",
            "intelligence/unified_analytics/group_c_adapter.py",
            "intelligence/migrations/003_performance_c.sql",
            "intelligence/migrations/004_performance_c_integrity.sql",
            "intelligence/migrations/056_sp_http_paid_idempotency.sql",
            ".github/workflows/unified-analytics-module.yml",
            "tests/test_unified_analytics_group_c_adapter.py",
            "docs/README.md","../secrets.txt",
        )
        for path in bad:
            with self.subTest(path=path):
                # Only the two 003/004 are forbidden by scope checker itself
                # via check_changed_paths (they are never in whitelist).
                self.assertFalse(allowed_change(path))
                self.assertEqual(check_changed_paths({path:"M"}),(path,))

    def test_checked_paths_renames_are_subject_to_same_restriction(self):
        self.assertEqual(
            check_changed_paths({
                "intelligence/group_c/c0.py":"R100",
                "intelligence/unified_analytics/group_c_adapter.py":"R100",
            }),
            ("intelligence/unified_analytics/group_c_adapter.py",),
        )
        self.assertEqual(check_changed_paths({}),())

    def test_audited_peers_have_overlapping_numbering_but_not_same_names(self):
        observed=inspect_migration_inventory(PEERS)
        self.assertEqual(observed["exact_filename_collisions"],())
        self.assertEqual(observed["shared_group_c_numeric_prefixes"],
                         tuple(range(56,65)))
        self.assertTrue(observed["merge_order_not_verified"])
        self.assertTrue(observed["combined_fresh_pg17_not_verified"])
        self.assertTrue(observed["combined_upgrade_pg17_not_verified"])
        self.assertGreater(observed["combined_migration_count"],40)

    def test_duplicate_real_file_path_is_reported_as_hard_conflict(self):
        clone=copy.deepcopy(PEERS)
        clone["peers"][0]["migrations"].append(MIGRATIONS[0])
        self.assertIn(MIGRATIONS[0],
                      inspect_migration_inventory(clone)["exact_filename_collisions"])

    def test_incomplete_migration_inventory_is_never_assumed_safe(self):
        for modified in (
            {"schema_version":"unknown","group_c":{},"peers":[]},
            {**PEERS,"peers":[]},
            {**PEERS,"group_c":{"migrations":[]}},
        ):
            with self.assertRaises(ReleaseAuditError):
                inspect_migration_inventory(modified)

    def test_known_unresolved_peer_dependencies_remain_hard_blockers(self):
        report=evaluate_release(facts(),PEERS)
        self.assertEqual(report["schema_version"],VERSION)
        self.assertEqual(report["readiness"],"BLOCKED_EXTERNAL_AND_QA")
        self.assertFalse(report["merge_ready"])
        self.assertFalse(report["merge_authorized"])
        self.assertFalse(report["real_iam_attested"])
        self.assertFalse(report["real_qdp_peer_attested"])
        self.assertFalse(report["real_unified_analytics_peer_attested"])
        self.assertFalse(report["github_ci_exact_head_green_attested"])
        self.assertIn("uae_c_effective_approval_unavailable",report["blockers"])
        self.assertIn("migration_prefix_overlap_combined_order_unverified",report["blockers"])
        self.assertIn("windows_real_pg17_exact_sha_unverified",report["blockers"])
        self.assertIn("local_exact_sha_e2e_missing",report["blockers"])

    def test_even_synthetic_green_and_exact_sha_do_not_authorize_real_iam(self):
        pretend={
            "git_sha":SHA,"local_synthetic_e2e_passed":True,
            "tests_skipped":0,"tests_failed":0,"tests_errored":0,
            "production_qdp_verified":True, # adversarial forged input
            "production_analytics_verified":True,
            "production_iam_verified":True,
        }
        report=evaluate_release(facts(),PEERS,local_e2e=pretend)
        self.assertFalse(report["merge_ready"])
        self.assertFalse(report["real_iam_attested"])
        self.assertFalse(report["real_qdp_peer_attested"])
        self.assertFalse(report["real_unified_analytics_peer_attested"])
        self.assertIn("synthetic_e2e_is_not_production_attestation",
                      report["blockers"])
        self.assertNotIn("local_exact_sha_e2e_not_passed",report["blockers"])

    def test_stale_sha_or_skipped_test_cannot_count_as_local_pass(self):
        for case in (
            {"git_sha":"b"*40,"local_synthetic_e2e_passed":True,
             "tests_skipped":0,"tests_failed":0,"tests_errored":0},
            {"git_sha":SHA,"local_synthetic_e2e_passed":True,
             "tests_skipped":1,"tests_failed":0,"tests_errored":0},
            {"git_sha":SHA,"local_synthetic_e2e_passed":False,
             "tests_skipped":0,"tests_failed":0,"tests_errored":0},
        ):
            with self.subTest(case=case):
                report=evaluate_release(facts(),PEERS,local_e2e=case)
                self.assertIn("local_exact_sha_e2e_not_passed",
                              report["blockers"])
                self.assertFalse(report["merge_ready"])

    def test_malformed_peer_sha_cannot_be_silently_treated_as_release(self):
        with self.assertRaises(ReleaseAuditError):
            evaluate_release({**facts(),"git_head_sha":"x"},PEERS)
        with self.assertRaises(ReleaseAuditError):
            evaluate_release(facts(),{**PEERS,"runtime_blockers":[]})

    def test_scope_audit_strictly_checks_both_immutable_git_blobs(self):
        with TemporaryDirectory() as temp:
            repo=Path(temp)
            for path in (
                "intelligence/migrate.py",*BASELINE_PROTECTED,
                *MIGRATIONS,*REQUIRED_RUNBOOKS,PEER_FILE,
            ):
                f=repo/path
                f.parent.mkdir(parents=True,exist_ok=True)
                f.write_text("test-only",encoding="utf-8")
            def mock_git(_repo,*args):
                if args==("rev-parse","HEAD"):
                    return SHA
                if args==("status","--porcelain"):
                    return ""
                if args[0]=="diff":
                    return "A\tintelligence/group_c/release_gate.py"
                if args[:2]==("rev-parse","main:intelligence/migrations/003_performance_c.sql"):
                    return "1"*40
                if args[:2]==("rev-parse","main:intelligence/migrations/004_performance_c_integrity.sql"):
                    return "2"*40
                if args[0]=="hash-object":
                    return ("1"*40 if args[1].endswith(
                        "003_performance_c.sql") else "2"*40)
                raise AssertionError("Unexpected git invocation: "+str(args))
            with patch("intelligence.group_c.release_gate._git",side_effect=mock_git):
                result=inspect_repo(repo,"main")
            self.assertEqual(result["git_head_sha"],SHA)
            self.assertEqual(result["changed_file_count"],1)
            self.assertTrue(result["allowed_scope_only"])
            def drift(_repo,*args):
                output=mock_git(_repo,*args)
                if args[0]=="hash-object" and args[1].endswith(
                        "004_performance_c_integrity.sql"):
                    return "f"*40
                return output
            with patch("intelligence.group_c.release_gate._git",side_effect=drift):
                with self.assertRaises(ReleaseAuditError):
                    inspect_repo(repo,"main")

    def test_dirty_worktree_is_not_exact_sha_evidence(self):
        with TemporaryDirectory() as temp:
            repo=Path(temp)
            (repo/"intelligence").mkdir()
            (repo/"intelligence/migrate.py").write_text("fixture")
            def status(_repo,*args):
                if args==("rev-parse","HEAD"):return SHA
                if args==("status","--porcelain"):return " M intelligence/group_c/c0.py"
                raise AssertionError("must stop on dirty files")
            with patch("intelligence.group_c.release_gate._git",side_effect=status):
                with self.assertRaises(ReleaseAuditError):
                    inspect_repo(repo,"main")

    def test_blockers_list_is_unique_with_no_secrets_or_numeric_score(self):
        report=evaluate_release(facts(),PEERS)
        self.assertEqual(len(report["blockers"]),len(set(report["blockers"])))
        self.assertNotIn("dsn",report)
        self.assertNotIn("access_token",report)
        self.assertNotIn("breakout_score",report)


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and
    os.getenv("GC_TEST_DISPOSABLE_DB")=="YES" and
    os.getenv("GC32_ALLOW_DB_MIGRATION")=="YES",
    "Only explicitly opted-in disposable PostgreSQL17 for GC32 DB checksum QA",
)
class TestGC32ReleaseGatePG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.conn=psycopg.connect(os.environ["CM_TEST_PG_DSN"],
                                 autocommit=True,connect_timeout=5)
        name,ver=cls.conn.execute(
            "SELECT current_database(),current_setting('server_version_num')::int"
        ).fetchone()
        if (not name.startswith(("contentmachine_gc_","gc_e2e_"))
                or not 170000<=ver<180000):
            cls.conn.close()
            raise AssertionError("Refusing GC32 migration on non-disposable PG17")
        from intelligence import migrate
        migrate.apply_migrations(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_original_003_004_sha_and_new_056_064_migration_checksums(self):
        for path in (*BASELINE_PROTECTED,*MIGRATIONS):
            with self.subTest(path=path):
                name=Path(path).name
                stored=self.conn.execute(
                    "SELECT sha256 FROM schema_migrations WHERE version=%s",
                    (name,),
                ).fetchone()
                self.assertIsNotNone(stored)
                self.assertEqual(
                    stored[0],hashlib.sha256((ROOT/path).read_text(encoding="utf-8").encode("utf-8")).hexdigest())

    def test_second_apply_migrations_returns_nothing(self):
        from intelligence import migrate
        self.assertEqual(migrate.apply_migrations(self.conn),[])


if __name__=="__main__":
    unittest.main()
