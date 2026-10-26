"""FIX-03: read-only QDP migration topology/preflight, no peer side effects."""
from __future__ import annotations

import hashlib
from pathlib import Path
from collections.abc import Iterable

from .contracts import QDPError

ROOT = Path(__file__).resolve().parents[1] / "migrations"
BASE = "016_qualified_data_pipeline.sql"
INTEGRATION = "040_qdp_integration_foundation.sql"
INBOX_QUARANTINE = "041_qdp_sre_inbox_quarantine.sql"
GROUP_PREPARATIONS = "042_qdp_group_current_preparations.sql"
GROUP_METADATA_FIX = "043_qdp_metadata_readability_fix.sql"
DELIVERY_ATTEMPTS = "044_qdp_delivery_attempt_journal.sql"
# Known Git blob SHA of the untouched QDP v1 migration, checked 2026-10-26.
BASE_GIT_BLOB_SHA1 = "e7a98e6a4cb2acf920ecb8a7160d3fb320eaf0ea"


def migration_topology(names: Iterable[str]) -> dict:
    """Pure dependency ordering report for QDP-owned SQL.

    Doesn't pretend the independent peer branch SQL has been merged.
    """
    ordered = sorted(names)
    if len(ordered) != len(set(ordered)):
        raise QDPError("qdp_duplicate_migration", "Duplicate SQL filenames")
    if any(required not in ordered for required in (BASE,INTEGRATION,INBOX_QUARANTINE,GROUP_PREPARATIONS,GROUP_METADATA_FIX,DELIVERY_ATTEMPTS)):
        raise QDPError("qdp_migration_missing", "QDP integration SQL not installed", 503)
    if not (ordered.index(BASE) < ordered.index(INTEGRATION) < ordered.index(INBOX_QUARANTINE) < ordered.index(GROUP_PREPARATIONS) < ordered.index(GROUP_METADATA_FIX) < ordered.index(DELIVERY_ATTEMPTS)):
        raise QDPError("qdp_migration_order", "QDP migration must follow 016 baseline", 503)
    return {
        "required": [BASE, INTEGRATION, INBOX_QUARANTINE, GROUP_PREPARATIONS, GROUP_METADATA_FIX, DELIVERY_ATTEMPTS],
        "after_base": ordered.index(INTEGRATION) > ordered.index(BASE),
        "local_count": len(ordered),
        "peer_live_sre_migration": "017_sre_offline_run_engine.sql" in ordered,
        "peer_collector_migrations":
            [x for x in ("016_collector_planning.sql","017_collector_worker.sql") if x in ordered],
        "peer_analytics_migrations":
            [x for x in ("016_analytics_unified.sql","017_analytics_jobs_and_read_gates.sql",
                         "018_analytics_audit_hardening.sql") if x in ordered],
        "group_a_migration": "017_group_a_technical_extraction.sql" in ordered,
        "main_psi_migrations":
            [x for x in ordered if x.startswith(tuple(f"{n:03d}_" for n in range(30,40)))],
    }


def verify_unchanged_base_sql() -> None:
    """Catch accidental editing of checksum-locked preexisting QDP migration."""
    path = ROOT / BASE
    data = path.read_bytes()
    blob = b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    if hashlib.sha1(blob).hexdigest() != BASE_GIT_BLOB_SHA1:
        raise QDPError("qdp_base_migration_changed",
                       "Previously checksummed QDP 016 migration changed", 503)


def require_qdp_schema(conn) -> dict:
    """Explicit read-only boot/merge preflight. Does not run migrations."""
    rows = conn.execute(
        "SELECT version,sha256 FROM schema_migrations ORDER BY version").fetchall()
    installed = {row["version"]: row["sha256"] for row in rows}
    topology = migration_topology(installed)
    for name in (BASE, INTEGRATION, INBOX_QUARANTINE, GROUP_PREPARATIONS, GROUP_METADATA_FIX, DELIVERY_ATTEMPTS):
        content = (ROOT / name).read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        if installed[name] != checksum:
            raise QDPError("qdp_checksum_mismatch",
                           "Applied QDP migration checksum mismatch", 503)
    verify_unchanged_base_sql()
    missing = conn.execute(
        """SELECT missing.name
           FROM (VALUES
             ('qdp_sre_inbox'), ('qdp_run_manifests'),
             ('qdp_item_lineage'), ('qdp_outbox'),
             ('qdp_outbox_deliveries'), ('qdp_delivery_receipts'),
             ('qdp_invalidations'), ('qdp_unrevoked_preparations'),
             ('qdp_sre_inbox_quarantine'),
             ('qdp_current_base_enrichments'),
             ('qdp_group_current_preparations'),
             ('qdp_delivery_attempt_log')
           ) AS missing(name)
           WHERE to_regclass(missing.name) IS NULL"""
    ).fetchall()
    if missing:
        raise QDPError("qdp_schema_incomplete",
                       "QDP integration tables/views missing", 503)
    return topology
