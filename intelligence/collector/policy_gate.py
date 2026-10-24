"""Provider rights, quota and strictly-zero-spend planning gate.

Phase II never dispatches an HTTP/DTK task or reserves a provider quota.
Policy is checked now, pinned into immutable plans, and MUST be checked
again immediately before any future provider submit.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .contracts import CollectorError


def zero_spend_cap(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= 24:
        raise CollectorError("invalid_budget", "Budget must be an explicit USD string")
    try:
        amount = Decimal(value)
    except (ValueError, InvalidOperation):
        raise CollectorError("invalid_budget", "Invalid USD budget") from None
    if not amount.is_finite() or amount != 0:
        raise CollectorError("paid_dispatch_not_supported",
                             "Only zero-spend, held planning is supported in Phase II")
    return "0.00"


def inspect_source_policy(conn, source_id: str, *, max_requests: int,
                          max_pages: int) -> dict:
    """Read actual source policy and quota from PostgreSQL; no external calls."""
    reasons = []
    row = conn.execute(
        """SELECT s.id,s.provider,s.source_type,s.status,s.can_search,
                  s.can_store_metadata,s.retention_days,
                  p.id,p.version,p.capabilities,p.permissions,
                  p.retention_policy,p.reviewed_at
           FROM sources s
           LEFT JOIN source_policy_versions p
              ON p.source_id=s.id AND p.is_active
           WHERE s.id=%s""",
        (source_id,),
    ).fetchone()
    if row is None:
        return {"allowed_for_held_plan": False, "blocks": ["source_not_registered"],
                "policy_version_id": None, "quota_available": None,
                "quota_reserved": 0, "paid_spend_usd": "0.00"}
    (ident,provider,source_type,status,can_search,can_store_metadata,
     retention_days,policy_id,version,caps,perms,retention,reviewed_at)=row
    if source_id != "dtk_tiktok":
        reasons.append("source_adapter_not_implemented")
    if status != "enabled":
        reasons.append("source_not_enabled")
    if policy_id is None or reviewed_at is None:
        reasons.append("source_policy_not_reviewed")
    if not can_search or not can_store_metadata:
        reasons.append("source_permissions_disabled")
    if not isinstance(caps,dict) or caps.get("keyword_search") is not True:
        reasons.append("keyword_search_not_permitted")
    if not isinstance(perms,dict) or (perms.get("can_search") is not True or
                                      perms.get("can_store_metadata") is not True):
        reasons.append("source_policy_rights_missing")
    if not isinstance(retention,dict) or (
        type(retention.get("retention_days")) is not int
        or retention["retention_days"] < 1
        or type(retention_days) is not int
        or retention_days < retention["retention_days"]
    ):
        reasons.append("retention_not_verified")
    # DTK pagination is not yet verified, particularly for guest TikTok search.
    if max_pages > 1:
        reasons.append("pagination_not_verified")
    # A configured, active quota window is required even for held plans.
    # Reading it does NOT reserve units; a future worker must re-check.
    quota = conn.execute(
        """SELECT limit_units,reserved_units,consumed_units
           FROM source_quota_ledger
           WHERE source_id=%s AND bucket_name='collector.requests'
             AND window_start_at<=now() AND window_end_at>now()
           ORDER BY window_start_at DESC LIMIT 1""", (source_id,),
    ).fetchone()
    available = None
    if quota is None or quota[0] is None:
        reasons.append("quota_not_configured")
    else:
        available = max(0, quota[0]-quota[1]-quota[2])
        if max_requests > available:
            reasons.append("quota_insufficient")
    return {
        "allowed_for_held_plan": len(reasons)==0,
        "blocks": reasons,
        "source_id": ident,
        "provider": provider,
        "source_type": source_type,
        "policy_version_id": policy_id,
        "policy_version": version,
        "quota_available": available,
        "quota_reserved": 0,
        "paid_spend_usd": "0.00",
        "external_dispatch_authorized": False,
        "rights_recheck_required_at_dispatch": True,
    }


def require_policy(policy: dict) -> None:
    if not policy["allowed_for_held_plan"]:
        raise CollectorError("source_policy_blocked",
                             "Source/policy/quota gate: " + ", ".join(policy["blocks"]), 409)
