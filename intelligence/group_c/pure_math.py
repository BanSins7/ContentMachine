"""GC-MOD-07: independent deterministic C1-C7 pure arithmetic.

Ported from Analytics metrics.py preserving raw v1 formula and field names.
Not a readiness/approval/DB gate: stages 08-25 must prove current D1,
publication, approved configuration/cohorts and rights before persistence.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import (
    Decimal, DecimalException, InvalidOperation, ROUND_HALF_UP, localcontext,
)

from .contracts import BREAKOUT_WEIGHTS, FRESHNESS_HALF_LIFE_H, ContractError

Q = Decimal("0.000000000001")
HOUR = Decimal("3600")
NUMERIC_MAX_ABS = Decimal("1000000000000000000")
CALCULATOR_VERSION = "group-c-decimal/v1"


class MetricError(ContractError):
    """Compatible error.code; never a complete production pipeline."""

    def __init__(self, code: str):
        super().__init__(code, code)


def d(value, name: str, *, required: bool = True, positive: bool = False) -> Decimal | None:
    """Finite exact SQL/JSON values; no floats or implicit zero."""
    if value is None and not required:
        return None
    if value is None or type(value) not in (Decimal, int, str):
        raise MetricError("missing_" + name if value is None else "invalid_" + name)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise MetricError("invalid_" + name) from exc
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise MetricError("invalid_" + name)
    return number


def q(value: Decimal) -> Decimal:
    """ROUND_HALF_UP to NUMERIC(30,12), reject overflow and context drift."""
    if type(value) is not Decimal or not value.is_finite():
        raise MetricError("invalid_numeric_result")
    try:
        with localcontext() as ctx:
            ctx.prec = 80
            ctx.rounding = ROUND_HALF_UP
            result = value.quantize(Q, rounding=ROUND_HALF_UP)
    except DecimalException as exc:
        raise MetricError("numeric_overflow") from exc
    if result.copy_abs() >= NUMERIC_MAX_ABS:
        raise MetricError("numeric_overflow")
    return result


def _time(value, label: str) -> datetime:
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() is None):
        raise MetricError("missing_" + label)
    try:
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise MetricError("invalid_" + label) from exc


def _elapsed_seconds(later: datetime, earlier: datetime) -> Decimal:
    """Exact microsecond timedelta; no float total_seconds conversion."""
    delta: timedelta = later - earlier
    return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / Decimal("1000000")


def _cohort(row: Mapping, label: str, purpose: str, *, needs_median: bool) -> None:
    if (row.get("is_frozen") is not True or row.get("purpose") != purpose
            or type(row.get("post_count")) is not int or row["post_count"] < 1
            or (needs_median and row.get("median_views") is None)):
        raise MetricError(label + "_cohort_not_approved")


def _exact_weight(value) -> Decimal:
    """Only allow JSON float weights equal to exact fixed contract values."""
    if type(value) is bool or type(value) not in (Decimal, int, str, float):
        raise MetricError("invalid_weights")
    try:
        value = Decimal(str(value))
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise MetricError("invalid_weights") from exc
    if not value.is_finite():
        raise MetricError("invalid_weights")
    return value


def compute_c_metrics(
    current: dict, previous: dict, publication: dict, config: dict,
    creator: dict, prior: dict, niche: dict | None = None,
) -> dict:
    """Pure port of C1-C7; complete is arithmetic status, NOT authorisation.

    Never call a provider or persist output here. The future worker must
    enforce QDP/D1 currentness, reviewed config/cohorts and durable lineage.
    """
    if any(not isinstance(item, Mapping) for item in
           (current, previous, publication, config, creator, prior)) or (
            niche is not None and not isinstance(niche, Mapping)):
        raise MetricError("invalid_input")
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.rounding = ROUND_HALF_UP
        if (not current.get("source_id") or
                current.get("source_id") != previous.get("source_id")):
            raise MetricError("incompatible_source")
        if (current.get("observation_time_assumed") is not False or
                previous.get("observation_time_assumed") is not False):
            raise MetricError("assumed_observation_time")
        if (not current.get("source_metric_version") or
                current.get("source_metric_version") != previous.get("source_metric_version")):
            raise MetricError("incompatible_metric_version")
        if (not current.get("source_metric_definition") or
                current.get("source_metric_definition") != previous.get("source_metric_definition")):
            raise MetricError("incompatible_definition")
        if (current.get("data_quality_status") != "ok" or
                previous.get("data_quality_status") != "ok" or
                any(x.get("precision", "exact") != "exact" for x in (current, previous))):
            raise MetricError("unverified_observation")
        if current.get("video_id") != previous.get("video_id"):
            raise MetricError("different_video")
        if (type(current.get("id")) is not int or current["id"] < 1
                or type(previous.get("id")) is not int or previous["id"] < 1):
            raise MetricError("invalid_snapshot_id")
        if current["id"] == previous["id"]:
            raise MetricError("identical_snapshot")
        if current.get("source_policy_version_id") != previous.get("source_policy_version_id"):
            raise MetricError("incompatible_policy_version")
        t2 = _time(current.get("observed_at"), "observed_at")
        t1 = _time(previous.get("observed_at"), "previous_at")
        born = _time(publication.get("published_at"), "published_at")
        if t2 <= t1 or t1 < born or t2 < born:
            raise MetricError("invalid_timestamp")
        delta = _elapsed_seconds(t2, t1) / HOUR
        age = _elapsed_seconds(t2, born) / HOUR
        v, v1 = d(current.get("views"), "views"), d(previous.get("views"), "previous_views")
        if v < v1:
            raise MetricError("counter_correction")
        if v == 0:
            raise MetricError("zero_views_rate_undefined")
        likes, comments, shares = (d(current.get(key), key)
                                  for key in ("likes", "comments", "shares"))
        if config.get("normalization_enabled") is not False:
            raise MetricError("normalization_not_allowed")
        half_life = d(config.get("freshness_half_life_h"), "half_life", positive=True)
        if half_life != FRESHNESS_HALF_LIFE_H:
            raise MetricError("invalid_half_life")
        weights = config.get("weights_json")
        expected = {"creator_ratio": BREAKOUT_WEIGHTS[0],
                    "views_growth_per_hour": BREAKOUT_WEIGHTS[1],
                    "weighted_engagement_smoothed": BREAKOUT_WEIGHTS[2],
                    "freshness_score": BREAKOUT_WEIGHTS[3]}
        if not isinstance(weights, Mapping) or set(weights) != set(expected):
            raise MetricError("invalid_weights")
        try:
            if any(_exact_weight(weights[key]) != val for key, val in expected.items()):
                raise MetricError("invalid_weights")
        except MetricError as exc:
            raise MetricError("invalid_weights") from exc
        alpha = d(config.get("alpha"), "alpha", positive=True)
        k = d(config.get("bayesian_k"), "k", positive=True)
        _cohort(creator, "creator", "creator", needs_median=True)
        _cohort(prior, "prior", "engagement_prior", needs_median=False)
        if niche is not None:
            _cohort(niche, "niche", "niche", needs_median=True)
        mc = d(creator.get("median_views"), "creator_median")
        mn = d(niche.get("median_views"), "niche_median") if niche is not None else None
        pl = d(prior.get("prior_like_rate"), "prior_like_rate")
        pc = d(prior.get("prior_comment_rate"), "prior_comment_rate")
        ps = d(prior.get("prior_share_rate"), "prior_share_rate")
        if any(rate > 1 for rate in (pl, pc, ps)):
            raise MetricError("invalid_prior_rate")
        ratio = (v + alpha) / (mc + alpha)
        outlier = (v + alpha) / (mn + alpha) if mn is not None else None
        growth = (v - v1) / delta
        l_raw, c_raw, s_raw = likes / v, comments / v, shares / v
        ls = (likes + k * pl) / (v + k)
        cs = (comments + k * pc) / (v + k)
        ss = (shares + k * ps) / (v + k)
        weighted = ls + Decimal("1.5") * cs + Decimal("3") * ss
        try:
            freshness = Decimal("100") * (Decimal("2") ** (-age / FRESHNESS_HALF_LIFE_H))
        except DecimalException as exc:
            raise MetricError("numeric_overflow") from exc
        creator_comp = q(BREAKOUT_WEIGHTS[0] * q(ratio))
        growth_comp = q(BREAKOUT_WEIGHTS[1] * q(growth))
        engage_comp = q(BREAKOUT_WEIGHTS[2] * q(weighted))
        fresh_comp = q(BREAKOUT_WEIGHTS[3] * q(freshness))
        return {
            "creator_median_views": q(mc),
            "niche_median_views": q(mn) if mn is not None else None,
            "alpha_used": q(alpha), "k_used": q(k),
            "p_l_used": q(pl), "p_c_used": q(pc), "p_s_used": q(ps),
            "like_rate_pct": q(l_raw * 100),
            "comment_rate_pct": q(c_raw * 100),
            "share_rate_pct": q(s_raw * 100),
            "engagement_rate_pct": q((likes + comments + shares) * 100 / v),
            "weighted_engagement_raw": q(l_raw + Decimal("1.5") * c_raw + Decimal("3") * s_raw),
            "like_rate_smoothed": q(ls),
            "comment_rate_smoothed": q(cs),
            "share_rate_smoothed": q(ss),
            "weighted_engagement_smoothed": q(weighted),
            "creator_ratio": q(ratio),
            "niche_outlier": q(outlier) if outlier is not None else None,
            "views_growth_per_hour": q(growth),
            "freshness_score": q(freshness),
            "score_creator_component": creator_comp,
            "score_growth_component": growth_comp,
            "score_engagement_component": engage_comp,
            "score_freshness_component": fresh_comp,
            "breakout_score": q(creator_comp + growth_comp + engage_comp + fresh_comp),
            "calculation_status": "complete", "score_status": "complete",
        }
