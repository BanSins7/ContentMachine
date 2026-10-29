"""GA-owned dependency inversion contracts; importable without any peer packages.

Protocol signatures are integration obligations, NOT claims of installed peers.
All peer capabilities are observed through explicit adapters, never auto-detected
by importing implementation-private QDP/PSI/Group B modules.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, runtime_checkable

STATE_AVAILABLE = "available"
STATE_UNAVAILABLE = "unavailable"
STATE_INCOMPATIBLE = "incompatible"
STATES = frozenset((STATE_AVAILABLE, STATE_UNAVAILABLE, STATE_INCOMPATIBLE))

PEER_VERSIONS = {
    "qdp": "qdp-handoff/v1",
    "media": "ga-media-manifest/v1",
    "transcript": "transcript/v1",
    "vision": "visual-preparation/v1",
    "results": "group-a-assembly/v1",
    "handoff": "group-a-to-b/v1",
    "queue": "group-a-jobs/v1",
}
REQUIRED_EXECUTION_PORTS = ("qdp", "media", "results", "handoff", "queue")
OPTIONAL_PORTS = ("transcript", "vision")
ALL_PORTS = tuple(PEER_VERSIONS)


@dataclass(frozen=True, slots=True)
class PeerCapability:
    """A statement made by a specific adapter, not by a user-controlled event."""

    port: str
    state: str
    observed_version: str | None = None
    reason_code: str | None = None

    def __post_init__(self):
        if self.port not in PEER_VERSIONS:
            raise ValueError("Unknown Group A port")
        if self.state not in STATES:
            raise ValueError("Unknown capability state")
        if self.observed_version is not None and (
            type(self.observed_version) is not str or not self.observed_version
        ):
            raise ValueError("Invalid observed capability version")
        if self.reason_code is not None and (
            type(self.reason_code) is not str or not self.reason_code
        ):
            raise ValueError("Invalid capability reason")
        if self.state == STATE_AVAILABLE and (
            self.observed_version != PEER_VERSIONS[self.port] or self.reason_code is not None
        ):
            raise ValueError("Available capability requires exact version and no error")
        if self.state == STATE_INCOMPATIBLE and (
            self.observed_version is None or
            self.observed_version == PEER_VERSIONS[self.port] or
            self.reason_code is None
        ):
            raise ValueError("Incompatible capability requires a different version")
        if self.state == STATE_UNAVAILABLE and self.reason_code is None:
            raise ValueError("Unavailable capability requires a reason")

    def wire(self) -> dict:
        return {
            "port": self.port,
            "state": self.state,
            "expected_version": PEER_VERSIONS[self.port],
            "observed_version": self.observed_version,
            "reason_code": self.reason_code,
        }


def unavailable(port: str, reason: str = "adapter_not_connected") -> PeerCapability:
    return PeerCapability(port=port, state=STATE_UNAVAILABLE, reason_code=reason)


def negotiate(port: str, observed: str | None,
              unavailable_reason: str = "peer_missing") -> PeerCapability:
    """Version negotiation cannot silently downgrade or coerce the version."""
    if port not in PEER_VERSIONS:
        raise ValueError("Unknown Group A port")
    if observed is None:
        return unavailable(port, unavailable_reason)
    if type(observed) is not str or not observed:
        raise ValueError("Observed version must be nonempty string or None")
    if observed != PEER_VERSIONS[port]:
        return PeerCapability(port, STATE_INCOMPATIBLE, observed, "version_mismatch")
    return PeerCapability(port, STATE_AVAILABLE, observed)


@runtime_checkable
class QDPReader(Protocol):
    def capability(self) -> PeerCapability: ...
    def read_preparation(self, pointer: Mapping[str, Any]) -> Mapping[str, Any] | None: ...


@runtime_checkable
class MediaCatalog(Protocol):
    def capability(self) -> PeerCapability: ...
    def read_verified_manifest(self, pointer: Mapping[str, Any]) -> Mapping[str, Any] | None: ...


@runtime_checkable
class TranscriptReader(Protocol):
    def capability(self) -> PeerCapability: ...
    def read_verified(self, media_sha256: str) -> Mapping[str, Any] | None: ...


@runtime_checkable
class VisionReader(Protocol):
    def capability(self) -> PeerCapability: ...
    def read_verified(self, media_sha256: str) -> Mapping[str, Any] | None: ...


@runtime_checkable
class ResultWriter(Protocol):
    def capability(self) -> PeerCapability: ...
    def write_immutable(self, result: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class HandoffConsumer(Protocol):
    def capability(self) -> PeerCapability: ...
    def publish_once(self, event: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class Clock(Protocol):
    def utcnow(self) -> datetime: ...


@runtime_checkable
class TaskQueue(Protocol):
    def capability(self) -> PeerCapability: ...
    def enqueue_once(self, task: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class GAPorts:
    qdp: QDPReader
    media: MediaCatalog
    transcript: TranscriptReader
    vision: VisionReader
    results: ResultWriter
    handoff: HandoffConsumer
    clock: Clock
    queue: TaskQueue

    def capability_map(self) -> dict[str, PeerCapability]:
        observed = {}
        for name in ALL_PORTS:
            adapter = getattr(self, name)
            value = adapter.capability()
            if not isinstance(value, PeerCapability) or value.port != name:
                raise ValueError("Invalid capability returned by " + name)
            observed[name] = value
        return observed
