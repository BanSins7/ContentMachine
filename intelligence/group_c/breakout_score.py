"""GC-MOD-19: C7 Breakout score math and provenance-bound diagnostic assembly.

Only the four frozen, recomputed C1/C3/C5/C6 stage receipts are accepted by
assemble_breakout_preview. A SHA256 receipt is tamper-evidence, *not* external
IAM authentication or current D1 authorization. The four upstream database
adapters use separate read-only transactions. Thus the assembly is NEVER a
production-complete score; GC-MOD-20 must independently re-read all facts,
review, current rights and activation in ONE atomic write transaction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any

from .bayesian_engagement import (
    BAYESIAN_ENGAGEMENT_VERSION, BayesianEngagementRates, BayesianEngagementResult,
    compute_bayesian_engagement,
)
from .contracts import BREAKOUT_WEIGHTS, FRESHNESS_HALF_LIFE_H, ContractError, canonical_hash
from .creator_ratio import C1_VERSION, CreatorRatioResult, compute_creator_ratio
from .freshness import (
    FRESHNESS_VERSION, FreshnessResult, _age_microseconds,
    compute_freshness_score,
)
from .pure_math import q
from .views_growth import (
    VIEW_GROWTH_VERSION, ViewsGrowthResult, _elapsed_microseconds,
    compute_views_growth_per_hour,
)

BREAKOUT_VERSION = "group-c-breakout-preview/v1"
_METRIC_DEFINITION = "platform_post_views"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_HOUR_MICROS = Decimal("3600000000")


def _decimal(value: Any, name: str, *, bounded: bool = False) -> Decimal:
    """Already-calculated C1/C3/C5/C6 NUMERIC(30,12), no default/coercion."""
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise ContractError("invalid_c7_metric", name + " must be a finite nonnegative Decimal")
    encoded = q(value)
    if encoded != value:
        raise ContractError("invalid_c7_precision", name + " must be exact NUMERIC(30,12)")
    if bounded and value > Decimal("100"):
        raise ContractError("invalid_c7_metric", name + " exceeds freshness range")
    return encoded


def _hash(value: Any, name: str) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise ContractError("invalid_c7_evidence", "Canonical SHA256 required: " + name)
    return value


def _id(value: Any, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ContractError("invalid_c7_identifier", name + " must be a positive integer")
    return value


@dataclass(frozen=True)
class BreakoutComponents:
    creator_ratio: Decimal
    views_growth_per_hour: Decimal
    weighted_engagement_smoothed: Decimal
    freshness_score: Decimal
    score_creator_component: Decimal
    score_growth_component: Decimal
    score_engagement_component: Decimal
    score_freshness_component: Decimal
    breakout_score: Decimal
    schema_version: str = BREAKOUT_VERSION

    @property
    def normalized(self) -> bool:
        return False


def compute_breakout_components(
    creator_ratio: Decimal, views_growth_per_hour: Decimal,
    weighted_engagement_smoothed: Decimal, freshness_score: Decimal,
) -> BreakoutComponents:
    """Exact C7 = .45*C1 + .30*C3 + .15*C5 + .10*C6, at scale 12.

    Quantize each contribution independently before summing, matching the
    frozen pure_math and existing 003/004 calculated_metrics integrity
    schema. C2 niche comparison has no weight; no normalization or cap.
    """
    values = (
        _decimal(creator_ratio, "creator_ratio"),
        _decimal(views_growth_per_hour, "views_growth_per_hour"),
        _decimal(weighted_engagement_smoothed, "weighted_engagement_smoothed"),
        _decimal(freshness_score, "freshness_score", bounded=True),
    )
    if len(BREAKOUT_WEIGHTS) != 4 or tuple(BREAKOUT_WEIGHTS) != (
        Decimal("0.45"), Decimal("0.30"), Decimal("0.15"), Decimal("0.10"),
    ):
        raise ContractError("invalid_c7_weights", "Canonical immutable C7 weights changed")
    with localcontext() as ctx:
        ctx.prec = 80
        components = tuple(q(weight * metric) for weight, metric in zip(BREAKOUT_WEIGHTS, values))
        score = q(sum(components, Decimal(0)))
    return BreakoutComponents(
        creator_ratio=values[0], views_growth_per_hour=values[1],
        weighted_engagement_smoothed=values[2], freshness_score=values[3],
        score_creator_component=components[0],
        score_growth_component=components[1],
        score_engagement_component=components[2],
        score_freshness_component=components[3],
        breakout_score=score,
    )


def _verify_c1(value: CreatorRatioResult) -> None:
    if (not isinstance(value, CreatorRatioResult)
            or value.schema_version != C1_VERSION
            or value.status != "c1_derived_unpersisted"
            or value.ready_for_c7 is not False):
        raise ContractError("invalid_c7_input_stage", "A current standalone C1 receipt is required")
    for name in ("video_id", "snapshot_id", "metadata_id",
                 "source_policy_version_id", "creator_id", "config_id",
                 "creator_freeze_id", "legacy_creator_cohort_id"):
        _id(getattr(value, name), name)
    for name in ("cohort_evidence_sha256", "review_subject_sha256",
                 "evidence_sha256"):
        _hash(getattr(value, name), name)
    calculated = compute_creator_ratio(value.views, value.creator_median_views,
                                       value.alpha_used)
    if calculated != _decimal(value.creator_ratio, "C1.creator_ratio"):
        raise ContractError("invalid_c7_input_math", "C1 value no longer matches raw views/median/alpha")
    expected = canonical_hash({
        "version": C1_VERSION,
        "project_id": value.project_id, "video_id": value.video_id,
        "snapshot_id": value.snapshot_id,
        "metadata_id": value.metadata_id, "source_id": value.source_id,
        "source_policy_version_id": value.source_policy_version_id,
        "metric_version": value.metric_version, "creator_id": value.creator_id,
        "config_id": value.config_id,
        "calculation_version": value.calculation_version,
        "creator_freeze_id": value.creator_freeze_id,
        "cohort_evidence_sha256": value.cohort_evidence_sha256,
        "review_subject_sha256": value.review_subject_sha256,
        "decision_id": value.review_decision_id,
        "activation_event_id": value.activation_event_id,
        "raw_views": value.views,
        "creator_median_views": str(q(value.creator_median_views)),
        "alpha": str(q(value.alpha_used)),
        "ratio": str(value.creator_ratio),
    })
    if expected != value.evidence_sha256:
        raise ContractError("invalid_c7_evidence", "C1 original evidence fingerprint changed")


def _verify_growth(value: ViewsGrowthResult) -> None:
    if (not isinstance(value, ViewsGrowthResult)
            or value.schema_version != VIEW_GROWTH_VERSION
            or value.status != "growth_derived_unapproved"
            or value.ready_for_breakout is not False):
        raise ContractError("invalid_c7_input_stage", "A current standalone C3 receipt is required")
    for name in ("video_id", "previous_snapshot_id", "current_snapshot_id",
                 "metadata_id", "source_policy_version_id"):
        _id(getattr(value, name), name)
    if value.previous_snapshot_id == value.current_snapshot_id:
        raise ContractError("invalid_c7_lineage", "C3 must refer to distinct raw snapshots")
    _hash(value.evidence_sha256, "C3.evidence_sha256")
    if (type(value.current_views) is not int
            or type(value.previous_views) is not int):
        raise ContractError("invalid_c7_input_math", "Exact raw integer C3 counters required")
    diff = value.current_views - value.previous_views
    if value.views_delta != diff or value.elapsed_microseconds != _elapsed_microseconds(
            value.previous_observed_at, value.current_observed_at):
        raise ContractError("invalid_c7_input_math", "C3 observed interval/uncorrected views changed")
    actual = compute_views_growth_per_hour(
        value.previous_views, value.current_views,
        value.previous_observed_at, value.current_observed_at,
    )
    if actual != _decimal(value.views_growth_per_hour, "C3.growth"):
        raise ContractError("invalid_c7_input_math", "C3 growth does not match raw facts")
    digest = canonical_hash({
        "version": VIEW_GROWTH_VERSION,
        "project_id": value.project_id, "video_id": value.video_id,
        "source_id": value.source_id,
        "source_policy_version_id": value.source_policy_version_id,
        "metric_definition": value.metric_definition,
        "metric_version": value.metric_version,
        "previous_snapshot_id": value.previous_snapshot_id,
        "current_snapshot_id": value.current_snapshot_id,
        "metadata_id": value.metadata_id,
        "previous_observed_at": value.previous_observed_at.isoformat(),
        "current_observed_at": value.current_observed_at.isoformat(),
        "previous_views": value.previous_views,
        "current_views": value.current_views,
        "views_delta": value.views_delta,
        "elapsed_microseconds": value.elapsed_microseconds,
        "views_growth_per_hour": str(value.views_growth_per_hour),
    })
    if digest != value.evidence_sha256:
        raise ContractError("invalid_c7_evidence", "C3 original evidence fingerprint changed")


def _verify_c5(value: BayesianEngagementResult) -> None:
    if (not isinstance(value, BayesianEngagementResult)
            or value.schema_version != BAYESIAN_ENGAGEMENT_VERSION
            or value.status != "c5_derived_unpersisted"
            or value.ready_for_breakout is not False):
        raise ContractError("invalid_c7_input_stage", "A current standalone C5 receipt is required")
    for name in ("video_id", "snapshot_id", "metadata_id", "config_id",
                 "prior_freeze_id", "legacy_prior_cohort_id",
                 "source_policy_version_id"):
        _id(getattr(value, name), name)
    for name in ("prior_evidence_sha256", "review_subject_sha256",
                 "evidence_sha256"):
        _hash(getattr(value, name), name)
    rates = value.rates
    if not isinstance(rates, BayesianEngagementRates):
        raise ContractError("invalid_c7_input_stage", "Typed C5 rate receipt is required")
    calculated = compute_bayesian_engagement(
        rates.views, rates.likes, rates.comments, rates.shares, rates.k_used,
        rates.p_l_used, rates.p_c_used, rates.p_s_used,
    )
    if calculated != rates:
        raise ContractError("invalid_c7_input_math", "C5 rates differ from raw counters/k/prior")
    _decimal(rates.weighted_engagement_smoothed, "C5.weighted")
    digest = canonical_hash({
        "version": BAYESIAN_ENGAGEMENT_VERSION,
        "project_id": value.project_id, "video_id": value.video_id,
        "snapshot_id": value.snapshot_id, "metadata_id": value.metadata_id,
        "source_id": value.source_id,
        "source_policy_version_id": value.source_policy_version_id,
        "metric_version": value.metric_version,
        "config_id": value.config_id,
        "calculation_version": value.calculation_version,
        "prior_freeze_id": value.prior_freeze_id,
        "prior_evidence_sha256": value.prior_evidence_sha256,
        "review_subject_sha256": value.review_subject_sha256,
        "decision_id": value.review_decision_id,
        "activation_event_id": value.activation_event_id,
        "views": rates.views, "likes": rates.likes,
        "comments": rates.comments, "shares": rates.shares,
        "k": str(rates.k_used), "p_l": str(rates.p_l_used),
        "p_c": str(rates.p_c_used), "p_s": str(rates.p_s_used),
        "like_rate_smoothed": str(rates.like_rate_smoothed),
        "comment_rate_smoothed": str(rates.comment_rate_smoothed),
        "share_rate_smoothed": str(rates.share_rate_smoothed),
        "weighted_engagement_smoothed": str(rates.weighted_engagement_smoothed),
    })
    if digest != value.evidence_sha256:
        raise ContractError("invalid_c7_evidence", "C5 original evidence fingerprint changed")


def _verify_freshness(value: FreshnessResult) -> None:
    if (not isinstance(value, FreshnessResult)
            or value.schema_version != FRESHNESS_VERSION
            or value.status != "freshness_derived_unapproved"
            or value.ready_for_breakout is not False):
        raise ContractError("invalid_c7_input_stage", "A current standalone C6 receipt is required")
    for name in ("video_id", "previous_snapshot_id", "snapshot_id",
                 "metadata_id", "source_policy_version_id"):
        _id(getattr(value, name), name)
    if value.previous_snapshot_id == value.snapshot_id:
        raise ContractError("invalid_c7_lineage", "C6 must refer to distinct raw snapshots")
    _hash(value.publication_evidence_sha256, "GC08.evidence_sha256")
    _hash(value.evidence_sha256, "C6.evidence_sha256")
    if value.half_life_hours != FRESHNESS_HALF_LIFE_H:
        raise ContractError("invalid_c7_weights", "C6 half-life must be exactly 48h")
    micros = _age_microseconds(value.published_at, value.observed_at)
    with localcontext() as ctx:
        ctx.prec = 80
        hours = Decimal(micros) / _HOUR_MICROS
    if micros != value.age_microseconds or hours != value.age_hours:
        raise ContractError("invalid_c7_input_math", "C6 original observation-anchored age changed")
    score = compute_freshness_score(value.published_at, value.observed_at)
    if score != _decimal(value.freshness_score, "C6.freshness", bounded=True):
        raise ContractError("invalid_c7_input_math", "C6 freshness does not match published_at/observed_at")
    digest = canonical_hash({
        "version": FRESHNESS_VERSION, "project_id": value.project_id,
        "video_id": value.video_id, "metadata_id": value.metadata_id,
        "source_id": value.source_id,
        "source_policy_version_id": value.source_policy_version_id,
        "previous_snapshot_id": value.previous_snapshot_id,
        "snapshot_id": value.snapshot_id,
        "metric_definition": value.metric_definition,
        "metric_version": value.metric_version,
        "published_at": value.published_at.isoformat(),
        "observed_at": value.observed_at.isoformat(),
        "age_microseconds": micros,
        "freshness_score": str(value.freshness_score),
        "half_life_hours": str(FRESHNESS_HALF_LIFE_H),
        "publication_evidence_sha256": value.publication_evidence_sha256,
    })
    if digest != value.evidence_sha256:
        raise ContractError("invalid_c7_evidence", "C6 original evidence fingerprint changed")


@dataclass(frozen=True)
class BreakoutPreview:
    """C7 numeric output + exact receipt pointers; NOT complete/authorized."""
    project_id: str
    video_id: int
    snapshot_id: int
    previous_snapshot_id: int
    metadata_id: int
    source_id: str
    source_policy_version_id: int
    metric_definition: str
    metric_version: str
    config_id: int
    calculation_version: str
    creator_freeze_id: int
    prior_freeze_id: int
    review_request_id: str
    review_decision_id: str
    activation_event_id: str
    review_subject_sha256: str
    creator_evidence_sha256: str
    growth_evidence_sha256: str
    engagement_evidence_sha256: str
    freshness_evidence_sha256: str
    publication_evidence_sha256: str
    components: BreakoutComponents
    evidence_sha256: str
    schema_version: str = BREAKOUT_VERSION
    status: str = "breakout_preview_unpersisted_unverified"
    score_status: str = "pending_review_and_atomic_revalidation"

    @property
    def ready_for_persistence(self) -> bool:
        return False


def assemble_breakout_preview(
    creator: CreatorRatioResult, growth: ViewsGrowthResult,
    engagement: BayesianEngagementResult, freshness: FreshnessResult,
) -> BreakoutPreview:
    """Recompute and cross-check C1/C3/C5/C6 receipts; diagnostic only.

    Receipt SHA checks prevent accidental mutation, not attacker fabrication;
    the only proof of current source authorization and human IAM is an
    independent DB/IAM recheck in GC-MOD-20's atomic write transaction.
    """
    _verify_c1(creator)
    _verify_growth(growth)
    _verify_c5(engagement)
    _verify_freshness(freshness)
    # Every metric must name exactly the same canonical video, source,
    # metric version and current raw D1 snapshot/metadata.
    identity = (
        ("project_id", (creator.project_id, growth.project_id,
                        engagement.project_id, freshness.project_id)),
        ("video_id", (creator.video_id, growth.video_id,
                      engagement.video_id, freshness.video_id)),
        ("snapshot_id", (creator.snapshot_id, growth.current_snapshot_id,
                         engagement.snapshot_id, freshness.snapshot_id)),
        ("metadata_id", (creator.metadata_id, growth.metadata_id,
                         engagement.metadata_id, freshness.metadata_id)),
        ("source_id", (creator.source_id, growth.source_id,
                       engagement.source_id, freshness.source_id)),
        ("source_policy_version_id", (
            creator.source_policy_version_id, growth.source_policy_version_id,
            engagement.source_policy_version_id, freshness.source_policy_version_id)),
        ("metric_version", (creator.metric_version, growth.metric_version,
                            engagement.metric_version, freshness.metric_version)),
    )
    for key, ids in identity:
        if len(set(ids)) != 1:
            raise ContractError("invalid_c7_lineage", "C7 input mismatch: " + key)
    if (growth.metric_definition != _METRIC_DEFINITION
            or freshness.metric_definition != _METRIC_DEFINITION
            or growth.previous_snapshot_id != freshness.previous_snapshot_id
            or growth.current_observed_at != freshness.observed_at
            or growth.current_views != creator.views
            or growth.current_views != engagement.rates.views):
        raise ContractError("invalid_c7_lineage", "C3/C6 time, metric, predecessor or current raw views mismatch")
    approval = (
        ("config_id", creator.config_id, engagement.config_id),
        ("calculation_version", creator.calculation_version, engagement.calculation_version),
        ("review_request_id", creator.review_request_id, engagement.review_request_id),
        ("review_decision_id", creator.review_decision_id, engagement.review_decision_id),
        ("activation_event_id", creator.activation_event_id, engagement.activation_event_id),
        ("review_subject_sha256", creator.review_subject_sha256, engagement.review_subject_sha256),
    )
    for name, a, b in approval:
        if a != b:
            raise ContractError("invalid_c7_approval", "C1/C5 approved subject differs: " + name)
    if (type(creator.project_id) is not str
            or not re.fullmatch("[0-9a-f]{32}", creator.project_id)
            or type(creator.source_id) is not str or not creator.source_id.strip()
            or type(creator.metric_version) is not str or not creator.metric_version.strip()):
        raise ContractError("invalid_c7_lineage", "Canonical project/source/version required")
    if any(type(x) is not str or not x.strip() for x in (
            creator.calculation_version, creator.review_request_id,
            creator.review_decision_id, creator.activation_event_id)):
        raise ContractError("invalid_c7_approval", "Explicit exact approval references required")
    result = compute_breakout_components(
        creator.creator_ratio, growth.views_growth_per_hour,
        engagement.rates.weighted_engagement_smoothed,
        freshness.freshness_score,
    )
    source_sha = {
        "creator": creator.evidence_sha256,
        "growth": growth.evidence_sha256,
        "engagement": engagement.evidence_sha256,
        "freshness": freshness.evidence_sha256,
        "publication": freshness.publication_evidence_sha256,
    }
    digest = canonical_hash({
        "version": BREAKOUT_VERSION,
        "project_id": creator.project_id, "video_id": creator.video_id,
        "snapshot_id": creator.snapshot_id,
        "previous_snapshot_id": growth.previous_snapshot_id,
        "metadata_id": creator.metadata_id, "source_id": creator.source_id,
        "source_policy_version_id": creator.source_policy_version_id,
        "metric_definition": _METRIC_DEFINITION,
        "metric_version": creator.metric_version,
        "config_id": creator.config_id,
        "calculation_version": creator.calculation_version,
        "creator_freeze_id": creator.creator_freeze_id,
        "prior_freeze_id": engagement.prior_freeze_id,
        "review_request_id": creator.review_request_id,
        "review_decision_id": creator.review_decision_id,
        "activation_event_id": creator.activation_event_id,
        "review_subject_sha256": creator.review_subject_sha256,
        "receipts": source_sha,
        "weights": {
            "creator": str(BREAKOUT_WEIGHTS[0]),
            "growth": str(BREAKOUT_WEIGHTS[1]),
            "engagement": str(BREAKOUT_WEIGHTS[2]),
            "freshness": str(BREAKOUT_WEIGHTS[3]),
        },
        "score_creator_component": str(result.score_creator_component),
        "score_growth_component": str(result.score_growth_component),
        "score_engagement_component": str(result.score_engagement_component),
        "score_freshness_component": str(result.score_freshness_component),
        "breakout_score": str(result.breakout_score),
    })
    return BreakoutPreview(
        project_id=creator.project_id, video_id=creator.video_id,
        snapshot_id=creator.snapshot_id,
        previous_snapshot_id=growth.previous_snapshot_id,
        metadata_id=creator.metadata_id, source_id=creator.source_id,
        source_policy_version_id=creator.source_policy_version_id,
        metric_definition=_METRIC_DEFINITION, metric_version=creator.metric_version,
        config_id=creator.config_id, calculation_version=creator.calculation_version,
        creator_freeze_id=creator.creator_freeze_id,
        prior_freeze_id=engagement.prior_freeze_id,
        review_request_id=creator.review_request_id,
        review_decision_id=creator.review_decision_id,
        activation_event_id=creator.activation_event_id,
        review_subject_sha256=creator.review_subject_sha256,
        creator_evidence_sha256=source_sha["creator"],
        growth_evidence_sha256=source_sha["growth"],
        engagement_evidence_sha256=source_sha["engagement"],
        freshness_evidence_sha256=source_sha["freshness"],
        publication_evidence_sha256=source_sha["publication"],
        components=result, evidence_sha256=digest,
    )
