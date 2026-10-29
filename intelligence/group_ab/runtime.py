"""Operator-controlled GA outbox to B pin/ACK handoff."""
from __future__ import annotations

import re
from uuid import uuid4
from intelligence.config import dsn, enabled, setting
from intelligence.qualified_data.contracts import QDPError

REQUIRED = (
    "schema_migrations", "qdp_group_current_preparations", "ga_runs",
    "ga_results", "ga_media_refs", "ga_outbox", "ga_outbox_attempts",
    "ga_outbox_ack_receipts", "group_b_inbox", "group_b_inbox_receipts",
    "group_b_runs",
)

def _flag(name):
    value = setting(name, "0").strip().lower()
    if value not in ("0", "1", "true", "false", "on", "off", "yes", "no"):
        raise QDPError("group_ab_invalid_config", "Boolean flag required", 503)
    return value in ("1", "true", "on", "yes")

def auto_dispatch_enabled():
    return _flag("CM_GROUP_A_B_AUTO_DISPATCH")

def _connect():
    import psycopg
    return psycopg.connect(dsn(), connect_timeout=5)

def _qdp_connect():
    from intelligence.qualified_data.repository import _connect as factory
    return factory()

def readiness():
    report = {
        "schema_version": "group-a-b-readiness/v1",
        "state": "unavailable", "reason_code": "disabled",
        "auto_dispatch_enabled": False,
        "b_analysis_completed": False, "provider_calls": 0,
    }
    try:
        if not enabled() or not dsn():
            report["reason_code"] = "database_disabled"
            return report
        report["auto_dispatch_enabled"] = auto_dispatch_enabled()
        if not _flag("CM_GROUP_A_B_ENABLED"):
            return report
        with _connect() as conn:
            missing = conn.execute(
                "SELECT rel FROM unnest(%s::text[]) AS t(rel) "
                "WHERE to_regclass('public.'||rel) IS NULL",
                (list(REQUIRED),),
            ).fetchall()
        if missing:
            report["reason_code"] = "peer_schema_missing"
            return report
        report.update(state="handoff_available", reason_code=None)
        return report
    except Exception:
        report["reason_code"] = "peer_probe_failed"
        return report

def dispatch_once(*, project_id=None):
    from .bridge import GroupABPipeline, require_scope
    require_scope(project_id)
    report = readiness()
    if report["state"] != "handoff_available":
        raise QDPError(report["reason_code"], "GA-B handoff unavailable", 503)
    pipeline = GroupABPipeline(_connect, _qdp_connect)
    state = pipeline.deliver_once(
        worker_id="ga-b-" + uuid4().hex[:12], project_id=project_id,
    )
    return {
        "schema_version": "group-a-b-dispatch/v1",
        "project_id": project_id, "outbox_delivery_state": state,
        "group_b_analysis_completed": False, "provider_calls": 0,
    }
