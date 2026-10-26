"""M02 OPTIONAL Collector -> SRE event contract (no ingestion side effects).

A validated event proves only *syntax and digest integrity*. It is NOT proof
of persisted D1 discovery, source policy, sender identity or project ownership.
M35 will implement a DB-authoritative inbox with all those checks.
"""
from __future__ import annotations

from ..v1_contracts import (
    canonical_uuid, code, fingerprint, positive, project_id, record, reject,
    sha256_digest, utc_time,
)

COLLECTOR_DELIVERY_VERSION = "sre.discovery_ready/v1"


def validate_collector_delivery(payload):
    record(payload, "collector_event", required=(
        "schema_version", "event_id", "producer", "project_id",
        "video_id", "discovery_id", "source_id", "source_policy_version_id",
        "source_request_id", "collector_query_ids", "d1_persisted_at",
        "occurred_at", "event_sha256"))
    if payload["schema_version"] != COLLECTOR_DELIVERY_VERSION:
        reject("collector_event.schema_version", "unsupported Collector delivery schema",
               "unsupported_schema_version")
    canonical_uuid(payload["event_id"], "collector_event.event_id")
    if payload["producer"] != "video_collector":
        reject("collector_event.producer", "expected video_collector")
    project_id(payload["project_id"])
    for key in ("video_id","discovery_id","source_policy_version_id"):
        positive(payload[key], "collector_event."+key)
    code(payload["source_id"], "collector_event.source_id")
    source_request = payload["source_request_id"]
    if source_request is not None:
        positive(source_request, "collector_event.source_request_id")
    candidate_ids = payload["collector_query_ids"]
    if type(candidate_ids) is not list or len(candidate_ids)>1000:
        reject("collector_event.collector_query_ids", "expected bounded UUID array")
    for i, candidate in enumerate(candidate_ids):
        canonical_uuid(candidate, f"collector_event.collector_query_ids[{i}]")
    if len(set(candidate_ids)) != len(candidate_ids) or candidate_ids != sorted(candidate_ids):
        reject("collector_event.collector_query_ids", "query ids must be unique and sorted")
    persisted = utc_time(payload["d1_persisted_at"], "collector_event.d1_persisted_at")
    occurred = utc_time(payload["occurred_at"], "collector_event.occurred_at")
    if persisted > occurred:
        reject("collector_event.occurred_at", "event cannot predate D1 persistence")
    sha256_digest(payload["event_sha256"], "collector_event.event_sha256")
    canonical = dict(payload)
    digest = canonical.pop("event_sha256")
    if fingerprint(canonical) != digest:
        reject("collector_event.event_sha256", "payload digest mismatch",
               "snapshot_mismatch")
    return dict(payload)


__all__ = ["COLLECTOR_DELIVERY_VERSION", "validate_collector_delivery"]
