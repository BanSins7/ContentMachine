"""M01: source/contract/migration inventory; no paid calls or schema writes.

Local usage:
  python -m scripts.m01_contract_audit
  CM_M01_DISPOSABLE_DB=YES CM_M01_DB_DSN=... python -m scripts.m01_contract_audit --db-ledger

The default scan is stdlib only, read-only, and offline. It establishes
source presence/invariants; it does NOT certify E2E behavior or AI quality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "architecture" / "M01_BRANCH_CONTRACT_SNAPSHOT_20261029.json"
MIGRATIONS = ROOT / "intelligence" / "migrations"
ID_REQUIRED = {
    "project_registry.id": "TEXT",
    "videos.id": "BIGINT",
    "sre_live_runs.run_id": "UUID",
    "sre_live_verified_pair_results.result_id": "UUID",
    "sre_live_human_reviews.review_id": "UUID",
    "qdp_runs.id": "UUID",
    "ga_results.id": "BIGINT",
    "group_b_runs.id": "UUID",
    "group_c_runs.id": "UUID",
    "uae_unified_snapshots.id": "BIGINT",
    "pie_research_runs.id": "UUID",
}
HARD_GUARDS = {
    "intelligence/migrate.py": [
        'sorted(MIGRATIONS.glob("[0-9]*.sql"))',
        "schema_migrations", "hashlib.sha256", "pg_advisory_xact_lock",
    ],
    "intelligence/migrations/062_sre_live_opus_receipts.sql": [
        "CREATE TABLE sre_live_opus_receipts",
        "evidence_status TEXT NOT NULL",
        "input_tokens BIGINT NOT NULL",
        "CREATE TRIGGER sre_m28_receipt_insert_guard",
    ],
    "intelligence/migrations/073_qdp_modern_sre_qualification.sql": [
        "sre_live_run_id UUID", "sre_live_result_id UUID",
        "qdp_exactly_one_sre_source", "qdp_exactly_one_sre_result",
    ],
    "intelligence/relevance/sre_qdp_dispatch.py": [
        "claim_sre_result_event", "receive_sre_live_item",
        "get_sre_live_receipt", "acknowledge_sre_result_event",
    ],
    "intelligence/qualified_data/group_a_runtime.py": [
        "dispatch_invalidation_once", "dispatch_once",
    ],
    "intelligence/qualified_data/group_bc_runtime.py": [
        "CM_QDP_GROUP_B_ENABLED", "CM_QDP_GROUP_C_ENABLED",
    ],
    "intelligence/unified_analytics/lineage.py": [
        "LEFT JOIN sre_live_runs lr", "COALESCE(r.id, lr.run_id)",
    ],
    "intelligence/unified_analytics/sre_adapter.py": [
        "SRE_MODERN_RESULTS_SQL", "sre_m36_live_only_export",
        "qdp_sre_live_events", "sre_live_result_invalidation_intents",
    ],
    "intelligence/unified_analytics/qdp_adapter.py": [
        "MODERN_LINEAGE_SQL", "sre_live_result_id",
        "qdp_sre_live_tombstones",
    ],
    "intelligence/migrations/085_unified_analytics_transaction_guards.sql": [
        "IF NEW.status = 'qualified' THEN",
        "IF NEW.checked IS TRUE OR NEW.is_current IS TRUE",
    ],
    "intelligence/patterns_insights/intake/uae.py": [
        "def verified_live(self) -> bool:", "def ready_for_analysis(self) -> bool:",
    ],
    "intelligence/patterns_insights/intake/provenance.py": [
        "def verified_live(self) -> bool:", "def ready_for_analysis(self) -> bool:",
    ],
}

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load_manifest() -> dict:
    obj = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if obj.get("schema_version") != "contentmachine-m01-contract-snapshot/v1":
        raise ValueError("Unsupported M01 source snapshot")
    return obj

def scan_migrations() -> dict:
    paths = sorted(MIGRATIONS.glob("[0-9]*.sql"))
    filenames = [p.name for p in paths]
    hashes = {p.name: digest(p) for p in paths}
    prefixes = Counter(x.split("_", 1)[0] for x in filenames)
    return {
        "count": len(paths),
        "ordered_filenames": filenames,
        "sha256_by_filename": hashes,
        "shared_numeric_prefixes": {k: v for k, v in sorted(prefixes.items()) if v > 1},
        "duplicate_full_filenames": len(filenames) != len(set(filenames)),
        "note": "Equal numeric prefixes are NOT duplicate migration versions. "
                "The ledger keys by full filename and sha256.",
    }

def scan_contracts(manifest: dict) -> dict:
    edges = []
    problems = []
    for edge in manifest["edges"]:
        missing = [field for field in ("producer", "consumer", "migration", "test", "oracle")
                   if not edge.get(field)]
        files = {
            "producer": ROOT / edge["producer"],
            "consumer": ROOT / edge["consumer"],
            "migration": MIGRATIONS / edge["migration"],
            "oracle_test": ROOT / edge["test"],
        }
        unavailable = [label for label, path in files.items() if not path.is_file()]
        # The oracle description is an acceptance SPEC, not executed evidence.
        result = {"edge_id": edge["id"], "from": edge["from"], "to": edge["to"],
                  "status": edge["status"], "schema": edge["schema"],
                  "source_paths_present": not unavailable,
                  "missing_paths": unavailable,
                  "test_oracle_defined": bool(edge.get("oracle")),
                  "oracle_executed": False}
        if missing or unavailable:
            problems.append({"edge": edge["id"], "missing_fields": missing,
                             "missing_paths": unavailable})
        edges.append(result)
    return {"edges": edges, "problems": problems, "count": len(edges)}

def scan_guards() -> dict:
    missing = {}
    for filename, tokens in HARD_GUARDS.items():
        path = ROOT / filename
        if not path.is_file():
            missing[filename] = ["missing_file"]
            continue
        data = path.read_text(encoding="utf-8")
        absent = [token for token in tokens if token not in data]
        if absent:
            missing[filename] = absent
    file_062 = MIGRATIONS / "062_sre_live_opus_receipts.sql"
    if file_062.is_file():
        s = file_062.read_text(encoding="utf-8")
        if (s.count("CREATE TABLE sre_live_opus_receipts") != 1 or
                s.count("CREATE TRIGGER sre_m28_receipt_insert_guard") != 1 or
                "evidence_sha256 ~ '^[0-9a-f]{64} BIGINT" in s):
            missing["062_sre_live_opus_receipts.sql"] = ["repair_not_single_or_corrupt"]
    return {"source_guards_present": not missing, "missing": missing,
            "note": "Text checks only; SQL is not parsed or executed."}

def scan_repository() -> dict:
    m = load_manifest()
    migrations = scan_migrations()
    contracts = scan_contracts(m)
    guards = scan_guards()
    ids = {
        key: {"expected": value, "snapshot": m["id_contracts"].get(key),
              "recorded": m["id_contracts"].get(key) == value}
        for key, value in ID_REQUIRED.items()
    }
    missing_ids = [key for key, entry in ids.items() if not entry["recorded"]]
    issues = []
    if migrations["duplicate_full_filenames"]:
        issues.append("duplicate_migration_filename")
    if not migrations["count"]:
        issues.append("migrations_not_found")
    if contracts["problems"]:
        issues.append("missing_handoff_oracle_or_source")
    if guards["missing"]:
        issues.append("critical_source_guard_missing")
    if missing_ids:
        issues.append("canonical_identity_snapshot_incomplete")
    return {
        "schema_version": "contentmachine-m01-audit/v1",
        "audit_type": "OFFLINE_SOURCE_CONTRACTS_ONLY",
        "snapshot_base_commit": m["stage_base_commit"],
        "repo": m["repository"],
        "branch_count": len(m["branch_heads"]),
        "identity_contracts": ids,
        "handoffs": contracts,
        "migration_inventory": migrations,
        "source_guards": guards,
        "findings": issues,
        "static_inventory_complete": not issues,
        "postgres_sql_migrations_executed": False,
        "real_pipeline_ever_validated_by_this_audit": False,
        "uat_or_production_pass": False,
    }

def check_disposable_ledger(inventory: dict) -> dict:
    """Only read-only PostgreSQL, with TWO explicit opt-in environment gates."""
    if os.environ.get("CM_M01_DISPOSABLE_DB") != "YES":
        return {"state": "blocked", "reason": "CM_M01_DISPOSABLE_DB_must_be_YES"}
    dsn = os.environ.get("CM_M01_DB_DSN")
    if not dsn:
        return {"state": "blocked", "reason": "CM_M01_DB_DSN_not_configured"}
    try:
        import psycopg
        with psycopg.connect(
            dsn, connect_timeout=5, autocommit=True,
            options="-c default_transaction_read_only=on",
        ) as conn:
            version = int(conn.execute("SHOW server_version_num").fetchone()[0])
            ledger_exists = conn.execute(
                "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
            ).fetchone()[0]
            if not ledger_exists:
                return {"state":"not_initialized","postgres_version_num":version,
                        "note":"No schema_migrations ledger; no writes performed"}
            rows = conn.execute("SELECT version,sha256 FROM public.schema_migrations").fetchall()
        checksums = inventory["sha256_by_filename"]
        mismatches = sorted(name for name, checksum in rows
                            if name in checksums and checksums[name] != checksum)
        untracked = sorted(name for name, _ in rows if name not in checksums)
        deployed = {name for name, _ in rows}
        pending = sorted(set(checksums) - deployed)
        return {
            "state": "matches" if not mismatches and not untracked and version >= 170000
                     else "blocked",
            "postgres_version_num":version,
            "applied_count":len(rows),
            "pending_count":len(pending),
            "pending_filenames":pending,
            "modified_applied_versions":mismatches,
            "deployed_files_missing_in_checkout":untracked,
            "062_applied": "062_sre_live_opus_receipts.sql" in deployed,
            "062_checksum_equal_to_checkout":(
                "062_sre_live_opus_receipts.sql" in deployed and
                "062_sre_live_opus_receipts.sql" not in mismatches
            ),
            "read_only":True,
        }
    except Exception as exc:
        return {"state":"unavailable","reason":type(exc).__name__,
                "read_only":True}

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="M01 ContentMachine source contract audit")
    parser.add_argument("--db-ledger", action="store_true",
                        help="read-only PG17 migration ledger; explicit disposable DB opt-in")
    parser.add_argument("--output", type=Path, default=None,
                        help="optional JSON report path; never include DSNs")
    args = parser.parse_args(argv)
    report = scan_repository()
    report["database_ledger"] = (
        check_disposable_ledger(report["migration_inventory"])
        if args.db_ledger else {"state":"not_checked"})
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str)+"\n",
                               encoding="utf-8")
    print(json.dumps({
        "schema_version":report["schema_version"],
        "base_commit":report["snapshot_base_commit"],
        "migrations":report["migration_inventory"]["count"],
        "shared_prefixes":len(report["migration_inventory"]["shared_numeric_prefixes"]),
        "handoffs":report["handoffs"]["count"],
        "source_inventory_complete":report["static_inventory_complete"],
        "problems":report["findings"],
        "db":report["database_ledger"],
        "production_e2e_pass":False,
    },ensure_ascii=False,default=str))
    if report["findings"]:
        return 2
    if args.db_ledger and report["database_ledger"]["state"] not in ("matches",):
        return 3
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
