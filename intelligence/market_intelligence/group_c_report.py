"""Independent Marketing Group C source-checked C0–C7 numerical receipts.

This is a self-contained read-only verifier of computations using copied C
math. It cannot authorize D1 rights, human approvals, or an official global
C7 score; it makes diagnostic calculations reproducible and tamper-evident.
The service passes source-policy-filtered raw D1 observations under the same
REPEATABLE READ transaction used to freeze Marketing snapshots.
"""
from __future__ import annotations
from decimal import Decimal
from .contracts import digest
from ._copied.group_c.creator_ratio import compute_creator_ratio
from ._copied.group_c.views_growth import compute_views_growth_per_hour
from ._copied.group_c.raw_engagement import compute_raw_engagement
from ._copied.group_c.bayesian_engagement import compute_bayesian_engagement
from ._copied.group_c.freshness import compute_freshness_score
from ._copied.group_c.breakout_score import compute_breakout_components
from .performance import timestamp

VERSION="mi-group-c-c0-c7-native-proof/v1"
Q=Decimal("0.000000000001")

def _equal(a,b):
    return Decimal(str(a))==Decimal(str(b))

def build_report(row,history):
    if type(row) is not dict or type(history) is not list:
        raise ValueError("c_report_input_invalid")
    c=row.get("group_c") or {}
    q=c.get("c0_quality_gate") or {}
    if q.get("schema_version")!="mi-c0-exact-metric-evidence/v1":
        raise ValueError("c0_quality_receipt_missing")
    if q.get("video_id")!=row.get("video_id"):
        raise ValueError("c_report_video_identity_changed")
    if c.get("original_full_c_certified") is not False:
        raise ValueError("c_unreviewed_global_certification_forbidden")
    raw=history[-1] if history else None
    verified={}
    if c.get("c0")=="eligible_latest_snapshot":
        if raw is None or q.get("state")!="eligible_exact_observation":
            raise ValueError("c0_mismatched_latest")
        if (q.get("snapshot_id")!=raw.get("id",row.get("metric_snapshot_id"))
            or q.get("source_id")!=raw.get("source_id",row.get("metric_source_id"))
            or q.get("metric_definition")!=raw.get("source_metric_definition",
                                                    row.get("metric_definition"))):
            raise ValueError("c0_snapshot_lineage_changed")
        if any(type(raw.get(x)) is not int for x in ("views","likes","comments","shares")):
            raise ValueError("c0_counter_not_exact")
        if c.get("c4") is not None:
            x=compute_raw_engagement(*(raw[k] for k in ("views","likes","comments","shares")))
            for k,actual in {
                "like_rate_fraction":x.like_rate_fraction,
                "comment_rate_fraction":x.comment_rate_fraction,
                "share_rate_fraction":x.share_rate_fraction,
                "engagement_rate_fraction":x.engagement_rate_fraction,
                "weighted_engagement_fraction":x.weighted_engagement_raw_fraction,
            }.items():
                if not _equal(c["c4"][k],actual):
                    raise ValueError("c4_original_math_mismatch")
            verified["C4"]=True
        if c.get("c6_freshness") is not None:
            published=timestamp(row.get("published_at"))
            observed=timestamp(raw.get("observed_at"))
            if published is None or observed is None or not _equal(
                 c["c6_freshness"],compute_freshness_score(published,observed)):
                raise ValueError("c6_original_math_mismatch")
            verified["C6"]=True
        if c.get("c1_creator_ratio") is not None:
            baseline=row.get("creator_baseline_views")
            if baseline is None or not _equal(
                c["c1_creator_ratio"],
                compute_creator_ratio(raw["views"],Decimal(str(baseline)),Q)):
                raise ValueError("c1_original_math_mismatch")
            verified["C1"]=True
        if c.get("c3_views_growth_per_hour") is not None:
            prior=history[-2] if len(history)>1 else None
            if (prior is None or
                q.get("prior_reason") is not None or
                timestamp(prior.get("observed_at")) is None or
                not _equal(c["c3_views_growth_per_hour"],
                    compute_views_growth_per_hour(
                        prior["views"],raw["views"],
                        timestamp(prior["observed_at"]),
                        timestamp(raw["observed_at"])))):
                raise ValueError("c3_original_math_or_pair_mismatch")
            verified["C3"]=True
        if c.get("c5") is not None:
            prior=c["c5"]
            if prior.get("state")!="observed_peer_prior_unapproved":
                raise ValueError("c5_unapproved_priors_mislabeled")
            x=compute_bayesian_engagement(
                raw["views"],raw["likes"],raw["comments"],raw["shares"],
                Decimal(prior["k"]),Decimal(prior["prior_like_rate"]),
                Decimal(prior["prior_comment_rate"]),Decimal(prior["prior_share_rate"]))
            if not _equal(prior["weighted_engagement_smoothed"],
                          x.weighted_engagement_smoothed):
                raise ValueError("c5_original_math_mismatch")
            verified["C5"]=True
        if c.get("c7_breakout_preview") is not None:
            preview=c["c7_breakout_preview"]
            if (preview.get("state")!="diagnostic_unapproved_not_persistable"
                or c.get("c5") is None):
                raise ValueError("c7_without_approved_inputs")
            x=compute_breakout_components(
                Decimal(c["c1_creator_ratio"]),
                Decimal(c["c3_views_growth_per_hour"]),
                Decimal(c["c5"]["weighted_engagement_smoothed"]),
                Decimal(c["c6_freshness"]))
            if not _equal(preview["score"],x.breakout_score):
                raise ValueError("c7_original_math_mismatch")
            verified["C7_math"]=True
        if c.get("c2_niche_outlier"):
            stage=c["c2_niche_outlier"]
            if stage["state"]=="observed_niche_comparison_unapproved" and (
                not stage.get("cohort_sha256") or
                stage.get("excluded_from_breakout_score") is not True):
                raise ValueError("c2_unapproved_niche_lineage")
            verified["C2_source_scoped"]=True
    else:
        if any(c.get(x) is not None for x in (
            "c4","c5","c6_freshness","c7_breakout_preview")):
            raise ValueError("c0_invalid_but_downstream_scored")
    payload={
        "schema_version":VERSION,"video_id":row.get("video_id"),
        "snapshot_id":q.get("snapshot_id"),
        "c0_snapshot_lineage_sha256":q.get("snapshot_lineage_sha256"),
        "c1_cohort_sha256":c.get("c1_baseline_cohort_sha256"),
        "c5_cohort_sha256":c.get("c5_peer_cohort_sha256"),
        "c2_cohort_sha256":(c.get("c2_niche_outlier") or {}).get("cohort_sha256"),
        "checked_stages":sorted(verified),
        "group_c_payload_sha256":digest({
            k:v for k,v in c.items() if k!="native_report"}),
        "state":"diagnostic_verified" if verified else "data_unavailable",
        "full_original_group_c_certified":False,
        "market_snapshot_is_a_diagnostic_not_global_c7_approval":True,
        "source_rights_checked_by_project_sql":True,
    }
    return {**payload,"report_sha256":digest(payload)}


def verify_report(report,c):
    if type(report) is not dict or type(c) is not dict:
        return False
    if report.get("schema_version")!=VERSION or report.get(
            "full_original_group_c_certified") is not False:
        return False
    try:
        return (report.get("report_sha256")==digest({
                k:v for k,v in report.items() if k!="report_sha256"})
            and report.get("group_c_payload_sha256")==digest({
                k:v for k,v in c.items() if k!="native_report"}))
    except (ValueError,TypeError,OverflowError):
        return False
