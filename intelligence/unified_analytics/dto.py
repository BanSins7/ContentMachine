"""UA-MOD-02: pure, immutable cross-module *claims*, never qualification proof.

Digests bind identifiers/versions to each other. They do not authenticate the
producer, verify source permissions, or certify that an upstream DB row exists.
Those checks are the responsibility of future UAE read adapters and gates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from enum import Enum
from uuid import UUID

from .contracts import AnalyticsBoundaryError, canonical_hash, content_type_for_kind

_PROJECT = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE = re.compile(r"[A-Za-z0-9._:-]{1,160}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9._:/-]{1,160}\Z")
_SCHEMA = re.compile(r"[a-z][a-z0-9._-]*/[a-zA-Z0-9._-]+\Z")
_PORTS = frozenset(("sre", "qdp", "group_a", "group_b", "group_c", "d1", "source_policy"))
DTO_VERSION = "uae-evidence-ref/v1"


def _reject(code: str, message: str) -> None:
    raise AnalyticsBoundaryError(code, message)


def _uuid(text: str, name: str) -> None:
    if type(text) is not str:
        _reject("invalid_uuid", name)
    try:
        parsed = UUID(text)
    except (ValueError, TypeError, AttributeError) as exc:
        raise AnalyticsBoundaryError("invalid_uuid", name) from exc
    if str(parsed) != text:
        _reject("invalid_uuid", name)


def _sha(text: str, name: str) -> None:
    if type(text) is not str or not _DIGEST.fullmatch(text):
        _reject("invalid_sha256", name)


@dataclass(frozen=True, slots=True)
class InputScope:
    """Canonical project/D1/QDP/source tuple; says nothing about qualification."""

    project_id: str
    video_id: int
    source_id: str
    source_policy_version_id: int
    content_kind: str
    qdp_run_id: str
    qdp_input_sha256: str

    def __post_init__(self) -> None:
        if type(self.project_id) is not str or not _PROJECT.fullmatch(self.project_id):
            _reject("invalid_project_id", "Expected a canonical ContentMachine project ID")
        if type(self.video_id) is not int or self.video_id <= 0:
            _reject("invalid_video_id", "Expected positive D1 videos.id")
        if type(self.source_id) is not str or not _SOURCE.fullmatch(self.source_id):
            _reject("invalid_source_id", "Expected canonical source identifier")
        if type(self.source_policy_version_id) is not int or self.source_policy_version_id <= 0:
            _reject("invalid_policy_id", "Expected a positive source policy version ID")
        content_type_for_kind(self.content_kind)  # image_post intentionally unsupported
        _uuid(self.qdp_run_id, "qdp_run_id")
        _sha(self.qdp_input_sha256, "qdp_input_sha256")

    @classmethod
    def from_pointer(cls, pointer: object) -> "InputScope":
        """Normalize existing UAE pointer; do not trust its supplied references."""
        from .contracts import UnifiedAnalyticsInputPointer
        if not isinstance(pointer, UnifiedAnalyticsInputPointer):
            _reject("invalid_pointer", "Expected UAE input pointer")
        return cls(
            project_id=pointer.project_id,
            video_id=pointer.video_id,
            source_id=pointer.source_id,
            source_policy_version_id=pointer.source_policy_version_id,
            content_kind=pointer.content_kind,
            qdp_run_id=pointer.qdp_run_id,
            qdp_input_sha256=pointer.preparation_sha256,
        )

    @property
    def scope_sha256(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """Untrusted exact-version reference. Never interpret as 'complete'."""

    scope: InputScope
    port: str
    record_key: str
    producer_schema: str
    producer_version: str
    payload_sha256: str
    schema_version: str = DTO_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.scope, InputScope):
            _reject("invalid_scope", "Evidence reference requires an InputScope")
        if type(self.port) is not str or self.port not in _PORTS:
            _reject("invalid_port", "Unsupported evidence port")
        if type(self.record_key) is not str or not _TOKEN.fullmatch(self.record_key):
            _reject("invalid_record_key", "Expected an opaque bounded exact record key")
        if type(self.producer_schema) is not str or not _SCHEMA.fullmatch(self.producer_schema):
            _reject("invalid_producer_schema", "Producer schema version must be explicit")
        if (type(self.producer_version) is not str
                or not _TOKEN.fullmatch(self.producer_version)):
            _reject("invalid_producer_version", "Producer execution/version pin required")
        _sha(self.payload_sha256, "payload_sha256")
        if self.schema_version != DTO_VERSION:
            _reject("unsupported_schema_version", "Unknown UAE evidence reference revision")

    @property
    def lineage_sha256(self) -> str:
        """Deterministic binding digest. NOT a producer signature or verification."""
        return canonical_hash({
            "schema_version": self.schema_version,
            "scope": asdict(self.scope),
            "port": self.port,
            "record_key": self.record_key,
            "producer_schema": self.producer_schema,
            "producer_version": self.producer_version,
            "payload_sha256": self.payload_sha256,
        })


class ReadState(str, Enum):
    LOCATED = "located"  # a peer *claims* a row; later UAE checks must verify it
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class EvidenceRead:
    """Distinguish an absent record from a missing integration, without fake data."""

    port: str
    scope: InputScope
    state: ReadState
    reference: EvidenceRef | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if type(self.port) is not str or self.port not in _PORTS:
            _reject("invalid_port", "Unknown read port")
        if not isinstance(self.scope, InputScope) or type(self.state) is not ReadState:
            _reject("invalid_read", "Typed scope and read state required")
        if self.state is ReadState.LOCATED:
            if self.reference is None or not isinstance(self.reference, EvidenceRef):
                _reject("unverified_reference", "Located results require an exact reference")
            if self.reference.port != self.port or self.reference.scope != self.scope:
                _reject("lineage_mismatch", "Result belongs to a different port/input scope")
            if self.reason_code is not None:
                _reject("invalid_read", "Located results cannot have a failure reason")
        elif self.reference is not None:
            _reject("invalid_read", "Missing/unavailable results must not carry a reference")
        elif (type(self.reason_code) is not str
              or re.fullmatch(r"[a-z][a-z0-9_]{1,79}\Z", self.reason_code) is None):
            _reject("invalid_reason", "Missing/unavailable results require typed reason")


@dataclass(frozen=True, slots=True)
class PatternsHandoffPointer:
    """Future output identity, NOT proof that any Patterns publisher exists."""

    project_id: str
    snapshot_id: int
    snapshot_sha256: str
    aggregate_sha256: str
    schema_version: str = "uae-pattern-handoff/v1"

    def __post_init__(self) -> None:
        if type(self.project_id) is not str or not _PROJECT.fullmatch(self.project_id):
            _reject("invalid_project_id", "Invalid project")
        if type(self.snapshot_id) is not int or self.snapshot_id <= 0:
            _reject("invalid_snapshot_id", "Invalid snapshot ID")
        _sha(self.snapshot_sha256, "snapshot_sha256")
        _sha(self.aggregate_sha256, "aggregate_sha256")
        if self.schema_version != "uae-pattern-handoff/v1":
            _reject("unsupported_schema_version", "Unsupported Patterns handoff")
