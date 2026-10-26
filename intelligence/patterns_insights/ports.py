"""PIE-MOD-02 dependency-inversion boundary, inert on import.

An advertised capability describes an injected adapter's *claimed interface*,
not trusted UAE facts, valid research data, source rights, or provider approval.
No automatic discovery, DB connection, network request, LLM call or worker start.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

from .contracts import PIEBoundaryError, PIEDatasetPointer

_SCHEMA = re.compile(r"[a-z][a-z0-9._-]*/[a-z0-9._-]+\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{1,79}\Z")

# PIE-owned adapter versions, NOT peer-provided runtime contracts.
# UAE's future row-level wire is proposed in PIE-MOD-01 and is unavailable.
PORT_SCHEMAS: Mapping[str, str] = MappingProxyType({
    "uae": "pie-uae-reader/v1",
    "source_policy": "pie-source-policy/v1",
    "store": "pie-artifact-store/v1",
    "publisher": "pie-insight-publisher/v1",
    "review": "pie-human-review/v1",
    "model": "pie-grounded-model/v1",
})
EXPECTED_PEER_WIRES: Mapping[str, str | None] = MappingProxyType({
    "uae": "uae-to-pie/v1",  # target; NOT currently emitted by UAE
    "source_policy": None,
    "store": None,
    "publisher": None,
    "review": None,
    "model": None,
})
REQUIRED_METHODS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "uae": ("capability", "read_qualified_manifest"),
    "source_policy": ("capability", "check_current_rights"),
    "store": ("capability", "write_immutable"),
    "publisher": ("capability", "publish_reviewed_insight"),
    "review": ("capability", "get_review_status"),
    "model": ("capability", "generate_grounded_insight"),
})


class CapabilityState(str, Enum):
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE = "incompatible"
    DECLARED = "declared"  # structure only; never means authorized/live/qualified


@dataclass(frozen=True, slots=True)
class PortCapability:
    """Untrusted, declarative adapter capability, checked without doing IO."""
    port: str
    state: CapabilityState
    adapter_schema: str | None = None
    peer_schema: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if type(self.port) is not str or self.port not in PORT_SCHEMAS:
            raise PIEBoundaryError("invalid_port", "Unknown PIE port")
        if type(self.state) is not CapabilityState:
            raise PIEBoundaryError("invalid_capability", "Invalid state")
        for schema in (self.adapter_schema, self.peer_schema):
            if schema is not None and (type(schema) is not str or not _SCHEMA.fullmatch(schema)):
                raise PIEBoundaryError("invalid_capability", "Invalid schema identifier")
        if self.state is CapabilityState.DECLARED:
            if self.adapter_schema is None or self.reason_code is not None:
                raise PIEBoundaryError("invalid_capability", "Declared capability requires an adapter schema")
        elif type(self.reason_code) is not str or not _REASON.fullmatch(self.reason_code):
            raise PIEBoundaryError("invalid_capability", "Failure status requires safe reason code")


@dataclass(frozen=True, slots=True)
class PortStatus:
    port: str
    state: CapabilityState
    expected_adapter_schema: str
    observed_adapter_schema: str | None
    expected_peer_schema: str | None
    observed_peer_schema: str | None
    reason_code: str | None

    @property
    def can_attempt_call(self) -> bool:
        """The adapter may be invoked by a future trusted integration stage."""
        return self.state is CapabilityState.DECLARED

    @property
    def verified_live(self) -> bool:
        """No metadata-only probe is able to certify a real provider read."""
        return False


@runtime_checkable
class UAEQualifiedReader(Protocol):
    def capability(self) -> PortCapability: ...
    def read_qualified_manifest(self, pointer: PIEDatasetPointer) -> object: ...


@runtime_checkable
class SourceRightsReader(Protocol):
    def capability(self) -> PortCapability: ...
    def check_current_rights(self, pointer: PIEDatasetPointer) -> object: ...


@runtime_checkable
class ArtifactStore(Protocol):
    def capability(self) -> PortCapability: ...
    def write_immutable(self, project_id: str, kind: str, sha256: str, data: bytes) -> str: ...


@runtime_checkable
class InsightPublisher(Protocol):
    def capability(self) -> PortCapability: ...
    def publish_reviewed_insight(self, project_id: str, version_id: str) -> str: ...


@runtime_checkable
class HumanReviewReader(Protocol):
    def capability(self) -> PortCapability: ...
    def get_review_status(self, project_id: str, version_id: str) -> object: ...


@runtime_checkable
class GroundedModelProvider(Protocol):
    def capability(self) -> PortCapability: ...
    def generate_grounded_insight(self, project_id: str, evidence: Mapping[str, object], approval_id: str) -> object: ...


PROTOCOLS: Mapping[str, type] = MappingProxyType({
    "uae": UAEQualifiedReader,
    "source_policy": SourceRightsReader,
    "store": ArtifactStore,
    "publisher": InsightPublisher,
    "review": HumanReviewReader,
    "model": GroundedModelProvider,
})


def _status(port: str, state: CapabilityState, reason_code: str | None = None,
            adapter_schema: str | None = None, peer_schema: str | None = None) -> PortStatus:
    return PortStatus(port, state, PORT_SCHEMAS[port], adapter_schema,
                      EXPECTED_PEER_WIRES[port], peer_schema, reason_code)


@dataclass(frozen=True, slots=True)
class PIEPorts:
    """Explicitly supplied dependencies; absent ports are always unavailable.

    Does not load optional UAE modules, query source policy, or call business/
    paid operations. A descriptor is not cryptographic attestation.
    """
    uae: UAEQualifiedReader | None = None
    source_policy: SourceRightsReader | None = None
    store: ArtifactStore | None = None
    publisher: InsightPublisher | None = None
    review: HumanReviewReader | None = None
    model: GroundedModelProvider | None = None

    def status(self, port: str) -> PortStatus:
        if type(port) is not str or port not in PORT_SCHEMAS:
            raise PIEBoundaryError("invalid_port", "Unknown PIE capability port")
        adapter: Any = getattr(self, port)
        if adapter is None:
            return _status(port, CapabilityState.UNAVAILABLE, "not_connected")
        try:
            # Runtime Protocol checks method presence only. Explicitly
            # require callability, then invoke ONLY metadata capability().
            if not isinstance(adapter, PROTOCOLS[port]) or any(
                not callable(getattr(adapter, name, None)) for name in REQUIRED_METHODS[port]
            ):
                return _status(port, CapabilityState.INCOMPATIBLE, "missing_methods")
        except Exception:
            return _status(port, CapabilityState.INCOMPATIBLE, "protocol_check_failed")
        try:
            candidate = adapter.capability()
        except Exception:
            # Never surface provider errors containing private context.
            return _status(port, CapabilityState.UNAVAILABLE, "capability_probe_failed")
        if not isinstance(candidate, PortCapability) or candidate.port != port:
            return _status(port, CapabilityState.INCOMPATIBLE, "invalid_descriptor")
        if candidate.state is not CapabilityState.DECLARED:
            return _status(port, candidate.state, candidate.reason_code,
                           candidate.adapter_schema, candidate.peer_schema)
        if candidate.adapter_schema != PORT_SCHEMAS[port]:
            return _status(port, CapabilityState.INCOMPATIBLE, "adapter_schema_mismatch",
                           candidate.adapter_schema, candidate.peer_schema)
        peer = EXPECTED_PEER_WIRES[port]
        if peer is not None and candidate.peer_schema != peer:
            return _status(port, CapabilityState.INCOMPATIBLE, "peer_schema_mismatch",
                           candidate.adapter_schema, candidate.peer_schema)
        return _status(port, CapabilityState.DECLARED, None,
                       candidate.adapter_schema, candidate.peer_schema)

    def report(self) -> tuple[PortStatus, ...]:
        """Stable, complete report; no reads, storage, worker or payment calls."""
        return tuple(self.status(name) for name in PORT_SCHEMAS)

    def require(self, port: str) -> object:
        """Return injected adapter *only* after metadata compatibility checks.

        Caller must still validate rights and actual returned evidence. This is
        deliberately not a production authorized reader or dispatcher.
        """
        status = self.status(port)
        if status.state is CapabilityState.UNAVAILABLE:
            raise PIEBoundaryError("capability_unavailable", f"PIE port unavailable: {port} ({status.reason_code})")
        if status.state is CapabilityState.INCOMPATIBLE:
            raise PIEBoundaryError("capability_incompatible", f"PIE port incompatible: {port} ({status.reason_code})")
        return getattr(self, port)
