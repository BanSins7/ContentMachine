"""SP-15E: optional, read-only Collector policy/quota bridge.

This is NOT a Collector source registry or an authorization to execute a
provider query. The external Collector branch has declarations and PostgreSQL
policy/quota tables, but NOT evidence for locale, credentials, costs, running
adapters or actual accepted search results.

A server-assembled declaration supplier may be injected. This module never
imports intelligence.collector, calls a provider, writes SQL or enables
operationally_ready from declared or unscoped policy flags.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Callable

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid

from .service import project_collector_declarations, unavailable_snapshot

SCHEMA = "search-planner-collector-policy-preflight/v1"
REGISTRY_SCHEMA = "collector-source-declarations/v1"
_SOURCE = re.compile(r"[a-z][a-z0-9_]{2,62}\Z")
REGISTRY_BOUND = 64


def _now_utc(now=None):
    clock = datetime.now(timezone.utc) if now is None else now
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise PipelineError("Invalid Collector policy observation clock", 422)
    return clock.astimezone(timezone.utc)


def _safe_declarations(raw, project_id, *, observed_at):
    pid = _pid(project_id)
    if type(raw) is not dict or set(raw) != {
        "schema_version", "project_id", "registry_version", "sources"
    } or raw["schema_version"] != REGISTRY_SCHEMA or raw["project_id"] != pid:
        raise PipelineError("Untrusted Collector declarations envelope", 409)
    sources = raw["sources"]
    if type(sources) is not list or len(sources) > REGISTRY_BOUND:
        raise PipelineError("Collector declarations exceed safe limit", 409)
    seen = set()
    for source in sources:
        if type(source) is not dict or set(source) != {
            "source_id", "platform", "integration_state", "capabilities",
            "operationally_verified", "source_policy_verified", "pricing_status"
        }:
            raise PipelineError("Untrusted Collector source declaration", 409)
        source_id = source["source_id"]
        if type(source_id) is not str or not _SOURCE.fullmatch(source_id) or source_id in seen:
            raise PipelineError("Invalid or repeated Collector source ID", 409)
        seen.add(source_id)
    # The existing SP09 translator performs full capability/contract checks
    # and forces every source to declared-only, regardless of the source flags.
    return project_collector_declarations(
        pid, registry_version=raw["registry_version"],
        descriptors=sources, observed_at=observed_at,
    )


def _preflight_sql(conn, source_id, clock):
    """Global source-policy evidence only, NOT project-level source approval.

    Requires Collector's declared sources/source_policy_versions/
    source_quota_ledger migrations; no queries for provider secrets.
    """
    reasons = []
    row = conn.execute(
        """SELECT s.status,s.can_search,s.can_store_metadata,s.retention_days,
                  p.version,p.capabilities,p.permissions,p.retention_policy,
                  p.reviewed_at
           FROM sources s LEFT JOIN source_policy_versions p
             ON p.source_id=s.id AND p.is_active
          WHERE s.id=%s""", (source_id,),
    ).fetchone()
    if row is None:
        return None, None, ["source_not_registered"]
    if len(row) != 9:
        raise PipelineError("Invalid Collector source policy DB row", 409)
    (status, can_search, can_store, retention_days, policy_version,
     capabilities, permissions, retention_policy, reviewed_at) = row
    if status != "enabled":
        reasons.append("source_not_enabled")
    if type(can_search) is not bool or type(can_store) is not bool:
        raise PipelineError("Invalid Collector policy permission types", 409)
    if not can_search or not can_store:
        reasons.append("source_permissions_disabled")
    if policy_version is None or reviewed_at is None:
        reasons.append("source_policy_not_reviewed")
    elif (not isinstance(reviewed_at, datetime) or
          reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None or
          reviewed_at > clock + timedelta(minutes=1) or
          reviewed_at < clock - timedelta(days=30)):
        reasons.append("source_policy_review_expired")
    if type(capabilities) is not dict or capabilities.get("keyword_search") is not True:
        reasons.append("keyword_search_not_permitted")
    if (type(permissions) is not dict or
            permissions.get("can_search") is not True or
            permissions.get("can_store_metadata") is not True):
        reasons.append("source_policy_rights_missing")
    if (type(retention_policy) is not dict or
            type(retention_policy.get("retention_days")) is not int or
            retention_policy["retention_days"] < 1 or
            type(retention_days) is not int or
            retention_days < retention_policy["retention_days"]):
        reasons.append("retention_not_verified")
    quota = conn.execute(
        """SELECT limit_units,reserved_units,consumed_units
             FROM source_quota_ledger
            WHERE source_id=%s AND bucket_name='collector.requests'
              AND window_start_at<=now() AND window_end_at>now()
            ORDER BY window_start_at DESC LIMIT 1""",
        (source_id,),
    ).fetchone()
    remaining = None
    if quota is None:
        reasons.append("quota_not_configured")
    else:
        if (len(quota) != 3 or any(type(v) is not int or v < 0 for v in quota) or
                quota[0] < quota[1] + quota[2]):
            raise PipelineError("Untrusted Collector quota accounting", 409)
        remaining = quota[0] - quota[1] - quota[2]
        if remaining == 0:
            reasons.append("quota_exhausted")
    return (policy_version if type(policy_version) in (int, str) else None,
            remaining, reasons)


class CollectorPolicySnapshotPort:
    """Optional SP09 port and operator diagnostics over real Collector SQL.

    declaration_supplier is a *trusted server-owned* callable and must not
    consume arbitrary client JSON. With none, there are zero sources and zero
    DB reads. Even when SQL says policy+quota are configured, there is no
    verified credential/locale/price/operational readiness, so SP09 matches
    remain disabled and unknown costs remain NULL.

    The read-only inspection makes global policy/quota visibility explicit;
    project-specific owner consent and Collector admission remain separate.
    """

    def __init__(self, *, declaration_supplier: Callable | None = None,
                 connect=None, clock=None):
        if declaration_supplier is not None and not callable(declaration_supplier):
            raise PipelineError("Invalid trusted Collector declaration supplier", 422)
        if connect is not None and not callable(connect):
            raise PipelineError("Invalid read-only Collector connection factory", 422)
        if clock is not None and not callable(clock):
            raise PipelineError("Invalid Collector preflight clock supplier", 422)
        self._supplier = declaration_supplier
        self._connect = connect or _connect
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def configured(self):
        return self._supplier is not None

    def snapshot(self, project_id):
        pid = _pid(project_id)
        clock = _now_utc(self._clock())
        if self._supplier is None:
            return unavailable_snapshot(pid, now=clock)
        raw = self._supplier(pid)
        # No SQL or provider call; this is a conservative SP09 v1 snapshot.
        return _safe_declarations(raw, pid, observed_at=clock)

    def inspection(self, project_id):
        pid = _pid(project_id)
        clock = _now_utc(self._clock())
        catalog = self.snapshot(pid)
        report = {
            "schema_version": SCHEMA,
            "project_id": pid,
            "observed_at": clock.isoformat(),
            "registry_version": catalog["registry_version"],
            "status": "declarations_unavailable" if self._supplier is None else "policy_preflight_only",
            "external_dispatch_authorized": False,
            "source_operations_executed": 0,
            "paid_provider_calls": 0,
            "sources": [],
        }
        if not catalog["sources"]:
            return report
        # One repeatable-read snapshot across every declared source, including
        # quota readings. NO SELECT of credentials, personal data or queries.
        evidence = []
        try:
            with self._connect() as conn:
                with conn.transaction():
                    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    for source in catalog["sources"]:
                        pversion, quota, reasons = _preflight_sql(
                            conn, source["source_id"], clock,
                        )
                        evidence.append((source, pversion, quota, reasons))
        except Exception:
            # Missing Collector migrations, unavailable DB or invalid rows:
            # no invented source readiness/quotas. Operator-safe generic code.
            report["status"] = "policy_preflight_unavailable"
            evidence = [(s, None, None, ["policy_or_quota_database_unavailable"])
                        for s in catalog["sources"]]
        for source, version, quota, reasons in evidence:
            blocked = sorted(set(reasons + [
                "credentials_not_verified", "provider_price_not_verified",
                "language_market_not_verified", "adapter_not_operationally_verified",
                "project_owner_source_consent_not_verified",
            ]))
            report["sources"].append({
                "source_id": source["source_id"],
                "platform": source["platform"],
                "policy_version": version,
                "quota_available_requests": quota,
                "global_policy_quota_preflight_passed": not reasons,
                "operationally_ready": False,
                "external_dispatch_authorized": False,
                "block_reasons": blocked,
            })
        return report
