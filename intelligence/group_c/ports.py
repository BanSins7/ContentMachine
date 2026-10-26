"""GC-MOD-02: Group C integration ports, versioned handoff and fail-closed wiring.

Only dependency-free Python Protocol declarations and guards live here. None
of the ports can query SQL, create work, use a provider or calculate scores.
Real adapters must prove project-scoped currentness before returning records.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence
from uuid import UUID

from .contracts import ContractError


class CapabilityState(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE = "incompatible"


# These are *adapter interface* revisions, not a claim that peer modules
# already implement them. Only QDP's wire handoff schema has a known peer name.
PORT_SCHEMAS = MappingProxyType({
    "qdp": "qdp-handoff/v1",
    "d1": "group-c-d1-reader/v1",
    "registry": "group-c-creator-topic/v1",
    "source_policy": "group-c-source-policy/v1",
    "cohorts": "group-c-cohort-approval/v1",
    "config": "group-c-config-approval/v1",
    "analytics": "group-c-analytics-outbox/v1",
})
# An adapter that only claims AVAILABLE but cannot perform the required reads
# is structurally incompatible even if its version string matches.
_REQUIRED_METHODS = MappingProxyType({
    "qdp": ("read_exact_preparation",),
    "d1": ("read_video", "read_snapshot", "read_previous_snapshots", "read_publication"),
    "registry": ("read_creator", "read_topic", "read_project_video"),
    "source_policy": ("read_effective_policy", "assert_eligible"),
    "cohorts": ("read_cohort", "read_members", "read_approval"),
    "config": ("read_config", "read_approval"),
    "analytics": ("enqueue_persisted_result",),
})
_PROJECT_RE = re.compile(r"[0-9a-f]{32}\Z")
_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


def _uuid(value: object, name: str) -> None:
    if type(value) is not str:
        raise ContractError("invalid_" + name, "Canonical UUID string required")
    try:
        canonical = str(UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ContractError("invalid_" + name, "Canonical UUID string required") from exc
    if canonical != value:
        raise ContractError("invalid_" + name, "Canonical UUID string required")


@dataclass(frozen=True)
class QDPHandoffNotice:
    """Exact QDP qdp-handoff/v1 wire envelope, *not* a verified C input.

    Constructing this envelope proves only syntax, NOT QDP readiness,
    DB/source identity, source-policy eligibility or durable receipt.
    A future GC-MOD-04 adapter must re-read QDP and D1 before accepting it.
    """
    event_id: str
    project_id: str
    qdp_run_id: str
    video_id: int
    analysis_group: str
    input_sha256: str
    schema_version: str = "qdp-handoff/v1"

    def __post_init__(self) -> None:
        _uuid(self.event_id, "event_id")
        _uuid(self.qdp_run_id, "qdp_run_id")
        if type(self.project_id) is not str or not _PROJECT_RE.fullmatch(self.project_id):
            raise ContractError("invalid_project_id", "Expected lower-case 32-hex project ID")
        if type(self.video_id) is not int or self.video_id < 1:
            raise ContractError("invalid_video_id", "Expected positive D1 videos.id")
        if self.analysis_group != "C":
            raise ContractError("invalid_analysis_group", "Only analysis_group=C is handled by Group C")
        if type(self.input_sha256) is not str or not _SHA_RE.fullmatch(self.input_sha256):
            raise ContractError("invalid_input_sha256", "Expected lower-case SHA256")
        if self.schema_version != PORT_SCHEMAS["qdp"]:
            raise ContractError("unsupported_handoff_schema", "Unsupported QDP handoff version")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> QDPHandoffNotice:
        """Strict wire shape; never assume C-normalized source/snapshot IDs."""
        if not isinstance(payload, Mapping):
            raise ContractError("invalid_handoff_payload", "Object required")
        required = {"event_id", "project_id", "qdp_run_id", "video_id",
                    "analysis_group", "input_sha256", "schema_version"}
        if set(payload) != required or not all(type(key) is str for key in payload):
            raise ContractError("invalid_handoff_payload", "Unexpected or absent QDP wire fields")
        return cls(**{key: payload[key] for key in required})


@dataclass(frozen=True)
class IntegrationCapability:
    """Observed version, not a hard-coded declaration of peer readiness."""
    port: str
    state: CapabilityState = CapabilityState.UNAVAILABLE
    observed_schema: str | None = None
    reason_code: str = "not_probed"

    def __post_init__(self) -> None:
        if type(self.port) is not str or self.port not in PORT_SCHEMAS:
            raise ContractError("invalid_port", "Unknown Group C port")
        if type(self.state) is not CapabilityState:
            raise ContractError("invalid_capability_state", "Unknown capability state")
        if (self.observed_schema is not None
                and (type(self.observed_schema) is not str or not self.observed_schema.strip())):
            raise ContractError("invalid_observed_schema", "Invalid observed schema")
        if type(self.reason_code) is not str or not self.reason_code.strip():
            raise ContractError("invalid_reason_code", "Reason must be nonempty")
        if self.state is CapabilityState.AVAILABLE and self.observed_schema is None:
            raise ContractError("missing_observed_schema", "Available requires observed peer schema")


def require_available(capability: IntegrationCapability, port: str) -> IntegrationCapability:
    """Reject unavailable, miswired and schema-incompatible peer capabilities."""
    if type(port) is not str or port not in PORT_SCHEMAS:
        raise ContractError("invalid_port", "Unknown Group C port")
    if not isinstance(capability, IntegrationCapability):
        raise ContractError("dependency_incompatible", "Malformed capability response")
    if capability.port != port:
        raise ContractError("dependency_incompatible", "Capability returned for wrong port")
    if capability.state is CapabilityState.UNAVAILABLE:
        raise ContractError("dependency_unavailable", "Required Group C dependency unavailable: " + port)
    if (capability.state is CapabilityState.INCOMPATIBLE
            or capability.observed_schema != PORT_SCHEMAS[port]):
        raise ContractError("dependency_incompatible", "Required Group C peer version incompatible: " + port)
    return capability


class QDPPreparationReader(Protocol):
    """Read actual prepared QDP C input; no event envelope -> snapshot coercion."""
    def capability(self, conn: Any) -> IntegrationCapability: ...
    def read_exact_preparation(self, conn: Any, notice: QDPHandoffNotice) -> Mapping[str, Any]: ...


class D1FactsReader(Protocol):
    """Canonical D1 records; never a duplicated metric or publication store."""
    def capability(self, conn: Any) -> IntegrationCapability: ...
    def read_video(self, conn: Any, video_id: int) -> Mapping[str, Any]: ...
    def read_snapshot(self, conn: Any, video_id: int, snapshot_id: int) -> Mapping[str, Any]: ...
    def read_previous_snapshots(self, conn: Any, video_id: int, source_id: str,
                                metric_definition: str, metric_version: str,
                                before: datetime, limit: int) -> Sequence[Mapping[str, Any]]: ...
    def read_publication(self, conn: Any, video_id: int,
                         metadata_id: int) -> Mapping[str, Any]: ...


class CreatorTopicRegistry(Protocol):
    """Registry of approved platform/creator/topic and project-video joins."""
    def capability(self, conn: Any) -> IntegrationCapability: ...
    def read_creator(self, conn: Any, creator_id: int) -> Mapping[str, Any]: ...
    def read_topic(self, conn: Any, topic_id: int) -> Mapping[str, Any]: ...
    def read_project_video(self, conn: Any, project_id: str, video_id: int) -> Mapping[str, Any]: ...


class SourcePolicyReader(Protocol):
    """Current source permission and expiry at BOTH write and read points."""
    def capability(self, conn: Any) -> IntegrationCapability: ...
    def read_effective_policy(self, conn: Any, source_id: str,
                              policy_version_id: int | None) -> Mapping[str, Any]: ...
    def assert_eligible(self, conn: Any, source_id: str, video_id: int,
                        snapshot_ids: Sequence[int], metadata_id: int,
                        cohort_ids: Sequence[int]) -> Mapping[str, Any]: ...


class CohortApprovalReader(Protocol):
    """Frozen, scoped peer membership, evidence and human review (no defaults)."""
    def capability(self, conn: Any) -> IntegrationCapability: ...
    def read_cohort(self, conn: Any, cohort_id: int) -> Mapping[str, Any]: ...
    def read_members(self, conn: Any, cohort_id: int) -> Sequence[Mapping[str, Any]]: ...
    def read_approval(self, conn: Any, cohort_id: int) -> Mapping[str, Any] | None: ...


class ConfigApprovalReader(Protocol):
    """Versioned alpha/k/prior policy, weights and explicit human approval."""
    def capability(self, conn: Any) -> IntegrationCapability: ...
    def read_config(self, conn: Any, config_id: int) -> Mapping[str, Any]: ...
    def read_approval(self, conn: Any, config_id: int) -> Mapping[str, Any] | None: ...


class AnalyticsPublisher(Protocol):
    """Transactional outbox for persisted C result IDs, NOT direct rescoring."""
    def capability(self, conn: Any) -> IntegrationCapability: ...
    def enqueue_persisted_result(self, conn: Any, project_id: str,
                                 calculated_metric_id: int,
                                 idempotency_sha256: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class GroupCPorts:
    """All peers optional, absent by default. Adapters injected explicitly.

    require() does not authorize processing by itself. The returned adapter's
    capability is only trustworthy when the implementation probes live data.
    """
    qdp: QDPPreparationReader | None = None
    d1: D1FactsReader | None = None
    registry: CreatorTopicRegistry | None = None
    source_policy: SourcePolicyReader | None = None
    cohorts: CohortApprovalReader | None = None
    config: ConfigApprovalReader | None = None
    analytics: AnalyticsPublisher | None = None

    def capability(self, port: str, conn: Any) -> IntegrationCapability:
        if type(port) is not str or port not in PORT_SCHEMAS:
            raise ContractError("invalid_port", "Unknown Group C port")
        adapter = getattr(self, port)
        if adapter is None:
            return IntegrationCapability(port=port)
        method = getattr(adapter, "capability", None)
        if not callable(method):
            raise ContractError("dependency_incompatible", "Peer lacks capability probe: " + port)
        try:
            cap = method(conn)
        except Exception as exc:
            raise ContractError("dependency_unavailable", "Peer capability probe failed: " + port) from exc
        if not isinstance(cap, IntegrationCapability):
            raise ContractError("dependency_incompatible", "Peer returned malformed capability: " + port)
        if cap.port != port:
            raise ContractError("dependency_incompatible", "Peer returned incorrect port: " + port)
        return cap

    def require(self, port: str, conn: Any) -> Any:
        cap = self.capability(port, conn)
        require_available(cap, port)
        adapter = getattr(self, port)
        if not all(callable(getattr(adapter, name, None))
                   for name in _REQUIRED_METHODS[port]):
            raise ContractError("dependency_incompatible", "Required peer methods missing: " + port)
        return adapter

    def require_all(self, ports: Sequence[str], conn: Any) -> Mapping[str, Any]:
        """Fail closed on FIRST missing peer; no partial dispatch/mutation."""
        if type(ports) not in (list, tuple) or not ports:
            raise ContractError("invalid_ports", "Nonempty port sequence required")
        # Resolve all before returning; callers can only use returned map if all pass.
        selected: dict[str, Any] = {}
        for port in ports:
            if port in selected:
                raise ContractError("duplicate_port", "Duplicate port requested")
            selected[port] = self.require(port, conn)
        return MappingProxyType(selected)
