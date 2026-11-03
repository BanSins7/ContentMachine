"""Group C — Скорость роста просмотров (GC-MOD-15).

Only exact raw D1 source observations, no provider, no guessed wall-clock
elapsed time, no counter correction clamping, no Breakout/C7 persistence.
A verified history pair may be measured with views-only counters; later
engagement scoring still requires all four counters and approved cohorts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from typing import Any

from .c0 import C0Pair, MAX_COUNTER, read_c0_pair, validate_c0_pair
from .contracts import ContractError, canonical_hash
from .pure_math import q

VIEW_GROWTH_VERSION = "group-c-views-growth/v1"
_MICROSECONDS_IN_HOUR = Decimal("3600000000")


@dataclass(frozen=True)
class ViewsGrowthResult:
    project_id: str
    video_id: int
    source_id: str
    source_policy_version_id: int
    metric_definition: str
    metric_version: str
    previous_snapshot_id: int
    current_snapshot_id: int
    metadata_id: int
    previous_observed_at: datetime
    current_observed_at: datetime
    previous_views: int
    current_views: int
    views_delta: int
    elapsed_microseconds: int
    views_growth_per_hour: Decimal
    evidence_sha256: str
    schema_version: str = VIEW_GROWTH_VERSION
    status: str = "growth_derived_unapproved"

    @property
    def ready_for_breakout(self) -> bool:
        return False


def _strict_views(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_COUNTER:
        raise ContractError("invalid_raw_counter", name + " must be a nonnegative exact BIGINT")
    return value


def _utc(value: Any, name: str) -> datetime:
    if (type(value) is not datetime or value.tzinfo is None
            or value.utcoffset() is None):
        raise ContractError("invalid_observation_interval",
                            name + " must be timezone-aware")
    try:
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ContractError("invalid_observation_interval", name) from exc


def _elapsed_microseconds(previous: datetime, current: datetime) -> int:
    before, after = _utc(previous, "previous_observed_at"), _utc(current, "current_observed_at")
    delta = after - before
    micros = ((delta.days * 86400 + delta.seconds) * 1000000
              + delta.microseconds)
    if micros <= 0:
        raise ContractError("invalid_observation_interval",
                            "Strictly increasing observed_at required")
    return micros


def compute_views_growth_per_hour(
    previous_views: int, current_views: int,
    previous_observed_at: datetime, current_observed_at: datetime,
) -> Decimal:
    """Exact (new-old)/(elapsed_microseconds / 3600000000), 12 digits.

    Do not use total_seconds() floats, collected_at, provider polling delay,
    datetime.now(), or the publication date for elapsed time.
    A decreasing counter is an invalid sample, never zero growth.
    """
    old = _strict_views(previous_views, "previous_views")
    new = _strict_views(current_views, "current_views")
    micros = _elapsed_microseconds(previous_observed_at, current_observed_at)
    if new < old:
        raise ContractError("counter_decreased",
                            "Raw view correction is not zero or positive growth")
    with localcontext() as context:
        context.prec = 80
        delta_views = Decimal(new - old)
        rate = delta_views * _MICROSECONDS_IN_HOUR / Decimal(micros)
        return q(rate)


def compute_verified_views_growth(pair: C0Pair) -> Decimal:
    """C0 validated same video/source/policy/view version, views-only mode."""
    if not isinstance(pair, C0Pair):
        raise ContractError("missing_snapshot", "Verified exact C0 pair required")
    checked = validate_c0_pair(
        pair.previous, pair.current, require_score_fields=False,
    )
    return compute_views_growth_per_hour(
        checked.previous.views, checked.current.views,
        checked.previous.observed_at, checked.current.observed_at,
    )


class PostgreSQLViewsGrowth:
    """Read a latest raw D1 pair, then compute diagnostic, unapproved growth."""

    def derive(
        self, conn: Any, *, project_id: str, video_id: int, source_id: str,
        source_policy_version_id: int, previous_snapshot_id: int,
        snapshot_id: int, metadata_id: int, metric_version: str,
        metric_definition: str = "platform_post_views",
        ports: Any = None, now: datetime | None = None,
    ) -> ViewsGrowthResult:
        # This reuses GC-MOD-06's ONE REPEATABLE READ/READ ONLY transaction:
        # D1 project-scoped rights, same source and exact policy version,
        # both snapshots, TTL, raw quality and latest two unfiltered IDs.
        pair = read_c0_pair(
            conn, project_id=project_id, video_id=video_id,
            source_id=source_id, source_policy_version_id=source_policy_version_id,
            previous_snapshot_id=previous_snapshot_id, snapshot_id=snapshot_id,
            metadata_id=metadata_id, metric_definition=metric_definition,
            metric_version=metric_version, ports=ports, now=now,
            require_score_fields=False,
        )
        result = compute_verified_views_growth(pair)
        previous, current = pair.previous, pair.current
        elapsed = _elapsed_microseconds(
            previous.observed_at, current.observed_at,
        )
        diff = current.views - previous.views
        digest = canonical_hash({
            "version": VIEW_GROWTH_VERSION,
            "project_id": project_id, "video_id": video_id,
            "source_id": source_id,
            "source_policy_version_id": source_policy_version_id,
            "metric_definition": current.metric_definition,
            "metric_version": current.metric_version,
            "previous_snapshot_id": previous.snapshot_id,
            "current_snapshot_id": current.snapshot_id,
            "metadata_id": metadata_id,
            "previous_observed_at": previous.observed_at.isoformat(),
            "current_observed_at": current.observed_at.isoformat(),
            "previous_views": previous.views, "current_views": current.views,
            "views_delta": diff, "elapsed_microseconds": elapsed,
            "views_growth_per_hour": str(result),
        })
        return ViewsGrowthResult(
            project_id=project_id, video_id=video_id, source_id=source_id,
            source_policy_version_id=source_policy_version_id,
            metric_definition=current.metric_definition,
            metric_version=current.metric_version,
            previous_snapshot_id=previous.snapshot_id,
            current_snapshot_id=current.snapshot_id, metadata_id=metadata_id,
            previous_observed_at=previous.observed_at,
            current_observed_at=current.observed_at,
            previous_views=previous.views, current_views=current.views,
            views_delta=diff, elapsed_microseconds=elapsed,
            views_growth_per_hour=result, evidence_sha256=digest,
        )
