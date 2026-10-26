"""Patterns & Insights Engine (PIE) standalone, fail-closed pure contracts.

This package does not initialize producer adapters, database connections, paid
model services or a statistical inference engine on import.
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
    'PIEBoundaryError', 'PIEDatasetPointer', 'canonical_hash',
    'CapabilityState', 'PortCapability', 'PortStatus', 'PIEPorts',
    'PORT_SCHEMAS', 'EXPECTED_PEER_WIRES', 'REQUIRED_METHODS',
    'UAEQualifiedReader', 'SourceRightsReader', 'ArtifactStore',
    'InsightPublisher', 'HumanReviewReader', 'GroundedModelProvider',
    'PIEExecutionSettings', 'MANIFEST_SCHEMA', 'DATASET_SCHEMA',
    'PIEObservation', 'PIEValidatedDataset', 'validate_dataset_manifest', 'load_dataset_json',
]
from .intake.uae import (
    HMACReceiptVerifier, UAEExportDescriptor, UAEExportEnvelope,
    UAEExportReceipt, UAERightsDecision, UAEReadBridge, UAEReadResult,
)
__all__ += [
    'HMACReceiptVerifier', 'UAEExportDescriptor', 'UAEExportEnvelope',
    'UAEExportReceipt', 'UAERightsDecision', 'UAEReadBridge', 'UAEReadResult',
]
from .intake.provenance import (ScopeEligibility, RowEligibility, RowProvenanceSubject,
                                ProvenanceAuthority, EligibilityReport, SourceEligibilityGate)
__all__ += ['ScopeEligibility', 'RowEligibility', 'RowProvenanceSubject',
            'ProvenanceAuthority', 'EligibilityReport', 'SourceEligibilityGate']
from .research.dataset_builder import (
    SNAPSHOT_SCHEMA, ResearchSampleWeight, ResearchBuildSpec,
    CandidateResearchSnapshot, build_candidate_dataset, replay_candidate_snapshot,
)
__all__ += [
    'SNAPSHOT_SCHEMA', 'ResearchSampleWeight', 'ResearchBuildSpec',
    'CandidateResearchSnapshot', 'build_candidate_dataset', 'replay_candidate_snapshot',
]
from .research.strata import (
    SCHEMA as STRATA_SCHEMA, AgeBucket, StrataPolicy, CandidateStrata,
    build_candidate_strata, replay_candidate_strata, require_one_stratum,
)
__all__ += [
    'STRATA_SCHEMA', 'AgeBucket', 'StrataPolicy', 'CandidateStrata',
    'build_candidate_strata', 'replay_candidate_strata', 'require_one_stratum',
]

from .research.outcomes import (
    OutcomeComparisonPolicy, DesignApprovalDecision, DesignApprovalAuthority,
    CandidateOutcomePlan, build_candidate_outcome_plan, replay_candidate_outcome_plan,
)
__all__ += [
    'OutcomeComparisonPolicy', 'DesignApprovalDecision', 'DesignApprovalAuthority',
    'CandidateOutcomePlan', 'build_candidate_outcome_plan', 'replay_candidate_outcome_plan',
]
