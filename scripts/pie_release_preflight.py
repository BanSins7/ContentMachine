#!/usr/bin/env python3
"""PIE-MOD-35 local QA launcher and nonrelease audit (no CI or paid network).

Run from Windows/Linux checkout:
  python scripts/pie_release_preflight.py --output pie-release-audit.json

No unstated check may become PASS. Skipped PG tests are not evidence of PASS.
This program never merges, publishes, sends an LLM call or mutates the repo
except for the explicit JSON output path. PG tests require disposable DSNs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from intelligence.patterns_insights.release_readiness import (
    GateReceipt,assess_standalone_readiness,MACHINE_GATES,
)

def _check(cmd:list[str],timeout:int,*,env=None)->tuple[str,str,int,str,str]:
    try:
        completed=subprocess.run(cmd,cwd=ROOT,env=env,timeout=timeout,
                                capture_output=True,text=True,
                                encoding="utf-8",errors="replace",check=False)
        joined=completed.stdout+"\n"+completed.stderr
        state="passed" if completed.returncode==0 else "failed"
        note="local_process_exit_zero" if state=="passed" else "local_process_exit_nonzero"
        return state,hashlib.sha256(joined.encode("utf-8")).hexdigest(),completed.returncode,note,joined
    except subprocess.TimeoutExpired as exc:
        digest=hashlib.sha256(("timed_out:"+repr(cmd)).encode()).hexdigest()
        return "failed",digest,124,"local_timeout",""
    except (OSError,ValueError):
        return "blocked","",127,"binary_unavailable",""


def observed_pg_case(log:str,method:str)->bool:
    """Trust only the exact named PostgreSQL unittest case with terminal 'ok'.

    A process exit 0 with unittest skipped tests is NEVER enough.
    """
    import re
    if type(log) is not str or type(method) is not str or not re.fullmatch(r"test_[a-z0-9_]{1,120}",method):
        return False
    return bool(re.search(
        r"^"+re.escape(method)+r"\\s+\\([^\\n]+\\)\\s+\\.\\.\\.\\s+ok\\s*$",
        log,re.MULTILINE))


def _receipt(name,state,digest=None,note=None):
    if state not in ("passed","failed","blocked","not_run"):
        raise ValueError("Only bounded local gate states")
    return GateReceipt(name,state,
        "local_test_runner" if state in ("passed","failed") else "unavailable",
        digest if digest else None,
        note or ("not_measured" if state=="not_run" else "local_unavailable"))


def _test_db_name(dsn):
    """Preflight only: SQL-side name is checked again by the test itself."""
    from urllib.parse import urlparse
    value=urlparse(dsn)
    return (value.scheme in ("postgresql","postgres")
            and len(value.path)>1 and
            "test" in value.path.lstrip("/").lower()
            and value.path.lstrip("/").lower() not in
            ("postgres","template0","template1","contentmachine"))


def main(argv=None):
    parser=argparse.ArgumentParser(description="Local PIE release QA; never authorizes merge")
    parser.add_argument("--output",default="pie-release-audit.json",
                        help="Path to local nonrelease JSON report")
    parser.add_argument("--no-run",action="store_true",
                        help="Emit explicit NOT RUN baseline without executing tests")
    args=parser.parse_args(argv)
    target=Path(args.output).resolve()
    if target.is_relative_to(ROOT/"intelligence") or target.is_relative_to(ROOT/"tests"):
        parser.error("Use an output path outside source/test directories")

    # A clean HEAD SHA is stable only for committed repository code; this
    # report intentionally does not claim uncommitted changes are evaluated.
    head=_check(["git","rev-parse","HEAD"],12)
    if head[0]!="passed":
        raise SystemExit("Cannot establish source HEAD. Run from Git checkout.")
    commit=subprocess.run(["git","rev-parse","HEAD"],cwd=ROOT,capture_output=True,
                          text=True,check=True,timeout=12).stdout.strip()
    if len(commit)!=40 or any(c not in "0123456789abcdef" for c in commit):
        raise SystemExit("Expected exact Git SHA1 commit identity")
    branch=subprocess.run(["git","branch","--show-current"],cwd=ROOT,
                          capture_output=True,text=True,check=True,timeout=12).stdout.strip()
    if branch!="feature/patterns-insights-full-module":
        raise SystemExit("Refusing release readiness assessment on another branch.")
    modified=subprocess.run(["git","status","--porcelain"],cwd=ROOT,
                            capture_output=True,text=True,check=True,timeout=12).stdout.strip()
    # The output audit is permitted as an untracked file on repeated run.
    # The requested local JSON output is an allowed generated *untracked* file.
    allowed_output="?? "+str(target.relative_to(ROOT)).replace(chr(92),"/") if target.is_relative_to(ROOT) else None
    pending=[line for line in modified.splitlines()
             if line and line!=allowed_output]
    cleanliness="clean" if not pending else "dirty_not_verified"
    receipts=[]
    commands=[]
    results={}

    def run_and_record(gate,cmd,timeout):
        state,digest,code,note,output=_check(cmd,timeout)
        results[gate]=(state,digest,code,note,output)
        receipts.append(_receipt(gate,state,digest,note))
        commands.append({"gate":gate,"argv":cmd,"exit_code":code,
                         "state":state,"output_sha256":digest or None})
        return state

    if not args.no_run:
        compile_state=run_and_record(
            "compileall",[sys.executable,"-m","compileall","-q",
                          "intelligence/patterns_insights","tests/test_pie_release_quality.py",
                          "tests/test_pie_release_pg17.py"],180)
        py_state=run_and_record(
            "python_full_suite",[sys.executable,"-m","unittest","discover",
                                 "-s","tests","-p","test_pie_*.py","-q"],300)
        node=shutil.which("node")
        if node:
            run_and_record("js_studio_suite",[node,"tests/test_pie_explorer_ui.cjs"],45)
        else:
            receipts.append(_receipt("js_studio_suite","blocked",note="node_binary_missing"))
        gold=run_and_record(
            "synthetic_gold_math",[sys.executable,"-m","unittest","discover",
                                   "-s","tests","-p","test_pie_release_quality.py","-q"],120)
        receipts.append(_receipt(
            "synthetic_holdout_drift",gold,
            commands[-1]["output_sha256"],"shared_synthetic_quality_suite"))
        # Never pretend one successful synthetic 12k vector means 10k real
        # qualified UAE posts / source-rights end-to-end are benchmarked.
        receipts.append(_receipt("performance_10000","blocked",
                                  note="real_qualified_uae_benchmark_missing"))

        dsns={key:os.environ.get(key,"") for key in (
            "CM_TEST_PG_DSN","CM_TEST_PG_FRESH_DSN","CM_TEST_PG_UPGRADE_DSN")}
        for key,value in dsns.items():
            if value and not _test_db_name(value):
                raise SystemExit("Refusing non-test-looking database DSN: "+key)
        has_base=bool(dsns["CM_TEST_PG_DSN"])
        has_fresh=bool(dsns["CM_TEST_PG_FRESH_DSN"])
        has_upgrade=bool(dsns["CM_TEST_PG_UPGRADE_DSN"])
        fixture=bool(os.getenv("CM_TEST_PIE_PROJECT_ID") and os.getenv("CM_TEST_PIE_RUN_ID"))
        if any(dsns.values()):
            # Verbose unittest output distinguishes an actual 'ok' from a
            # skipped suite. Never promote a skipped PG fixture to PASS.
            pg_command=[sys.executable,"-m","unittest","discover",
                        "-s","tests","-p","test_pie_release_pg17.py","-v"]
            pg_state,pg_digest,pg_exit,pg_note,pg_log=_check(pg_command,240)
            commands.append({"gate":"pg17_run_observed","argv":pg_command,
                             "exit_code":pg_exit,"state":pg_state,
                             "output_sha256":pg_digest or None})
            method_checks={
                "pg17_fresh":("test_fresh_schema_all_migrations_apply_and_replay",has_fresh),
                "pg17_upgrade":("test_real_upgrade_applies_only_new_pending_migrations",has_upgrade),
                "pg17_isolation":("test_tenant_scope_prevents_claiming_unrelated_job",has_base and fixture),
                "pg17_immutability":("test_real_immutable_ledger_update_rejected",has_base and fixture),
                "pg17_two_worker_fencing":("test_true_two_connections_single_claim",has_base and fixture),
                "pg17_crash_recovery":("test_real_expired_lease_cannot_complete_after_recovery",has_base and fixture),
                "migration_203_deployed_checksum":("test_no_modified_migration_checksum",has_base),
            }
            for name,(method,eligible) in method_checks.items():
                confirmed=observed_pg_case(pg_log,method)
                state=("passed" if pg_state=="passed" and eligible and confirmed
                       else "failed" if pg_state=="failed" and eligible
                       else "blocked")
                receipts.append(_receipt(name,state,pg_digest if state in ("passed","failed") else None,
                               "postgresql17_case_observed_ok" if state=="passed"
                               else "postgresql17_case_failed" if state=="failed"
                               else "disposable_seeded_pg17_case_skipped"))
        else:
            for name in ("pg17_fresh","pg17_upgrade","pg17_isolation",
                         "pg17_immutability","pg17_two_worker_fencing",
                         "pg17_crash_recovery","migration_203_deployed_checksum"):
                receipts.append(_receipt(name,"blocked",note="disposable_pg17_dsn_missing"))

        # Only the current host OS can be assessed. No cross-OS PASS invented.
        current="windows_compatibility" if platform.system()=="Windows" else (
                "linux_compatibility" if platform.system()=="Linux" else None)
        for name in ("windows_compatibility","linux_compatibility"):
            if name==current and compile_state==py_state=="passed":
                # Local OS check is not full release; external dependencies
                # and rights gates remain independent.
                digest=hashlib.sha256(
                    (platform.platform()+commit+name).encode()).hexdigest()
                receipts.append(_receipt(name,"passed",digest,
                                         "local_os_python_suite_passed"))
            else:
                receipts.append(_receipt(name,"blocked",note="other_os_or_python_suite_failed"))
    report=assess_standalone_readiness(
        hashlib.sha256(("git-sha1:"+commit).encode()).hexdigest(),
        tuple(receipts))
    # The native SHA1 commit is tagged and SHA256-bound in the audit;
    # process output digests are evidence IDs, not cryptographic signatures.
    data={
        "schema_version":"pie-local-preflight-report/v1",
        "commit_sha1":commit,"branch":branch,
        "working_tree":cleanliness,
        "local_os":platform.system(),
        "commands":commands,"readiness":report.as_dict(),
        "runtime_capabilities":{
            "live_current_uae_rights":"not_verified",
            "independent_human_gold":"not_verified",
            "billing_and_export_license":"not_verified",
        },
        "release_approved":False,
        "merge_authorized":False,
        "model_payment_authorized":False,
    }
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(data,ensure_ascii=False,sort_keys=True,
                                 indent=2)+"\n",encoding="utf-8")
    print("PIE release decision:",report.status)
    print("Observed gates:",report.gates_passed,"/",report.gates_total)
    print("Report saved:",target)
    print("This tool NEVER grants release, merger or publication.")
    return 0 if (not report.failed_gates and not report.missing_gates and
                 cleanliness=="clean") else 2


if __name__=="__main__":
    raise SystemExit(main())
