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
from .config import GASettings, settings_from_environment

CAPABILITY_SCHEMA = "group-a-capabilities/v1"
QDP_CONTRACT = "qdp-handoff/v1"
BOOTSTRAP_REASON = "qdp_handoff_not_integrated"


def live_handoff_ready() -> bool:
    """Never activate legacy QDP execution just because tables are present."""
    return False


def capability_snapshot() -> dict:
    available = all(shutil.which(name) for name in ("ffmpeg", "ffprobe"))
    try:
        settings = settings_from_environment()
        config_error = None
    except ValueError:
        # Config is invalid: never activate a service or expose raw env values.
        settings = GASettings()
        config_error = "invalid_feature_flags"
    plan = GroupAService(settings=settings).capabilities()
    if config_error is not None:
        plan["reason_codes"].insert(0, config_error)
    return {
        "ok": True,
        "schema_version": CAPABILITY_SCHEMA,
        "module_stage": "GA-MOD-07",
        "frame_sampling": {
            "contract": "group-a-frame-manifest/v1",
            "state": "offline_only",
            "coverage": "sparse_observations",
            "ocr_coverage": "unknown",
            "storage": "atomic_local_files_not_sql",
            "automatic_worker": False,
        },
        "technical_extraction": {
            "method_version": "group-a-ffmpeg-technical-v2",
            "state": "offline_only",
            "strict_roots_required": True,
            "a003_default_state": "partial",
            "automated_worker": False,
        },
        "media_input": {
            "contract": "ga-media-manifest/v1",
            "state": "opt_in_unwired",
            "verification": "sha256_size_magic_fd_roots",
            "allow_network_acquisition": False,
        },
        "qdp_intake": {
            "schema_version": "qdp.preparation_ready/v1",
            "state": "opt_in_unwired",
            "ack_schema_version": "group-a-receipt/v1",
            "automatic_ingest": False,
        },
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
