"""Group A pure orchestration readiness, isolated from I/O and paid providers.

Planning validates a QDP pointer and a current, authoritative peer snapshot
through injected ports; this stage NEVER creates tasks or writes A003/A004.
Durable queue/inbox/ACK and live start are reserved for GA-MOD-03/04.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .adapters import disconnected_ports
from .config import GASettings
from .contracts_v1 import (ContractViolation, ensure_same_lineage,
                           media_manifest_v1, qdp_handoff_v1)
from .ports import (GAPorts, PeerCapability, REQUIRED_EXECUTION_PORTS,
                    STATE_AVAILABLE, unavailable)

SCHEMA = "group-a-plan/v1"


@dataclass(frozen=True, slots=True)
class Plan:
    state: str
    reason_codes: tuple[str, ...]
    project_id: str | None
    qdp_run_id: str | None
    video_id: int | None
    manifest_sha256: str | None

    def wire(self) -> dict:
        return {
            "schema_version": SCHEMA, "state": self.state,
            "reason_codes": list(self.reason_codes),
            "project_id": self.project_id, "qdp_run_id": self.qdp_run_id,
            "video_id": self.video_id, "manifest_sha256": self.manifest_sha256,
        }


class NotReady(RuntimeError):
    def __init__(self, codes: tuple[str, ...]):
        self.code = "group_a_not_ready"
        self.reason_codes = codes
        super().__init__("Group A has no live QDP integration: " + ",".join(codes))


def _capability(adapters: GAPorts, port: str) -> PeerCapability:
    """Hostile adapter payloads cannot grant capabilities to a different port."""
    adapter = getattr(adapters, port)
    try:
        value = adapter.capability()
    except (AttributeError, RuntimeError, ValueError):
        return unavailable(port, "capability_probe_failed")
    if not isinstance(value, PeerCapability) or value.port != port:
        return unavailable(port, "invalid_capability")
    return value


def readiness(adapters: GAPorts, settings: GASettings) -> tuple[str, ...]:
    """Returns stable deterministic refusal codes, no dynamic imports."""
    reasons = []
    if not settings.enabled:
        reasons.append("module_disabled")
    if not settings.qdp_handoff_enabled:
        reasons.append("qdp_handoff_disabled")
    for port in REQUIRED_EXECUTION_PORTS:
        value = _capability(adapters, port)
        if value.state != STATE_AVAILABLE:
            reasons.append(port + "_" + value.state)
    # No wired transaction boundary in GA-MOD-02 even if injected fakes look
    # complete. A true live activation also needs the versioned GA SQL/inbox.
    reasons.append("durable_handoff_not_installed")
    return tuple(reasons)


class GroupAService:
    def __init__(self, ports: GAPorts | None = None,
                 settings: GASettings | None = None):
        self.ports = disconnected_ports() if ports is None else ports
        self.settings = GASettings() if settings is None else settings
        if not isinstance(self.ports, GAPorts) or not isinstance(self.settings, GASettings):
            raise TypeError("Explicit Group A ports and settings required")

    def capabilities(self) -> dict:
        observed = {
            key: _capability(self.ports, key).wire()
            for key in ("qdp", "media", "transcript", "vision", "results", "handoff", "queue")
        }
        codes = readiness(self.ports, self.settings)
        return {
            "schema_version": "group-a-port-readiness/v1",
            "state": "unavailable" if codes else "available",
            "reason_codes": list(codes),
            "peers": observed,
            "flags": {
                "enabled": self.settings.enabled,
                "qdp_handoff_enabled": self.settings.qdp_handoff_enabled,
                "allow_psi_reuse": self.settings.allow_psi_reuse,
            },
        }

    def inspect(self, pointer: Mapping[str, Any]) -> Plan:
        """Dry-run only; always no durable writes, no background workers."""
        p = qdp_handoff_v1(pointer)
        reasons = readiness(self.ports, self.settings)
        if reasons:
            return Plan("unavailable", reasons, p["project_id"], p["qdp_run_id"],
                        p["video_id"], None)
        # This branch is currently unreachable until future GA stage explicitly
        # replaces the durable gate. Keep non-mutating peer validation central.
        return self.validate_authoritative_pointer(p)

    def validate_authoritative_pointer(self, pointer: Mapping[str, Any]) -> Plan:
        """Pure planning against injected readers; never accepts caller's event alone.

        Callable for adapter contract/unit tests; NOT an activation mechanism.
        """
        p = qdp_handoff_v1(pointer)
        qcap, mcap = _capability(self.ports, "qdp"), _capability(self.ports, "media")
        if qcap.state != STATE_AVAILABLE or mcap.state != STATE_AVAILABLE:
            missing = tuple(x + "_unavailable" for x, c in (("qdp", qcap), ("media", mcap))
                            if c.state != STATE_AVAILABLE)
            return Plan("unavailable", missing, p["project_id"],
                        p["qdp_run_id"], p["video_id"], None)
        try:
            current = self.ports.qdp.read_preparation(p)
        except (OSError, RuntimeError, TimeoutError, ConnectionError):
            return Plan("unavailable", ("qdp_read_failed",),
                        p["project_id"], p["qdp_run_id"], p["video_id"], None)
        if current is None:
            return Plan("blocked", ("preparation_missing",),
                        p["project_id"], p["qdp_run_id"], p["video_id"], None)
        if type(current) is not dict or set(current) != {"pointer", "state"}:
            raise ContractViolation("invalid_authoritative_preparation", "qdp.read_preparation")
        live = qdp_handoff_v1(current["pointer"])
        if live != p or current["state"] != "ready":
            return Plan("blocked", ("preparation_changed_or_revoked",),
                        p["project_id"], p["qdp_run_id"], p["video_id"], None)
        try:
            raw_manifest = self.ports.media.read_verified_manifest(p)
        except (OSError, RuntimeError, TimeoutError, ConnectionError):
            return Plan("unavailable", ("media_read_failed",),
                        p["project_id"], p["qdp_run_id"], p["video_id"], None)
        if raw_manifest is None:
            return Plan("blocked", ("verified_media_missing",),
                        p["project_id"], p["qdp_run_id"], p["video_id"], None)
        manifest = media_manifest_v1(raw_manifest)
        ensure_same_lineage(p, manifest)
        return Plan("plan_only", ("durable_handoff_not_installed",),
                    p["project_id"], p["qdp_run_id"], p["video_id"],
                    manifest["manifest_sha256"])

    def start(self, pointer: Mapping[str, Any]) -> None:
        """No fake jobs: a durable GA-MOD-04 inbox MUST replace this gate."""
        qdp_handoff_v1(pointer)
        raise NotReady(readiness(self.ports, self.settings))

    def make_qdp_receiver(self, connect, qdp_factory, media_factory):
        """Explicitly inject an opt-in durable QDP intake. No auto-dispatch.

        A stored inbox/queue receipt does not enable analysis workers or imply
        media bytes have been verified by the future GA-MOD-05 resolver.
        """
        from .handoff import GroupAInboxReceiver
        return GroupAInboxReceiver(connect, qdp_factory, media_factory,
                                  settings=self.settings)
