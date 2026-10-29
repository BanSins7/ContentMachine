"""Read-only, fail-closed ContentMachine pipeline readiness audit.

Run on a real checkout containing main + all integrated peer modules.
This does NOT simulate paid SRE results, create QDP runs, or claim PIE approval.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = {
    "search_planner": "intelligence/search_planner/service.py",
    "collector": "intelligence/collector/sre_delivery.py",
    "sre_live_outbox": "intelligence/relevance/live_result_outbox.py",
    "sre_qdp_dispatch": "intelligence/relevance/sre_qdp_dispatch.py",
    "qdp_live_intake": "intelligence/qualified_data/sre_live_snapshot.py",
    "qdp_group_a": "intelligence/qualified_data/group_a_runtime.py",
    "qdp_group_bc": "intelligence/qualified_data/group_bc_runtime.py",
    "group_a_group_b": "intelligence/group_ab/runtime.py",
    "group_b_group_c": "intelligence/group_bc/pipeline.py",
    "group_c_uae": "intelligence/group_c_uae_pipeline.py",
    "uae_qualification": "intelligence/unified_analytics/worker.py",
    "uae_pie": "intelligence/uae_pie_pipeline.py",
    "pie_orchestrator": "intelligence/patterns_insights/research/orchestrator.py",
}
MIGRATIONS = (
    "062_sre_live_opus_receipts.sql",
    "072_sre_qdp_live_event_inbox.sql",
    "073_qdp_modern_sre_qualification.sql",
    "074_qdp_modern_sre_current_rights.sql",
    "055_group_b_runtime.sql", "056_group_c_runtime.sql",
    "091_uae_group_c_receptions.sql",
    "207_pie_orchestrator_queue.sql",
)
DB_RELATIONS = (
    "sre_live_runs", "sre_live_verified_pair_results",
    "sre_live_human_reviews", "sre_live_result_outbox",
    "qdp_sre_live_events", "qdp_sre_live_tombstones",
    "qdp_runs", "qdp_group_current_preparations", "ga_runs",
    "group_b_inbox", "group_b_runs", "group_c_inbox",
    "group_c_outbox", "uae_group_c_receptions",
    "uae_unified_snapshots",
)
CONTRACT_TESTS = (
    "tests.test_sre_qdp_integrated_main",
    "tests.test_qdp_group_a_main_wiring",
    "tests.test_group_ab_pipeline",
    "tests.test_group_bc_pipeline",
    "tests.test_group_c_uae_pipeline",
    "tests.test_uae_pie_pipeline",
)


def _file(path):
    return ROOT / path


def _parse(path):
    target = _file(path)
    if not target.is_file():
        return "missing_file"
    try:
        ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
        return "syntax_ok"
    except (SyntaxError, UnicodeError, OSError):
        return "invalid_python_source"


def static_report():
    modules = {name: _parse(path) for name, path in MODULES.items()}
    migrations = {name: (_file("intelligence/migrations/" + name).is_file())
                  for name in MIGRATIONS}
    findings = []
    sre = _file("intelligence/unified_analytics/sre_adapter.py")
    qdp = _file("intelligence/unified_analytics/qdp_adapter.py")
    worker = _file("intelligence/unified_analytics/worker.py")
    pie = _file("intelligence/uae_pie_pipeline.py")
    pie_read = _file("intelligence/patterns_insights/intake/uae.py")
    pie_gate = _file("intelligence/patterns_insights/intake/provenance.py")
    if sre.is_file() and "JOIN sre_classification_runs c ON c.id=lr.run_id" in sre.read_text(encoding="utf-8"):
        findings.append("uae_sre_reader_requires_legacy_run_for_modern_sre")
    if qdp.is_file():
        source = qdp.read_text(encoding="utf-8")
        if "ON s.id=r.sre_run_id" in source and "sre_live_run_id" not in source:
            findings.append("uae_qdp_reader_requires_legacy_sre_foreign_key")
        if "tuple[int, ...]" in source and "sre_live_result_id" not in source:
            findings.append("uae_qdp_reader_rejects_modern_m30_uuid_results")
    if worker.is_file() and "worker_unapproved_qualification_denied" in worker.read_text(encoding="utf-8"):
        findings.append("uae_positive_qualification_operator_release_blocked")
    for label, path in (("pie_live_attestation_default_denied", pie_read),
                        ("pie_admission_authority_default_denied", pie_gate)):
        if path.is_file():
            source=path.read_text(encoding="utf-8")
            if "def ready_for_analysis(self) -> bool:" in source and "return False" in source:
                findings.append(label)
    if not pie.is_file():
        findings.append("uae_pie_transport_absent")
    missing = [name for name, status in modules.items() if status != "syntax_ok"]
    missing += [name for name, present in migrations.items() if not present]
    return {
        "module_python_syntax": modules,
        "required_migrations_present": migrations,
        "blocking_contracts": findings,
        "unavailable_or_invalid": missing,
        "positive_pipeline_to_pie_verified": False,
        "static_ready": not missing,
    }


def database_report():
    """Read-only; require a disposable PostgreSQL DSN, never real prod."""
    if os.environ.get("CM_TEST_DISPOSABLE_DB") != "YES":
        return {"state":"blocked", "reason_code":"disposable_database_opt_in_required"}
    dsn = os.environ.get("CM_TEST_PG_DSN")
    if not dsn:
        return {"state":"blocked", "reason_code":"CM_TEST_PG_DSN_missing"}
    try:
        import psycopg
        with psycopg.connect(
            dsn, connect_timeout=6, autocommit=True,
            options="-c default_transaction_read_only=on",
        ) as conn:
            values = conn.execute(
                "SELECT " + ", ".join(
                    "to_regclass(%s) IS NOT NULL" for _ in DB_RELATIONS
                ),
                DB_RELATIONS,
            ).fetchone()
            installed = conn.execute(
                "SELECT to_regclass('schema_migrations') IS NOT NULL"
            ).fetchone()[0]
        return {
            "state": "schema_present" if all(values) and installed else "blocked",
            "relations": dict(zip(DB_RELATIONS, map(bool, values))),
            "migration_ledger_present": bool(installed),
            "actual_video_processed": False,
            "pie_verified_live": False,
        }
    except Exception:
        return {"state":"blocked", "reason_code":"postgresql_read_only_probe_failed"}


def tests_report():
    """The selected contract tests do not replace a real SRE/PG/PIE E2E."""
    report={}
    for name in CONTRACT_TESTS:
        result=subprocess.run(
            [sys.executable, "-m", "unittest", "discover",
             "-s", "tests", "-p", name.split(".")[-1] + ".py", "-q"],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=120, check=False,
        )
        report[name]={"status":"pass" if result.returncode==0 else "fail",
                      "exit_code":result.returncode}
    return report


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--database",action="store_true",
                        help="Read-only, disposable PG probe; needs explicit env opt-in")
    parser.add_argument("--contract-tests",action="store_true",
                        help="Run selected local unit/contract suites; no paid execution")
    args=parser.parse_args(argv)
    report={"schema_version":"contentmachine-pipeline-to-pie-audit/v1",
            "analysis_mode":"read_only_no_paid_requests",
            "static":static_report(),
            "postgres":database_report() if args.database else {"state":"not_executed"},
            "contract_tests":tests_report() if args.contract_tests else {"state":"not_executed"},
            "live_source_video_verified":False,
            "uae_positive_qualification_verified":False,
            "pie_real_admission_verified":False,
            "full_e2e_pass":False}
    print(json.dumps(report,ensure_ascii=False,sort_keys=True))
    if (report["static"]["unavailable_or_invalid"]
        or report["static"]["blocking_contracts"]
        or (args.database and report["postgres"]["state"]!="schema_present")
        or (args.contract_tests and any(
           item["status"]!="pass"
           for item in report["contract_tests"].values()))):
        return 2
    # This preflight is NOT a real paid/live/PIE pipeline execution.
    # Never signal full E2E PASS until an authenticated production-grade
    # executor supplies a separate positive verification report.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
