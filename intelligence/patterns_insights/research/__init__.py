"""Pure research candidate construction and deterministic descriptive grouping."""
from .dataset_builder import (
    SNAPSHOT_SCHEMA, ResearchSampleWeight, ResearchBuildSpec,
    CandidateResearchSnapshot, build_candidate_dataset, replay_candidate_snapshot,
)
from .strata import (
    SCHEMA as STRATA_SCHEMA, AgeBucket, StrataPolicy, CandidateStrata,
    build_candidate_strata, replay_candidate_strata, require_one_stratum,
)

__all__ = [
    'SNAPSHOT_SCHEMA', 'ResearchSampleWeight', 'ResearchBuildSpec',
    'CandidateResearchSnapshot', 'build_candidate_dataset', 'replay_candidate_snapshot',
    'STRATA_SCHEMA', 'AgeBucket', 'StrataPolicy', 'CandidateStrata',
    'build_candidate_strata', 'replay_candidate_strata', 'require_one_stratum',
]

from .outcomes import (
    OutcomeComparisonPolicy, DesignApprovalDecision, DesignApprovalAuthority,
    CandidateOutcomePlan, build_candidate_outcome_plan, replay_candidate_outcome_plan,
)
__all__ += [
    'OutcomeComparisonPolicy', 'DesignApprovalDecision', 'DesignApprovalAuthority',
    'CandidateOutcomePlan', 'build_candidate_outcome_plan', 'replay_candidate_outcome_plan',
]

from .controls import (
    MatchingPolicy, CandidateMatchedControls,
    build_candidate_controls, replay_candidate_controls,
)
__all__ += [
    'MatchingPolicy', 'CandidateMatchedControls',
    'build_candidate_controls', 'replay_candidate_controls',
]

from .feature_matrix import (
    FeatureColumn, FeatureMatrixPolicy, CandidateFeatureMatrix,
    build_candidate_feature_matrix, replay_candidate_feature_matrix,
)
__all__ += [
    'FeatureColumn', 'FeatureMatrixPolicy', 'CandidateFeatureMatrix',
    'build_candidate_feature_matrix', 'replay_candidate_feature_matrix',
]

from .coverage import (
    CoveragePolicy, CandidateCoverageAudit,
    build_candidate_coverage_audit, replay_candidate_coverage_audit,
)
__all__ += [
    'CoveragePolicy', 'CandidateCoverageAudit',
    'build_candidate_coverage_audit', 'replay_candidate_coverage_audit',
]

from .marginal import (
    MarginalHypothesis, MarginalDiscoveryPolicy, CandidateMarginalDiscovery,
    build_candidate_marginal_discovery, replay_candidate_marginal_discovery,
)
__all__ += [
    'MarginalHypothesis', 'MarginalDiscoveryPolicy', 'CandidateMarginalDiscovery',
    'build_candidate_marginal_discovery', 'replay_candidate_marginal_discovery',
]

from .effects import (
    EffectEstimationPolicy, CandidateDescriptiveEffects,
    build_candidate_descriptive_effects, replay_candidate_descriptive_effects,
)
__all__ += [
    'EffectEstimationPolicy', 'CandidateDescriptiveEffects',
    'build_candidate_descriptive_effects', 'replay_candidate_descriptive_effects',
]

from .uncertainty import (
    UncertaintyPolicy, CandidateUncertaintyIntervals,
    build_candidate_uncertainty_intervals, replay_candidate_uncertainty_intervals,
)
__all__ += [
    'UncertaintyPolicy', 'CandidateUncertaintyIntervals',
    'build_candidate_uncertainty_intervals', 'replay_candidate_uncertainty_intervals',
]

from .multiplicity import (
    MultiplicityPolicy, SyntheticPValue, SyntheticPValueEvidence,
    CandidateMultiplicityReport, benjamini_hochberg,
    build_candidate_multiplicity_report, replay_candidate_multiplicity_report,
)
__all__ += [
    'MultiplicityPolicy', 'SyntheticPValue', 'SyntheticPValueEvidence',
    'CandidateMultiplicityReport', 'benjamini_hochberg',
    'build_candidate_multiplicity_report', 'replay_candidate_multiplicity_report',
]

from .holdout import (
    HoldoutPolicy, HoldoutMediaLineage, CandidateHoldoutSplit,
    build_candidate_holdout_split, replay_candidate_holdout_split,
    require_candidate_holdout_scope, require_candidate_cv_scope,
)
__all__ += [
    'HoldoutPolicy', 'HoldoutMediaLineage', 'CandidateHoldoutSplit',
    'build_candidate_holdout_split', 'replay_candidate_holdout_split',
    'require_candidate_holdout_scope', 'require_candidate_cv_scope',
]

