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
