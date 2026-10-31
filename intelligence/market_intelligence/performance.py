"""Standalone Market Intelligence performance engine (Group C math, reimplemented).

Consumes only canonical Discovery metric_snapshots that have already passed
source/policy/time/precision guards. Never calls or reads Group C. Creator
baseline uses all observed comparable posts (not just viral seed selection),
with minimum sample size. Missing evidence remains null.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime,timezone,timedelta
from math import exp, log, isfinite
from statistics import median
from decimal import Decimal
from ._copied.group_c.creator_ratio import compute_creator_ratio
from ._copied.group_c.views_growth import compute_views_growth_per_hour
from .contracts import MarketError

METHOD="mi-copied-c-exact-performance/v2"
MIN_CREATOR_BASELINE=5
MIN_FORMAT_CREATORS=3
MAX_POSTS=2000
AGE_FACTOR=2.0

def timestamp(value):
    if isinstance(value,datetime):
        if value.tzinfo is None:return None
        return value.astimezone(timezone.utc)
    if not isinstance(value,str):return None
    try:
        dt=datetime.fromisoformat(value.replace("Z","+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (ValueError,OverflowError):return None

def age_hours(published,observed):
    p,o=timestamp(published),timestamp(observed)
    return (o-p).total_seconds()/3600 if p and o and o>=p else None

def _ratio(views,baseline):
    if type(views) is not int or views<0 or baseline is None or baseline<=0:return None
    # The copied Group C exact NUMERIC kernel; the MI epsilon is deliberately
    # tiny and fixed, rather than an unapproved Group C production config.
    calculated=compute_creator_ratio(views,Decimal(str(baseline)),
                                       Decimal("0.000000000001"))
    return round(float(calculated),5)

def _number(value):
    return value if type(value) is int and 0<=value<=2**63-1 else None

def enrich(rows: list[dict], histories: dict[int,list[dict]]|None=None) -> list[dict]:
    """Compute author-relative medians + growth from eligible chronological snapshots.

    Histories have already been checked for policy, definition, source, scope,
    retention and data_quality_status by the SQL reader.
    """
    if len(rows)>MAX_POSTS:raise MarketError("too_many_videos","Слишком много видео для анализа.")
    histories=histories or {}
    annotated=[]
    groups=defaultdict(list)
    for row in rows:
        vid=row["video_id"];platform=row["platform"];author=row.get("creator_id")
        latest=(histories.get(vid) or [None])[-1]
        rec=dict(row)
        rec["creator_ratio"]=None;rec["creator_baseline_views"]=None
        rec["creator_baseline_post_count"]=0
        rec["views_growth_per_hour"]=None
        rec["views_growth_per_hour_exact"]=None
        rec["creator_ratio_exact"]=None
        rec["engagement_rate"]=None;rec["freshness_score"]=None
        rec["metric_analysis_method"]=METHOD
        if latest is not None:
            rec["views"]=_number(latest.get("views"))
            rec["metric_snapshot_id"]=latest.get("id")
            rec["metric_observed_at"]=latest.get("observed_at")
            rec["metric_source_id"]=latest.get("source_id")
            rec["metric_provider"]=latest.get("metric_provider") or latest.get("source_id")
            rec["metric_definition"]=latest.get("source_metric_definition")
            rec["metric_version"]=latest.get("source_metric_version")
            rec["publication_age_hours"]=age_hours(rec.get("published_at"),latest.get("observed_at"))
            views=rec["views"]
            if views is not None and views>0:
                counts=[_number(latest.get(k)) for k in ("likes","comments","shares")]
                if all(c is not None for c in counts):
                    rec["engagement_rate"]=round(sum(counts)/views,6)
            if rec["publication_age_hours"] is not None:
                rec["freshness_score"]=round(100*exp(-log(2)*rec["publication_age_hours"]/48),4)
            hist=histories.get(vid) or []
            if len(hist)>1:
                before=hist[-2]
                old,t0=_number(before.get("views")),timestamp(before.get("observed_at"))
                new,t1=views,timestamp(latest.get("observed_at"))
                if (old is not None and new is not None and t0 and t1 and t1>t0 and new>=old
                    and before.get('source_id')==latest.get('source_id')
                    and before.get('source_metric_definition')==latest.get('source_metric_definition')
                    and before.get('source_metric_version')==latest.get('source_metric_version')):
                    exact=compute_views_growth_per_hour(old,new,t0,t1)
                    rec["views_growth_per_hour"]=round(float(exact),4)
                    rec["views_growth_per_hour_exact"]=str(exact)
            if author is not None and rec["publication_age_hours"] is not None:
                key=(platform,str(author),rec["metric_provider"],
                     rec["metric_definition"],rec["metric_version"])
                groups[key].append(rec)
        annotated.append(rec)
    for rec in annotated:
        if rec.get("publication_age_hours") is None or rec["metric_snapshot_id"] is None:continue
        age=rec["publication_age_hours"]
        key=(rec["platform"],str(rec["creator_id"]),rec.get("metric_provider"),
             rec.get("metric_definition"),rec.get("metric_version"))
        comps=[]
        for other in groups.get(key,[]):
            if other["video_id"]==rec["video_id"]:continue
            other_age=other["publication_age_hours"]
            if age is None or other_age is None or age<1 or other_age<1:continue
            if not (age/AGE_FACTOR<=other_age<=age*AGE_FACTOR):continue
            # Baseline must not include a target post or comparison with
            # a different platform/source/version/age window.
            if type(other.get("views")) is int:comps.append(other["views"])
        rec["creator_baseline_post_count"]=len(comps)
        if len(comps)>=MIN_CREATOR_BASELINE:
            rec["creator_baseline_views"]=median(comps)
            rec["creator_ratio"]=_ratio(rec["views"],rec["creator_baseline_views"])
            if rec["creator_ratio"] is not None:
                rec["creator_ratio_exact"]=str(compute_creator_ratio(
                    rec["views"],Decimal(str(rec["creator_baseline_views"])),
                    Decimal("0.000000000001")))
    return annotated
