"""Descriptive Unified Analytics over *qualified* A/B/C snapshots only.

No unapproved score weighting, cross-platform pooling, significance claims or
causal attribution. Group by fixed A/B/SRE/C versions to prevent silent mixing.
"""
from __future__ import annotations
import re
from collections import defaultdict
from decimal import Decimal
from statistics import median

from .contracts import AnalyticsError, _json_value
from .service import _all, _connect, _source

DIMENSIONS = frozenset({
    "content_kind", "video_format", "hook_mechanism", "story_role",
    "editing_type", "subtitle_type", "cta_type", "topic_id",
})
MAX_INPUT_POSTS = 50000

def _dimension_values(row: dict, dimension: str) -> list[str]:
    b = row.get("features_b") or {}
    if dimension == "content_kind":
        return [row["content_kind"]] if row.get("content_kind") else []
    if dimension == "video_format":
        fmt = b.get("format") or {}
        key = "video_primary" if row.get("content_kind") in ("video","slideshow_video") else "carousel_format"
        return [str(fmt[key])] if fmt.get(key) else []
    if dimension == "hook_mechanism":
        return sorted(set(str(s) for s in (b.get("hook") or {}).get("mechanisms",[]) if s))
    if dimension == "story_role":
        story = b.get("story") or {}
        found=[]
        for part in ("frames","intervals","slides"):
            found += [x["role"] for x in (story.get(part) or []) if x.get("role")]
        return sorted(set(found))
    if dimension == "editing_type":
        key = (b.get("editing") or {}).get("editing_type")
        return [str(key)] if key else []
    if dimension == "subtitle_type":
        key = (b.get("subtitles") or {}).get("subtitle_type")
        return [str(key)] if key else []
    if dimension == "cta_type":
        types = sorted(set(str(c["cta_type"]) for c in b.get("ctas",[]) if c.get("cta_type")))
        if not types and (b.get("cta_summary") or {}).get("has_cta") is False:
            return ["none_observed"]
        return types
    if dimension == "topic_id":
        topic = (b.get("topic") or {}).get("topic_id")
        return [str(topic)] if topic is not None else []
    raise AnalyticsError("unknown_dimension","Unsupported analytics dimension")

def _percentile(sorted_values, numerator: int, denominator: int) -> Decimal:
    # Linear interpolation between sample indices, deterministic for even n.
    if len(sorted_values)==1:
        return sorted_values[0]
    idx=Decimal(len(sorted_values)-1)*Decimal(numerator)/Decimal(denominator)
    lo=int(idx)
    return sorted_values[lo]+(sorted_values[min(lo+1,len(sorted_values)-1)]-sorted_values[lo])*(idx-lo)

def summarize_qualified(rows: list[dict], dimension: str, min_sample: int) -> dict:
    if dimension not in DIMENSIONS:
        raise AnalyticsError("unknown_dimension","Unsupported analytics dimension")
    if type(min_sample) is not int or not 2<=min_sample<=10000:
        raise AnalyticsError("invalid_min_sample","Explicit minimum sample must be 2..10000")
    if len(rows)>MAX_INPUT_POSTS:
        raise AnalyticsError("too_many_posts","Narrow project/source/time window to 50000 posts",413)
    groups=defaultdict(list)
    seen=set()
    for row in rows:
        if row.get("status")!="qualified":
            raise AnalyticsError("unqualified_input","Aggregation never accepts partial/blocked rows")
        if row.get("score_status")!="complete" or row.get("breakout_score") is None:
            raise AnalyticsError("incomplete_score","Cannot aggregate unverified C score")
        if row.get("source_metric_definition") is None:
            raise AnalyticsError("unknown_metric_definition","Source metric definition must be known")
        cversion=row.get("calculation_config_id")
        if type(cversion) is not int:
            raise AnalyticsError("invalid_c_version","C config ID required")
        base=(
            row.get("platform"),row.get("content_kind"),row.get("source_metric_definition"),
            (row.get("features_a") or {}).get("analysis_version"),
            (row.get("features_b") or {}).get("analysis_version"),
            (row.get("features_b") or {}).get("taxonomy_version"),
            (row.get("lineage") or {}).get("relevance_taxonomy_version_id"),
            cversion,
        )
        ident=(row.get("video_id"),base)
        if ident in seen:
            raise AnalyticsError("duplicate_qualified_video","Deduplicate video snapshots before aggregation")
        seen.add(ident)
        raw=row.get("breakout_score")
        try:
            value=Decimal(str(raw))
        except Exception:
            raise AnalyticsError("invalid_score","Invalid C score")
        if not value.is_finite():
            raise AnalyticsError("invalid_score","Nonfinite C score")
        for group_value in _dimension_values(row,dimension):
            groups[(base,group_value)].append(value)
    segments=[]
    suppressed=0
    for (base,value),scores in groups.items():
        if len(scores)<min_sample:
            suppressed+=1
            continue
        scores.sort()
        med=median(scores)
        segments.append({
            "stratum":{
                "platform":base[0],"content_kind":base[1],
                "source_metric_definition":base[2],
                "a_version":base[3],"b_analysis_version":base[4],
                "b_taxonomy_version":base[5],
                "sre_taxonomy_version_id":base[6],
                "c_config_id":base[7],
            },
            "dimension":dimension,"value":value,"sample_size":len(scores),
            "breakout_median":str(med),
            "breakout_p25":str(_percentile(scores,1,4)),
            "breakout_p75":str(_percentile(scores,3,4)),
            "breakout_mean":str(sum(scores,Decimal(0))/len(scores)),
        })
    segments.sort(key=lambda x:(-x["sample_size"],x["stratum"]["platform"] or "",
                                 str(x["stratum"]["c_config_id"]),x["value"]))
    return {
        "dimension":dimension,
        "min_sample":min_sample,
        "qualified_video_count":len(seen),
        "descriptive_only":True,
        "confidence_intervals_computed":False,
        "causal_claims_allowed":False,
        "low_sample_groups_suppressed":suppressed,
        "segments":segments,
    }

def aggregate_project(project_id: str, source_id: str, c_config_id: int,
                      dimension: str, min_sample: int, *, connect=None) -> dict:
    if type(project_id) is not str or not re.fullmatch(r"[0-9a-f]{32}",project_id):
        raise AnalyticsError("invalid_project","Canonical project ID required")
    if type(c_config_id) is not int or c_config_id<1:
        raise AnalyticsError("invalid_c_config","Explicit C configuration ID required")
    # Validate before any DB work, including source rights.
    summarize_qualified([],dimension,min_sample)
    factory=connect or _connect
    with factory() as conn:
        _source(conn,source_id)
        rows=_all(conn, """
          SELECT DISTINCT ON (u.video_id)
            u.video_id,u.content_kind,u.features_a,u.features_b,u.lineage,u.status,
            v.platform,m.source_metric_definition,c.calculation_config_id,
            c.score_status,c.breakout_score
          FROM analytics_visible_snapshots u
          JOIN videos v ON v.id=u.video_id
          JOIN calculated_metrics c ON c.id=u.calculated_metric_id
          JOIN metric_snapshots m ON m.id=u.metric_snapshot_id
          WHERE u.project_id=%s AND u.source_id=%s AND u.status='qualified'
            AND c.calculation_config_id=%s AND c.score_status='complete'
          ORDER BY u.video_id,u.created_at DESC,u.id DESC
          LIMIT 50001
        """,(project_id,source_id,c_config_id))
        return {
            "project_id":project_id,"source_id":source_id,"calculation_config_id":c_config_id,
            **summarize_qualified(rows,dimension,min_sample),
        }
