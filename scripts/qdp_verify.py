#!/usr/bin/env python3
"""Standalone, no-GitHub-runner QDP QA. Never accepts production DB implicitly.

Examples:
  python scripts/qdp_verify.py
  QDP_DISPOSABLE_TEST_DB=YES CM_TEST_PG_DSN=postgresql://... \
    python scripts/qdp_verify.py --with-postgres --report /tmp/qdp-qa.json

Running without --with-postgres means UNVERIFIED_PG17, never PASS.
"""
from __future__ import annotations
import argparse
import compileall
import json
import os
import pathlib
import subprocess
import sys
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

UNITS = (
    "tests.test_qualified_data_pipeline",
    "tests.test_qdp_integration_contracts",
    "tests.test_qdp_live_sre_intake",
    "tests.test_qdp_coverage",
    "tests.test_qdp_schema_migrations.SchemaStaticTests",
    "tests.test_qdp_qualification",
    "tests.test_qdp_sre_inbox",
    "tests.test_qdp_run_manifest",
    "tests.test_qdp_provenance",
    "tests.test_qdp_metric_history",
    "tests.test_qdp_media_readiness",
    "tests.test_qdp_preparation",
    "tests.test_qdp_worker_hardening",
    "tests.test_qdp_outbox_delivery",
    "tests.test_qdp_group_a_bridge",
    "tests.test_qdp_downstream_compatibility",
    "tests.test_qdp_invalidation",
    "tests.test_qdp_studio",
    "tests.test_qdp_observability",
    "tests.test_qdp_release_gate",
    "tests.test_qdp_host_authorization",
)
PG = (
    "tests.test_qualified_data_db",
    "tests.test_qdp_schema_migrations.PostgresIntegrationMigrationTests",
)



class EvidenceResult(unittest.TextTestResult):
    """Keep exact test IDs and outcomes; aggregate counts alone are not QA-001 proof."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.test_results = {}

    def startTest(self, test):
        super().startTest(test)
        self.test_results[test.id()] = "STARTED"

    def addSuccess(self, test):
        super().addSuccess(test)
        self.test_results[test.id()] = "PASS"

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.test_results[test.id()] = "FAIL"

    def addError(self, test, err):
        super().addError(test, err)
        self.test_results[test.id()] = "ERROR"

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.test_results[test.id()] = "SKIPPED"

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.test_results[test.id()] = "EXPECTED_FAILURE"

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.test_results[test.id()] = "UNEXPECTED_SUCCESS"


def current_git_sha():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, check=True, timeout=5)
        sha = result.stdout.strip().lower()
        if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
            return sha
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--with-postgres", action="store_true",
                   help="Run disposable PostgreSQL 17 suite (requires explicit guard)")
    p.add_argument("--report", type=pathlib.Path,
                   help="Write machine-readable QA evidence. Does not contain DSN.")
    args = p.parse_args()
    if args.with_postgres and (not os.environ.get("CM_TEST_PG_DSN") or
                               os.environ.get("QDP_DISPOSABLE_TEST_DB") != "YES"):
        p.error("PostgreSQL QA requires disposable CM_TEST_PG_DSN and "
                "QDP_DISPOSABLE_TEST_DB=YES; production databases are forbidden")
    if args.with_postgres:
        try:
            import psycopg
            with psycopg.connect(os.environ["CM_TEST_PG_DSN"], connect_timeout=5) as db:
                server = int(db.execute("SHOW server_version_num").fetchone()[0])
                if not 170000 <= server < 180000:
                    p.error("QDP PostgreSQL integration test requires PostgreSQL 17")
        except ImportError:
            p.error("psycopg driver required for PostgreSQL 17 tests")
    modules = list(UNITS) + (list(PG) if args.with_postgres else [])
    suite = unittest.TestLoader().loadTestsFromNames(modules)
    result = unittest.TextTestRunner(verbosity=2, failfast=False,
                                     resultclass=EvidenceResult).run(suite)
    # Separate syntax checking is useful when dependencies are unavailable.
    syntax = compileall.compile_dir(str(ROOT / "intelligence/qualified_data"),
                                    quiet=1)
    try:
        node = subprocess.run(
            ["node", "--check", str(ROOT / "site/qualified-data.js")],
            capture_output=True, text=True, check=False)
        node_ok = node.returncode == 0
    except FileNotFoundError:
        # JS syntax cannot be marked PASS when Node is missing.
        node_ok = None
    # No skipped test is a full acceptance pass; a missing Node is not success.
    pg_skipped = bool(result.skipped)
    ok = (result.wasSuccessful() and syntax and node_ok is True and
          not result.skipped and
          all(status == "PASS" for status in result.test_results.values()))
    report = {
        "schema_version": "qdp-qa-evidence/v1",
        "git_sha": current_git_sha(),
        "test_results": dict(sorted(result.test_results.items())),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "python_version": sys.version.split()[0],
        "tests_run": result.testsRun,
        "tests_failed": len(result.failures),
        "tests_errored": len(result.errors),
        "tests_skipped": len(result.skipped),
        "unit_status": "PASS" if result.wasSuccessful() else "FAIL",
        "pg17_status": ("PASS" if result.wasSuccessful() and not pg_skipped
                        else "FAIL") if args.with_postgres else "NOT_RUN",
        "python_syntax": "PASS" if syntax else "FAIL",
        "js_syntax": ("PASS" if node_ok else
                      ("NOT_RUN" if node_ok is None else "FAIL")),
        "overall": ("PASS" if args.with_postgres and node_ok is True
                    else "PARTIAL") if ok else "FAIL",
        "note": ("Database QA ran on caller-confirmed disposable instance"
                 if args.with_postgres else "PG17 was not executed"),
    }
    print("\nQDP_QA_REPORT=" + json.dumps(report, sort_keys=True))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
