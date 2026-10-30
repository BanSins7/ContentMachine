"""GC-MOD-31 cross-platform local PG17 E2E verification gate.

This is TEST-ONLY and cannot claim real QDP/Analytics production handshake.
It records exact local Git SHA, OS, PG version, test counts and fixture status.
Write JSON containing no DB URI, bearer, user identity or source metric values.

  GC_TEST_DISPOSABLE_DB=YES GC31_ALLOW_SYNTHETIC_QDP=YES \
  CM_TEST_PG_DSN=postgresql://.../contentmachine_gc_e2e \
  python -m intelligence.group_c.e2e_gate --report gc31-e2e-local.json

Exit 0 only when every E2E test actually runs and passes; skips are NOT PASS.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import unittest

VERSION="group-c-crossplatform-e2e-report/v1"
MODULE="test_group_c_crossplatform_e2e.py"
MIN_E2E_TESTS=12

def git_sha():
    try:
        result=subprocess.run(
            ["git","rev-parse","HEAD"],check=True,capture_output=True,
            text=True,timeout=5,
        )
    except (OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired):
        return None
    value=result.stdout.strip()
    return value if len(value)==40 and all(c in "0123456789abcdef" for c in value) else None


def _guard():
    if (os.getenv("GC_TEST_DISPOSABLE_DB")!="YES"
            or os.getenv("GC31_ALLOW_SYNTHETIC_QDP")!="YES"
            or not os.getenv("CM_TEST_PG_DSN")):
        raise RuntimeError("Independent disposable PG17 and synthetic QDP opt-in required")
    import psycopg
    with psycopg.connect(os.environ["CM_TEST_PG_DSN"],
                         autocommit=True,connect_timeout=5) as conn:
        name,ver=conn.execute(
            "SELECT current_database(),current_setting('server_version_num')::int"
        ).fetchone()
        if (not name.startswith(("contentmachine_gc_","gc_e2e_"))
                or not 170000<=ver<180000):
            raise RuntimeError("Refusing to run outside named disposable PostgreSQL17")
        for table in ("qdp_outbox","qdp_group_current_preparations",
                      "gc_test_qdp_preparations",
                      "gc25_disposable_analytics_receipts"):
            if conn.execute(
                "SELECT to_regclass(%s)",("public."+table,),
            ).fetchone()[0] is not None:
                raise RuntimeError(
                    "Refusing to alter existing QDP/Analytics tables or fixtures"
                )
        return ver,sha256(name.encode("utf-8")).hexdigest()[:16]


def _report(tests,pg_major,db_fingerprint,revision):
    """Report only after an ACTUAL completed unittest run."""
    if not isinstance(tests,unittest.result.TestResult):
        raise TypeError("Unittest Result required")
    return {
        "schema_version":VERSION,
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "platform":platform.system(),
        "python_version":platform.python_version(),
        "git_sha":revision,
        "git_sha_verified":revision is not None,
        "pg_major":pg_major//10000,
        "disposable_db_fingerprint":db_fingerprint,
        "tests_run":tests.testsRun,
        "tests_failed":len(tests.failures),
        "tests_errored":len(tests.errors),
        "tests_skipped":len(tests.skipped),
        "tests_expected_failures":len(tests.expectedFailures),
        "tests_unexpected_success":len(tests.unexpectedSuccesses),
        "production_qdp_verified":False,
        "production_analytics_verified":False,
        "production_iam_verified":False,
        "local_synthetic_e2e_passed":(
            tests.wasSuccessful()
            and tests.testsRun>=MIN_E2E_TESTS
            and not tests.skipped
            and revision is not None
        ),
    }


def main():
    ap=argparse.ArgumentParser(description="GC31 exact HEAD local PG17 E2E gate")
    ap.add_argument("--report",required=True,type=Path)
    args=ap.parse_args()
    # No "PASS" report if DB preflight fails. Never echo the DSN.
    ver,fingerprint=_guard()
    revision=git_sha()
    suite=unittest.defaultTestLoader.discover(
        start_dir="tests",pattern=MODULE,
    )
    runner=unittest.TextTestRunner(verbosity=2)
    results=runner.run(suite)
    report=_report(results,ver,fingerprint,revision)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(
        json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+"\n",
        encoding="utf-8",
    )
    print("GC31 local evidence:",args.report,
          "status=PASS_TEST_DOUBLE_ONLY" if report["local_synthetic_e2e_passed"]
          else "status=NOT_VERIFIED")
    return 0 if report["local_synthetic_e2e_passed"] else 1


if __name__=="__main__":
    sys.exit(main())
