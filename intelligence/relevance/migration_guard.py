"""M05: SRE-owned, read-only PostgreSQL migration compatibility preflight.

Never edits a historical SQL file or writes schema_migrations. The existing
intelligence.migrate.apply_migrations is the ONLY schema writer. Opt-in CLI
is a dry-run; no provider/worker/other feature-branch dependency is imported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Mapping

from intelligence.migrate import MIGRATIONS

VERSION = "sre-migration-preflight/v1"
# Historical SRE Git blob identities, pinned when merging main into SRE at M01.
# Hash canonical UTF-8 text (normalized LF to match Git and migrate.py).
PINNED_SRE_GIT_BLOBS = {
    "016_sre_d1_intake_rights.sql": "d77350188169f4aeff9379d8075dc4d4ea6707de",
    "017_sre_offline_run_engine.sql": "21f80e816d3c6f87c4624461be07410136c21ad5",
}
# Required merged-main baseline: QV and Project Source / Knowledge A+B.
REQUIRED_MAIN = frozenset(
    ("016_query_validation.sql",) +
    tuple(f"{number:03d}_{suffix}.sql" for number, suffix in (
        (30, "project_source_intelligence_foundation"),
        (31, "project_reference_registry"),
        (32, "project_research_file_library"),
        (33, "project_document_extraction"),
        (34, "project_web_source_extractor"),
        (35, "project_video_acquisition"),
        (36, "project_unified_transcripts"),
        (37, "project_multimodal_vision"),
        (38, "project_timeline_fusion"),
        (39, "project_evidence_grounding"),
        (40, "project_context_assembly"),
        (41, "tb_approved_project_context"),
        (42, "ke_input_bundles"),
        (43, "ke_full_context_packs"),
        (44, "ke_multi_pack_plans"),
        (45, "ke_fact_request_batches"),
        (46, "ke_fact_execution_ledger"),
        (47, "ke_fact_normalization"),
        (48, "ke_independent_grounding"),
        (49, "ke_fact_dedup_conflicts"),
        (50, "ke_quality_gate"),
        (51, "ke_human_review"),
        (52, "ke_knowledge_snapshot"),
        (53, "ke_c_qualified_execution"),
    ))
)
# M11 is required for the standalone LIVE namespace, but unlike the
# historical 016/017 checksum pins it is a new forward-only migration.
REQUIRED_SRE_M11 = frozenset(("054_sre_live_core.sql",))
REQUIRED_SRE_M13 = frozenset(("055_sre_live_batch_coverage.sql",))
REQUIRED_SRE_M15 = frozenset(("056_sre_live_dispatch_fences.sql",))
REQUIRED_SRE_M18 = frozenset(("057_sre_local_embedding_cache.sql",))
REQUIRED_SRE_M23 = frozenset(("058_sre_live_opus_quotes.sql",))
REQUIRED_SRE_M24 = frozenset(("059_sre_live_opus_consents.sql",))
REQUIRED_SRE_M25 = frozenset(("060_sre_live_budget_ledger.sql",))
REQUIRED_SRE_M26 = frozenset(("061_sre_live_opus_dispatch_intents.sql",))
REQUIRED_SRE_M28 = frozenset(("062_sre_live_opus_receipts.sql",))
REQUIRED_SRE_M29 = frozenset(("063_sre_live_opus_reconciliation.sql",))
REQUIRED_SRE_M30 = frozenset(("064_sre_live_verified_coverage.sql",))
REQUIRED_SRE_M31 = frozenset(("065_sre_live_diagnostic_checkpoints.sql",))
REQUIRED_SRE_M32 = frozenset(("066_sre_live_human_reviews.sql",))
REQUIRED_SRE_M33 = frozenset(("067_sre_live_gold_benchmarks.sql",))
REQUIRED_SRE_M34 = frozenset(("068_sre_live_source_revocation.sql",))
_FILE_NAME = re.compile(r"^[0-9]{3}_[a-z0-9_]+\.sql$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class MigrationBlocked(RuntimeError):
    """Manifest invalid even before opening a database."""


def _sql_text(path: Path) -> str:
    # read_text's universal-newline normalization matches migrate.py.
    return path.read_text(encoding="utf-8")


def build_manifest(root: Path | None = None) -> dict[str, str]:
    """Validate an exact checked-out migration set and return deployed hashes.

    Historical SRE SQL must match the original Git blob, not merely itself.
    This also works on Windows CRLF checkouts.
    """
    directory = Path(root) if root is not None else MIGRATIONS
    files = sorted(directory.glob("[0-9]*.sql"))
    if not files:
        raise MigrationBlocked("migration_directory_empty")
    entries: dict[str, str] = {}
    for path in files:
        if not _FILE_NAME.fullmatch(path.name) or not path.is_file() or path.is_symlink():
            raise MigrationBlocked("invalid_migration_path")
        data = _sql_text(path).encode("utf-8")
        if not data.strip():
            raise MigrationBlocked("empty_migration")
        pinned = PINNED_SRE_GIT_BLOBS.get(path.name)
        if pinned:
            git_bytes = b"blob " + str(len(data)).encode("ascii") + b"\x00" + data
            if hashlib.sha1(git_bytes).hexdigest() != pinned:
                raise MigrationBlocked("historical_sre_sql_modified:" + path.name)
        entries[path.name] = hashlib.sha256(data).hexdigest()
    required = REQUIRED_MAIN | PINNED_SRE_GIT_BLOBS.keys() | REQUIRED_SRE_M11 | REQUIRED_SRE_M13 | REQUIRED_SRE_M15 | REQUIRED_SRE_M18 | REQUIRED_SRE_M23 | REQUIRED_SRE_M24 | REQUIRED_SRE_M25 | REQUIRED_SRE_M26 | REQUIRED_SRE_M28 | REQUIRED_SRE_M29 | REQUIRED_SRE_M30 | REQUIRED_SRE_M31 | REQUIRED_SRE_M32 | REQUIRED_SRE_M33 | REQUIRED_SRE_M34
    absent = sorted(required - entries.keys())
    if absent:
        raise MigrationBlocked("missing_baseline_migrations:" + ",".join(absent))
    return entries


def assess_applied(applied: Mapping[str, str] | None, *,
                   root: Path | None = None) -> dict:
    """Pure, fail-closed report. None means a fresh DB with no ledger table.

    Any unknown deployed version or mismatching hash blocks upgrade. Do not
    silently repair the ledger or rewrite SRE/main historical SQL.
    """
    manifest = build_manifest(root)
    seen = dict(applied or {})
    unknown = sorted(set(seen) - set(manifest))
    mismatch = sorted(name for name in seen.keys() & manifest.keys()
                      if not isinstance(seen[name], str)
                      or not _SHA256.fullmatch(seen[name])
                      or seen[name] != manifest[name])
    pending = sorted(set(manifest) - set(seen))
    installed = sorted(seen)
    # Upgrading main to SRE may add previously absent 016/017 after 053 was
    # installed; report this as an explicit historical-order insertion.
    last_installed = installed[-1] if installed else ""
    retroactive = [name for name in pending if name < last_installed]
    max_prefix = max(int(name[:3]) for name in manifest)
    reasons = (["unknown_applied_version"] if unknown else []) + (
        ["applied_checksum_mismatch"] if mismatch else [])
    return {
        "schema_version": VERSION,
        "status": "blocked" if reasons else "ready",
        "reasons": reasons,
        "ledger_present": applied is not None,
        "manifest_count": len(manifest),
        "applied_count": len(installed),
        "pending": pending,
        "retroactive_additions": retroactive,
        "unknown_applied": unknown,
        "checksum_mismatch": mismatch,
        "next_sre_migration_prefix": f"{max_prefix + 1:03d}",
        "manifest_fingerprint": hashlib.sha256(
            "".join(f"{name}\t{digest}\n" for name, digest in sorted(manifest.items()))
            .encode("utf-8")
        ).hexdigest(),
    }


def preflight_read_only(conn, *, root: Path | None = None) -> dict:
    """Inspect an opt-in PostgreSQL session; never create or change tables.

    Pass an idle autocommit connection, with explicitly selected search_path
    when using a disposable test schema. READ ONLY is enforced by PostgreSQL.
    """
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        exists = conn.execute("SELECT to_regclass('schema_migrations')").fetchone()[0]
        if exists is None:
            rows = None
        else:
            raw = conn.execute(
                "SELECT version, sha256 FROM schema_migrations ORDER BY version"
            ).fetchall()
            rows = dict(raw)
            if len(rows) != len(raw):
                raise MigrationBlocked("duplicate_installed_version")
        return assess_applied(rows, root=root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SRE migration preflight; no SQL writes")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", action="store_true", help="verify local source only")
    group.add_argument("--dry-run", action="store_true",
                       help="read PostgreSQL ledger (CM_DB_DSN), never execute SQL DDL")
    args = parser.parse_args(argv)
    try:
        if args.manifest:
            result = assess_applied(None)
        else:
            # Optional dependency and credentials are loaded ONLY in explicit dry-run.
            from intelligence.config import dsn
            value = dsn()
            if not value:
                print(json.dumps({"status": "blocked", "reasons": ["dsn_not_configured"]}))
                return 2
            import psycopg
            with psycopg.connect(value, connect_timeout=5, autocommit=True) as conn:
                result = preflight_read_only(conn)
    except (MigrationBlocked, OSError, UnicodeError, ValueError) as exc:
        # Never leak connection strings or arbitrary DB/server error text.
        reason = str(exc) if isinstance(exc, MigrationBlocked) else "manifest_unreadable"
        print(json.dumps({"status": "blocked", "reasons": [reason]}, sort_keys=True))
        return 2
    except Exception:
        print(json.dumps({"status": "blocked", "reasons": ["database_preflight_failed"]}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
