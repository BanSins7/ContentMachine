"""VC-MOD-15: reproducible offline and disposable PG17 Collector release gate.

python -m scripts.collector_release_gate --unit-only
CM_TEST_PG_DSN=postgresql://... python -m scripts.collector_release_gate --strict-ci

Never connects to DTK/OpenRouter/TikTok or real SRE. Uses subprocess tests
from the current checkout, not claimed statuses for old CI revisions.
Failure output is summarized without leaking DB DSN/credentials.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
SUITES={
    "ports_qv":(
        "test_video_collector_ports_v1.py",
        "test_video_collector_adapters_v1.py",
        "test_video_collector_qv_reader_v1.py",
        "test_video_collector_qv_publisher_v1.py",
    ),
    "planner_owner":(
        "test_video_collector_manifest_planner_v1.py",
        "test_video_collector_manifest_access_v1.py",
        "test_video_collector_operator_ui_v1.py",
        "test_video_collector_stage2.py",
    ),
    "worker_dtk":(
        "test_video_collector_dtk_stage7.py",
        "test_video_collector_worker_stage8.py",
        "test_video_collector_worker_resilience_v1.py",
        "test_video_collector_inspector_stage9.py",
        "test_video_collector_inspector_contract_v1.py",
    ),
    "d1_completion":(
        "test_video_collector_ingest_v1.py",
        "test_video_collector_completion_v1.py",
        "test_video_collector_status_v1.py",
        "test_video_collector_results_v1.py",
    ),
    "outbox_recovery":(
        "test_video_collector_sre_delivery_v1.py",
        "test_video_collector_recovery_v1.py",
        "test_video_collector_operator_cli_v1.py",
    ),
    "migration_contracts":(
        "test_video_collector_schema_audit.py",
        "test_video_collector_schema_v1.py",
        "test_video_collector_integration_baseline.py",
    ),
}
PG_TESTS=("test_video_collector_pg15_integration.py",)
JS_FILES=("site/collector.js","site/app.js")


def _run(cmd):
    try:
        proc=subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                            timeout=180, check=False)
    except (OSError,subprocess.TimeoutExpired):
        return False,"unavailable_or_timeout"
    # Do not return raw stderr: DB connection exceptions may contain DSN.
    return proc.returncode==0,"ok" if proc.returncode==0 else "test_failed"


def _pg17():
    value=os.environ.get("CM_TEST_PG_DSN")
    if not value:
        return {"configured":False,"server_version_num":None,"ready":False}
    try:
        import psycopg
        with psycopg.connect(value, connect_timeout=5) as conn:
            raw=conn.execute("SHOW server_version_num").fetchone()[0]
        num=int(raw)
        return {"configured":True,"server_version_num":num,
                "ready":170000<=num<180000}
    except Exception:
        return {"configured":True,"server_version_num":None,"ready":False}


def gate(*, strict_ci=False,unit_only=False):
    if strict_ci and unit_only:
        raise ValueError("Choose strict-ci OR unit-only")
    pg=_pg17() if strict_ci else {
        "configured":bool(os.environ.get("CM_TEST_PG_DSN")),
        "server_version_num":None,"ready":False,
    }
    report={"schema_version":"collector.release_gate/v1",
            "mode":"strict_ci" if strict_ci else "unit_only",
            "pg17":pg,"provider_requests":0,"sre_deliveries":0,
            "case_suites":{}}
    ready=True
    for group,files in SUITES.items():
        checks=[]
        for file in files:
            ok,reason=_run([sys.executable,"-m","unittest","discover",
                            "-s","tests","-p",file,"-v"])
            checks.append({"test_file":file,"passed":ok,"reason":reason})
            ready=ready and ok
        report["case_suites"][group]=checks
    pgcheck=[]
    if strict_ci:
        ready=ready and pg["ready"]
        for file in PG_TESTS:
            if pg["ready"]:
                ok,reason=_run([sys.executable,"-m","unittest","discover",
                                "-s","tests","-p",file,"-v"])
            else:
                ok,reason=False,"pg17_missing_or_wrong_version"
            pgcheck.append({"test_file":file,"passed":ok,"reason":reason})
            ready=ready and ok
    else:
        pgcheck=[{"test_file":file,"passed":False,
                  "reason":"skipped_no_pg_requirement"} for file in PG_TESTS]
    report["postgres_integration"]=pgcheck
    node=shutil.which("node")
    js=[]
    for path in JS_FILES:
        if node:
            ok,reason=_run([node,"--check",path])
        else:
            ok,reason=False,"node_missing"
        js.append({"file":path,"passed":ok,"reason":reason})
        if strict_ci:
            ready=ready and ok
    report["js_syntax"]=js
    # Executing offline cases does not prove live provider connectivity,
    # real SRE M35 inbox readiness or actual billed provider costs.
    report["live_dtk_verified"]=False
    report["authenticated_sre_inbox_verified"]=False
    report["complete_pipeline_e2e_verified"]=False
    report["passed"]=ready
    return report


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description="Offline/PG17 Collector QA; no provider calls")
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--unit-only",action="store_true")
    mode.add_argument("--strict-ci",action="store_true")
    args=parser.parse_args(argv)
    result=gate(strict_ci=args.strict_ci,unit_only=args.unit_only)
    print(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2))
    return 0 if result["passed"] else 2


if __name__=="__main__":
    raise SystemExit(main())