from .adjustment import (
    AdjustmentPolicy, AdjustmentCovariate, CandidateAdjustmentReport,
    build_candidate_adjustment_report, replay_candidate_adjustment_report,
)
__all__ += [
    'AdjustmentPolicy', 'AdjustmentCovariate', 'CandidateAdjustmentReport',
    'build_candidate_adjustment_report', 'replay_candidate_adjustment_report',
]

from .stability import (
    StabilityWindow, StabilityPolicy, CandidateStabilityReport,
    build_candidate_stability_report, replay_candidate_stability_report,
)
__all__ += [
    'StabilityWindow', 'StabilityPolicy', 'CandidateStabilityReport',
    'build_candidate_stability_report', 'replay_candidate_stability_report',
]

from .interactions import (
    InteractionPredicate, InteractionHypothesis, InteractionPolicy,
    CandidateInteractionReport, build_candidate_interaction_report,
    replay_candidate_interaction_report,
)
__all__ += [
    'InteractionPredicate', 'InteractionHypothesis', 'InteractionPolicy',
    'CandidateInteractionReport', 'build_candidate_interaction_report',
    'replay_candidate_interaction_report',
]

from .story_motifs import (
    StoryMotif, StoryMotifPolicy, StoryRoleCoordinates, CandidateStoryMotifReport,
    count_story_ngram, build_candidate_story_motifs, replay_candidate_story_motifs,
)
__all__ += [
    'StoryMotif', 'StoryMotifPolicy', 'StoryRoleCoordinates', 'CandidateStoryMotifReport',
    'count_story_ngram', 'build_candidate_story_motifs', 'replay_candidate_story_motifs',
]

from .technical_numeric import (
    TechnicalNumericFactor, TechnicalNumericPolicy, CandidateTechnicalNumericReport,
    build_candidate_technical_numeric_report, replay_candidate_technical_numeric_report,
)
__all__ += [
    'TechnicalNumericFactor', 'TechnicalNumericPolicy', 'CandidateTechnicalNumericReport',
    'build_candidate_technical_numeric_report', 'replay_candidate_technical_numeric_report',
]

from .robustness import (
    SensitivityFactor, RobustnessPolicy, CandidateRobustnessReport,
    build_candidate_robustness_report, replay_candidate_robustness_report,
)
__all__ += [
    'SensitivityFactor', 'RobustnessPolicy', 'CandidateRobustnessReport',
    'build_candidate_robustness_report', 'replay_candidate_robustness_report',
]

from .pattern_quality import (
    PatternQualityPolicy, CandidatePatternQualityReport,
    build_candidate_pattern_quality_report, replay_candidate_pattern_quality_report,
)
__all__ += [
    'PatternQualityPolicy', 'CandidatePatternQualityReport',
    'build_candidate_pattern_quality_report', 'replay_candidate_pattern_quality_report',
]

from .pattern_registry import (
    RegistryOwner, CandidateRegistryEntry, CandidateRegistryBatch, RegistryWriteReceipt,
    build_candidate_pattern_registry_batch, replay_candidate_pattern_registry_batch,
    persist_candidate_pattern_registry,
)
__all__ += [
    'RegistryOwner', 'CandidateRegistryEntry', 'CandidateRegistryBatch',
    'RegistryWriteReceipt', 'build_candidate_pattern_registry_batch',
    'replay_candidate_pattern_registry_batch', 'persist_candidate_pattern_registry',
]

from .pattern_queries import (
    RegistryQuery, RedactedPattern, PatternQueryPage, CurrentProjectAuthorizer,
    FreshUAEReader, CursorSigner, query_pattern_registry,
    export_redacted_pattern_page,
)
__all__ += [
    'RegistryQuery', 'RedactedPattern', 'PatternQueryPage',
    'CurrentProjectAuthorizer', 'FreshUAEReader', 'CursorSigner',
    'query_pattern_registry', 'export_redacted_pattern_page',
]

from .pattern_invalidation import (
    SourceInvalidationNotice, CurrentInvalidationAuthority, InvalidationReceipt,
    invalidation_target_predicate, is_revision_invalidated,
    require_noninvalidated_pattern_for_insight, record_source_invalidation,
)
__all__ += [
    'SourceInvalidationNotice', 'CurrentInvalidationAuthority', 'InvalidationReceipt',
    'invalidation_target_predicate', 'is_revision_invalidated',
    'require_noninvalidated_pattern_for_insight', 'record_source_invalidation',
]

from .insight_semantics import (
    CandidateInsightPrompt, CandidateInsightDraft,
    prepare_candidate_insight_prompt, compile_candidate_insight_draft,
    replay_candidate_insight_draft,
)
__all__ += [
    'CandidateInsightPrompt', 'CandidateInsightDraft',
    'prepare_candidate_insight_prompt', 'compile_candidate_insight_draft',
    'replay_candidate_insight_draft',
]
