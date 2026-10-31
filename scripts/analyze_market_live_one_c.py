"""Offline C4 exact-math and C6 clock-based preview from one LIVE Bright Data metrics JSON.

This script makes no provider requests, no DB writes, no QDP calls, and never
labels a local saved_at timestamp as a provider-verified C0 observation.
Usage:
python -m scripts.analyze_market_live_one_c --state PATH --metrics PATH --output PATH
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys

from intelligence.market_intelligence._copied.group_c.raw_engagement import compute_raw_engagement
from intelligence.market_intelligence._copied.group_c.freshness import compute_freshness_score
from scripts.probe_market_brightdata_one import _safe_read

MAX_METRIC_BYTES=200_000
METRIC_SCHEMA="market-brightdata-single-video-metrics/v1"
REPORT_SCHEMA="market-live-one-group-c-offline-preview/v1"
MAX_COUNT=2**63-1


def _date(value,field):
    if type(value) is not str:
        raise ValueError(field+"_missing")
    try:
        dt=datetime.fromisoformat(value.replace("Z","+00:00"))
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError()
        return dt.astimezone(timezone.utc)
    except (ValueError,OverflowError):
        raise ValueError(field+"_invalid") from None


def _count(value,key,positive=False):
    if type(value) is not int or value<0 or value>MAX_COUNT or (positive and value==0):
        raise ValueError("invalid_exact_counter_"+key)
    return value


def analyze(*,state_file:Path,metrics_file:Path)->dict:
    state=_safe_read(state_file)
    if state.get("snapshot_id") is None:
        raise ValueError("snapshot_not_verified")
    if state.get("state")!="metrics_saved":
        raise ValueError("state_not_metrics_saved")
    if state.get("recovery_kind")!="verified_one_video_snapshot_from_read_only_listing":
        raise ValueError("video_source_verification_missing")
    file_size=metrics_file.stat().st_size
    if not 0<file_size<=MAX_METRIC_BYTES:
        raise ValueError("metrics_file_size_invalid")
    raw_bytes=metrics_file.read_bytes()
    content_hash=sha256(raw_bytes).hexdigest()
    if state.get("metrics_file_sha256")!=content_hash:
        raise ValueError("metrics_file_sha256_mismatch")
    try:
        metrics=json.loads(raw_bytes)
    except (ValueError,UnicodeError):
        raise ValueError("metrics_invalid_json") from None
    if type(metrics) is not dict or metrics.get("schema_version")!=METRIC_SCHEMA:
        raise ValueError("metrics_schema_mismatch")
    for key in ("snapshot_id","dataset_id","platform"):
        if metrics.get(key)!=state.get(key):
            raise ValueError("metrics_source_mismatch_"+key)
    expected_url_hash=sha256(state["url"].encode("utf-8")).hexdigest()
    if metrics.get("url_sha256")!=expected_url_hash:
        raise ValueError("metrics_video_identity_mismatch")
    if metrics.get("provider")!="brightdata":
        raise ValueError("metrics_provider_mismatch")
    if metrics.get("not_full_group_c_certification") is not True:
        raise ValueError("metrics_certification_marker_missing")
    if metrics.get("not_a_second_metric_snapshot") is not True:
        raise ValueError("metrics_snapshot_scope_invalid")
    if metrics.get("collection_state")!="one_observation_only":
        raise ValueError("metrics_collection_scope_invalid")
    views=_count(metrics.get("views"),"views",positive=True)
    likes=_count(metrics.get("likes"),"likes")
    comments=_count(metrics.get("comments"),"comments")
    shares=_count(metrics.get("shares"),"shares")
    saves=metrics.get("saves")
    if saves is not None:
        saves=_count(saves,"saves")
    numeric=metrics.get("numeric_fields")
    if type(numeric) is not dict:
        raise ValueError("numeric_fields_invalid")
    # Reject a tampered metric alias. Do not add both aliases as engagements.
    for key,canonical in (("play_count",views),("digg_count",likes),
                          ("comment_count",comments),("share_count",shares),
                          ("num_share_count",shares),("collect_count",saves)):
        observed=numeric.get(key)
        if observed is not None and canonical is not None and observed!=canonical:
            raise ValueError("numeric_alias_mismatch_"+key)
    reported_at=_date(metrics.get("observed_at"),"local_saved_at")
    published_at=_date(metrics.get("published_at"),"published_at")
    if reported_at<published_at:
        raise ValueError("publication_after_local_save")
    raw=compute_raw_engagement(views,likes,comments,shares)
    pct=raw.as_percentages()
    c4={
        "state":"exact_math_from_provider_public_counters_not_c0_certified",
        "source_counters":{"views":views,"likes":likes,"comments":comments,"shares":shares},
        "like_rate_fraction":str(raw.like_rate_fraction),
        "comment_rate_fraction":str(raw.comment_rate_fraction),
        "share_rate_fraction":str(raw.share_rate_fraction),
        "engagement_rate_fraction":str(raw.engagement_rate_fraction),
        "weighted_engagement_fraction":str(raw.weighted_engagement_raw_fraction),
        "engagement_rate_percent":str(pct.engagement_rate_percent),
        "weighted_engagement_percent":str(pct.weighted_engagement_percent),
        "saves_excluded_from_official_c4":True,
        "duplicate_share_aliases_not_double_counted":True,
    }
    diagnostic_c6={
        "state":"clock_based_preview_only_not_source_verified",
        "freshness_score":str(compute_freshness_score(published_at,reported_at)),
        "published_at":published_at.isoformat(),
        "clock_value_used":reported_at.isoformat(),
        "clock_origin":"local_json_saved_at_not_provider_observed_at",
        "eligible_as_original_group_c_c6":False,
    }
    other={"saves_count":saves,
           "saves_per_view_fraction":str(Decimal(saves)/Decimal(views)) if saves is not None else None,
           "total_public_interactions_with_saves":likes+comments+shares+saves if saves is not None else None}
    return {
        "schema_version":REPORT_SCHEMA,
        "platform":state["platform"],
        "provider":"brightdata",
        "snapshot_id":state["snapshot_id"],
        "source_metrics_sha256":content_hash,
        "video_url_sha256":expected_url_hash,
        "actual_public_counter_observation":True,
        "provider_observation_timestamp_verified":False,
        "source_rights_or_D1_quality_gate_certified":False,
        "group_c_full_certification":False,
        "c0":{"state":"unavailable","reason":"no_source_verified_D1_observed_at_or_policy_receipt"},
        "c1":{"state":"unavailable","reason":"creator_peer_baseline_missing"},
        "c2":{"state":"unavailable","reason":"niche_cohort_missing"},
        "c3":{"state":"unavailable","reason":"single_observation_no_growth_pair"},
        "c4":c4,
        "c5":{"state":"unavailable","reason":"comparable_peer_prior_missing"},
        "c6":diagnostic_c6,
        "c7":{"state":"unavailable","reason":"missing_approved_c1_c3_c5_c6"},
        "independent_numeric_fields":len(numeric)-(
            1 if "share_count" in numeric and "num_share_count" in numeric else 0),
        "extra_descriptive_metrics_not_c4":other,
        "watch_time_or_retention_inferred":False,
        "network_requests":0,
        "paid_triggers":0,
        "database_writes":0,
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state",type=Path,required=True)
    parser.add_argument("--metrics",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError("output_already_exists_no_overwrite")
        report=analyze(state_file=args.state,metrics_file=args.metrics)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        # No replacement of a prior local report.
        with args.output.open("x",encoding="utf-8") as f:
            json.dump(report,f,indent=2,ensure_ascii=False)
            f.write("\n")
        print("Offline Marketing Group C diagnostic saved:",args.output)
        print("C4 engagement (%):",report["c4"]["engagement_rate_percent"])
        print("C4 weighted engagement (%):",report["c4"]["weighted_engagement_percent"])
        print("C6 local-clock preview (NOT source-certified):",report["c6"]["freshness_score"])
        print("C0/C1/C2/C3/C5/C7 remain unavailable; C6 not certified.")
        print("Provider requests: 0; paid triggers: 0; DB writes: 0.")
    except (ValueError,OSError,OverflowError,ArithmeticError) as exc:
        code="local_file_error" if isinstance(exc,OSError) else (
            str(exc) if isinstance(exc,ValueError) else "numeric_calculation_failed")
        parser.exit(2,"Blocked: "+code+"\n")
    return 0


if __name__=="__main__":
    sys.exit(main())
