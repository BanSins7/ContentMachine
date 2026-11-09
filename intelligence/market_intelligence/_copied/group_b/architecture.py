"""Owned Group B runtime domains; declarations, not running services."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class DomainBoundary:
    responsibility: str
    ports: tuple[str, ...]
    stage: str

BOUNDARIES = {
    "intake": DomainBoundary("Durable QDP inbox and exact A pointer", ("qdp", "storage", "group_a"), "GB-MOD-04/05"),
    "classify": DomainBoundary("Semantic B1/B2/B3/B4/B6/B7/B8", ("model", "budget", "media"), "GB-MOD-09/13-20"),
    "deterministic": DomainBoundary("Local B5 and media sampling", ("group_a", "media"), "GB-MOD-12/15"),
    "validation": DomainBoundary("Evidence and schema admission, never model trust", ("group_a", "media"), "GB-MOD-21"),
    "storage": DomainBoundary("Atomic B analyses, versioning and outbox", ("storage",), "GB-MOD-03/22"),
    "review": DomainBoundary("Human decisions, immutable revisions", ("review", "storage"), "GB-MOD-23"),
    "delivery": DomainBoundary("Analytics handoff from approved completed B", ("analytics", "storage"), "GB-MOD-24/25"),
}
