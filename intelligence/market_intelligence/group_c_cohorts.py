"""Marketing-native C1/C2/C5 cohort selection and content evidence.

A deterministic read-only frozen-in-report peer set for each target, not an
approved external GC SQL cohort. The same platform/provider/view definition,
version, comparable post age and independently observed creator are required.
A niche ID must originate from a valid Marketing B8 native report; text hints
or unapproved guessed label IDs cannot choose financial comparisons.
"""
from __future__ import annotations
from collections import Counter
from decimal import Decimal
from statistics import median
from .contracts import digest
from ._copied.group_c.niche_comparison import compute_niche_comparison

VERSION="mi-c-frozen-diagnostic-cohort/v1"
MIN_PEERS=5
MIN_CREATORS=2
ALPHA=Decimal("0.000000000001")

def _age_match(t,c):
    t_age,c_age=t.get("publication_age_hours"),c.get("publication_age_hours")
    return (type(t_age) in (float,int) and type(c_age) in (float,int)
            and t_age>=1 and c_age>=1 and t_age/2<=c_age<=t_age*2)


def _ident(row):
    return (row.get("platform"),row.get("metric_source_id"),
            row.get("metric_definition"),row.get("metric_version"))


def _topic_id(row):
    b=(row.get("group_b") or {})
    if not isinstance(b,dict):
        return None
    native=b.get("native_report") or {}
    stage=(b.get("blocks") or {}).get("B8") or {}
    mapping=stage.get("taxonomy_match") or {}
    from .group_b_report import verify_group_b_report,verify_group_b_payload
    group_a=row.get("group_a") or {}
    if (not verify_group_b_report(
            native,group_a.get("source_media_sha256"),
            group_a.get("report_sha256"))
        or not verify_group_b_payload(native,b)
        or native.get("schema_version")!="mi-group-b-native-report/v1"
        or native.get("original_full_group_b_certified") is not False
        or mapping.get("state")!="catalog_matched"
        or mapping.get("mi_approved_topic_match") is not True
        or native.get("b8_catalog_sha256")!=mapping.get("catalog_sha256")
        or native.get("b8_topic_id")!=mapping.get("topic_id")
        or type(mapping.get("topic_id")) is not int
        or mapping["topic_id"]<=0):
        return None
    return mapping["topic_id"],mapping.get("catalog_sha256")


def freeze_diagnostic_peers(target,observations,*,purpose,require_topic=False):
    """Return evidence fingerprint and actual peer observations or unavailable."""
    if purpose not in ("creator","niche","bayesian"):
        raise ValueError("mi_unknown_cohort_purpose")
    selected=[]
    target_topic=_topic_id(target) if require_topic else None
    if require_topic and target_topic is None:
        return {"state":"unavailable","reason":"b8_approved_topic_missing",
                "purpose":purpose,"peers":[]}
    for row,newest in observations:
        if row["video_id"]==target["video_id"] or newest is None:
            continue
        if _ident(target)!=_ident(row) or not _age_match(target,row):
            continue
        if (type(newest.get("views")) is not int or newest["views"]<0
            or (purpose!="creator" and newest["views"]==0)):
            continue
        if purpose=="creator":
            if target.get("creator_id") is None or target["creator_id"]!=row.get("creator_id"):
                continue
        else:
            if target.get("creator_id") is not None and row.get("creator_id")==target.get("creator_id"):
                continue
            if row.get("creator_id") is None:
                continue
        if require_topic and _topic_id(row)!=target_topic:
            continue
        selected.append((row,newest))
    selected.sort(key=lambda item:item[0]["video_id"])
    authors={str(r.get("creator_id")) for r,_ in selected}
    if len(selected)<MIN_PEERS:
        return {"state":"unavailable","reason":"insufficient_comparable_posts",
                "purpose":purpose,"peer_count":len(selected),"peers":[]}
    if purpose!="creator" and len(authors)<MIN_CREATORS:
        return {"state":"unavailable","reason":"insufficient_independent_authors",
                "purpose":purpose,"peer_count":len(selected),"peers":[]}
    ids=[{
          "video_id":row["video_id"],
          "creator_id":str(row.get("creator_id")),
          "snapshot_id":raw.get("id"),
          "source_id":row.get("metric_source_id"),
          "source_policy_version_id":raw.get("source_policy_version_id"),
          "metric_definition":row.get("metric_definition"),
          "metric_version":row.get("metric_version"),
          "observed_at":str(raw.get("observed_at")),
          "views":raw["views"],
          "likes":raw.get("likes"),"comments":raw.get("comments"),
          "shares":raw.get("shares"),
          "quality":raw.get("data_quality_status"),
          "precision":raw.get("precision"),
          "observation_time_assumed":raw.get("observation_time_assumed")
        } for row,raw in selected]
    evidence={
        "schema_version":VERSION,"purpose":purpose,
        "target_video_id":target["video_id"],
        "target_snapshot_id":target.get("metric_snapshot_id"),
        "topic_and_taxonomy":list(target_topic) if target_topic else None,
        "metric_scope":list(_ident(target)),
        "comparison_age_factor":"2.0",
        "selected_peer_evidence":ids,
        "peer_count":len(ids),"independent_authors":len(authors),
        "sample_selection":"market_discovered_videos_not_complete_platform_universe",
        "approval_state":"not_human_approved"}
    return {**evidence,"cohort_sha256":digest(evidence),
            "state":"frozen_diagnostic_unapproved","reason":None,
            "peers":selected}


def compute_c2_niche_diagnostic(target,observations):
    cohort=freeze_diagnostic_peers(target,observations,
                                   purpose="niche",require_topic=True)
    if cohort["state"]!="frozen_diagnostic_unapproved":
        return {"state":"unavailable","reason":cohort["reason"],
                "outlier_ratio":None}
    v=next((raw for row,raw in observations
           if row["video_id"]==target["video_id"]),None)
    if v is None:return {"state":"unavailable","reason":"target_metrics_missing",
                          "outlier_ratio":None}
    middle=median([raw["views"] for _,raw in cohort["peers"]])
    ratio=compute_niche_comparison(v["views"],str(middle),ALPHA)
    return {"state":"observed_niche_comparison_unapproved",
            "outlier_ratio":str(ratio),
            "niche_median_views":str(middle),
            "peer_count":cohort["peer_count"],
            "independent_creators":cohort["independent_authors"],
            "topic_id":_topic_id(target)[0],
            "cohort_sha256":cohort["cohort_sha256"],
            "alpha_used":str(ALPHA),
            "excluded_from_breakout_score":True}
