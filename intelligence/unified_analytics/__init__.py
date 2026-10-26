"""Unified Analytics standalone import surface (no live adapters or writes).

UA-MOD-02: capability/DTO definitions, NOT a cross-module qualified result.
No Group C scorer, peer imports, provider calls, DB connections or worker.
"""
from .contracts import (
    AnalyticsBoundaryError,
    UnifiedAnalyticsInputPointer,
    canonical_hash,
    content_type_for_kind,
)
from .configuration import UAEConfiguration
from .dto import (
    InputScope, EvidenceRef, EvidenceRead, ReadState, PatternsHandoffPointer,
)
from .ports import (
    CapabilityState, PortCapability, PortStatus, UAEPorts,
    SREReader, QDPReader, GroupAReader, GroupBReader, GroupCReader,
    D1Reader, SourcePolicyReader, PatternsPublisher,
)

__all__ = [
    "AnalyticsBoundaryError", "UnifiedAnalyticsInputPointer",
    "canonical_hash", "content_type_for_kind", "UAEConfiguration",
    "InputScope", "EvidenceRef", "EvidenceRead", "ReadState",
    "PatternsHandoffPointer", "CapabilityState", "PortCapability",
    "PortStatus", "UAEPorts", "SREReader", "QDPReader", "GroupAReader",
    "GroupBReader", "GroupCReader", "D1Reader", "SourcePolicyReader",
    "PatternsPublisher",
]
