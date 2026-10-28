"""UA31 OS-neutral, one-shot local QA launcher. No GH Actions or daemon.

From repository root:
    python -m intelligence.unified_analytics.qa_runner
    python -m intelligence.unified_analytics.qa_runner --with-pg17 --confirm-disposable

Default mode deliberately strips CM_TEST_PG_DSN and runs non-PG UA31
adversarial + UA30 fixture tests; PG17 is opt-in, distinct from known
production DSNs. Results are local, measured only if this CLI ACTUALLY runs.
Never put DSNs or user data in returned summary.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

QA_WIRE="uae-local-qa-runner/v1"
ROOT=Path(__file__).resolve().parents[2]
PURE_FILES=(
    "test_unified_analytics_adversarial_http.py",
    "test_unified_analytics_adversarial_workers.py",
    "test_unified_analytics_adversarial_precision.py",
    "test_unified_analytics_adversarial_static.py",
    "test_unified_analytics_scale_benchmark.py",
    "test_unified_analytics_qa_runner.py",
    "test_unified_analytics_release_preflight.py",
    "test_unified_analytics_full_fixture_e2e.py",
    "test_unified_analytics_full_fixture_statistics.py",
    "test_unified_analytics_full_fixture_revisions.py",
    "test_unified_analytics_full_fixture_static.py",
)
PG_FILES=(
    "test_unified_analytics_adversarial_pg17.py",
    "test_unified_analytics_scale_benchmark_pg17.py",
    "test_unified_analytics_full_fixture_e2e_pg17.py",
    "test_unified_analytics_full_qdp_queue_e2e_pg17.py",
)
TIME_LIMIT_PURE=180
TIME_LIMIT_PG=600


def validate_pg(opt:bool,confirm:bool,env:dict[str,str])->None:
    if type(opt) is not bool or type(confirm) is not bool:
        raise ValueError("ua31_invalid_qa_options")
    if opt and not confirm:
        raise ValueError("ua31_pg17_disposable_confirmation_required")
    if opt:
        dsn=env.get("CM_TEST_PG_DSN")
        if (type(dsn) is not str or not dsn.strip()
            or any(dsn==env.get(x) for x in (
                "CM_DB_DSN","CM_UAE_WORKER_DSN","CM_UAE_OPS_DSN"
            ))):
            raise ValueError("ua31_pg17_disposable_dsn_required")


def parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--with-pg17",action="store_true")
    p.add_argument("--confirm-disposable",action="store_true")
    return p


def execute(*,pg:bool,confirm:bool,env:dict[str,str],
            runner=subprocess.run,clock=time.perf_counter)->dict:
    validate_pg(pg,confirm,env)
    isolated=dict(env)
    if not pg:
        isolated.pop("CM_TEST_PG_DSN",None)
    isolated["PYTHONPATH"]=str(ROOT)+os.pathsep+isolated.get("PYTHONPATH","")
    cases=PURE_FILES+(PG_FILES if pg else ())
    outcomes=[]
    for filename in cases:
        # Whitelist file basenames; no user-supplied test paths/args.
        start=clock()
        try:
            response=runner([
                sys.executable,"-m","unittest","discover","-s","tests",
                "-p",filename,"-v",
            ],cwd=str(ROOT),env=isolated,
                capture_output=True,text=True,
                timeout=TIME_LIMIT_PG if filename in PG_FILES else TIME_LIMIT_PURE,
                check=False)
            code=response.returncode
            status="passed" if code==0 else "failed"
        except subprocess.TimeoutExpired:
            code=None;status="timeout"
        except (OSError,ValueError):
            code=None;status="runner_unavailable"
        outcomes.append({
            "suite":filename,"status":status,"exit_code":code,
            "duration_seconds":round(max(0,clock()-start),3),
        })
        if status!="passed":
            # Avoid leaking exception tracebacks/DSN from captured test output.
            break
    return {
        "schema_version":QA_WIRE,
        "os":sys.platform,
        "python_version":sys.version.split()[0],
        "with_pg17":pg,
        "results":outcomes,
        "all_passed":len(outcomes)==len(cases)
                      and all(x["status"]=="passed" for x in outcomes),
        "qualified_positive_e2e_verified":False,
        "qualified_50k_load_verified":False,
    }


def main(argv:list[str]|None=None,*,environ:dict[str,str]|None=None)->int:
    args=parser().parse_args(argv)
    env=dict(os.environ) if environ is None else environ
    try:
        report=execute(pg=args.with_pg17,
                       confirm=args.confirm_disposable,env=env)
        print(json.dumps(report,sort_keys=True,separators=(",",":")))
        return 0 if report["all_passed"] else 1
    except ValueError:
        print("UAE31 local QA refused: explicit disposable PostgreSQL17 required",
              file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("UAE31 local QA interrupted",file=sys.stderr)
        return 130


if __name__=="__main__":
    raise SystemExit(main())
