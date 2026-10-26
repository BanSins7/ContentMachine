"""VC-MOD-01: stable, IO-free boundary DTOs and structural ports.

The actual QV reader, D1 transaction and SRE consumer are implemented in later
VC-MOD stages. Constructing one of these DTOs never attests that an untrusted
caller has database approval; only a backend adapter may establish that.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Any

from .contracts import (
    CollectorError, HASH_RE, LANGUAGE_RE, NODE_RE, PROJECT_RE,
    QUERY_MODES, SAFE_SOURCE_RE, require_dict, require_id, require_int,
    require_text, require_uuid, hash_object,
)

QV_READY_EVENT = "validated_queries_ready/v1"
QV_BUNDLE_SCHEMA = "validated_queries/v1"
DISCOVERY_COMMITTED_EVENT = "collector.discovery_persisted/v1"
SRE_READY_EVENT = "collector.sre_ready/v1"
MAX_QV_QUERIES = 480
MAX_QV_HYPOTHESES = 48
MAX_D1_ID = (2 ** 63) - 1
BOUNDARY_ERRORS = frozenset({
    "invalid_schema", "invalid_schema_version", "invalid_identifier",
    "invalid_uuid", "invalid_limit", "invalid_text", "invalid_query_count",
    "project_scope_mismatch", "publication_scope_mismatch",
    "invalid_port_payload", "identity_type_mismatch",
    "event_integrity_mismatch", "source_not_verified",
})


def _bigint(value: object, field: str) -> int:
    if type(value) is not int or not 1 <= value <= MAX_D1_ID:
        raise CollectorError("identity_type_mismatch", "Invalid D1 bigint: " + field)
    return value


def _query_uuid(value: object) -> str:
    # Production QV always uses canonical UUID, unlike historic offline fixtures.
    return require_uuid(value, "candidate_id")


def _query_ids(values: object, *, nonempty=True) -> tuple[str, ...]:
    if type(values) not in (list, tuple) or len(values) > MAX_QV_QUERIES or (
        nonempty and not values
    ):
        raise CollectorError("invalid_query_count", "Invalid QV query ID list")
    checked = tuple(_query_uuid(value) for value in values)
    if len(set(checked)) != len(checked):
        raise CollectorError("invalid_port_payload", "Duplicate candidate ID")
    return checked


@dataclass(frozen=True, slots=True)
class QvReadyV1:
    """Shape of the event actually emitted by main's pending_validated_outbox.

    NOTE: parsing does NOT mark it trusted; publisher must verify the DB row,
    generation, project, taxonomy and event delivery under transaction.
    """
    project_id: str
    outbox_id: str
    validation_run_id: str
    generation_id: str
    selected_count: int

    def __post_init__(self) -> None:
        require_id(self.project_id, "project_id", PROJECT_RE)
        for value, field in (
            (self.outbox_id, "outbox_id"),
            (self.validation_run_id, "validation_run_id"),
            (self.generation_id, "generation_id"),
        ):
            require_uuid(value, field)
        require_int(self.selected_count, "selected_count", 0, MAX_QV_QUERIES)

    @classmethod
    def parse(cls, project_id: str, raw: dict) -> "QvReadyV1":
        require_dict(
            raw, {"outbox_id", "validation_run_id", "event_type", "payload_pointer"},
            "qv_outbox",
        )
        if raw["event_type"] != QV_READY_EVENT:
            raise CollectorError("invalid_schema_version", "Unsupported QV event")
        pointer = require_dict(
            raw["payload_pointer"],
            {"validation_run_id", "generation_id", "schema_version",
             "embedding_mode", "selected_count"}, "qv_event_pointer",
        )
        if pointer["schema_version"] != QV_BUNDLE_SCHEMA:
            raise CollectorError("invalid_schema_version", "Unsupported QV bundle")
        if pointer["embedding_mode"] != "semantic_local":
            raise CollectorError("source_not_verified", "Production QV must use semantic_local", 409)
        if raw["validation_run_id"] != pointer["validation_run_id"]:
            raise CollectorError("event_integrity_mismatch", "QV event ID mismatch", 409)
        return cls(
            project_id, raw["outbox_id"], raw["validation_run_id"],
            pointer["generation_id"], pointer["selected_count"],
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": QV_READY_EVENT,
            "project_id": self.project_id,
            "outbox_id": self.outbox_id,
            "validation_run_id": self.validation_run_id,
            "generation_id": self.generation_id,
            "selected_count": self.selected_count,
            "embedding_mode": "semantic_local",
        }


@dataclass(frozen=True, slots=True)
class QvBundleShapeV1:
    """Structurally checked selected QV data; approval is not implied."""
    project_id: str
    validation_run_id: str
    generation_id: str
    query_ids: tuple[str, ...]
    coverage_count: int

    @classmethod
    def parse(cls, notice: QvReadyV1, bundle: dict) -> "QvBundleShapeV1":
        require_dict(bundle, {
            "schema_version", "validation_run_id", "generation_id", "project_id",
            "embedding_model_key", "embedding_mode", "config_sha256",
            "source_bundle_sha256", "status", "queries", "coverage", "decisions",
        }, "qv_bundle")
        if bundle["schema_version"] != QV_BUNDLE_SCHEMA:
            raise CollectorError("invalid_schema_version", "QV bundle schema mismatch")
        if bundle["status"] != "completed" or bundle["embedding_mode"] != "semantic_local":
            raise CollectorError("source_not_verified", "QV is not production completed", 409)
        if any((
            bundle["project_id"] != notice.project_id,
            bundle["validation_run_id"] != notice.validation_run_id,
            bundle["generation_id"] != notice.generation_id,
        )):
            raise CollectorError("project_scope_mismatch", "QV source ancestry mismatch", 409)
        for field in ("config_sha256", "source_bundle_sha256"):
            require_id(bundle[field], field, HASH_RE)
        require_text(bundle["embedding_model_key"], "embedding_model_key", 240)
        rows, decisions, coverage = (
            bundle["queries"], bundle["decisions"], bundle["coverage"],
        )
        if type(rows) is not list or len(rows) != notice.selected_count:
            raise CollectorError("event_integrity_mismatch", "Selected count changed", 409)
        if type(decisions) is not list or not len(rows) <= len(decisions) <= MAX_QV_QUERIES:
            raise CollectorError("invalid_port_payload", "Invalid QV decisions list")
        if type(coverage) is not list or not 0 <= len(coverage) <= MAX_QV_HYPOTHESES:
            raise CollectorError("invalid_port_payload", "Invalid QV coverage list")
        seen = []
        for row in rows:
            if type(row) is not dict or not {
                "candidate_id", "hypothesis_id", "direction_id", "text",
                "query_mode", "language",
            }.issubset(row):
                raise CollectorError("invalid_port_payload", "Invalid QV candidate")
            seen.append(_query_uuid(row["candidate_id"]))
            require_id(row["hypothesis_id"], "hypothesis_id", NODE_RE)
            require_id(row["direction_id"], "direction_id", NODE_RE)
            require_text(row["text"], "query_text", 500)
            require_id(row["language"], "language", LANGUAGE_RE)
            if type(row["query_mode"]) is not str or row["query_mode"] not in QUERY_MODES:
                raise CollectorError("invalid_port_payload", "Unsupported QV query mode")
        if len(set(seen)) != len(seen):
            raise CollectorError("event_integrity_mismatch", "Duplicate selected candidate", 409)
        selected_ids = []
        all_decisions = []
        for row in decisions:
            if type(row) is not dict or not {"candidate_id", "decision"}.issubset(row):
                raise CollectorError("invalid_port_payload", "Invalid QV decision")
            cid = _query_uuid(row["candidate_id"])
            all_decisions.append(cid)
            if row["decision"] == "selected":
                selected_ids.append(cid)
            elif row["decision"] != "rejected":
                raise CollectorError("invalid_port_payload", "Invalid QV decision state")
        if len(set(all_decisions)) != len(all_decisions) or set(seen) != set(selected_ids):
            raise CollectorError("event_integrity_mismatch", "QV decisions do not match selected", 409)
        from collections import Counter
        selected_by_hypothesis = Counter(row["hypothesis_id"] for row in rows)
        covered_hypotheses = set()
        for row in coverage:
            if type(row) is not dict or not {
                "hypothesis_id", "source_count", "selected_count", "outcome",
            }.issubset(row):
                raise CollectorError("invalid_port_payload", "Invalid QV coverage")
            require_id(row["hypothesis_id"], "hypothesis_id", NODE_RE)
            require_int(row["source_count"], "source_count", 0, 16)
            require_int(row["selected_count"], "selected_count", 0, 16)
            hyp = row["hypothesis_id"]
            if hyp in covered_hypotheses or row["selected_count"] > row["source_count"]:
                raise CollectorError("event_integrity_mismatch", "Invalid QV coverage counts", 409)
            covered_hypotheses.add(hyp)
            if selected_by_hypothesis[hyp] != row["selected_count"]:
                raise CollectorError("event_integrity_mismatch", "QV coverage does not match selected", 409)
        if not set(selected_by_hypothesis).issubset(covered_hypotheses):
            raise CollectorError("event_integrity_mismatch", "Selected hypothesis missing coverage", 409)
        return cls(notice.project_id, notice.validation_run_id,
                   notice.generation_id, _query_ids(seen, nonempty=False),
                   len(coverage))


@dataclass(frozen=True, slots=True)
class D1VideoRefV1:
    video_id: int
    discovery_id: int
    source_metadata_id: int

    def __post_init__(self) -> None:
        _bigint(self.video_id, "video_id")
        _bigint(self.discovery_id, "discovery_id")
        _bigint(self.source_metadata_id, "source_metadata_id")

    def to_dict(self) -> dict:
        return {"video_id": self.video_id, "discovery_id": self.discovery_id,
                "source_metadata_id": self.source_metadata_id}


@dataclass(frozen=True, slots=True)
class DiscoveryCommittedV1:
    project_id: str
    collector_run_id: int
    source_request_id: int
    checkpoint_id: int
    provider_task_id: str
    publication_id: str
    taxonomy_version: int
    taxonomy_sha256: str
    query_ids: tuple[str, ...]
    page_sha256: str
    videos: tuple[D1VideoRefV1, ...]

    def __post_init__(self) -> None:
        require_id(self.project_id, "project_id", PROJECT_RE)
        for name in ("collector_run_id", "source_request_id", "checkpoint_id"):
            _bigint(getattr(self, name), name)
        require_uuid(self.publication_id, "publication_id")
        require_int(self.taxonomy_version, "taxonomy_version", 1, (2**31)-1)
        require_id(self.taxonomy_sha256, "taxonomy_sha256", HASH_RE)
        require_id(self.page_sha256, "page_sha256", HASH_RE)
        require_text(self.provider_task_id, "provider_task_id", 255)
        _query_ids(self.query_ids)
        if type(self.query_ids) is not tuple:
            raise CollectorError("invalid_port_payload", "Immutable UUID query tuple required")
        if type(self.videos) is not tuple or any(type(x) is not D1VideoRefV1 for x in self.videos):
            raise CollectorError("invalid_port_payload", "Invalid D1 video references")

    def to_dict(self) -> dict:
        return {"schema_version": DISCOVERY_COMMITTED_EVENT,
                "project_id": self.project_id, "collector_run_id": self.collector_run_id,
                "source_request_id": self.source_request_id,
                "checkpoint_id": self.checkpoint_id, "provider_task_id": self.provider_task_id,
                "publication_id": self.publication_id, "taxonomy_version": self.taxonomy_version,
                "taxonomy_sha256": self.taxonomy_sha256, "query_ids": list(self.query_ids),
                "page_sha256": self.page_sha256,
                "videos": [record.to_dict() for record in self.videos]}

    @property
    def fingerprint(self) -> str:
        return hash_object(self.to_dict())


@dataclass(frozen=True, slots=True)
class SREReadyV1:
    project_id: str
    collector_run_id: int
    source_request_id: int
    video: D1VideoRefV1
    publication_id: str
    taxonomy_version: int
    taxonomy_sha256: str
    query_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        require_id(self.project_id, "project_id", PROJECT_RE)
        _bigint(self.collector_run_id, "collector_run_id")
        _bigint(self.source_request_id, "source_request_id")
        if type(self.video) is not D1VideoRefV1:
            raise CollectorError("invalid_port_payload", "Persisted D1 video required")
        if type(self.query_ids) is not tuple:
            raise CollectorError("invalid_port_payload", "Immutable UUID query tuple required")
        require_uuid(self.publication_id, "publication_id")
        require_int(self.taxonomy_version, "taxonomy_version", 1, (2**31)-1)
        require_id(self.taxonomy_sha256, "taxonomy_sha256", HASH_RE)
        _query_ids(self.query_ids)

    def to_dict(self) -> dict:
        record = {
            "schema_version": SRE_READY_EVENT, "project_id": self.project_id,
            "collector_run_id": self.collector_run_id, "source_request_id": self.source_request_id,
            "video": self.video.to_dict(), "publication_id": self.publication_id,
            "taxonomy_version": self.taxonomy_version, "taxonomy_sha256": self.taxonomy_sha256,
            "query_ids": list(self.query_ids), "evidence_scope": "metadata_only",
            "relevance_state": "not_evaluated",
        }
        record["delivery_key"] = hash_object(record)
        return record


@dataclass(frozen=True, slots=True)
class CollectorFeatureFlags:
    qv_consumer_enabled: bool
    provider_dispatch_enabled: bool
    sre_delivery_enabled: bool


def runtime_flags() -> CollectorFeatureFlags:
    """Read flags ONLY on explicit invocation. Importing ports is completely offline.

    Durable SRE outbox *write* is mandatory with ingest, independent of whether
    the optional SRE delivery consumer is enabled.
    """
    from intelligence.config import setting
    return CollectorFeatureFlags(
        qv_consumer_enabled=setting("CM_COLLECTOR_QV_CONSUMER_ENABLED", "0") == "1",
        provider_dispatch_enabled=setting("CM_COLLECTOR_LIVE_ENABLED", "0") == "1",
        sre_delivery_enabled=setting("CM_COLLECTOR_SRE_DELIVERY_ENABLED", "0") == "1",
    )


class SearchPlannerQVPort(Protocol):
    def pending_validated_outbox(self, project_id: str, *, limit: int = 50) -> list[dict]: ...
    def get_validated_bundle(self, project_id: str, validation_run_id: str) -> dict: ...


class SourceProviderPort(Protocol):
    # Technical capability mapping stays in Collector registry.py, not provider transport.
    def status(self) -> dict: ...
    def submit(self, action: str, params: dict) -> dict: ...
    def task(self, task_id: str) -> dict: ...


class CanonicalDiscoveryPort(Protocol):
    # Called inside Collector-owned PostgreSQL transaction by future ingest.py.
    def persist_page(self, conn: Any, page: Any, *, project_id: str,
                     source_request_id: int, checkpoint_id: int) -> DiscoveryCommittedV1: ...


class SREHandoffPort(Protocol):
    # Enqueue-only inside the ingest transaction; SRE may be absent.
    def enqueue(self, conn: Any, event: SREReadyV1) -> None: ...
