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

from .research.controls import (
    MatchingPolicy, CandidateMatchedControls,
    build_candidate_controls, replay_candidate_controls,
)
__all__ += [
    'MatchingPolicy', 'CandidateMatchedControls',
    'build_candidate_controls', 'replay_candidate_controls',
]

from .research.feature_matrix import (
    FeatureColumn, FeatureMatrixPolicy, CandidateFeatureMatrix,
    build_candidate_feature_matrix, replay_candidate_feature_matrix,
)
__all__ += [
    'FeatureColumn', 'FeatureMatrixPolicy', 'CandidateFeatureMatrix',
    'build_candidate_feature_matrix', 'replay_candidate_feature_matrix',
]

from .research.coverage import (
    CoveragePolicy, CandidateCoverageAudit,
    build_candidate_coverage_audit, replay_candidate_coverage_audit,
)
__all__ += [
    'CoveragePolicy', 'CandidateCoverageAudit',
    'build_candidate_coverage_audit', 'replay_candidate_coverage_audit',
]

from .research.marginal import (
    MarginalHypothesis, MarginalDiscoveryPolicy, CandidateMarginalDiscovery,
    build_candidate_marginal_discovery, replay_candidate_marginal_discovery,
)
__all__ += [
    'MarginalHypothesis', 'MarginalDiscoveryPolicy', 'CandidateMarginalDiscovery',
    'build_candidate_marginal_discovery', 'replay_candidate_marginal_discovery',
]

from .research.effects import (
    EffectEstimationPolicy, CandidateDescriptiveEffects,
    build_candidate_descriptive_effects, replay_candidate_descriptive_effects,
)
__all__ += [
    'EffectEstimationPolicy', 'CandidateDescriptiveEffects',
    'build_candidate_descriptive_effects', 'replay_candidate_descriptive_effects',
]

from .research.uncertainty import (
    UncertaintyPolicy, CandidateUncertaintyIntervals,
    build_candidate_uncertainty_intervals, replay_candidate_uncertainty_intervals,
)
__all__ += [
    'UncertaintyPolicy', 'CandidateUncertaintyIntervals',
    'build_candidate_uncertainty_intervals', 'replay_candidate_uncertainty_intervals',
]

from .research.multiplicity import (
    MultiplicityPolicy, SyntheticPValue, SyntheticPValueEvidence,
    CandidateMultiplicityReport, benjamini_hochberg,
    build_candidate_multiplicity_report, replay_candidate_multiplicity_report,
)
__all__ += [
    'MultiplicityPolicy', 'SyntheticPValue', 'SyntheticPValueEvidence',
    'CandidateMultiplicityReport', 'benjamini_hochberg',
    'build_candidate_multiplicity_report', 'replay_candidate_multiplicity_report',
]

from .research.holdout import (
    HoldoutPolicy, HoldoutMediaLineage, CandidateHoldoutSplit,
    build_candidate_holdout_split, replay_candidate_holdout_split,
    require_candidate_holdout_scope, require_candidate_cv_scope,
)
__all__ += [
    'HoldoutPolicy', 'HoldoutMediaLineage', 'CandidateHoldoutSplit',
    'build_candidate_holdout_split', 'replay_candidate_holdout_split',
    'require_candidate_holdout_scope', 'require_candidate_cv_scope',
]

from .research.adjustment import (
    AdjustmentPolicy, AdjustmentCovariate, CandidateAdjustmentReport,
    build_candidate_adjustment_report, replay_candidate_adjustment_report,
)
__all__ += [
    'AdjustmentPolicy', 'AdjustmentCovariate', 'CandidateAdjustmentReport',
    'build_candidate_adjustment_report', 'replay_candidate_adjustment_report',
]

from .research.stability import (
    StabilityWindow, StabilityPolicy, CandidateStabilityReport,
    build_candidate_stability_report, replay_candidate_stability_report,
)
__all__ += [
    'StabilityWindow', 'StabilityPolicy', 'CandidateStabilityReport',
    'build_candidate_stability_report', 'replay_candidate_stability_report',
]

from .research.interactions import (
    InteractionPredicate, InteractionHypothesis, InteractionPolicy,
    CandidateInteractionReport, build_candidate_interaction_report,
    replay_candidate_interaction_report,
)
__all__ += [
    'InteractionPredicate', 'InteractionHypothesis', 'InteractionPolicy',
    'CandidateInteractionReport', 'build_candidate_interaction_report',
    'replay_candidate_interaction_report',
]

from .research.story_motifs import (
    StoryMotif, StoryMotifPolicy, StoryRoleCoordinates, CandidateStoryMotifReport,
    count_story_ngram, build_candidate_story_motifs, replay_candidate_story_motifs,
)
__all__ += [
    'StoryMotif', 'StoryMotifPolicy', 'StoryRoleCoordinates', 'CandidateStoryMotifReport',
    'count_story_ngram', 'build_candidate_story_motifs', 'replay_candidate_story_motifs',
]

from .research.technical_numeric import (
    TechnicalNumericFactor, TechnicalNumericPolicy, CandidateTechnicalNumericReport,
    build_candidate_technical_numeric_report, replay_candidate_technical_numeric_report,
)
__all__ += [
    'TechnicalNumericFactor', 'TechnicalNumericPolicy', 'CandidateTechnicalNumericReport',
    'build_candidate_technical_numeric_report', 'replay_candidate_technical_numeric_report',
]

from .research.robustness import (
    SensitivityFactor, RobustnessPolicy, CandidateRobustnessReport,
    build_candidate_robustness_report, replay_candidate_robustness_report,
)
__all__ += [
    'SensitivityFactor', 'RobustnessPolicy', 'CandidateRobustnessReport',
    'build_candidate_robustness_report', 'replay_candidate_robustness_report',
]

