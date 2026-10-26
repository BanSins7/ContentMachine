"""Patterns & Insights Engine (PIE) standalone contracts and inert ports.

Not a live UAE integration, statistical inference engine, or paid model runner.
"""
from .contracts import PIEBoundaryError, PIEDatasetPointer, canonical_hash
from .ports import (CapabilityState, PortCapability, PortStatus, PIEPorts,
                    PORT_SCHEMAS, EXPECTED_PEER_WIRES, REQUIRED_METHODS,
                    UAEQualifiedReader, SourceRightsReader, ArtifactStore,
                    InsightPublisher, HumanReviewReader, GroundedModelProvider)
from .settings import PIEExecutionSettings

__all__ = [
    "PIEBoundaryError", "PIEDatasetPointer", "canonical_hash",
    "CapabilityState", "PortCapability", "PortStatus", "PIEPorts",
    "PORT_SCHEMAS", "EXPECTED_PEER_WIRES", "REQUIRED_METHODS",
    "UAEQualifiedReader", "SourceRightsReader", "ArtifactStore",
    "InsightPublisher", "HumanReviewReader", "GroundedModelProvider",
    "PIEExecutionSettings",
]
