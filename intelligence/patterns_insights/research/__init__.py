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
