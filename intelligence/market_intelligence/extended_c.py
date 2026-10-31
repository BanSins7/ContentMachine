"""Evidence-scoped MI execution of the copied Group C numerical kernels.

This executes C4/C5/C6/C7 *math* on already rights-filtered D1 observations.
C5 uses observed comparable peers only as a diagnostic prior. It is NOT a
signed/approved Group C prior freeze, and the C7 output is deliberately NOT a
certified original Group C C7 persistence receipt. Unknown remains null.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from statistics import median

from ._copied.group_c.raw_engagement import compute_raw_engagement
from ._copied.group_c.bayesian_engagement import compute_bayesian_engagement
from ._copied.group_c.freshness import compute_freshness_score
from ._copied.group_c.breakout_score import compute_breakout_components
from .performance import timestamp
from .contracts import digest
from .group_c_quality import validate_history
from .group_c_cohorts import freeze_diagnostic_peers, compute_c2_niche_diagnostic

VERSION = "mi-copied-c-evidence-scoped/v2"
MIN_PRIOR_PEERS = 5


def _valid(row):
    return all(type(row.get(k)) is int and row[k] >= 0
               for k in ("views", "likes", "comments", "shares")) and row["views"] > 0


def _age_match(target, candidate):
    t, c = target.get("publication_age_hours"),candidate.get("publication_age_hours")
    return (t is not None and c is not None and t >= 1 and c >= 1
            and t / 2 <= c <= t * 2)


def _wire_decimal(value):
    return str(value) if value is not None else None


def enrich_exact_c(rows: list[dict], histories: dict[int, list[dict]],
                   raw_histories: dict[int, list[dict]] | None = None) -> list[dict]:
    """Produce per-video C receipts with explicit *unapproved* lineage markers.

    All inputs were scoped to active source/policy and checked latest public
    snapshots by the caller; no synthetic retention or other external metric.
    """
    cohorts = defaultdict(list)
    output = []
    for original in rows:
        row = dict(original)
        newest = (histories.get(row["video_id"]) or [None])[-1]
        raw_points=(raw_histories if raw_histories is not None else histories).get(row["video_id"]) or []
        strict_c0=bool(raw_points and "data_quality_status" in raw_points[-1])
        quality=validate_history(row,raw_points,strict=strict_c0)
        c = {
            "schema_version": VERSION,
            "c0_quality_gate":quality,
            "state": "partial",
            "original_full_c_certified": False,
            "metric_snapshot_id": row.get("metric_snapshot_id"),
            "method": "copied_group_c_exact_math",
            "c0": ("eligible_latest_snapshot"
                   if quality["state"]=="eligible_exact_observation" else "unavailable"),
            "c1_creator_ratio": _wire_decimal(row.get("creator_ratio")),
            "c2_niche_outlier": None,
            "c1_baseline_cohort_sha256":None,
            "c5_peer_cohort_sha256":None,
            "c3_views_growth_per_hour": _wire_decimal(row.get("views_growth_per_hour")),
            "c4": None, "c5": None, "c6_freshness": None,
            "c7_breakout_preview": None, "limitations": [],
        }
        if (newest is None or c["c0"]!="eligible_latest_snapshot"
            or not _valid(newest)):
            c["limitations"].append("c0_"+quality.get("reason","missing"))
            c["limitations"].append("raw_counters_missing")
        else:
            try:
                base = compute_raw_engagement(
                    newest["views"], newest["likes"], newest["comments"], newest["shares"])
                c["c4"] = {
                    "like_rate_fraction": str(base.like_rate_fraction),
                    "comment_rate_fraction": str(base.comment_rate_fraction),
                    "share_rate_fraction": str(base.share_rate_fraction),
                    "engagement_rate_fraction": str(base.engagement_rate_fraction),
                    "weighted_engagement_fraction": str(base.weighted_engagement_raw_fraction),
                    "state": "exact_unapproved",
                }
            except (ValueError, ArithmeticError, OverflowError) as exc:
                c["limitations"].append("c4_" + type(exc).__name__)
            pub, observed = timestamp(row.get("published_at")),timestamp(newest.get("observed_at"))
            if pub is not None and observed is not None and observed >= pub:
                try:
                    c["c6_freshness"] = str(compute_freshness_score(pub,observed))
                except (ValueError, ArithmeticError, OverflowError):
                    c["limitations"].append("freshness_source_invalid")
            else:
                c["limitations"].append("published_at_unverified")
            key=(row.get("platform"),row.get("metric_source_id"),
                 row.get("metric_definition"),row.get("metric_version"))
            cohorts[key].append((row,newest))
        row["group_c"] = c
        output.append(row)
    for row in output:
        c=row["group_c"]
        newest=(histories.get(row["video_id"]) or [None])[-1]
        if (newest is None or c["c0"]!="eligible_latest_snapshot"
            or not _valid(newest)):
            continue
        key=(row.get("platform"),row.get("metric_source_id"),
             row.get("metric_definition"),row.get("metric_version"))
        # C2 is optional and independent of C5; a valid niche can exist
        # even when the overall Bayesian comparison sample is insufficient.
        c["c2_niche_outlier"]=compute_c2_niche_diagnostic(row,cohorts[key])
        creator=freeze_diagnostic_peers(row,cohorts[key],purpose="creator")
        if creator["state"]=="frozen_diagnostic_unapproved":
            c["c1_baseline_cohort_sha256"]=creator["cohort_sha256"]
        cohort=freeze_diagnostic_peers(row,cohorts[key],purpose="bayesian")
        if cohort["state"]!="frozen_diagnostic_unapproved":
            c["limitations"].append("c5_"+cohort["reason"])
            continue
        peers=cohort["peers"]
        peer_authors={str(other.get("creator_id")) for other,_ in peers}
        c["c5_peer_cohort_sha256"]=cohort["cohort_sha256"]
        c["c5_peer_snapshot_ids"]=[
            raw.get("id") for _,raw in peers]
        total_views=sum(raw["views"] for _,raw in peers)
        if total_views<=0:
            c["limitations"].append("c5_zero_peer_views")
            continue
        # Decimal division must be quantized to the precise original C5 input
        # contract (NUMERIC(30,12)). This is a MI diagnostic cohort, NOT the
        # original signed prior snapshot required for official C5/C7.
        q=Decimal("0.000000000001")
        prior=lambda field:(Decimal(sum(raw[field] for _,raw in peers)) /
                            Decimal(total_views)).quantize(q)
        k=Decimal(str(median([raw["views"] for _,raw in peers]))).quantize(q)
        if k<=0:
            c["limitations"].append("c5_nonpositive_empirical_k")
            continue
        try:
            bayes=compute_bayesian_engagement(
                newest["views"],newest["likes"],newest["comments"],newest["shares"],
                k,prior("likes"),prior("comments"),prior("shares"))
            c["c5"]={
                "state":"observed_peer_prior_unapproved",
                "cohort_sha256":cohort["cohort_sha256"],
                "comparison_count":len(peers),
                "independent_comparison_authors":len(peer_authors),
                "k":str(bayes.k_used),
                "prior_like_rate":str(bayes.p_l_used),
                "prior_comment_rate":str(bayes.p_c_used),
                "prior_share_rate":str(bayes.p_s_used),
                "weighted_engagement_smoothed":str(bayes.weighted_engagement_smoothed)
            }
            if (c["c1_creator_ratio"] is not None and
                c["c3_views_growth_per_hour"] is not None and
                c["c6_freshness"] is not None):
                score=compute_breakout_components(
                    Decimal(c["c1_creator_ratio"]),
                    Decimal(c["c3_views_growth_per_hour"]),
                    bayes.weighted_engagement_smoothed,
                    Decimal(c["c6_freshness"]))
                c["c7_breakout_preview"]={
                    "state":"diagnostic_unapproved_not_persistable",
                    "score":str(score.breakout_score),
                    "weights":"0.45_C1+0.30_C3+0.15_C5+0.10_C6",
                    "source_metric_snapshot_id":c["metric_snapshot_id"],
                    "inputs_sha256":digest({
                        "c1":c["c1_creator_ratio"],
                        "c3":c["c3_views_growth_per_hour"],
                        "c5":c["c5_peer_cohort_sha256"],
                        "c6":c["c6_freshness"],
                        "snapshot":c["metric_snapshot_id"]})
                }
            else:
                c["limitations"].append("c7_missing_comparable_c1_c3_or_c6")
        except (ValueError, ArithmeticError, OverflowError):
            c["limitations"].append("c5_or_c7_input_invalid")
        c["limitations"].append("official_c5_c7_require_approved_config_cohort_and_review")
    return output
