"""Bootstrap capability boundary for a standalone Group A checkout.

GA-MOD-02: A1 remains usable for local, offline technical measurement.  No
QDP inbox/contract adapter is installed yet: do not silently claim that the
legacy QDP database tables exist or that background ingestion is ready.
GA-MOD-04 must replace this explicit deny-by-default handoff.
"""
from __future__ import annotations

import shutil
from .contracts import GroupAError
from .service import GroupAService

CAPABILITY_SCHEMA = "group-a-capabilities/v1"
QDP_CONTRACT = "qdp-handoff/v1"
BOOTSTRAP_REASON = "qdp_handoff_not_integrated"


def live_handoff_ready() -> bool:
    """Never activate legacy QDP execution just because tables are present."""
    return False


def capability_snapshot() -> dict:
    available = all(shutil.which(name) for name in ("ffmpeg", "ffprobe"))
    plan = GroupAService().capabilities()
    return {
        "ok": True,
        "schema_version": CAPABILITY_SCHEMA,
        "module_stage": "GA-MOD-02",
        "adapter_readiness": plan,
        "qdp_handoff": {
            "contract": QDP_CONTRACT,
            "state": "unavailable",
            "reason_code": BOOTSTRAP_REASON,
        },
        "runtime": {
            "state": "disabled",
            "reason_code": BOOTSTRAP_REASON,
        },
        "technical_a1": {
            "state": "tools_detected" if available else "tools_unavailable",
            "mode": "local_only",
            "result_scope": "duration_aspect_and_technical_scenes_only",
        },
    }


def require_live_handoff() -> None:
    """Gate network-facing API and automatic workers during bootstrap."""
    raise GroupAError(
        BOOTSTRAP_REASON,
        "Group A requires a versioned QDP handoff; standalone bootstrap is offline-only",
        503,
    )
