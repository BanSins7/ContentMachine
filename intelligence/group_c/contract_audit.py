"""GC-MOD-01: immutable C0-C7 field/formula/status contract inventory.

This is a *declarative audit*, not a scoring implementation and not a
substitute for live QDP/D1/config/cohort checks. The implementation of the
actual guarded scorer is scheduled for GC-MOD-07 onwards.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from .contracts import ContractError

CONTRACT_VERSION = "group-c-contract-matrix/v1"
NUMERIC_STORAGE = "NUMERIC(30,12)"
SCALE = 12

# Authoritative columns in 001/002 + 003/004. Absence (NULL) is distinct from 0.
RAW_SNAPSHOT_FACTS = (
    "views", "likes", "comments", "shares", "saves", "followers",
)
SNAPSHOT_PROVENANCE = (
    "id", "video_id", "source_id", "observed_at", "collected_at",
    "source_metric_definition", "source_metric_version",
    "source_policy_version_id", "observation_time_assumed",
    "data_quality_status", "precision", "expires_at",
)
PUBLICATION_PROVENANCE = (
    "id", "video_id", "source_id", "published_at", "deleted_at", "expires_at",
)
CALCULATION_LINEAGE = (
    "post_id", "snapshot_id", "previous_snapshot_id",
    "published_metadata_id", "calculation_config_id", "creator_cohort_id",
    "niche_cohort_id", "prior_cohort_id",
)
DB_SCORE_STATUSES = frozenset((
    "complete", "insufficient_data", "pending_configuration",
    "invalid_timestamp", "incompatible_source", "counter_correction",
    "calculation_error",
))

@dataclass(frozen=True)
class StageContract:
    code: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    formula: str
    units: str
    optional_for_c7: bool = False


METRIC_CONTRACTS = (
    StageContract(
        "C0",
        RAW_SNAPSHOT_FACTS + SNAPSHOT_PROVENANCE,
        RAW_SNAPSHOT_FACTS,
        "read_exact_raw_observation_without_substitution",
        "nonnegative integer counters; UTC-aware observation time",
    ),
    StageContract(
        "C1",
        ("views", "alpha", "creator_median_views", "creator_cohort_id"),
        ("creator_ratio",),
        "(views + alpha)/(creator_median_views + alpha)", "dimensionless",
    ),
    StageContract(
        "C2",
        ("views", "alpha", "niche_median_views", "niche_cohort_id"),
        ("niche_outlier",),
        "(views + alpha)/(niche_median_views + alpha)",
        "dimensionless", optional_for_c7=True,
    ),
    StageContract(
        "C3",
        ("views", "previous_views", "observed_at", "previous_observed_at"),
        ("views_growth_per_hour",),
        "(views - previous_views)/elapsed_hours", "views/hour",
    ),
    StageContract(
        "C4",
        ("views", "likes", "comments", "shares"),
        ("like_rate_pct", "comment_rate_pct", "share_rate_pct",
         "engagement_rate_pct", "weighted_engagement_raw"),
        "ER=(L+C+S)/V; WR=(L+1.5*C+3*S)/V; *_pct=100*raw_ratio",
        "percentage points for *_pct; fraction for weighted_engagement_raw",
    ),
    StageContract(
        "C5",
        ("views", "likes", "comments", "shares", "bayesian_k",
         "prior_like_rate", "prior_comment_rate", "prior_share_rate",
         "prior_cohort_id"),
        ("like_rate_smoothed", "comment_rate_smoothed",
         "share_rate_smoothed", "weighted_engagement_smoothed"),
        "Ls=(L+k*pL)/(V+k); Cs=(C+k*pC)/(V+k); Ss=(S+k*pS)/(V+k); Ws=Ls+1.5*Cs+3*Ss",
        "fraction",
    ),
    StageContract(
        "C6",
        ("observed_at", "published_at", "published_metadata_id"),
        ("freshness_score",),
        "100*2^(-elapsed_hours_since_published_at/48)",
        "0..100 (freshness only)",
    ),
    StageContract(
        "C7",
        ("creator_ratio", "views_growth_per_hour",
         "weighted_engagement_smoothed", "freshness_score"),
        ("score_creator_component", "score_growth_component",
         "score_engagement_component", "score_freshness_component",
         "breakout_score"),
        "0.45*C1+0.30*C3+0.15*C5+0.10*C6",
        "raw unbounded composite (not a probability, not normalized)",
    ),
)

# Existing 003/004 database vocabulary: detailed reasons need a separate
# audit/queue layer; never insert reason_code into score_status directly.
# Not every reason below can be checked by the current 003/004 triggers.
_BLOCKER_TO_DB_STATUS = {
    "missing_snapshot": "insufficient_data",
    "missing_raw_counter": "insufficient_data",
    "zero_views_rate_undefined": "insufficient_data",
    "missing_prior_snapshot": "insufficient_data",
    "missing_publication_metadata": "insufficient_data",
    "missing_creator_cohort": "insufficient_data",
    "missing_prior_cohort": "insufficient_data",
    "empty_cohort": "insufficient_data",
    "missing_metric_definition": "incompatible_source",
    "incompatible_metric_definition": "incompatible_source",
    "incompatible_metric_version": "incompatible_source",
    "different_video_or_source": "incompatible_source",
    "source_policy_unavailable": "incompatible_source",
    "source_expired_or_retracted": "incompatible_source",
    "unverified_or_assumed_observation": "insufficient_data",
    "unapproved_config": "pending_configuration",
    "unapproved_cohort": "pending_configuration",
    "missing_alpha_or_k_or_priors": "pending_configuration",
    "invalid_observation_interval": "invalid_timestamp",
    "invalid_publication_timestamp": "invalid_timestamp",
    "counter_decreased": "counter_correction",
    "arithmetic_error": "calculation_error",
}
BLOCKER_TO_DB_STATUS = MappingProxyType(_BLOCKER_TO_DB_STATUS)


def status_for_blocker(reason: str) -> str:
    """Map a recognized blocking reason to a legal NON-COMPLETE DB status.

    This function does not score data or infer complete from no blockers.
    """
    if type(reason) is not str or reason not in BLOCKER_TO_DB_STATUS:
        raise ContractError("unmapped_c_blocker", "Unknown Group C blocker")
    return BLOCKER_TO_DB_STATUS[reason]


def validate_contract_manifest() -> None:
    """Fail loudly if the frozen manifest is accidentally made inconsistent."""
    if tuple(x.code for x in METRIC_CONTRACTS) != tuple(
        "C" + str(i) for i in range(8)
    ):
        raise ContractError("stage_contract_drift", "Unexpected C0-C7 stage inventory")
    if {x.code for x in METRIC_CONTRACTS if x.optional_for_c7} != {"C2"}:
        raise ContractError("stage_contract_drift", "Only C2 is optional")
    expected = (
        "score_creator_component", "score_growth_component",
        "score_engagement_component", "score_freshness_component",
        "breakout_score",
    )
    if METRIC_CONTRACTS[7].outputs != expected:
        raise ContractError("stage_contract_drift", "C7 outputs changed")
    if "niche_outlier" in METRIC_CONTRACTS[7].inputs:
        raise ContractError("stage_contract_drift", "C2 must never contribute to C7")
    if not set(BLOCKER_TO_DB_STATUS.values()) <= (DB_SCORE_STATUSES - {"complete"}):
        raise ContractError("invalid_score_status", "Blocker mapped to unsupported status")
