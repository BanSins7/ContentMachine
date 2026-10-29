"""M03: fail-closed, side-effect-free operational readiness for SRE.

The checker is NOT a source authorization mechanism and NOT a replacement for
live D1 checks before each run or provider dispatch. No automatic DB connect,
provider POST, env read or implicit worker startup. Explicit callbacks can
supply a read-only DB preflight and authenticated project-specific policy.
"""
from __future__ import annotations

from .config import SRERuntimeSettings, optional_dependency
from .v1_contracts import reject

HEALTH_VERSION = "sre-health/v1"
STATUS = frozenset(("disabled", "available", "blocked_config",
                    "blocked_dependency", "blocked_policy"))
NEEDED_TABLES = frozenset((
    "project_registry", "sre_workspaces", "sre_taxonomy_versions",
    "sre_content_versions", "sre_readable_content_versions",
    "sre_offline_runs", "sre_offline_items",
))


def check_read_only_schema(conn):
    """Opt-in explicit PostgreSQL preflight: only read in a read-only txn.

    A schema presence check does not prove publication approval, tenant access,
    valid source policy, provider availability or applied migration hashes.
    """
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        found = conn.execute(
            """SELECT c.relname FROM pg_class c
               JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname=current_schema()
                 AND c.relname=ANY(%s::text[])
                 AND c.relkind IN ('r','p','v','m')""",
            (sorted(NEEDED_TABLES),),
        ).fetchall()
        names = {row[0] for row in found}
        return names == NEEDED_TABLES


def diagnose(settings, *, db_probe=None, policy_probe=None,
             dependency_probe=optional_dependency):
    """Return public, credential-free readiness of the S4 offline worker ONLY.

    A bare enabled flag can never produce "available". `db_probe` must be a
    caller-authorized read-only callable: **it is not called when disabled**.
    `policy_probe` must specifically validate an authenticated project and
    active D1 rights; no supplied proof -> blocked_policy. Neither executes
    a job, spends money nor enables live classification.
    """
    if not isinstance(settings, SRERuntimeSettings):
        reject("settings", "expected vetted SRERuntimeSettings")
    active = settings.flags["enabled"]
    status = "disabled"
    reason = "sre_disabled"
    if active:
        if not settings.database_enabled or not settings.database_configured:
            status,reason = "blocked_config","postgres_not_configured"
        else:
            try:
                driver_present = dependency_probe("psycopg")
            except Exception:
                driver_present = False
            if driver_present is not True:
                status,reason = "blocked_dependency","postgres_driver_unavailable"
            elif db_probe is None:
                status,reason = "blocked_config","database_preflight_required"
            else:
                try:
                    okay = db_probe()
                except Exception:
                    okay = False
                if okay is not True:
                    status,reason = "blocked_config","database_preflight_failed"
                elif policy_probe is None:
                    status,reason = "blocked_policy","project_policy_preflight_required"
                else:
                    try:
                        permitted = policy_probe()
                    except Exception:
                        permitted = False
                    if permitted is True:
                        status,reason = "available","offline_runtime_preflight_passed"
                    else:
                        status,reason = "blocked_policy","project_source_not_approved"
    return {
        "schema_version": HEALTH_VERSION,
        "status": status,
        "reason_code": reason,
        "runtime_mode": "offline_fixture",
        "can_dispatch_provider": False,
        "can_start_live_worker": False,
        "worker_enabled": bool(active and settings.flags["offline_worker"]
                               and status == "available"),
        "project_authorized": bool(status == "available"),
    }


__all__ = ["HEALTH_VERSION", "STATUS", "check_read_only_schema", "diagnose"]
