"""Read-only Video Collector migration history preflight (VC-MOD-02).

Does not mutate schema_migrations, install extensions, reconcile legacy branch
migrations, fetch secrets, contact TikTok, or launch a Worker.

Usage:
    CM_DB_DSN=postgresql://... python -m scripts.collector_schema_audit
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from intelligence import migrate

_COLLECTOR_V1 = (
    "054_collector_planning.sql",
    "055_collector_worker.sql",
    "056_collector_qv_manifest.sql",
    "057_collector_discovery_outbox.sql",
)
_LEGACY_VC = (
    "016_collector_planning.sql",
    "017_collector_worker.sql",
)
_MAIN_ANCHOR = (
    "016_query_validation.sql",
    "053_ke_c_qualified_execution.sql",
)


def audit_history(rows, migration_dir: Path = migrate.MIGRATIONS) -> dict:
    """Pure history analysis; rows must be (version, sha256), read from DB."""
    history = {}
    for version, digest in rows:
        if type(version) is not str or type(digest) is not str:
            raise ValueError("Invalid schema_migrations history")
        if version in history and history[version] != digest:
            raise ValueError("Duplicate migration history entry")
        history[version] = digest
    present = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
               for p in migration_dir.glob("[0-9]*.sql")}
    legacy = sorted(set(history).intersection(_LEGACY_VC))
    changed = sorted(k for k, v in history.items()
                     if k in present and present[k] != v)
    missing_files = sorted(set(history) - set(present))
    missing_expected = sorted(set(_COLLECTOR_V1) - set(present))
    pending_collector = sorted(k for k in _COLLECTOR_V1
                               if k in present and k not in history)
    blockers = []
    if legacy:
        blockers.append("legacy_collector_migration_names")
    if changed:
        blockers.append("previously_applied_checksum_mismatch")
    if missing_files:
        blockers.append("installed_migration_file_missing")
    if missing_expected:
        blockers.append("expected_collector_sql_missing")
    # Missing main anchors is normal for a new disposable empty DB but
    # suspicious for a populated project with later installed versions.
    absent_anchors = [k for k in _MAIN_ANCHOR if k not in history]
    if history and absent_anchors and any(k in history for k in _COLLECTOR_V1):
        blockers.append("collector_installed_without_main_anchors")
    return {
        "schema": "collector.migration_audit/v1",
        "safe_to_run_standard_migrator": not blockers,
        "blockers": blockers,
        "legacy_collector_versions": legacy,
        "changed_applied_files": changed,
        "installed_versions_missing_from_repository": missing_files,
        "expected_collector_sql_missing": missing_expected,
        "collector_versions_pending": pending_collector,
        "installed_version_count": len(history),
        "action": ("STOP; back up the database and reconcile legacy SQL names/"
                   "checksums manually in a disposable restored clone"
                   if blockers else "Proceed with ordinary immutable migration runner"),
    }


def main() -> None:
    from intelligence.config import dsn
    d = dsn()
    if not d:
        raise SystemExit("CM_DB_DSN is not set")
    import psycopg
    with psycopg.connect(d, connect_timeout=5) as conn:
        # A schema without prior migration ledger is not an installed database.
        exists = conn.execute("SELECT to_regclass('schema_migrations')").fetchone()[0]
        rows = (conn.execute("SELECT version,sha256 FROM schema_migrations").fetchall()
                if exists else [])
    report = audit_history(rows)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if not report["safe_to_run_standard_migrator"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
