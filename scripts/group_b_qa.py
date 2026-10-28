#!/usr/bin/env python3
"""GB-MOD-28 reproducible isolated Group B QA runner.

Modes:
  static: all Group B/test Python compile, deterministic fixtures, JS syntax
  pure:   static + ALL non-DB Group B Python suites + Node UI
  pg17:   STRICT all DB-only Group B suites; skip is an error
  full:   pure + strict pg17 (requires disposable PG17)

Writes safe machine-readable report even on failures; never writes
connections, environment variables, credentials, HTTP payloads or media.
No network/provider calls in the runner. A unit test PASS is not a
real-model quality gate or receipt.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr,redirect_stdout
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import unittest

ROOT=Path(__file__).resolve().parents[1]
TESTS=ROOT/"tests"
BTESTS=tuple(sorted(TESTS.glob("test_group_b_*.py")))
EXPECTED=(
    "test_group_b_validation.py",
    "test_group_b_provider.py",
    "test_group_b_review.py",
    "test_group_b_invalidation.py",
    "test_group_b_analytics.py",
    "test_group_b_http.py",
    "test_group_b_recovery.py",
    "test_group_b_release_golden.py",
    "test_group_b_atomic_db.py",
    "test_group_b_recovery_db.py",
)
COMPILE=tuple(sorted((ROOT/"intelligence"/"group_b").rglob("*.py")))+BTESTS
NEVER_REPORT=("CM_TEST_PG_DSN","DATABASE_URL","OPENROUTER_API_KEY","PASSWORD",
              "AUTHORIZATION","TOKEN","SECRET")
SCHEMA="group-b-qa-evidence/v1"


def _record(name,state,*,count=0,skipped=0,elapsed_ms=0,reason=None):
    return {"suite":name,"status":state,"tests":count,"skipped":skipped,
            "elapsed_ms":elapsed_ms,"reason_code":reason}


def _compile_files():
    start=time.monotonic()
    if not BTESTS or not all((TESTS/name).is_file() for name in EXPECTED):
        return _record("compile_and_discovery","fail",
                       reason="expected_tests_missing")
    errors=[]
    for p in COMPILE:
        try:
            compile(p.read_bytes(),str(p.relative_to(ROOT)),"exec")
        except (SyntaxError,UnicodeError,ValueError):
            errors.append(p.relative_to(ROOT).as_posix())
    if errors:
        # Safe paths only. No source text/contents in the report.
        print("Python parse failure in:",", ".join(errors),file=sys.stderr)
    return _record("compile_and_discovery","fail" if errors else "pass",
                   count=len(COMPILE),elapsed_ms=int((time.monotonic()-start)*1000),
                   reason="python_syntax_error" if errors else None)


def _node():
    start=time.monotonic()
    node=shutil.which("node")
    if not node:
        return _record("node_ui","fail",reason="node_binary_missing")
    for cmd in (
        [node,"--check",str(ROOT/"site"/"group-b.js")],
        [node,"--test",str(TESTS/"test_group_b_ui.cjs")],
    ):
        try:
            result=subprocess.run(cmd,cwd=ROOT,timeout=120,
                capture_output=True,text=True,errors="replace")
        except (subprocess.TimeoutExpired,OSError):
            return _record("node_ui","fail",reason="node_execution_unavailable")
        if result.returncode:
            print("GB28 Node test failed:",cmd[1],file=sys.stderr)
            # Deliberately do not store raw stderr in the report.
            return _record("node_ui","fail",elapsed_ms=int(
                (time.monotonic()-start)*1000),reason="node_suite_failed")
    return _record("node_ui","pass",count=2,elapsed_ms=int(
        (time.monotonic()-start)*1000))


def _db_requirement():
    dsn=os.getenv("CM_TEST_PG_DSN")
    if not dsn:
        return "pg17_dsn_required"
    try:
        from psycopg.conninfo import conninfo_to_dict
        name=conninfo_to_dict(dsn).get("dbname","").lower()
        if "test" not in name and "_ci" not in name:
            return "database_not_disposable"
        import psycopg
        with psycopg.connect(dsn,connect_timeout=4) as conn:
            version=conn.execute("SHOW server_version_num").fetchone()[0]
            if int(version)//10000!=17:
                return "postgres_17_required"
    except Exception:
        return "postgres_database_unreachable_or_driver_missing"
    return None


def _suites(paths,mode):
    before=time.monotonic()
    sys.path.insert(0,str(TESTS))
    sys.path.insert(0,str(ROOT))
    suite=unittest.TestSuite()
    loader=unittest.TestLoader()
    for path in paths:
        suite.addTests(loader.discover(str(TESTS),pattern=path.name))
    expected=suite.countTestCases()
    if not expected:
        return _record(mode,"fail",reason="no_tests_discovered")
    # No secret-containing traceback/headers persisted in report.
    from io import StringIO
    buffer=StringIO()
    runner=unittest.TextTestRunner(stream=buffer,verbosity=1)
    try:
        outcome=runner.run(suite)
        skipped=len(outcome.skipped)
        state=("pass" if outcome.wasSuccessful() and
               (mode!="pg17" or skipped==0) else "fail")
        if state!="pass":
            print("GB28 "+mode+" failed: "+str(len(outcome.failures))+
                  " failures, "+str(len(outcome.errors))+" errors, "+
                  str(skipped)+" skipped.",file=sys.stderr)
            # Only print the test IDs and sanitized type categories, not the
            # provider strings, source content or untrusted exception bodies.
            for test,_ in outcome.failures+outcome.errors:
                print("  FAIL:",test.id(),file=sys.stderr)
        return _record(mode,state,count=outcome.testsRun,skipped=skipped,
                       elapsed_ms=int((time.monotonic()-before)*1000),
                       reason=None if state=="pass" else
                       "test_failure_or_unexpected_skip")
    except Exception:
        return _record(mode,"fail",count=expected,
                       reason="runner_internal_error")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--mode",choices=("static","pure","pg17","full"),
                        required=True)
    parser.add_argument("--report",default="qa-evidence/group-b.json")
    args=parser.parse_args()
    begin=time.monotonic()
    evidence={
        "schema_version":SCHEMA,
        "run_utc":datetime.now(timezone.utc).isoformat(),
        "commit_sha":os.getenv("GITHUB_SHA") or "unverified_local_checkout",
        "mode":args.mode,"platform":sys.platform,
        "python_major_minor":str(sys.version_info.major)+"."+
                             str(sys.version_info.minor),
        "synthetic_fixture_only":True,
        "provider_paid_post_executed":False,
        "real_model_quality_measured":False,
        "pg17_required":args.mode in ("pg17","full"),
        "results":[],
    }
    records=evidence["results"]
    records.append(_compile_files())
    if args.mode in ("static","pure","full") and records[-1]["status"]=="pass":
        records.append(_node())
    if args.mode in ("pure","full") and all(x["status"]=="pass" for x in records):
        records.append(_suites(
            [p for p in BTESTS if not p.name.endswith("_db.py")],
            "pure"
        ))
    if args.mode in ("pg17","full") and all(x["status"]=="pass" for x in records):
        requirement=_db_requirement()
        if requirement:
            records.append(_record("pg17","blocked",reason=requirement))
        else:
            records.append(_suites(
                [p for p in BTESTS if p.name.endswith("_db.py")],
                "pg17"
            ))
    evidence["elapsed_ms"]=int((time.monotonic()-begin)*1000)
    evidence["status"]="pass" if records and all(
        x["status"]=="pass" for x in records
    ) else ("blocked" if any(x["status"]=="blocked" for x in records)
             else "fail")
    target=Path(args.report)
    if not target.is_absolute():
        target=ROOT/target
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(evidence,indent=2,sort_keys=True,
                   allow_nan=False)+"\n",encoding="utf-8")
    print("Group B "+args.mode+" QA result:",evidence["status"],
          "| checked:",",".join(x["suite"] for x in records))
    print("Safe report:",target.relative_to(ROOT) if
          target.is_relative_to(ROOT) else "external_output")
    return 0 if evidence["status"]=="pass" else 1


if __name__=="__main__":
    raise SystemExit(main())
