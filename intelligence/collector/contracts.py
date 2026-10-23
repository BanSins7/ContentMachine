"""Immutable, strictly validated offline Video Collector V1 data contracts.

The structs carry *structural* validation only. A JSON claim that Query
Validation accepted a query does NOT establish server-side authorization.
No paid calls, provider IO or media processing live in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Literal
from uuid import UUID

from intelligence.taxonomy_builder.pipeline_contracts import sha256

PROJECT_RE = re.compile(r"[0-9a-f]{32}\Z")
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
NODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{1,127}\Z")
LANGUAGE_RE = re.compile(r"[a-z]{2,3}(?:-[A-Z]{2})?\Z")
SAFE_SOURCE_RE = re.compile(r"[a-z][a-z0-9_]{2,62}\Z")
QUERY_MODES = frozenset(("keyword", "hashtag"))
PLAN_SCHEMA = "collector_plan/v1"
QUERY_SET_SCHEMA = "validated_query_set/v1"
TASK_SCHEMA = "source_task/v1"
PAGE_SCHEMA = "provider_page/v1"
CANDIDATE_SCHEMA = "candidate_envelope/v1"


class CollectorError(ValueError):
    """A predictable, machine-readable offline planning/validation failure."""

    def __init__(self, code: str, detail: str, http_status: int = 422):
        super().__init__(detail)
        self.code = code
        self.http_status = http_status


def require_dict(value: object, keys: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != keys:
        raise CollectorError("invalid_schema", label + ": invalid or unexpected fields")
    return value


def require_id(value: object, label: str, pattern=NODE_RE) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise CollectorError("invalid_identifier", "Invalid " + label)
    return value


def require_uuid(value: object, label: str) -> str:
    if type(value) is not str:
        raise CollectorError("invalid_uuid", "Invalid " + label)
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise CollectorError("invalid_uuid", "Invalid " + label) from None
    if str(parsed) != value:
        raise CollectorError("invalid_uuid", "Noncanonical " + label)
    return value


def require_int(value: object, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CollectorError("invalid_limit", label + " outside bounds")
    return value


def require_text(value: object, label: str, maximum: int) -> str:
    if type(value) is not str:
        raise CollectorError("invalid_text", "Invalid " + label)
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum or any(
        ord(char) < 32 or 127 <= ord(char) <= 159 for char in value
    ):
        raise CollectorError("invalid_text", "Invalid " + label)
    return normalized


def hash_object(value: object) -> str:
    return sha256(value)


def valid_datetime(value: datetime | None, label: str) -> None:
    if value is not None and (
        not isinstance(value, datetime) or value.tzinfo is None or
        value.utcoffset() is None
    ):
        raise CollectorError("invalid_datetime", label + " must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ValidatedQuery:
    query_id: str
    hypothesis_id: str
    direction_id: str
    mode: Literal["keyword", "hashtag"]
    text: str
    language: str
    validation_state: Literal["accepted"] = "accepted"

    def to_dict(self) -> dict:
        return {
            "query_id": self.query_id,
            "hypothesis_id": self.hypothesis_id,
            "direction_id": self.direction_id,
            "mode": self.mode,
            "text": self.text,
            "language": self.language,
            "validation_state": self.validation_state,
        }


@dataclass(frozen=True, slots=True)
class ValidatedQuerySet:
    project_id: str
    taxonomy_publication_id: str
    taxonomy_version: int
    taxonomy_sha256: str
    generation_id: str
    validation_version: str
    queries: tuple[ValidatedQuery, ...]

    def to_dict(self) -> dict:
        return {
            "schema_version": QUERY_SET_SCHEMA,
            "project_id": self.project_id,
            "taxonomy_publication_id": self.taxonomy_publication_id,
            "taxonomy_version": self.taxonomy_version,
            "taxonomy_sha256": self.taxonomy_sha256,
            "generation_id": self.generation_id,
            "validation_version": self.validation_version,
            "queries": [item.to_dict() for item in self.queries],
        }

    @property
    def fingerprint(self) -> str:
        return hash_object(self.to_dict())


@dataclass(frozen=True, slots=True)
class CollectorLimits:
    max_tasks: int = 20
    max_pages_per_task: int = 1
    max_results_per_page: int = 20
    max_total_requests: int = 40
    max_total_candidates: int = 480

    def to_dict(self) -> dict:
        return {
            "max_tasks": self.max_tasks,
            "max_pages_per_task": self.max_pages_per_task,
            "max_results_per_page": self.max_results_per_page,
            "max_total_requests": self.max_total_requests,
            "max_total_candidates": self.max_total_candidates,
        }


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    source_id: str
    platform: str
    integration_state: str
    operations: tuple[tuple[str, str], ...]
    operationally_verified: bool
    source_policy_verified: bool
    pricing_status: str

    def operation_for(self, mode: str) -> str | None:
        return dict(self.operations).get(mode)

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "platform": self.platform,
            "integration_state": self.integration_state,
            "capabilities": [
                {"mode": mode, "operation": operation}
                for mode, operation in self.operations
            ],
            "operationally_verified": self.operationally_verified,
            "source_policy_verified": self.source_policy_verified,
            "pricing_status": self.pricing_status,
        }


@dataclass(frozen=True, slots=True)
class SourceTask:
    task_fingerprint: str
    source_id: str
    platform: str
    operation: str
    mode: str
    query_text: str
    language: str
    query_ids: tuple[str, ...]
    hypothesis_ids: tuple[str, ...]
    direction_ids: tuple[str, ...]
    max_pages: int
    max_results_per_page: int

    def to_dict(self) -> dict:
        return {
            "schema_version": TASK_SCHEMA,
            "task_fingerprint": self.task_fingerprint,
            "source_id": self.source_id,
            "platform": self.platform,
            "operation": self.operation,
            "method": self.mode,
            "params": {"keyword": self.query_text} if self.operation == "search_posts" else {},
            "language": self.language,
            "query_ids": list(self.query_ids),
            "hypothesis_ids": list(self.hypothesis_ids),
            "direction_ids": list(self.direction_ids),
            "max_pages": self.max_pages,
            "max_results_per_page": self.max_results_per_page,
        }


@dataclass(frozen=True, slots=True)
class CollectorPlan:
    plan_hash: str
    source_id: str
    query_set: ValidatedQuerySet
    limits: CollectorLimits
    source: SourceDescriptor
    tasks: tuple[SourceTask, ...]
    rejected: tuple[dict, ...]
    block_reasons: tuple[str, ...]
    requested_result_upper_bound: int
    max_external_requests_upper_bound: int
    covered_hypothesis_ids: tuple[str, ...]
    uncovered_hypothesis_ids: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "schema_version": PLAN_SCHEMA,
            "status": "preview_only",
            "execution_enabled": False,
            "plan_id": self.plan_hash[:24],
            "plan_hash": self.plan_hash,
            "project_id": self.query_set.project_id,
            "taxonomy_publication_id": self.query_set.taxonomy_publication_id,
            "taxonomy_version": self.query_set.taxonomy_version,
            "taxonomy_sha256": self.query_set.taxonomy_sha256,
            "generation_id": self.query_set.generation_id,
            "query_set_sha256": self.query_set.fingerprint,
            "query_provenance_verified": False,
            "source": self.source.to_dict(),
            "source_id": self.source_id,
            "limits": self.limits.to_dict(),
            "tasks": [item.to_dict() for item in self.tasks],
            "rejected_queries": list(self.rejected),
            "blocks": list(self.block_reasons),
            "summary": {
                "input_query_count": len(self.query_set.queries),
                "planned_task_count": len(self.tasks),
                "rejected_query_count": len(self.rejected),
                "input_hypothesis_count": len(
                    {q.hypothesis_id for q in self.query_set.queries}
                ),
                "covered_hypothesis_ids": list(self.covered_hypothesis_ids),
                "uncovered_hypothesis_ids": list(self.uncovered_hypothesis_ids),
                "max_external_requests_upper_bound": self.max_external_requests_upper_bound,
                "requested_result_upper_bound": self.requested_result_upper_bound,
                "actual_results": None,
                "estimated_cost_usd": None,
                "pricing_status": self.source.pricing_status,
            },
            "disclaimer": (
                "Offline structural preview only. Query approvals, provider "
                "readiness, source rights, provider paging, costs and actual "
                "results are NOT verified. No provider calls or money movement."
            ),
        }


@dataclass(frozen=True, slots=True)
class CandidateEnvelope:
    """Normalized downstream DTO. This phase does not construct live candidates."""
    project_id: str
    taxonomy_publication_id: str
    source_id: str
    provider_task_id: str
    query_ids: tuple[str, ...]
    hypothesis_ids: tuple[str, ...]
    platform: Literal["youtube", "tiktok", "instagram"]
    platform_video_id: str
    canonical_url: str
    fetched_at: datetime
    published_at: datetime | None = None
    observed_at: datetime | None = None
    title: str | None = None
    content_kind: str = "unknown"

    def __post_init__(self) -> None:
        require_id(self.project_id, "project", PROJECT_RE)
        require_uuid(self.taxonomy_publication_id, "publication")
        require_id(self.source_id, "source", SAFE_SOURCE_RE)
        if not self.provider_task_id or not self.platform_video_id:
            raise CollectorError("invalid_candidate", "Missing provider identity")
        if not self.query_ids or not self.hypothesis_ids:
            raise CollectorError("invalid_candidate", "Missing query provenance")
        if self.platform not in {"youtube", "tiktok", "instagram"}:
            raise CollectorError("invalid_candidate", "Unsupported platform")
        valid_datetime(self.fetched_at, "fetched_at")
        valid_datetime(self.published_at, "published_at")
        valid_datetime(self.observed_at, "observed_at")
        from intelligence.discovery.normalize import canonical_video_url, CandidateError
        try:
            platform, video_id, canonical = canonical_video_url(self.canonical_url)
        except CandidateError:
            raise CollectorError("invalid_candidate", "Invalid canonical URL") from None
        if (platform, video_id, canonical) != (
            self.platform, self.platform_video_id, self.canonical_url
        ):
            raise CollectorError("invalid_candidate", "Canonical identity mismatch")


@dataclass(frozen=True, slots=True)
class ProviderPage:
    """Provider paging DTO. No assumptions about DTK live payload fields."""
    source_id: str
    provider_task_id: str
    items: tuple[CandidateEnvelope, ...]
    fetched_at: datetime
    next_cursor: str | None = None
    has_more: bool = False
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        require_id(self.source_id, "source", SAFE_SOURCE_RE)
        if not isinstance(self.provider_task_id, str) or not self.provider_task_id:
            raise CollectorError("invalid_provider_page", "Missing provider task ID")
        if type(self.items) is not tuple or any(
            type(item) is not CandidateEnvelope for item in self.items
        ):
            raise CollectorError("invalid_provider_page", "Invalid page items")
        if type(self.has_more) is not bool:
            raise CollectorError("invalid_provider_page", "Invalid has_more")
        if self.next_cursor is not None and (
            type(self.next_cursor) is not str or
            not 1 <= len(self.next_cursor) <= 320
        ):
            raise CollectorError("invalid_provider_page", "Invalid next_cursor")
        valid_datetime(self.fetched_at, "fetched_at")
