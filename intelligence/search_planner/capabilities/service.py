"""SP-09: versioned, trust-separated, read-only source capability discovery.

This module does NOT contain a source registry. All snapshots originate from
an internal server-assembled CollectorCapabilitiesPort. The draft Collector
declarations can be projected into this versioned shape but NEVER upgraded to
readiness merely from declarations. SP-10 will bind query data to verified QV
database rows; SP-12/Collector separately authorize any external dispatch.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Callable, Mapping

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.taxonomy_builder.pipeline_repository import _pid
from intelligence.search_planner.ports import CollectorCapabilitiesPort

CAPABILITY_SCHEMA = "search-planner-source-capabilities/v1"
MATCH_SCHEMA = "search-planner-capability-match/v1"
_ALLOWED_MODES = frozenset(("keyword", "hashtag"))
_ID_RE = re.compile(r"[a-z][a-z0-9_]{2,62}\Z")
_OPERATION_RE = re.compile(r"[a-z][a-z0-9_]{2,63}\Z")
_LANG_RE = re.compile(r"[a-z]{2,3}(?:-[A-Z]{2})?\Z")
_MARKET_RE = re.compile(r"[A-Z]{2}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_MAX_SOURCE_COUNT = 64
_MAX_CAPABILITIES = 16


def _require_dict(value: object, expected: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != expected:
        raise PipelineError("Invalid SP capability " + label + " schema", 409)
    return value


def _datetime(value: object) -> datetime:
    if type(value) is not str or len(value) > 40:
        raise PipelineError("Invalid capability observation timestamp", 409)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PipelineError("Invalid capability observation timestamp", 409) from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise PipelineError("Capability timestamps must be timezone-aware", 409)
    return dt.astimezone(timezone.utc)


def _price(value: object, pricing: str) -> str | None:
    if pricing == "unknown":
        if value is not None:
            raise PipelineError("Unknown source price must remain unknown", 409)
        return None
    if type(value) is not str or not 1 <= len(value) <= 24:
        raise PipelineError("Source price must be a pinned decimal string", 409)
    try:
        result = Decimal(value)
    except (ValueError, InvalidOperation) as exc:
        raise PipelineError("Invalid pinned source price", 409) from exc
    if (not result.is_finite() or result < 0 or result.as_tuple().exponent < -8 or
            (pricing == "verified_free" and result != 0) or
            (pricing == "verified_paid" and result <= 0)):
        raise PipelineError("Unverified or incorrectly priced source operation", 409)
    return format(result, "f")


def _scope(values: object, *, market: bool, allow_empty: bool = False) -> tuple[str, ...]:
    if (type(values) is not list or (not values and not allow_empty) or
            len(values) > 100 or any(type(v) is not str for v in values) or
            len(set(values)) != len(values)):
        raise PipelineError("Source locale/market scope missing or duplicated", 409)
    regex = _MARKET_RE if market else _LANG_RE
    if any(type(v) is not str or (v != "*" and regex.fullmatch(v) is None)
           for v in values):
        raise PipelineError("Invalid source locale/market scope", 409)
    if "*" in values and len(values) != 1:
        raise PipelineError("Wildcard source scope must be exclusive", 409)
    return tuple(sorted(values))


def _check_source(raw: object) -> dict:
    s = _require_dict(raw, {
        "source_id", "platform", "integration_state", "enabled",
        "operationally_verified", "source_policy_verified",
        "credentials_verified", "pricing_status", "cost_usd_per_request",
        "quota_available_requests", "capabilities",
    }, "source")
    if (type(s["source_id"]) is not str or not _ID_RE.fullmatch(s["source_id"]) or
            type(s["platform"]) is not str or not _ID_RE.fullmatch(s["platform"]) or
            type(s["integration_state"]) is not str or
            not 1 <= len(s["integration_state"]) <= 128):
        raise PipelineError("Invalid source descriptor identity", 409)
    for field in ("enabled", "operationally_verified",
                  "source_policy_verified", "credentials_verified"):
        if type(s[field]) is not bool:
            raise PipelineError("Unverified source readiness type", 409)
    status = s["pricing_status"]
    if status not in ("unknown", "verified_free", "verified_paid"):
        raise PipelineError("Unknown source pricing classification", 409)
    _price(s["cost_usd_per_request"], status)
    quota = s["quota_available_requests"]
    if quota is not None and (type(quota) is not int or not 0 <= quota <= 1_000_000):
        raise PipelineError("Invalid available source quota", 409)
    if type(s["capabilities"]) is not list or len(s["capabilities"]) > _MAX_CAPABILITIES:
        raise PipelineError("Invalid source capability operations", 409)
    seen = set()
    for raw_cap in s["capabilities"]:
        cap = _require_dict(raw_cap, {
            "mode", "operation", "languages", "markets", "max_query_length",
            "max_pages", "pagination_verified",
        }, "operation")
        if (type(cap["mode"]) is not str or cap["mode"] not in _ALLOWED_MODES or
                type(cap["operation"]) is not str or
                _OPERATION_RE.fullmatch(cap["operation"]) is None):
            raise PipelineError("Unsupported query mode/operation descriptor", 409)
        if cap["mode"] in seen:
            raise PipelineError("Duplicate source operation for query mode", 409)
        seen.add(cap["mode"])
        for key, maximum in (("max_query_length", 500), ("max_pages", 20)):
            if type(cap[key]) is not int or not 1 <= cap[key] <= maximum:
                raise PipelineError("Invalid verified operation bound", 409)
        if type(cap["pagination_verified"]) is not bool:
            raise PipelineError("Missing explicit pagination readiness", 409)
        # A legacy declaration may be incomplete, but never treat missing
        # geographic/locale evidence as ready for an enabled source.
        allow_unknown = not (s["enabled"] and s["operationally_verified"])
        _scope(cap["languages"], market=False, allow_empty=allow_unknown)
        _scope(cap["markets"], market=True, allow_empty=allow_unknown)
    return s


def validate_capability_snapshot(snapshot: Mapping[str, Any], *, project_id: str,
                                 now: datetime | None = None) -> dict:
    """Check entire producer document without downgrading missing to supported.

    The SHA is only a content fingerprint, NOT a signature/authentication
    attestation. Only a trusted injected port may supply a snapshot to SP.
    """
    pid = _pid(project_id)
    if type(snapshot) is not dict:
        raise PipelineError("Trusted source capability snapshot missing", 409)
    s = _require_dict(snapshot, {
        "schema_version", "project_id", "registry_version",
        "captured_at", "expires_at", "sources",
    }, "snapshot")
    if s["schema_version"] != CAPABILITY_SCHEMA or s["project_id"] != pid:
        raise PipelineError("Capability snapshot schema/project mismatch", 409)
    if (type(s["registry_version"]) is not str or
            not 1 <= len(s["registry_version"]) <= 128 or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}",
                             s["registry_version"])):
        raise PipelineError("Unversioned source capability registry", 409)
    captured, expires = _datetime(s["captured_at"]), _datetime(s["expires_at"])
    if not captured < expires <= captured + timedelta(hours=24):
        raise PipelineError("Capability snapshot lifetime invalid", 409)
    clock = now if now is not None else datetime.now(timezone.utc)
    if (not isinstance(clock, datetime) or clock.tzinfo is None or
            clock.utcoffset() is None):
        raise PipelineError("Invalid verification clock", 422)
    if captured > clock.astimezone(timezone.utc) + timedelta(minutes=1):
        raise PipelineError("Future-dated capability observation", 409)
    if (type(s["sources"]) is not list or len(s["sources"]) > _MAX_SOURCE_COUNT):
        raise PipelineError("Capability catalog exceeds source limit", 409)
    seen = set()
    for source in s["sources"]:
        source = _check_source(source)
        if source["source_id"] in seen:
            raise PipelineError("Duplicate capability source", 409)
        seen.add(source["source_id"])
    return deepcopy(s)


def _match_one(source: dict, query: dict, *, snapshot_blocked: bool) -> dict:
    reasons = []
    cap = next((c for c in source["capabilities"]
                if c["mode"] == query["query_mode"]), None)
    if cap is None:
        reasons.append("unsupported_query_mode")
    else:
        if len(query["text"]) > cap["max_query_length"]:
            reasons.append("query_length_exceeded")
        if not cap["languages"] or ("*" not in cap["languages"] and query["language"] not in cap["languages"]):
            reasons.append("language_not_verified")
        if not cap["markets"]:
            reasons.append("market_not_verified")
        elif query["market"] is None:
            if cap["markets"] != ["*"]:
                reasons.append("market_not_specified")
        elif "*" not in cap["markets"] and query["market"] not in cap["markets"]:
            reasons.append("market_not_verified")
        if not cap["pagination_verified"] and cap["max_pages"] != 1:
            reasons.append("pagination_not_verified")
    if snapshot_blocked:
        reasons.append("capability_snapshot_expired")
    if not source["enabled"]:
        reasons.append("source_disabled")
    if not source["operationally_verified"]:
        reasons.append("source_not_operationally_verified")
    if not source["credentials_verified"]:
        reasons.append("credentials_not_verified")
    if not source["source_policy_verified"]:
        reasons.append("source_policy_not_verified")
    if source["pricing_status"] == "unknown":
        reasons.append("pricing_not_verified")
    if source["quota_available_requests"] is None:
        reasons.append("quota_not_verified")
    elif source["quota_available_requests"] < 1:
        reasons.append("quota_exhausted")
    return {
        "source_id": source["source_id"],
        "platform": source["platform"],
        "declared_operation": cap["operation"] if cap is not None else None,
        "capability_declared": cap is not None,
        "operationally_ready": (
            cap is not None and source["enabled"] and
            source["operationally_verified"] and source["credentials_verified"]
        ),
        "policy_and_budget_verified": (
            source["source_policy_verified"] and
            source["pricing_status"] != "unknown" and
            source["quota_available_requests"] is not None and
            source["quota_available_requests"] >= 1
        ),
        "planning_compatible": not reasons,
        "estimated_single_request_usd": (
            source["cost_usd_per_request"] if not reasons else None
        ),
        "block_reasons": sorted(set(reasons)),
        # Structural capability is never authorization, even if matched:
        "external_dispatch_authorized": False,
    }


def match_capabilities(snapshot: dict, query: dict, *, project_id: str,
                       now: datetime | None = None) -> dict:
    """Pure preview; query is not evidence of QV acceptance or owner consent."""
    pid = _pid(project_id)
    checked = validate_capability_snapshot(snapshot, project_id=pid, now=now)
    q = _require_dict(query, {"query_mode", "text", "language", "market"}, "query")
    if type(q["query_mode"]) is not str or q["query_mode"] not in _ALLOWED_MODES:
        raise PipelineError("Unsupported search query mode", 422)
    if (type(q["text"]) is not str or not 1 <= len(q["text"].strip()) <= 120
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in q["text"])):
        raise PipelineError("Untrusted source query text", 422)
    if type(q["language"]) is not str or _LANG_RE.fullmatch(q["language"]) is None:
        raise PipelineError("Invalid query language", 422)
    if (q["market"] is not None and
            (type(q["market"]) is not str or
             _MARKET_RE.fullmatch(q["market"]) is None)):
        raise PipelineError("Invalid market constraint", 422)
    clock = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.tzinfo is None:
        raise PipelineError("Invalid capability matching clock", 422)
    stale = clock.astimezone(timezone.utc) >= _datetime(checked["expires_at"])
    matches = [_match_one(source, q, snapshot_blocked=stale)
               for source in sorted(checked["sources"], key=lambda s: s["source_id"])]
    return {
        "schema_version": MATCH_SCHEMA,
        "project_id": pid,
        "catalog_sha256": sha256(checked),
        "registry_version": checked["registry_version"],
        "catalog_status": "expired" if stale else (
            "available" if checked["sources"] else "unavailable"
        ),
        "query_mode": q["query_mode"],
        "matches": matches,
        "compatible_source_ids": [
            m["source_id"] for m in matches if m["planning_compatible"]
        ],
        "external_dispatch_authorized": False,
        "provider_calls_in_this_match": 0,
    }


def unavailable_snapshot(project_id: str, *, now: datetime | None = None) -> dict:
    """No adapter is a truthful empty, short-lived snapshot, not a fake source."""
    clock = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise PipelineError("Invalid capability clock", 422)
    clock = clock.astimezone(timezone.utc)
    return {
        "schema_version": CAPABILITY_SCHEMA,
        "project_id": _pid(project_id),
        "registry_version": "collector-adapter-unavailable/v1",
        "captured_at": clock.isoformat(),
        "expires_at": (clock + timedelta(minutes=1)).isoformat(),
        "sources": [],
    }


def project_collector_declarations(project_id: str, *, registry_version: str,
                                   descriptors: list[dict], observed_at: datetime) -> dict:
    """Safe translator for legacy Collector registry.list_sources() output.

    The actual PR #20 lacks verified locale, credentials, quota and pricing
    evidence. Map all sources to DECLARED-ONLY with blocked operational
    readiness. No import of Collector, no copy of its registry or execution.
    """
    clock = observed_at
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise PipelineError("Invalid Collector observation clock", 422)
    if type(descriptors) is not list or len(descriptors) > _MAX_SOURCE_COUNT:
        raise PipelineError("Invalid external Collector source descriptors", 409)
    sources = []
    for d in descriptors:
        _require_dict(d, {
            "source_id", "platform", "integration_state", "capabilities",
            "operationally_verified", "source_policy_verified",
            "pricing_status",
        }, "legacy collector source")
        if type(d["capabilities"]) is not list:
            raise PipelineError("Invalid Collector operation declarations", 409)
        operations = []
        for cap in d["capabilities"]:
            _require_dict(cap, {"mode", "operation"}, "collector operation")
            # Collector currently declares max 120 keyword search_posts; not
            # verified provider readiness, locale/market or paginated search.
            if type(cap["mode"]) is not str or cap["mode"] not in _ALLOWED_MODES or type(cap["operation"]) is not str:
                raise PipelineError("Invalid Collector declared operation", 409)
            operations.append({
                "mode": cap["mode"],
                "operation": cap["operation"],
                "languages": [], "markets": [],
                "max_query_length": 120 if cap["operation"] == "search_posts" else 1,
                "max_pages": 1, "pagination_verified": False,
            })
        # Deliberately do not propagate declarations claiming 'verified'
        # into authoritative live eligibility. Adapter attestation is SP09+.
        sources.append({
            "source_id": d["source_id"], "platform": d["platform"],
            "integration_state": d["integration_state"], "enabled": False,
            "operationally_verified": False, "source_policy_verified": False,
            "credentials_verified": False, "pricing_status": "unknown",
            "cost_usd_per_request": None, "quota_available_requests": None,
            "capabilities": operations,
        })
    snapshot = {
        "schema_version": CAPABILITY_SCHEMA,
        "project_id": _pid(project_id),
        "registry_version": registry_version,
        "captured_at": clock.astimezone(timezone.utc).isoformat(),
        "expires_at": (clock.astimezone(timezone.utc) +
                       timedelta(minutes=5)).isoformat(),
        "sources": sources,
    }
    # Legacy declarations have no verified locale or market bounds. Strict
    # catalog validator accepts empty scopes here ONLY because every source is
    # disabled and operationally unverified. Match rejects this uncertainty.
    return validate_capability_snapshot(snapshot, project_id=project_id, now=clock)


class CapabilityMatcher:
    """Trusted injected port, no direct import or fallback Collector calls."""

    def __init__(self, port: CollectorCapabilitiesPort | None = None,
                 *, clock: Callable[[], datetime] | None = None) -> None:
        if port is not None and not callable(getattr(port, "snapshot", None)):
            raise PipelineError("Invalid Collector capabilities snapshot port", 422)
        if clock is not None and not callable(clock):
            raise PipelineError("Invalid capabilities clock port", 422)
        self._port = port
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def snapshot(self, project_id: str) -> dict:
        pid = _pid(project_id)
        now = self._clock()
        if self._port is None:
            return unavailable_snapshot(pid, now=now)
        # The application must assemble this port server-side from a trusted
        # read-only source policy/health/quota adapter, never browser JSON.
        raw = self._port.snapshot(pid)
        return validate_capability_snapshot(raw, project_id=pid, now=now)

    def match(self, project_id: str, query: dict) -> dict:
        pid = _pid(project_id)
        now = self._clock()
        snapshot = (unavailable_snapshot(pid, now=now) if self._port is None
                    else self._port.snapshot(pid))
        return match_capabilities(snapshot, query, project_id=pid, now=now)
