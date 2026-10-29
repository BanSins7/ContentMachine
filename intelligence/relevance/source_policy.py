"""S3 read-time source-policy decision. Pure, fail-closed; no network or writes.

A permission expressed by a D1 source is not implied by can_store_metadata.
Current explicit source + reviewed, active policy permissions are mandatory.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .v1_contracts import reject, utc_time


def verified_rights(source, policy, *, external_llm=False):
    """Return an explicit, effective rights dict from server-fetched D1 rows.

    source and policy are DB-authoritative mappings, NOT request JSON.
    """
    if not isinstance(source, dict) or not isinstance(policy, dict):
        reject("source_policy", "missing D1 source/policy", "rights_denied")
    permissions = policy.get("permissions")
    if not isinstance(permissions, dict):
        reject("source_policy", "invalid permissions", "rights_denied")
    # M08: malformed or nonnumeric retention can bypass the SQL view's
    # numeric-only retention predicate after an external policy edit. Never
    # treat bad policy JSON as an unlimited grant at send or result-read time.
    retention = policy.get("retention_policy")
    if type(retention) is not dict:
        reject("retention_policy", "missing reviewed retention JSON", "rights_denied")
    for name, value in (
        ("source.retention_days", source.get("retention_days")),
        ("policy.retention_days", retention.get("retention_days")),
    ):
        if value is not None and (type(value) is not int or not 1 <= value <= 36500):
            reject(name, "invalid live retention duration", "rights_denied")
    if (source.get("status") != "enabled" or source.get("can_store_metadata") is not True
            or policy.get("is_active") is not True
            or policy.get("reviewed_at") is None
            or policy.get("source_id") != source.get("id")
            or permissions.get("can_store_metadata") is not True
            or permissions.get("can_semantic_classify") is not True):
        reject("source_policy", "D1 policy does not permit SRE semantic processing",
               "rights_denied")
    media = source.get("can_analyze_media") is True and permissions.get("can_analyze_media") is True
    out = {
        "can_store_metadata": True,
        "can_semantic_classify": True,
        "can_external_llm": permissions.get("can_external_llm") is True,
        "can_analyze_media": media,
    }
    if external_llm and not out["can_external_llm"]:
        reject("source_policy.permissions.can_external_llm",
               "external LLM transmission not allowed", "rights_denied")
    return out


def expiry_for_intake(source, policy, observation, *, now):
    """Bound content expiry by source, policy, and D1 observation timestamps."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        reject("now", "aware datetime required")
    if not isinstance(source, dict) or not isinstance(policy, dict):
        reject("policy", "source/policy unavailable", "rights_denied")
    retention = policy.get("retention_policy")
    if not isinstance(retention, dict):
        reject("retention_policy", "invalid policy", "rights_denied")
    limits = []
    for name, value in (
        ("source.retention_days", source.get("retention_days")),
        ("policy.retention_days", retention.get("retention_days")),
    ):
        if value is not None:
            if type(value) is not int or not 1 <= value <= 36500:
                reject(name, "retention must be a positive integer", "rights_denied")
            limits.append(now + timedelta(days=value))
    if observation is not None:
        if not isinstance(observation, dict):
            reject("observation", "invalid metadata observation")
        if observation.get("deleted_at") is not None or observation.get("is_current") is not True:
            reject("observation", "metadata observation revoked", "rights_denied")
        expiry = observation.get("expires_at")
        if expiry is not None:
            if not isinstance(expiry, datetime) or expiry.tzinfo is None:
                reject("observation.expires_at", "expected DB timezone-aware timestamp")
            limits.append(expiry)
    final = min(limits) if limits else None
    if final is not None and final <= now:
        reject("expires_at", "D1 metadata or retention has expired", "rights_expired")
    return final


def utc_iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None
