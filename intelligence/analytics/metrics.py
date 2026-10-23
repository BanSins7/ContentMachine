"""Source-first C1-C7 arithmetic. Raw formula and weights are immutable.

C is never scored without separately authorized alpha, k, frozen compatible
cohorts, complete observations, reliable published_at, and same-source growth.
"""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

Q = Decimal("0.000000000001")
HOUR = Decimal("3600")
WEIGHTS = (Decimal("0.45"), Decimal("0.30"), Decimal("0.15"), Decimal("0.10"))

class MetricError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

def d(value, name, *, required=True, positive=False):
    if value is None and not required:
        return None
    if value is None or isinstance(value, bool):
        raise MetricError("missing_" + name)
    try:
        value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise MetricError("invalid_" + name) from None
    if not value.is_finite() or value < 0 or (positive and value == 0):
        raise MetricError("invalid_" + name)
    return value

def q(value):
    return value.quantize(Q, rounding=ROUND_HALF_UP)

def _time(value, label):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MetricError("missing_" + label)
    return value

def compute_c_metrics(current: dict, previous: dict, publication: dict,
                      config: dict, creator: dict, prior: dict,
                      niche: dict | None = None) -> dict:
    """Compute typed values compatible with calculated_metrics, no database IO."""
    with localcontext() as ctx:
        ctx.prec = 60
        if current.get("source_id") != previous.get("source_id"):
            raise MetricError("incompatible_source")
        if (current.get("observation_time_assumed") is not False
                or previous.get("observation_time_assumed") is not False):
            raise MetricError("assumed_observation_time")
        if (not current.get("source_metric_version")
                or current.get("source_metric_version") != previous.get("source_metric_version")):
            raise MetricError("incompatible_metric_version")
        if (not current.get("source_metric_definition")
                or current.get("source_metric_definition") != previous.get("source_metric_definition")):
            raise MetricError("incompatible_definition")
        if (current.get("data_quality_status") != "ok"
                or previous.get("data_quality_status") != "ok"):
            raise MetricError("unverified_observation")
        if current.get("video_id") != previous.get("video_id"):
            raise MetricError("different_video")
        if current.get("id") == previous.get("id"):
            raise MetricError("identical_snapshot")
        t2, t1 = _time(current.get("observed_at"), "observed_at"), _time(previous.get("observed_at"), "previous_at")
        born = _time(publication.get("published_at"), "published_at")
        if t2 <= t1 or t1 < born or t2 < born:
            raise MetricError("invalid_timestamp")
        delta = Decimal(str((t2 - t1).total_seconds())) / HOUR
        age = Decimal(str((t2 - born).total_seconds())) / HOUR
        v, v1 = d(current.get("views"), "views"), d(previous.get("views"), "previous_views")
        if v < v1:
            raise MetricError("counter_correction")
        if v == 0:
            raise MetricError("zero_views_rate_undefined")
        likes, comments, shares = (d(current.get(key), key) for key in ("likes", "comments", "shares"))
        if config.get("normalization_enabled") is not False:
            raise MetricError("normalization_not_allowed")
        if str(config.get("freshness_half_life_h")) not in ("48", "48.0", "48.000000"):
            raise MetricError("invalid_half_life")
        expected_weights = {"creator_ratio": 0.45, "views_growth_per_hour": 0.30,
                            "weighted_engagement_smoothed": 0.15, "freshness_score": 0.10}
        if config.get("weights_json") != expected_weights:
            # psycopg json numeric values may be Decimal or float; dict equality is numeric.
            raise MetricError("invalid_weights")
        alpha = d(config.get("alpha"), "alpha", positive=True)
        k = d(config.get("bayesian_k"), "k", positive=True)
        if (not creator.get("is_frozen") or creator.get("purpose") != "creator"
                or creator.get("median_views") is None or creator.get("post_count", 0) < 1):
            raise MetricError("creator_cohort_not_approved")
        if (not prior.get("is_frozen") or prior.get("purpose") != "engagement_prior"
                or prior.get("post_count", 0) < 1):
            raise MetricError("prior_cohort_not_approved")
        if niche is not None and (not niche.get("is_frozen") or niche.get("purpose") != "niche"
                                  or niche.get("median_views") is None):
            raise MetricError("niche_cohort_not_approved")
        mc = d(creator.get("median_views"), "creator_median")
        mn = d(niche.get("median_views"), "niche_median") if niche else None
        pl = d(prior.get("prior_like_rate"), "prior_like_rate")
        pc = d(prior.get("prior_comment_rate"), "prior_comment_rate")
        ps = d(prior.get("prior_share_rate"), "prior_share_rate")
        ratio = (v + alpha) / (mc + alpha)
        outlier = (v + alpha) / (mn + alpha) if mn is not None else None
        growth = (v - v1) / delta
        l_raw, c_raw, s_raw = likes / v, comments / v, shares / v
        ls = (likes + k * pl) / (v + k)
        cs = (comments + k * pc) / (v + k)
        ss = (shares + k * ps) / (v + k)
        weighted = ls + Decimal("1.5") * cs + Decimal("3") * ss
        # Decimal exponent avoids binary floating point; store rounded at NUMERIC scale.
        freshness = Decimal("100") * (Decimal("2") ** (-age / Decimal("48")))
        creator_comp, growth_comp = q(WEIGHTS[0] * q(ratio)), q(WEIGHTS[1] * q(growth))
        engage_comp, fresh_comp = q(WEIGHTS[2] * q(weighted)), q(WEIGHTS[3] * q(freshness))
        return {
            "creator_median_views": q(mc), "niche_median_views": q(mn) if mn is not None else None,
            "alpha_used": q(alpha), "k_used": q(k), "p_l_used": q(pl), "p_c_used": q(pc), "p_s_used": q(ps),
            "like_rate_pct": q(l_raw * 100), "comment_rate_pct": q(c_raw * 100),
            "share_rate_pct": q(s_raw * 100), "engagement_rate_pct": q((likes + comments + shares) * 100 / v),
            "weighted_engagement_raw": q(l_raw + Decimal("1.5") * c_raw + Decimal("3") * s_raw),
            "like_rate_smoothed": q(ls), "comment_rate_smoothed": q(cs),
            "share_rate_smoothed": q(ss), "weighted_engagement_smoothed": q(weighted),
            "creator_ratio": q(ratio), "niche_outlier": q(outlier) if outlier is not None else None,
            "views_growth_per_hour": q(growth), "freshness_score": q(freshness),
            "score_creator_component": creator_comp, "score_growth_component": growth_comp,
            "score_engagement_component": engage_comp, "score_freshness_component": fresh_comp,
            "breakout_score": sum((creator_comp, growth_comp, engage_comp, fresh_comp), Decimal(0)),
            "calculation_status": "complete", "score_status": "complete",
        }
