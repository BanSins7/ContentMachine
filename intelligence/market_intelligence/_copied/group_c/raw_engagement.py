"""GC-MOD-16: Сырые показатели вовлечённости (C4).

Rates are FRACTIONS. Display-only PERCENTAGES require an explicit conversion.
No Bayesian smoothing, durable score, provider calls, or Analytics publishing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Any

from .c0 import C0Pair, MAX_COUNTER, read_c0_pair, validate_c0_pair
from .contracts import ContractError, canonical_hash
from .pure_math import q

RAW_ENGAGEMENT_VERSION = "group-c-raw-engagement/v1"
_COMMENT_WEIGHT = Decimal("1.5")
_SHARE_WEIGHT = Decimal("3")
_PERCENT = Decimal("100")


def _count(value: Any, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or not 0 <= value <= MAX_COUNTER:
        raise ContractError("invalid_raw_counter", name + " requires nonnegative exact BIGINT")
    if positive and value == 0:
        raise ContractError("zero_views_rate_undefined", "Cannot divide by zero views")
    return value


@dataclass(frozen=True)
class RawEngagementPercentages:
    like_rate_percent: Decimal
    comment_rate_percent: Decimal
    share_rate_percent: Decimal
    engagement_rate_percent: Decimal
    weighted_engagement_percent: Decimal
    unit: str = "percent"


@dataclass(frozen=True)
class RawEngagementRates:
    views: int
    likes: int
    comments: int
    shares: int
    like_rate_fraction: Decimal
    comment_rate_fraction: Decimal
    share_rate_fraction: Decimal
    engagement_rate_fraction: Decimal
    weighted_engagement_raw_fraction: Decimal
    unit: str = "fraction"

    def as_percentages(self) -> RawEngagementPercentages:
        """Derive percentage from raw counts, avoiding rounded-rate * 100."""
        with localcontext() as ctx:
            ctx.prec = 80
            v = Decimal(self.views)
            l, c, s = map(Decimal, (self.likes, self.comments, self.shares))
            return RawEngagementPercentages(
                like_rate_percent=q(_PERCENT * l / v),
                comment_rate_percent=q(_PERCENT * c / v),
                share_rate_percent=q(_PERCENT * s / v),
                engagement_rate_percent=q(_PERCENT * (l + c + s) / v),
                weighted_engagement_percent=q(
                    _PERCENT * (l + _COMMENT_WEIGHT * c + _SHARE_WEIGHT * s) / v),
            )


def compute_raw_engagement(
    views: int, likes: int, comments: int, shares: int,
) -> RawEngagementRates:
    """L/V, C/V, S/V, (L+C+S)/V, (L+1.5*C+3*S)/V.

    Do not infer missing counters, clip fractions, or include saves.
    Use unrounded Decimal ratios; each result fits NUMERIC(30,12).
    """
    v = _count(views, "views", positive=True)
    l = _count(likes, "likes")
    c = _count(comments, "comments")
    s = _count(shares, "shares")
    with localcontext() as ctx:
        ctx.prec = 80
        dv = Decimal(v)
        dl, dc, ds = map(Decimal, (l, c, s))
        return RawEngagementRates(
            views=v, likes=l, comments=c, shares=s,
            like_rate_fraction=q(dl / dv),
            comment_rate_fraction=q(dc / dv),
            share_rate_fraction=q(ds / dv),
            engagement_rate_fraction=q((dl + dc + ds) / dv),
            weighted_engagement_raw_fraction=q(
                (dl + _COMMENT_WEIGHT * dc + _SHARE_WEIGHT * ds) / dv),
        )


def compute_verified_raw_engagement(pair: C0Pair) -> RawEngagementRates:
    """Validate the C0 pair; only the DB entry point verifies live D1 rights."""
    if not isinstance(pair, C0Pair):
        raise ContractError("missing_snapshot", "A typed C0 pair is mandatory")
    current = validate_c0_pair(
        pair.previous, pair.current, require_score_fields=True,
    ).current
    return compute_raw_engagement(
        current.views, current.likes, current.comments, current.shares,
    )


@dataclass(frozen=True)
class RawEngagementResult:
    project_id: str
    video_id: int
    source_id: str
    source_policy_version_id: int
    metric_definition: str
    metric_version: str
    previous_snapshot_id: int
    current_snapshot_id: int
    metadata_id: int
    current_observed_at: datetime
    rates: RawEngagementRates
    evidence_sha256: str
    schema_version: str = RAW_ENGAGEMENT_VERSION
    status: str = "raw_engagement_derived_unapproved"

    @property
    def ready_for_breakout(self) -> bool:
        return False

    @property
    def percentages(self) -> RawEngagementPercentages:
        return self.rates.as_percentages()


class PostgreSQLRawEngagement:
    """Read-only diagnostic C4 result from latest raw, project-scoped D1."""

    def derive(
        self, conn: Any, *, project_id: str, video_id: int, source_id: str,
        source_policy_version_id: int, previous_snapshot_id: int,
        snapshot_id: int, metadata_id: int, metric_version: str,
        metric_definition: str = "platform_post_views",
        ports: Any = None, now: datetime | None = None,
    ) -> RawEngagementResult:
        # GC06 owns a REPEATABLE READ, READ ONLY source transaction.
        # Both most recent raw observations must match the supplied IDs.
        pair = read_c0_pair(
            conn, project_id=project_id, video_id=video_id,
            source_id=source_id, source_policy_version_id=source_policy_version_id,
            previous_snapshot_id=previous_snapshot_id, snapshot_id=snapshot_id,
            metadata_id=metadata_id, metric_definition=metric_definition,
            metric_version=metric_version, ports=ports, now=now,
            require_score_fields=True,
        )
        rates = compute_verified_raw_engagement(pair)
        old, current = pair.previous, pair.current
        digest = canonical_hash({
            "version": RAW_ENGAGEMENT_VERSION,
            "project_id": project_id, "video_id": current.video_id,
            "source_id": current.source_id,
            "source_policy_version_id": current.source_policy_version_id,
            "metric_definition": current.metric_definition,
            "metric_version": current.metric_version,
            "metadata_id": metadata_id,
            "previous_snapshot_id": old.snapshot_id,
            "current_snapshot_id": current.snapshot_id,
            "current_observed_at": current.observed_at.isoformat(),
            "views": rates.views, "likes": rates.likes,
            "comments": rates.comments, "shares": rates.shares,
            "unit": rates.unit,
            "like_rate_fraction": str(rates.like_rate_fraction),
            "comment_rate_fraction": str(rates.comment_rate_fraction),
            "share_rate_fraction": str(rates.share_rate_fraction),
            "engagement_rate_fraction": str(rates.engagement_rate_fraction),
            "weighted_engagement_raw_fraction":
                str(rates.weighted_engagement_raw_fraction),
        })
        return RawEngagementResult(
            project_id=project_id, video_id=current.video_id,
            source_id=current.source_id,
            source_policy_version_id=current.source_policy_version_id,
            metric_definition=current.metric_definition,
            metric_version=current.metric_version,
            previous_snapshot_id=old.snapshot_id,
            current_snapshot_id=current.snapshot_id,
            metadata_id=metadata_id,
            current_observed_at=current.observed_at,
            rates=rates, evidence_sha256=digest,
        )