from .research.pattern_quality import (
    PatternQualityPolicy, CandidatePatternQualityReport,
    build_candidate_pattern_quality_report, replay_candidate_pattern_quality_report,
)
__all__ += [
    'PatternQualityPolicy', 'CandidatePatternQualityReport',
    'build_candidate_pattern_quality_report', 'replay_candidate_pattern_quality_report',
]

from .research.pattern_registry import (
    RegistryOwner, CandidateRegistryEntry, CandidateRegistryBatch, RegistryWriteReceipt,
    build_candidate_pattern_registry_batch, replay_candidate_pattern_registry_batch,
    persist_candidate_pattern_registry,
)
__all__ += [
    'RegistryOwner', 'CandidateRegistryEntry', 'CandidateRegistryBatch',
    'RegistryWriteReceipt', 'build_candidate_pattern_registry_batch',
    'replay_candidate_pattern_registry_batch', 'persist_candidate_pattern_registry',
]

from .research.pattern_queries import (
    RegistryQuery, RedactedPattern, PatternQueryPage, CurrentProjectAuthorizer,
    FreshUAEReader, CursorSigner, query_pattern_registry,
    export_redacted_pattern_page,
)
__all__ += [
    'RegistryQuery', 'RedactedPattern', 'PatternQueryPage',
    'CurrentProjectAuthorizer', 'FreshUAEReader', 'CursorSigner',
    'query_pattern_registry', 'export_redacted_pattern_page',
]

from .research.pattern_invalidation import (
    SourceInvalidationNotice, CurrentInvalidationAuthority, InvalidationReceipt,
    invalidation_target_predicate, is_revision_invalidated,
    require_noninvalidated_pattern_for_insight, record_source_invalidation,
)
__all__ += [
    'SourceInvalidationNotice', 'CurrentInvalidationAuthority', 'InvalidationReceipt',
    'invalidation_target_predicate', 'is_revision_invalidated',
    'require_noninvalidated_pattern_for_insight', 'record_source_invalidation',
]

from .research.insight_semantics import (
    CandidateInsightPrompt, CandidateInsightDraft,
    prepare_candidate_insight_prompt, compile_candidate_insight_draft,
    replay_candidate_insight_draft,
)
__all__ += [
    'CandidateInsightPrompt', 'CandidateInsightDraft',
    'prepare_candidate_insight_prompt', 'compile_candidate_insight_draft',
    'replay_candidate_insight_draft',
]

from .research.model_governance import (
    OpenRouterModelSnapshot, OpenRouterTransport, OpenRouterHTTPS,
    CandidateModelQuote, SpendAuthority, ModelReservation, ModelDispatchReceipt,
    verify_model_catalog, model_quote, reserve_model_attempt,
    dispatch_reserved_once,
)
__all__ += [
    'OpenRouterModelSnapshot', 'OpenRouterTransport', 'OpenRouterHTTPS',
    'CandidateModelQuote', 'SpendAuthority', 'ModelReservation',
    'ModelDispatchReceipt', 'verify_model_catalog', 'model_quote',
    'reserve_model_attempt', 'dispatch_reserved_once',
]

from .research.insight_generation import (
    CandidateInsightGeneration, InsightGenerationReceipt,
    build_candidate_insight_generation, replay_candidate_insight_generation,
    generate_candidate_insight,
)
__all__ += [
    'CandidateInsightGeneration', 'InsightGenerationReceipt',
    'build_candidate_insight_generation', 'replay_candidate_insight_generation',
    'generate_candidate_insight',
]

from .research.insight_verification import (
    CandidateInsightVerification, CandidateInsightVerificationReceipt,
    verify_candidate_insight,
)
__all__ += [
    'CandidateInsightVerification', 'CandidateInsightVerificationReceipt',
    'verify_candidate_insight',
]

from .research.human_review import (
    ReviewAction, CurrentReviewGrant, CurrentReviewAuthority,
    CandidateReviewEvent, CandidateReviewReceipt,
    append_candidate_review, replay_candidate_review_event,
)
__all__ += [
    'ReviewAction', 'CurrentReviewGrant', 'CurrentReviewAuthority',
    'CandidateReviewEvent', 'CandidateReviewReceipt',
    'append_candidate_review', 'replay_candidate_review_event',
]

from .research.orchestrator import (
    JobAuthority, JobIntent, JobReceipt, LeasedJob, JobTransition,
    DeterministicTaskHandler, enqueue_job, claim_deterministic_job,
    heartbeat_job, checkpoint_job, complete_job, fail_job,
    cancel_job, recover_expired_jobs, execute_one_deterministic,
)
__all__ += [
    'JobAuthority', 'JobIntent', 'JobReceipt', 'LeasedJob', 'JobTransition',
    'DeterministicTaskHandler', 'enqueue_job', 'claim_deterministic_job',
    'heartbeat_job', 'checkpoint_job', 'complete_job', 'fail_job',
    'cancel_job', 'recover_expired_jobs', 'execute_one_deterministic',
]

from .explorer import (
    ExplorerPorts, VerifiedActor, explore, explore_for_request,
)
from .http_api import install_pie_explorer_runtime
__all__ += [
    'ExplorerPorts', 'VerifiedActor', 'explore', 'explore_for_request',
    'install_pie_explorer_runtime',
]

from .release_readiness import (
    GateReceipt, StandaloneReadiness, ReleaseGateError,
    assess_standalone_readiness,
)
__all__ += [
    'GateReceipt','StandaloneReadiness','ReleaseGateError',
    'assess_standalone_readiness',
]
