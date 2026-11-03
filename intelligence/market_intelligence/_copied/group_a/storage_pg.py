"""GA-MOD-03 PostgreSQL persistence boundary, safe without QDP/PSI packages.

No runtime connection is made on import. GA 054 may be installed on top of
main without QDP tables. Readiness diagnostics are read-only. Public writes
and reads of QDP-dependent analysis remain gated until GA-MOD-04/18.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .capabilities import require_live_handoff
from .contracts_v1 import ContractViolation, group_a_to_b_v1, qdp_handoff_v1
from .ports import PeerCapability, unavailable

STORAGE_SCHEMA = "group-a-storage/v1"
TABLES = (
    "ga_runs", "ga_media_refs", "ga_inbox", "ga_steps",
    "ga_results", "ga_outbox", "ga_cache_refs",
)
VIEWS = ("ga_local_readable_results", "ga_legacy_a1_inventory")


def migration_snapshot(conn) -> dict:
    """Read only PostgreSQL metadata; never infer a successful QDP handshake.

    Connection/search_path is deliberately injected by the caller. Does not
    connect to an ambient production database or modify SQL/schema.
    """
    if conn is None:
        raise ValueError("A PostgreSQL connection is required")
    names = tuple(TABLES) + tuple(VIEWS)
    missing = []
    for name in names:
        row = conn.execute("SELECT to_regclass(%s)", (name,)).fetchone()
        if not row or row[0] is None:
            missing.append(name)
    return {
        "schema_version": STORAGE_SCHEMA,
        "state": "installed" if not missing else "incomplete",
        "missing_relations": missing,
        "legacy_qdp_required": False,
        "live_handoff_ready": False,
    }


class PostgreSQLGroupAStore:
    """Opt-in PostgreSQL adapter skeleton; intentionally not auto-wired.

    Stage 03 installs the full durable schema. Stage 04 supplies authoritative
    QDP verification and inbox receipt, stage 18 finalizes writer wiring.
    Nothing here is proof of a QDP-ready project or local verified bytes.
    """

    def __init__(self, connect):
        if not callable(connect):
            raise TypeError("An explicit PostgreSQL connection factory is required")
        self._connect = connect

    def capability(self) -> PeerCapability:
        return unavailable("results", "qdp_handoff_not_integrated")

    def schema_snapshot(self) -> dict:
        with self._connect() as conn:
            return migration_snapshot(conn)

    def write_immutable(self, result: Mapping[str, Any]) -> Mapping[str, Any]:
        # Validate inputs (no coercion), but never persist on an unintegrated
        # QDP pointer. The real transaction writer is wired in GA-MOD-18.
        group_a_to_b_v1(result)
        require_live_handoff()
        raise AssertionError("Unreachable before GA-MOD-18")

    def read_result(self, project_id: str, qdp_run_id: str,
                    video_id: int) -> Mapping[str, Any] | None:
        # Never leak DB results prior to current QDP/rights check.
        qdp_handoff_v1({
            "schema_version": "qdp-handoff/v1",
            "event_id": "00000000-0000-0000-0000-000000000000",
            "project_id": project_id, "qdp_run_id": qdp_run_id,
            "video_id": video_id, "analysis_group": "A",
            "input_sha256": "0" * 64,
        })
        require_live_handoff()
        raise AssertionError("Unreachable before GA-MOD-04")


class PostgreSQLGroupATaskQueue:
    """Immutable queue schema is installed; no fake enqueue without QDP."""

    def __init__(self, connect):
        if not callable(connect):
            raise TypeError("An explicit PostgreSQL connection factory is required")
        self._connect = connect

    def capability(self) -> PeerCapability:
        return unavailable("queue", "qdp_handoff_not_integrated")

    def enqueue_once(self, task: Mapping[str, Any]) -> Mapping[str, Any]:
        if type(task) is not dict:
            raise ContractViolation("invalid_shape", "task")
        require_live_handoff()
        raise AssertionError("Unreachable before GA-MOD-04")
