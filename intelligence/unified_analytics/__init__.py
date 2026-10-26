"""Unified Analytics standalone import surface (no live adapters or writes).

UA-MOD-06: pinned lineage, pure gate and fail-closed LIVE SRE reader; no automatic qualification.
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
from .lineage import (
    LINEAGE_SCHEMA, CanonicalLineagePins, CanonicalLineage, resolve_canonical_lineage,
)
from .sre_adapter import (
    SRE_PEER_WIRE, SREApprovedRelevancePins, SREApprovedRelevanceReader,
)
from .qualification import (
    GATE_SCHEMA, ObservationState, QualificationObservation, QualificationInput,
    validate_request, canonical_gate_hash, evaluate_qualification,
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
    "GATE_SCHEMA", "ObservationState", "QualificationObservation", "QualificationInput",
    "validate_request", "canonical_gate_hash", "evaluate_qualification",
    "LINEAGE_SCHEMA", "CanonicalLineagePins", "CanonicalLineage",
    "resolve_canonical_lineage",
    "SRE_PEER_WIRE", "SREApprovedRelevancePins",
    "SREApprovedRelevanceReader",
]
