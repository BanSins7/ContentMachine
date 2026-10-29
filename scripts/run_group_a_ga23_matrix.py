#!/usr/bin/env python3
"""GA23 local, explicit, evidence-producing Windows/Linux QA matrix runner.

No test executes on import. No downloading, installing, modifying other
branches, contacting external API or paid provider. Real DB tests run only
with --pg17 AND explicit disposable-DB confirmation env. Secret DB DSN
is NEVER written to report and raw logs are not persisted.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
STAGES={
    "compileall": [sys.executable,"-m","compileall","-q",
                   "intelligence/group_a","tests","scripts/benchmark_group_a.py"],
    "frozen_contract": [sys.executable,"-m","unittest","discover",
                        "-s","tests","-p","test_group_a_contracts_v1.py","-v"],
    "pure_peer": [sys.executable,"-m","unittest","discover",
                  "-s","tests","-p","test_group_a_e2e.py","-v"],
    "api_studio": [sys.executable,"-m","unittest","discover",
                   "-s","tests","-p","test_group_a_http_studio.py","-v"],
    "benchmark": [sys.executable,"-m","unittest","discover",
                  "-s","tests","-p","test_group_a_benchmark.py","-v"],
    "all_group_a": [sys.executable,"-m","unittest","discover",
                    "-s","tests","-p","test_group_a_*.py","-v"],
    "pg17_migrations": [sys.executable,"-m","unittest","discover",
                        "-s","tests","-p","test_group_a_migration_matrix.py","-v"],
    "pg17_frozen_e2e": [sys.executable,"-m","unittest","discover",
                        "-s","tests","-p","test_group_a_e2e.py","-v"],
    "pg17_runtime": [sys.executable,"-m","unittest","discover",
                     "-s","tests","-p","test_group_a_runtime.py","-v"],
    "pg17_delivery": [sys.executable,"-m","unittest","discover",
                      "-s","tests","-p","test_group_a_delivery.py","-v"],
}


def command_identity():
    def git(*args):
        try:
            p=subprocess.run(["git",*args],cwd=ROOT,
                             stdin=subprocess.DEVNULL,capture_output=True,
                             timeout=8,check=False)
            return p.stdout.decode("utf-8",errors="replace").strip()[:100] if p.returncode==0 else None
        except (OSError,subprocess.TimeoutExpired):
            return None
    return {"commit_sha":git("rev-parse","HEAD"),
            "branch":git("rev-parse","--abbrev-ref","HEAD")}


def run(command,*,timeout,env):
    started=time.perf_counter()
    try:
        p=subprocess.run(command,cwd=ROOT,env=env,
                         stdin=subprocess.DEVNULL,capture_output=True,
                         timeout=timeout,check=False)
    except subprocess.TimeoutExpired:
        return {"status":"TIMEOUT","exit_code":None,
                "duration_sec":round(time.perf_counter()-started,3),
                "stdout_sha256":None,"stderr_sha256":None}
    except OSError:
        return {"status":"FAIL","exit_code":None,
                "duration_sec":round(time.perf_counter()-started,3),
                "stdout_sha256":None,"stderr_sha256":None}
    # A unittest run can be "OK (skipped=N)" with ALL tests skipped.
    # Runner marks conditional DB tests NOT_RUN unless explicit opt-in,
    # but no partial skip is misrepresented as full peer E2E PASS.
    return {
        "status":"PASS" if p.returncode==0 else "FAIL",
        "exit_code":p.returncode,
        "duration_sec":round(time.perf_counter()-started,3),
        "stdout_sha256":sha256(p.stdout).hexdigest(),
        "stderr_sha256":sha256(p.stderr).hexdigest(),
        "stdout_bytes":len(p.stdout),"stderr_bytes":len(p.stderr),
        "unittest_skipped_marker_present":b"skipped=" in p.stderr or b"skipped=" in p.stdout,
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True,
                        help="New output JSON report (will not overwrite)")
    parser.add_argument("--media",action="store_true",
                        help="Run synthetic fixture benchmark and unit media regressions")
    parser.add_argument("--pg17",action="store_true",
                        help="Run disposable PG17 migrations + A-to-B fake E2E")
    parser.add_argument("--all-group-a",action="store_true",
                        help="Run all GA unittests (may require FFmpeg/Tesseract/DB)")
    parser.add_argument("--timeout",type=int,default=300)
    args=parser.parse_args(argv)
    if not 30<=args.timeout<=3600:
        parser.error("--timeout must be 30..3600 sec per step")
    output=args.output.resolve()
    if output.exists():
        parser.error("Never overwrite earlier evidence; choose a NEW report filename")
    output.parent.mkdir(parents=True,exist_ok=True)
    expected_branch="feature/group-a-full-module"
    identity=command_identity()
    results={}
    base=("compileall","frozen_contract","pure_peer","api_studio")
    selected=list(base)
    if shutil.which("node"):
        selected.append("studio_js")
    if args.media:
        selected.append("benchmark")
    if args.all_group_a:
        selected.append("all_group_a")
    if args.pg17:
        if (not os.environ.get("CM_TEST_PG_DSN") or
            os.environ.get("CM_GA23_DISPOSABLE_ACK")!="yes"):
            parser.error(
                "--pg17 requires CM_TEST_PG_DSN and CM_GA23_DISPOSABLE_ACK=yes "
                "for a disposable PostgreSQL 17 database"
            )
        selected.extend((
            "pg17_migrations","pg17_frozen_e2e",
            "pg17_runtime","pg17_delivery"
        ))
    env=dict(os.environ)
    for stage in selected:
        command=(["node","--check","intelligence/group_a/site/group-a.js"]
                 if stage=="studio_js" else STAGES[stage])
        # Do not include commands with credentials/DSNs in the report.
        results[stage]=run(command,timeout=args.timeout,env=env)
        print(stage,results[stage]["status"],file=sys.stderr)
    for absent in set(STAGES)|{"studio_js"}-set(results):
        results.setdefault(absent,{"status":"NOT_RUN"})
    # Status is always NOT_RUN until actual independently integrated
    # peer branches are installed on an isolated combined checkout.
    statuses={
        "qdp_real_peer":"NOT_RUN",
        "sre_psi_real_peer":"NOT_RUN",
        "group_b_real_peer":"NOT_RUN",
        "analytics_real_peer":"NOT_RUN",
        "licensed_human_gold_model_accuracy":"NOT_RUN",
        "windows_pg17_e2e":"NOT_RUN",
    }
    report={
        "schema_version":"ga23-local-compatibility-evidence/v1",
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "host":{"os":platform.system(),"os_release":platform.release(),
                "arch":platform.machine(),
                "python":sys.version.split()[0]},
        "git":identity,"expected_branch":expected_branch,
        "branch_sha_verified":(
            identity["branch"]==expected_branch and
            bool(identity["commit_sha"])
        ),
        "checks":results,
        "unverified_peer_matrix":statuses,
        "merge_gate":"NOT_READY_REAL_PEER_PG17_RELEASE_QA_REQUIRED",
        "source":"locally_executed_only_if_actual_steps_above_have_status_PASS",
        "raw_test_logs_persisted":False,
        "database_dsn_persisted":False,
    }
    output.write_text(
        json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8"
    )
    print("GA23 matrix evidence:",output)
    return 1 if any(item["status"] in ("FAIL","TIMEOUT")
                    for item in results.values()) else 0


if __name__=="__main__":
    raise SystemExit(main())
