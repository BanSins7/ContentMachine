"""VC-MOD-08: strict, durable-safe, round-trippable Inspector DTO v1.

This is a purely in-memory, bounded handoff to the future atomic D1 ingest.
It never serializes raw DTK JSON, signed media URLs, opaque cursors,
captions, auth fields or arbitrary provider metadata. No DB/provider calls.
"""
from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone

from intelligence.taxonomy_builder.pipeline_contracts import sha256
from .contracts import CandidateEnvelope, CollectorError, ProviderPage, require_uuid
from .provider_inspector import InspectedPage, INSPECTOR_VERSION, MAX_PROVIDER_ITEMS

SCHEMA_VERSION = "collector.inspected_page/v1"
_ALLOWED_CANDIDATE = frozenset(x.name for x in fields(CandidateEnvelope))
_DATE_FIELDS = frozenset(("fetched_at", "published_at", "observed_at"))
_SEQUENCE_FIELDS = frozenset(("query_ids", "hypothesis_ids", "quality_reasons"))
_REQUIRED_ROOT = frozenset((
    "schema_version", "inspector_version", "project_id", "provider_task_id",
    "taxonomy_publication_id", "taxonomy_version", "generation_id",
    "query_ids", "hypothesis_ids", "source_id", "fetched_at",
    "has_more", "cursor_present", "raw_item_count", "examined_count",
    "accepted_count", "valid_count", "partial_count", "rejected_count",
    "overflow_count", "quality_reasons", "rejected", "items",
    "raw_result_persisted", "metrics_persisted", "ingestion_pending",
    "payload_sha256",
))


def _fail() -> None:
    raise CollectorError("invalid_inspected_page",
                         "Stored Inspector DTO is incomplete or inconsistent", 409)


def _datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if type(value) is not str or len(value) > 80:
        _fail()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail()
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail()
    if parsed.astimezone(timezone.utc).isoformat() != value:
        _fail()
    return parsed.astimezone(timezone.utc)


def _record_candidate(candidate: CandidateEnvelope) -> dict:
    if type(candidate) is not CandidateEnvelope:
        _fail()
    result = {}
    for field in fields(CandidateEnvelope):
        key = field.name
        value = getattr(candidate, key)
        if key in _DATE_FIELDS:
            result[key] = value.astimezone(timezone.utc).isoformat() if value is not None else None
        elif key in _SEQUENCE_FIELDS:
            result[key] = list(value)
        else:
            result[key] = value
    return result


def serialize_inspected_page(inspected: InspectedPage) -> dict:
    """Return only allowlisted verified fields; checksum all records and counts."""
    if type(inspected) is not InspectedPage:
        _fail()
    page = inspected.page
    if type(page) is not ProviderPage:
        _fail()
    overview = inspected.summary()
    if (inspected.raw_item_count > MAX_PROVIDER_ITEMS
        or inspected.raw_item_count != len(page.items) + len(inspected.rejected)
        or overview["rejected_count"] != len(inspected.rejected)
        or overview["accepted_count"] != len(page.items)
        or overview["examined_count"] + overview["overflow_count"] != inspected.raw_item_count
        or overview["valid_count"] + overview["partial_count"] != len(page.items)):
        _fail()
    result = {
        "schema_version": SCHEMA_VERSION,
        "inspector_version": INSPECTOR_VERSION,
        "project_id": page.project_id,
        "provider_task_id": page.provider_task_id,
        "taxonomy_publication_id": page.taxonomy_publication_id,
        "taxonomy_version": page.taxonomy_version,
        "generation_id": page.generation_id,
        "query_ids": list(page.query_ids),
        "hypothesis_ids": list(page.hypothesis_ids),
        "source_id": page.source_id,
        "fetched_at": page.fetched_at.astimezone(timezone.utc).isoformat(),
        "has_more": page.has_more,
        # Only the presence bit; raw cursor is never a retained artifact.
        "cursor_present": page.next_cursor is not None,
        "raw_item_count": overview["raw_item_count"],
        "examined_count": overview["examined_count"],
        "accepted_count": overview["accepted_count"],
        "valid_count": overview["valid_count"],
        "partial_count": overview["partial_count"],
        "rejected_count": overview["rejected_count"],
        "overflow_count": overview["overflow_count"],
        "quality_reasons": dict(inspected.quality_reasons),
        "rejected": [dict(value) for value in inspected.rejected],
        "items": [_record_candidate(value) for value in page.items],
        "raw_result_persisted": False,
        "metrics_persisted": False,
        "ingestion_pending": True,
    }
    result["payload_sha256"] = sha256(result)
    return result


