"""Patterns & Insights Engine (PIE) standalone contracts and inert ports.

Not a live UAE integration, statistical inference engine, or paid model runner.
"""
from .contracts import PIEBoundaryError, PIEDatasetPointer, canonical_hash
from .ports import (CapabilityState, PortCapability, PortStatus, PIEPorts,
                    PORT_SCHEMAS, EXPECTED_PEER_WIRES, REQUIRED_METHODS,
                    UAEQualifiedReader, SourceRightsReader, ArtifactStore,
                    InsightPublisher, HumanReviewReader, GroundedModelProvider)
from .settings import PIEExecutionSettings
from .dataset_contract import (MANIFEST_SCHEMA, DATASET_SCHEMA, PIEObservation,
                               PIEValidatedDataset, validate_dataset_manifest, load_dataset_json)

__all__ = [
    "PIEBoundaryError", "PIEDatasetPointer", "canonical_hash",
    "CapabilityState", "PortCapability", "PortStatus", "PIEPorts",
    "PORT_SCHEMAS", "EXPECTED_PEER_WIRES", "REQUIRED_METHODS",
    "UAEQualifiedReader", "SourceRightsReader", "ArtifactStore",
    "InsightPublisher", "HumanReviewReader", "GroundedModelProvider",
    "PIEExecutionSettings", "MANIFEST_SCHEMA", "DATASET_SCHEMA",
    "PIEObservation", "PIEValidatedDataset", "validate_dataset_manifest", "load_dataset_json",
]

# Explicit opt-in UAE intake only. Import does not initialize any producer.
from .intake.uae import (
    HMACReceiptVerifier, UAEExportDescriptor, UAEExportEnvelope,
    UAEExportReceipt, UAERightsDecision, UAEReadBridge, UAEReadResult,
)
__all__ += [
    'HMACReceiptVerifier', 'UAEExportDescriptor', 'UAEExportEnvelope',
    'UAEExportReceipt', 'UAERightsDecision', 'UAEReadBridge', 'UAEReadResult',
]
