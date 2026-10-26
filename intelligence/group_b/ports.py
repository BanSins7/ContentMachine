"""Standalone Group B Protocol ports; no peer integration or model calls.

Proposed version strings must be negotiated by later real adapters. Declared
capability is NOT proof of a complete upstream/downstream handshake.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable

from .contracts import GroupBInputPointer
from .errors import DependencyContractMismatch, DependencyProbeFailed, DependencyUnavailable

EXPECTED_CONTRACTS: Mapping[str, str] = {
    "qdp": "qdp-handoff/v1",
    "group_a": "group-a-to-b/v1",
    "media": "group-b-media/v1",
    "model": "group-b-model/v1",
    "budget": "group-b-budget/v1",
    "storage": "group-b-storage/v1",
    "review": "group-b-review/v1",
    "analytics": "group-b-analytics/v1",
}
PORT_NAMES = tuple(EXPECTED_CONTRACTS)


class PortState(str, Enum):
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE = "incompatible"
    DECLARED = "declared"  # a local claim, never production readiness


@dataclass(frozen=True, slots=True)
class PortCapability:
    contract_version: str
    available: bool
    reason: str = ""

    def __post_init__(self):
        if type(self.contract_version) is not str or not self.contract_version:
            raise ValueError("Capability must declare a version")
        if type(self.available) is not bool:
            raise ValueError("available must be a bool")
        if type(self.reason) is not str or len(self.reason) > 128:
            raise ValueError("reason must be short text")
        if not self.available and not self.reason:
            raise ValueError("Unavailable capability requires a reason")


@dataclass(frozen=True, slots=True)
class PortStatus:
    name: str
    state: PortState
    expected_contract: str
    observed_contract: str | None = None
    reason: str = ""

    @property
    def can_attempt_handshake(self) -> bool:
        return self.state is PortState.DECLARED


@runtime_checkable
class CapabilityPort(Protocol):
    def capability(self) -> PortCapability: ...


@runtime_checkable
class QDPPort(CapabilityPort, Protocol):
    def fetch_preparation(self, *, project_id: str, video_id: int, qdp_run_id: str) -> Mapping[str, Any]: ...
    def acknowledge(self, *, event_id: str, durable_receipt: str) -> None: ...


@runtime_checkable
class GroupAPort(CapabilityPort, Protocol):
    def fetch_snapshot(self, *, pointer: GroupBInputPointer) -> Mapping[str, Any]: ...


@runtime_checkable
class MediaPort(CapabilityPort, Protocol):
    def fetch_manifest(self, *, pointer: GroupBInputPointer) -> Mapping[str, Any]: ...
    def access_allowed(self, *, pointer: GroupBInputPointer) -> bool: ...


@runtime_checkable
class ModelPort(CapabilityPort, Protocol):
    def quote(self, *, model: str, evidence_manifest: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def invoke_approved(self, *, approved_attempt: Mapping[str, Any], evidence_manifest: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class BudgetPort(CapabilityPort, Protocol):
    def reserve(self, *, attempt_id: str, quote: Mapping[str, Any], approval_id: str) -> str: ...
    def reconcile(self, *, attempt_id: str, provider_usage: Mapping[str, Any]) -> None: ...


@runtime_checkable
class StoragePort(CapabilityPort, Protocol):
    def commit_intake(self, *, event_id: str, pointer: GroupBInputPointer) -> str: ...
    def save_analysis(self, *, analysis: Mapping[str, Any]) -> int: ...


@runtime_checkable
class ReviewPort(CapabilityPort, Protocol):
    def submit_review(self, *, analysis_id: int, decision: Mapping[str, Any]) -> str: ...


@runtime_checkable
class AnalyticsPort(CapabilityPort, Protocol):
    def publish_completion(self, *, analysis_id: int, lineage: Mapping[str, Any]) -> str: ...


PROTOCOLS: Mapping[str, type] = {
    "qdp": QDPPort, "group_a": GroupAPort, "media": MediaPort,
    "model": ModelPort, "budget": BudgetPort, "storage": StoragePort,
    "review": ReviewPort, "analytics": AnalyticsPort,
}


@dataclass(frozen=True, slots=True)
class GroupBPorts:
    """Explicit injection only. Probe MUST be metadata-only and side-effect-free.

    Protocol methods are defined now but NOT invoked by status/require/report.
    A declared port still requires genuine upstream handshake and validation
    in GB-MOD-04/05/25. No implicit imports, DB writes or provider calls.
    """

    qdp: QDPPort | None = None
    group_a: GroupAPort | None = None
    media: MediaPort | None = None
    model: ModelPort | None = None
    budget: BudgetPort | None = None
    storage: StoragePort | None = None
    review: ReviewPort | None = None
    analytics: AnalyticsPort | None = None

    def status(self, name: str) -> PortStatus:
        if name not in EXPECTED_CONTRACTS:
            raise ValueError("Unknown Group B port")
        expected = EXPECTED_CONTRACTS[name]
        port = getattr(self, name)
        if port is None:
            return PortStatus(name, PortState.UNAVAILABLE, expected, reason="not_connected")
        if not isinstance(port, PROTOCOLS[name]):
            return PortStatus(name, PortState.INCOMPATIBLE, expected, reason="missing_protocol_methods")
        try:
            observed = port.capability()
        except Exception:
            # Never include an upstream exception: it may contain secrets or media.
            return PortStatus(name, PortState.UNAVAILABLE, expected, reason="probe_failed")
        if not isinstance(observed, PortCapability):
            return PortStatus(name, PortState.INCOMPATIBLE, expected, reason="invalid_probe_response")
        if observed.contract_version != expected:
            return PortStatus(name, PortState.INCOMPATIBLE, expected,
                              observed_contract=observed.contract_version, reason="version_mismatch")
        if not observed.available:
            return PortStatus(name, PortState.UNAVAILABLE, expected,
                              observed_contract=observed.contract_version, reason="provider_unavailable")
        return PortStatus(name, PortState.DECLARED, expected, observed_contract=observed.contract_version)

    def require(self, name: str) -> CapabilityPort:
        status = self.status(name)
        if status.state is PortState.INCOMPATIBLE:
            raise DependencyContractMismatch(name)
        if status.state is PortState.UNAVAILABLE:
            if status.reason == "probe_failed":
                raise DependencyProbeFailed(name)
            raise DependencyUnavailable(name, status.reason)
        return getattr(self, name)

    def report(self) -> tuple[PortStatus, ...]:
        """Local metadata-only status. Not production readiness."""
        return tuple(self.status(name) for name in PORT_NAMES)
