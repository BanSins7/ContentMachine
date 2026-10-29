"""FIX-00: QDP-owned, dependency-free integration boundary declarations.

These Protocols describe obligations of future adapters, NOT assurances that
SRE/Collector/Group A/B/C/Analytics already implement them. Concrete adapters
and authoritative database validation belong to FIX-01..FIX-15.
No imports of external module implementations or network/provider calls here.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from ..contracts import QDPError

_PROJECT_ID = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class CapabilityState(str, Enum):
    """An unknown or missing peer is never treated as available."""
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True)
class IntegrationCapability:
    """Observed capability, not a guessed peer schema/version."""
    port: str
    state: CapabilityState = CapabilityState.UNAVAILABLE
    observed_schema: str | None = None
    reason_code: str = "not_probed"

    def __post_init__(self) -> None:
        if not self.port or type(self.port) is not str:
            raise ValueError("port must be nonempty")
        if not isinstance(self.state, CapabilityState):
            raise ValueError("state must be a CapabilityState")


def require_available(capability: IntegrationCapability) -> None:
    """Explicit fail-closed gate for future DB/consumer adapters."""
    if capability.state is CapabilityState.AVAILABLE:
        return
    code = ("dependency_incompatible"
            if capability.state is CapabilityState.INCOMPATIBLE
            else "dependency_unavailable")
    raise QDPError(code, "Required integration capability is not available: "
                   + capability.port, 503)


@dataclass(frozen=True)
class QDPHandoffPointer:
    """QDP-owned proposed qdp-handoff/v1 pointer (not a peer's existing DTO).

    This is a reference to a committed preparation. Consumers must re-read
    current QDP state by project/run/video/group/hash before accepting it.
    Constructing this DTO does NOT authorize or dispatch external work.
    """
    event_id: str
    project_id: str
    qdp_run_id: str
    video_id: int
    analysis_group: str
    input_sha256: str
    schema_version: str = "qdp-handoff/v1"

    def __post_init__(self) -> None:
        for field in ("event_id", "qdp_run_id"):
            value = getattr(self, field)
            try:
                if str(uuid.UUID(value)) != value:
                    raise ValueError("noncanonical")
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError(field + " must be a canonical UUID") from exc
        if not isinstance(self.project_id, str) or not _PROJECT_ID.fullmatch(self.project_id):
            raise ValueError("invalid project ID")
        if type(self.video_id) is not int or self.video_id <= 0:
            raise ValueError("invalid canonical video ID")
        if self.analysis_group not in ("A", "B", "C"):
            raise ValueError("invalid analysis group")
        if not isinstance(self.input_sha256, str) or not _DIGEST.fullmatch(self.input_sha256):
            raise ValueError("invalid preparation SHA256")
        if self.schema_version != "qdp-handoff/v1":
            raise ValueError("unsupported QDP handoff schema version")


class SreRunReader(Protocol):
    """Peer adapter must read authoritative project-scoped SRE records."""
    def capability(self, conn: Any) -> IntegrationCapability: ...
    def list_completed_live(self, conn: Any, project_id: str,
                            limit: int, cursor: str | None) -> Sequence[Mapping[str, Any]]: ...
    def read_completed_live(self, conn: Any, project_id: str,
                            sre_run_id: str) -> Mapping[str, Any]: ...
    def read_expected_manifest(self, conn: Any, project_id: str,
                               sre_run_id: str) -> Mapping[str, Any]: ...
    def read_results(self, conn: Any, project_id: str,
                     sre_run_id: str, video_id: int | None = None) -> Sequence[Mapping[str, Any]]: ...
    def read_effective_human_labels(self, conn: Any, project_id: str,
                                    sre_run_id: str) -> Sequence[Mapping[str, Any]]: ...


class D1VideoReader(Protocol):
    """D1 owns source provenance. QDP must never create a second video catalog."""
    def capability(self, conn: Any) -> IntegrationCapability: ...
    def read_canonical_video(self, conn: Any, video_id: int) -> Mapping[str, Any]: ...
    def read_provenance(self, conn: Any, project_id: str, video_id: int,
                        content_version_id: int) -> Mapping[str, Any]: ...
    def read_exact_metadata(self, conn: Any, video_id: int, source_id: str,
                            metadata_observation_id: int) -> Mapping[str, Any] | None: ...
    def read_metric_observations(self, conn: Any, video_id: int, source_id: str,
                                 source_policy_version_id: int,
                                 limit: int = 20) -> Sequence[Mapping[str, Any]]: ...
    def read_media_asset(self, conn: Any, video_id: int, source_id: str,
                         media_asset_id: int) -> Mapping[str, Any] | None: ...


class PreparationConsumer(Protocol):
    """Consumer ACK means durable inbox/job commit, NOT completed analysis."""
    def capability(self, conn: Any) -> IntegrationCapability: ...
    def receive(self, conn: Any, pointer: QDPHandoffPointer,
                event_payload_sha256: str) -> Mapping[str, Any]: ...
    def read_receipt(self, conn: Any, event_id: str) -> Mapping[str, Any] | None: ...


class AnalyticsResultReader(Protocol):
    """Read verified completed outputs only; never synthesize missing A/B/C."""
    def capability(self, conn: Any) -> IntegrationCapability: ...
    def read_completed(self, conn: Any, project_id: str,
                       qdp_run_id: str, video_id: int) -> Mapping[str, Any] | None: ...
