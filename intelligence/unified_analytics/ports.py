"""UA-MOD-02 dependency-inverted ports; no peer imports, DB, HTTP or provider calls.

A compatible capability means *adapter interface negotiation only*, NEVER a
verified LIVE classification, QDP qualified item, completed analysis or permission.
Only later source-backed adapter validations may advance qualification.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

from .contracts import AnalyticsBoundaryError
from .dto import EvidenceRead, EvidenceRef, InputScope, PatternsHandoffPointer, ReadState

# These are UAE adapter interfaces, not claims that upstream exposes them.
PORT_SCHEMAS: Mapping[str, str] = MappingProxyType({
    "sre": "uae-sre-reader/v1",
    "qdp": "uae-qdp-reader/v1",
    "group_a": "uae-group-a-reader/v1",
    "group_b": "uae-group-b-reader/v1",
    "group_c": "uae-group-c-reader/v1",
    "d1": "uae-d1-reader/v1",
    "source_policy": "uae-source-policy-reader/v1",
    "patterns": "uae-pattern-publisher/v1",
})
# The only peer *wire* versions verified by inspection; never invent B/C versions.
KNOWN_PEER_WIRES: Mapping[str, str | None] = MappingProxyType({
    "sre": "sre.classifications_ready/v1",
    "qdp": "qdp-handoff/v1",
    "group_a": "group-a-to-b/v1",
    "group_b": None,
    "group_c": None,
    "d1": None,
    "source_policy": None,
    "patterns": None,
})
PORT_READ_METHODS: Mapping[str, str] = MappingProxyType({
    "sre": "read_current_relevance",
    "qdp": "read_qualified_membership",
    "group_a": "read_completed_analysis",
    "group_b": "read_completed_analysis",
    "group_c": "read_persisted_metric",
    "d1": "read_canonical_video",
    "source_policy": "read_current_policy",
})


class CapabilityState(str, Enum):
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE = "incompatible"
    DECLARED = "declared"  # can attempt read; does NOT prove source facts


@dataclass(frozen=True, slots=True)
class PortCapability:
    port: str
    state: CapabilityState
    adapter_schema: str | None = None
    peer_schema: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if type(self.port) is not str or self.port not in PORT_SCHEMAS:
            raise AnalyticsBoundaryError("invalid_port", "Unknown UAE port")
        if type(self.state) is not CapabilityState:
            raise AnalyticsBoundaryError("invalid_capability", "Unknown capability state")
        for value in (self.adapter_schema, self.peer_schema):
            if value is not None and (type(value) is not str or not 1 <= len(value) <= 128):
                raise AnalyticsBoundaryError("invalid_capability", "Invalid schema version")
        if self.state is CapabilityState.DECLARED:
            if self.adapter_schema is None or self.reason_code is not None:
                raise AnalyticsBoundaryError("invalid_capability", "Declared port needs schema and no error")
        elif (type(self.reason_code) is not str
              or not self.reason_code or len(self.reason_code) > 80):
            raise AnalyticsBoundaryError("invalid_capability", "Unavailable/incompatible requires reason")


@dataclass(frozen=True, slots=True)
class PortStatus:
    port: str
    state: CapabilityState
    expected_adapter_schema: str
    observed_adapter_schema: str | None = None
    expected_peer_schema: str | None = None
    observed_peer_schema: str | None = None
    reason_code: str | None = None

    @property
    def can_attempt_read(self) -> bool:
        return self.state is CapabilityState.DECLARED

    @property
    def qualified(self) -> bool:
        """A capability report can *never* qualify a publication."""
        return False


@runtime_checkable
class SREReader(Protocol):
    def capability(self) -> PortCapability: ...
    def read_current_relevance(self, scope: InputScope) -> EvidenceRead: ...


@runtime_checkable
class QDPReader(Protocol):
    def capability(self) -> PortCapability: ...
    def read_qualified_membership(self, scope: InputScope) -> EvidenceRead: ...


@runtime_checkable
class GroupAReader(Protocol):
    def capability(self) -> PortCapability: ...
    def read_completed_analysis(self, scope: InputScope) -> EvidenceRead: ...


@runtime_checkable
class GroupBReader(Protocol):
    def capability(self) -> PortCapability: ...
    def read_completed_analysis(self, scope: InputScope) -> EvidenceRead: ...


@runtime_checkable
class GroupCReader(Protocol):
    def capability(self) -> PortCapability: ...
    def read_persisted_metric(self, scope: InputScope) -> EvidenceRead: ...


@runtime_checkable
class D1Reader(Protocol):
    def capability(self) -> PortCapability: ...
    def read_canonical_video(self, scope: InputScope) -> EvidenceRead: ...


@runtime_checkable
class SourcePolicyReader(Protocol):
    def capability(self) -> PortCapability: ...
    def read_current_policy(self, scope: InputScope) -> EvidenceRead: ...


@runtime_checkable
class PatternsPublisher(Protocol):
    def capability(self) -> PortCapability: ...
    def publish_manifest(self, pointer: PatternsHandoffPointer) -> str: ...


# Protocols only constrain structural shape. Real adapters MUST validate current
# DB source/policy, execution mode, QDP item, version and artifact hashes.
PROTOCOLS: Mapping[str, type] = MappingProxyType({
    "sre": SREReader,
    "qdp": QDPReader,
    "group_a": GroupAReader,
    "group_b": GroupBReader,
    "group_c": GroupCReader,
    "d1": D1Reader,
    "source_policy": SourcePolicyReader,
    "patterns": PatternsPublisher,
})


def _status(port: str, state: CapabilityState, reason: str | None = None,
            observed_adapter_schema: str | None = None,
            observed_peer_schema: str | None = None) -> PortStatus:
    return PortStatus(
        port, state, PORT_SCHEMAS[port], observed_adapter_schema,
        KNOWN_PEER_WIRES[port], observed_peer_schema, reason,
    )


@dataclass(frozen=True, slots=True)
class UAEPorts:
    """All optional, dependency-free. Explicitly inject concrete adapters.

    No auto-discovery/import of feature branch implementations. Every capability
    call MUST be read-only metadata; a later DB adapter must verify source facts.
    """

    sre: SREReader | None = None
    qdp: QDPReader | None = None
    group_a: GroupAReader | None = None
    group_b: GroupBReader | None = None
    group_c: GroupCReader | None = None
    d1: D1Reader | None = None
    source_policy: SourcePolicyReader | None = None
    patterns: PatternsPublisher | None = None

    def status(self, port: str) -> PortStatus:
        if type(port) is not str or port not in PORT_SCHEMAS:
            raise AnalyticsBoundaryError("invalid_port", "Unknown UAE port")
        adapter: Any = getattr(self, port)
        if adapter is None:
            return _status(port, CapabilityState.UNAVAILABLE, "not_connected")
        try:
            if not isinstance(adapter, PROTOCOLS[port]):
                return _status(port, CapabilityState.INCOMPATIBLE, "missing_protocol_methods")
            candidate = adapter.capability()
        except Exception:
            # A probe may contain credentials or DB details in its error.
            return _status(port, CapabilityState.UNAVAILABLE, "probe_failed")
        if not isinstance(candidate, PortCapability) or candidate.port != port:
            return _status(port, CapabilityState.INCOMPATIBLE, "invalid_capability")
        if candidate.state is CapabilityState.UNAVAILABLE:
            return _status(port, CapabilityState.UNAVAILABLE, candidate.reason_code,
                           candidate.adapter_schema, candidate.peer_schema)
        if candidate.state is CapabilityState.INCOMPATIBLE:
            return _status(port, CapabilityState.INCOMPATIBLE, candidate.reason_code,
                           candidate.adapter_schema, candidate.peer_schema)
        if candidate.adapter_schema != PORT_SCHEMAS[port]:
            return _status(port, CapabilityState.INCOMPATIBLE, "adapter_schema_mismatch",
                           candidate.adapter_schema, candidate.peer_schema)
        expected_peer = KNOWN_PEER_WIRES[port]
        if expected_peer is not None and candidate.peer_schema != expected_peer:
            return _status(port, CapabilityState.INCOMPATIBLE, "peer_schema_mismatch",
                           candidate.adapter_schema, candidate.peer_schema)
        # Unknown B/C output wire is not silently invented. This is just a local
        # adapter declaration; qualified evidence always requires later checks.
        return _status(port, CapabilityState.DECLARED, None,
                       candidate.adapter_schema, candidate.peer_schema)

    def report(self) -> tuple[PortStatus, ...]:
        return tuple(self.status(port) for port in PORT_SCHEMAS)

    def require(self, port: str) -> Any:
        state = self.status(port)
        if state.state is CapabilityState.UNAVAILABLE:
            raise AnalyticsBoundaryError("dependency_unavailable", "Peer not usable: " + port)
        if state.state is CapabilityState.INCOMPATIBLE:
            raise AnalyticsBoundaryError("dependency_incompatible", "Peer contract mismatch: " + port)
        return getattr(self, port)

    def read(self, port: str, scope: InputScope) -> EvidenceRead:
        """Call one injected reader; the result remains an untrusted evidence *claim*.

        This step checks only DTO/port/scope/schema integrity. It intentionally
        does NOT interpret located as qualified or check the DB/policy itself.
        """
        if type(port) is not str or port not in PORT_READ_METHODS:
            raise AnalyticsBoundaryError("invalid_port", "Port is not an evidence reader")
        if not isinstance(scope, InputScope):
            raise AnalyticsBoundaryError("invalid_scope", "Expected InputScope")
        adapter = self.require(port)
        method = getattr(adapter, PORT_READ_METHODS[port])
        try:
            result = method(scope)
        except AnalyticsBoundaryError:
            raise
        except Exception as exc:
            raise AnalyticsBoundaryError("dependency_read_failed", "Peer read failed: " + port) from exc
        if not isinstance(result, EvidenceRead):
            raise AnalyticsBoundaryError("invalid_peer_response", "Expected typed EvidenceRead")
        if result.port != port or result.scope != scope:
            raise AnalyticsBoundaryError("lineage_mismatch", "Peer evidence scope mismatch")
        if result.state is ReadState.LOCATED:
            reference: EvidenceRef = result.reference  # type: ignore[assignment]
            expected = KNOWN_PEER_WIRES[port]
            if expected is not None and reference.producer_schema != expected:
                raise AnalyticsBoundaryError("peer_schema_mismatch", "Unexpected producer record schema")
        return result
