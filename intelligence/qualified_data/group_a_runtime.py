"""Production-wired QDP -> real Group A inbox, opt-in and rights-gated.

No synthetic receipts or media; GA receiver independently validates QDP,
on-disk bytes and current source permission inside its own SQL transaction.
QDP writes the ACK only after that transaction commits.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from intelligence.config import dsn, enabled, setting
from intelligence.group_a.config import flag_value
from .contracts import QDPError

_REQUIRED = ("qdp_outbox","qdp_outbox_deliveries","qdp_delivery_receipts",
             "qdp_group_current_preparations","qdp_preparations",
             "ga_inbox","ga_runs","ga_steps","ga_qdp_revocation_receipts",
             "qdp_invalidations","media_assets")


def _flag(name):
    try:
        return flag_value(setting(name,"0"),name=name)
    except ValueError as exc:
        raise QDPError("group_a_invalid_configuration",name+" must be boolean",503) from exc


def auto_dispatch_enabled():
    return _flag("CM_QDP_GROUP_A_AUTO_DISPATCH")


def _connect_ga():
    from psycopg import connect
    return connect(dsn(),autocommit=True,connect_timeout=5)


def _configuration():
    if not enabled() or not dsn():
        raise QDPError("group_a_database_disabled","PostgreSQL integration disabled",503)
    for name in ("CM_QDP_GROUP_A_ENABLED","CM_GROUP_A_ENABLED",
                 "CM_GROUP_A_QDP_HANDOFF_ENABLED"):
        if not _flag(name):
            raise QDPError("group_a_disabled",name+" is not enabled",503)
    raw=setting("CM_QDP_GROUP_A_MEDIA_ROOTS","")
    roots=raw.split(os.pathsep) if raw else []
    if not roots or any(
        not value or not Path(value).is_absolute() or
        not Path(value).is_dir() for value in roots
    ):
        raise QDPError("group_a_media_roots_missing",
                       "Approved existing absolute Group A media roots required",503)
    return [str(Path(v).resolve()) for v in roots]


def readiness():
    """Read-only operational diagnostics, never imply technical analysis done."""
    try:
        roots=_configuration()
        with _connect_ga() as conn:
            row=conn.execute(
                "SELECT "+",".join("to_regclass(%s)" for _ in _REQUIRED),
                _REQUIRED,
            ).fetchone()
        if row is None or any(v is None for v in row):
            raise QDPError("group_a_schema_missing",
                           "QDP/Group A inbox migrations not installed",503)
        return {
            "schema_version":"qdp-group-a-readiness/v1",
            "state":"intake_available",
            "reason_code":None,
            "media_roots_configured":len(roots),
            "auto_dispatch_enabled":auto_dispatch_enabled(),
            "receipt_means_analysis_complete":False,
            "external_calls":0,
        }
    except QDPError as exc:
        return {
            "schema_version":"qdp-group-a-readiness/v1",
            "state":"unavailable","reason_code":exc.code,
            "media_roots_configured":0,
            "auto_dispatch_enabled":False,
            "receipt_means_analysis_complete":False,
            "external_calls":0,
        }
    except Exception:
        return {
            "schema_version":"qdp-group-a-readiness/v1",
            "state":"unavailable","reason_code":"group_a_database_probe_failed",
            "media_roots_configured":0,"auto_dispatch_enabled":False,
            "receipt_means_analysis_complete":False,"external_calls":0,
        }


def dispatch_group_a_once(*, project_id=None):
    """One QDP lease to a real GA initial task. Never infer GA result completion.

    Manual calls must specify exact project scope. A dedicated, explicitly
    enabled QDP worker may omit it to process the next available project.
    """
    if project_id is not None and (type(project_id) is not str or
        re.fullmatch(r"[0-9a-f]{32}",project_id) is None):
        raise QDPError("invalid_project","Exact project scope required",422)
    state=readiness()
    if state["state"]!="intake_available":
        raise QDPError(state["reason_code"],"Group A durable handoff unavailable",503)
    from intelligence.qualified_data.integrations.group_a import from_installed_group_a
    from intelligence.qualified_data.outbox_delivery import dispatch_once
    from intelligence.qualified_data.revocation_delivery import dispatch_invalidation_once
    from intelligence.qualified_data.repository import _connect as qdp_connect
    from intelligence.group_a.qdp_revocation import PostgresQDPRevocationReceiver
    # Compensating revocations have priority over any new ready event. A
    # committed receipt is required before QDP can ACK a revocation.
    result=dispatch_invalidation_once(
        "group_a",receiver=PostgresQDPRevocationReceiver(_connect_ga),
        connect=qdp_connect,project_id=project_id,
    )
    kind="preparation_invalidated" if result is not None else "preparation_ready"
    if result is None:
        receiver=from_installed_group_a(
            _connect_ga,media_roots=_configuration(),enabled=True,
        )
        result=dispatch_once(
            "group_a",receiver=receiver,connect=qdp_connect,project_id=project_id,
        )
    return {
        "schema_version":"qdp-group-a-dispatch/v1",
        "project_id":project_id,
        "result":result,
        "event_type":kind if result is not None else None,
        "group_a_analysis_completed":False,
        "external_calls":0,
    }
