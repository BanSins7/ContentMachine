"""Opt-in, local QDP -> B and QDP -> C durable fan-out.

Each consumer is independently leased, committed and ACKed by the original
QDP FIX-12 outbox dispatcher. One can fail without falsely ACKing the other.
No paid model work, C7 calculation, or Group A result is fabricated.
"""
from __future__ import annotations

import re

from intelligence.config import dsn, enabled, setting
from intelligence.qualified_data.contracts import QDPError

_REQUIRED = {
    "common":(
        "qdp_outbox","qdp_outbox_deliveries","qdp_group_current_preparations",
        "qdp_preparations","qdp_delivery_receipts",
    ),
    "B":(
        "group_b_inbox","group_b_runs","group_b_inbox_receipts",
        "group_b_block_states",
    ),
    "C":(
        "group_c_inbox","group_c_runs","group_c_jobs",
    ),
}


def _flag(name):
    value=setting(name,"0").strip().lower()
    if value not in ("0","1","false","true","off","on","no","yes"):
        raise QDPError("group_bc_invalid_configuration",name+" must be boolean",503)
    return value in ("1","true","on","yes")


def auto_dispatch_enabled():
    return _flag("CM_QDP_GROUP_BC_AUTO_DISPATCH")


def _connect_b():
    import psycopg
    return psycopg.connect(dsn(),autocommit=True,connect_timeout=5)


def _connect_c():
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(dsn(),autocommit=True,connect_timeout=5,
                           row_factory=dict_row)


def readiness():
    """No fabricated readiness: inspect opt-in and actual relation presence."""
    state={"schema_version":"qdp-group-bc-readiness/v1",
           "B":{"state":"unavailable","reason_code":"disabled"},
           "C":{"state":"unavailable","reason_code":"disabled"},
           "auto_dispatch_enabled":False,"provider_calls":0}
    try:
        if not enabled() or not dsn():
            for g in ("B","C"):
                state[g]["reason_code"]="database_disabled"
            return state
        state["auto_dispatch_enabled"]=auto_dispatch_enabled()
        with _connect_b() as conn:
            for group,flag in (("B","CM_QDP_GROUP_B_ENABLED"),
                               ("C","CM_QDP_GROUP_C_ENABLED")):
                if not _flag(flag):
                    continue
                tables=(*_REQUIRED["common"],*_REQUIRED[group])
                missing=conn.execute(
                    """SELECT tablename FROM unnest(%s::text[]) AS t(tablename)
                       WHERE to_regclass('public.' || t.tablename) IS NULL""",
                    (list(tables),),
                ).fetchall()
                if missing:
                    state[group]={"state":"unavailable","reason_code":"schema_missing"}
                else:
                    state[group]={"state":"intake_available","reason_code":None}
        return state
    except QDPError as exc:
        for group in ("B","C"):
            state[group]={"state":"unavailable","reason_code":exc.code}
        return state
    except Exception:
        for group in ("B","C"):
            state[group]={"state":"unavailable","reason_code":"database_probe_failed"}
        return state


def dispatch_groups_bc_once(*,project_id=None):
    """Independent B then C committed QDP outbox deliveries, at most 1 each.

    Host must first configure both group gates; when only one is enabled,
    the other is untouched. No other QDP delivery row is consumed.
    """
    if project_id is not None and (
        type(project_id) is not str or
        re.fullmatch(r"[0-9a-f]{32}",project_id) is None
    ):
        raise QDPError("invalid_project","Exact project scope required",422)
    report=readiness()
    if all(report[group]["state"]!="intake_available" for group in ("B","C")):
        raise QDPError("group_bc_unavailable","Both group intakes unavailable",503)
    from intelligence.qualified_data.outbox_delivery import dispatch_once
    from intelligence.qualified_data.repository import _connect as qdp_connect

    results={"B":None,"C":None}
    if report["B"]["state"]=="intake_available":
        try:
            from intelligence.qualified_data.integrations.group_b import from_installed_group_b
            b=from_installed_group_b(_connect_b,enabled=True)
            results["B"]=dispatch_once(
                "group_b",receiver=b,connect=qdp_connect,project_id=project_id,
            )
        except QDPError as exc:
            results["B"]={"state":"unavailable","reason_code":exc.code}
    if report["C"]["state"]=="intake_available":
        try:
            from intelligence.qualified_data.integrations.group_c import from_installed_group_c
            c=from_installed_group_c(_connect_c,enabled=True)
            results["C"]=dispatch_once(
                "group_c",receiver=c,connect=qdp_connect,project_id=project_id,
            )
        except QDPError as exc:
            results["C"]={"state":"unavailable","reason_code":exc.code}
    return {
        "schema_version":"qdp-group-bc-dispatch/v1",
        "project_id":project_id,"results":results,
        "group_b_analysis_complete":False,
        "group_c_score_complete":False,
        "provider_calls":0,
    }
