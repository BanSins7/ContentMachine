"""Deterministic no-op ports for installations lacking upstream modules.

Never import QDP/PSI internals, hit the network, or open a database.
Unavailable I/O raises before it can create fabricated work.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..ports import GAPorts, PeerCapability, unavailable

class PortUnavailable(RuntimeError):
    def __init__(self, port: str):
        super().__init__(port + ": adapter_not_connected")
        self.port = port
        self.code = "adapter_not_connected"


class MissingAdapter:
    def __init__(self, port: str):
        if port not in ("qdp", "media", "transcript", "vision", "results", "handoff", "queue"):
            raise ValueError("Unrecognized port")
        self.port = port

    def capability(self) -> PeerCapability:
        return unavailable(self.port)

    def read_preparation(self, pointer):
        raise PortUnavailable(self.port)

    def read_verified_manifest(self, pointer):
        raise PortUnavailable(self.port)

    def read_verified(self, media_sha256):
        raise PortUnavailable(self.port)

    def write_immutable(self, result):
        raise PortUnavailable(self.port)

    def publish_once(self, event):
        raise PortUnavailable(self.port)

    def enqueue_once(self, task):
        raise PortUnavailable(self.port)


class UTCClock:
    def utcnow(self) -> datetime:
        return datetime.now(timezone.utc)


def disconnected_ports() -> GAPorts:
    return GAPorts(
        qdp=MissingAdapter("qdp"), media=MissingAdapter("media"),
        transcript=MissingAdapter("transcript"), vision=MissingAdapter("vision"),
        results=MissingAdapter("results"), handoff=MissingAdapter("handoff"),
        clock=UTCClock(), queue=MissingAdapter("queue"),
    )