def parse_inspected_page(raw: dict, *, project_id: str,
                         provider_task_id: str) -> tuple[ProviderPage, dict]:
    """Verify hash, exact schema, canonical provenance and every candidate.

    No caller can claim the page was written to D1. The returned page is
    ephemeral; a separate guarded ingestion transaction is still required.
    """
    if type(raw) is not dict or set(raw) != _REQUIRED_ROOT:
        _fail()
    if raw["schema_version"] != SCHEMA_VERSION or raw["inspector_version"] != INSPECTOR_VERSION:
        _fail()
    try:
        require_uuid(provider_task_id, "provider_task_id")
    except CollectorError:
        _fail()
    if (type(raw["project_id"]) is not str or raw["project_id"] != project_id
        or raw["provider_task_id"] != provider_task_id
        or raw["raw_result_persisted"] is not False
        or raw["metrics_persisted"] is not False
        or raw["ingestion_pending"] is not True
        or type(raw["payload_sha256"]) is not str
        or len(raw["payload_sha256"]) != 64):
        _fail()
    unsigned = {k:v for k,v in raw.items() if k != "payload_sha256"}
    try:
        if sha256(unsigned) != raw["payload_sha256"]:
            _fail()
    except (TypeError, ValueError):
        _fail()
    if type(raw["items"]) is not list or len(raw["items"]) > MAX_PROVIDER_ITEMS:
        _fail()
    if type(raw["rejected"]) is not list or len(raw["rejected"]) > MAX_PROVIDER_ITEMS:
        _fail()
    if (type(raw["has_more"]) is not bool or type(raw["cursor_present"]) is not bool
        or (raw["cursor_present"] and not raw["has_more"])):
        _fail()
    count_keys = ("raw_item_count", "examined_count", "accepted_count",
                  "valid_count", "partial_count", "rejected_count", "overflow_count")
    if any(type(raw[k]) is not int or not 0 <= raw[k] <= MAX_PROVIDER_ITEMS for k in count_keys):
        _fail()
    if (raw["raw_item_count"] != len(raw["items"]) + len(raw["rejected"])
        or raw["accepted_count"] != len(raw["items"])
        or raw["rejected_count"] != len(raw["rejected"])
        or raw["valid_count"] + raw["partial_count"] != raw["accepted_count"]
        or raw["examined_count"] + raw["overflow_count"] != raw["raw_item_count"]):
        _fail()
    if (type(raw["quality_reasons"]) is not dict
        or len(raw["quality_reasons"]) > MAX_PROVIDER_ITEMS):
        _fail()
    if any(type(k) is not str or not 1 <= len(k) <= 80
           or type(v) is not int or not 0 <= v <= MAX_PROVIDER_ITEMS
           for k,v in raw["quality_reasons"].items()):
        _fail()
    if (type(raw["query_ids"]) is not list or not raw["query_ids"]
        or type(raw["hypothesis_ids"]) is not list or not raw["hypothesis_ids"]
        or any(type(x) is not str or not x for x in raw["query_ids"]+raw["hypothesis_ids"])):
        _fail()
    observations = []
    for entry in raw["items"]:
        if type(entry) is not dict or set(entry) != _ALLOWED_CANDIDATE:
            _fail()
        converted = dict(entry)
        for name in _DATE_FIELDS:
            converted[name] = _datetime(converted[name])
        for name in _SEQUENCE_FIELDS:
            vals = converted[name]
            if type(vals) is not list or any(type(v) is not str for v in vals):
                _fail()
            converted[name] = tuple(vals)
        try:
            observations.append(CandidateEnvelope(**converted))
        except (CollectorError, TypeError, ValueError):
            _fail()
    if (len({v.platform_video_id for v in observations}) != len(observations)
        or sum(v.quality == "partial" for v in observations) != raw["partial_count"]):
        _fail()
    rejected = []
    for item in raw["rejected"]:
        if (type(item) is not dict or set(item) != {"index","reason"}
            or type(item["index"]) is not int
            or not 0 <= item["index"] < raw["raw_item_count"]
            or type(item["reason"]) is not str
            or not 1 <= len(item["reason"]) <= 80):
            _fail()
        rejected.append(item)
    if len({v["index"] for v in rejected}) != len(rejected):
        _fail()
    # Recalculate explanatory QA reasons from verified observations and rejects.
    from collections import Counter
    counts = Counter()
    for item in observations:
        counts.update(item.quality_reasons)
    counts.update(v["reason"] for v in rejected)
    if dict(counts) != raw["quality_reasons"]:
        _fail()
    try:
        page = ProviderPage(
            project_id=raw["project_id"],
            taxonomy_publication_id=raw["taxonomy_publication_id"],
            taxonomy_version=raw["taxonomy_version"],
            generation_id=raw["generation_id"],
            query_ids=tuple(raw["query_ids"]),
            hypothesis_ids=tuple(raw["hypothesis_ids"]),
            source_id=raw["source_id"],
            provider_task_id=raw["provider_task_id"],
            items=tuple(observations),
            fetched_at=_datetime(raw["fetched_at"]),
            has_more=raw["has_more"],
            # Opaque cursor is intentionally not durable.
            next_cursor=None,
        )
    except (CollectorError, TypeError, ValueError):
        _fail()
    if (page.source_id != "dtk_tiktok"
        or any(c.fetched_at != page.fetched_at for c in page.items)
        or any(c.source_id != page.source_id for c in page.items)):
        _fail()
    return page, {
        "schema_version": SCHEMA_VERSION,
        "payload_sha256": raw["payload_sha256"],
        "raw_item_count": raw["raw_item_count"],
        "accepted_count": raw["accepted_count"],
        "rejected_count": raw["rejected_count"],
        "valid_count": raw["valid_count"],
        "partial_count": raw["partial_count"],
        "overflow_count": raw["overflow_count"],
        "has_more": raw["has_more"],
        "cursor_present": raw["cursor_present"],
        "raw_result_persisted": False,
        "metrics_persisted": False,
        "ingestion_pending": True,
    }
