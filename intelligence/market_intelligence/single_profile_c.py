"""Single-profile, source-honest Group C diagnostic.

A reused immutable Bright Data profile snapshot can supply source-reported
counts, but NEVER a verified observation clock or independent-creator cohort.
This module performs real copied C1/C4 mathematics and descriptive publication
recency, leaving strict C0/C2/C3/C5/C6/C7 absent when prerequisites are missing.
No provider access, database writes, model calls or network in this module.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from statistics import median
from urllib.parse import urlsplit

from intelligence.discovery.normalize import canonical_video_url, CandidateError
from .brightdata_provider import normalize_record
from .metric_fields import numeric_fields
from .tiktok_top100 import canonical_tiktok_profile, rank_profile_posts
from ._copied.group_c.creator_ratio import compute_creator_ratio
from ._copied.group_c.raw_engagement import compute_raw_engagement

VERSION = "marketing-single-profile-c-diagnostic/v1"
EPSILON = Decimal("0.000000000001")
MIN_CREATOR_PEERS = 5
MAX_POSTS = 5000


def _iso_datetime(value):
    if type(value) is not str:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    return date.astimezone(timezone.utc) if date.tzinfo is not None else None


def _post_kind(url, record):
    kind=urlsplit(url).path.split("/")
    if len(kind)<4 or kind[2] not in ("photo","video"):
        return None
    # Disagreement between the provider and URL invalidates comparison.
    kinds=[]
    for key in ("type","post_type","content_type"):
        value=record.get(key)
        if type(value) is str:
            lower=value.lower()
            if lower in ("photo","photos","image","images","slideshow","carousel"):
                kinds.append("photo")
            elif lower in ("video","clip"):
                kinds.append("video")
    if record.get("is_photo") is True:kinds.append("photo")
    if record.get("is_video") is True:kinds.append("video")
    if kinds and len(set(kinds))>1:return None
    if kinds:return kinds[0]
    return kind[2]


def _exact_author(handle, normalized):
    author=normalized.get("author")
    # Raw TikTok author metadata is not always present. The URL identity
    # is still checked, but do not promote missing author metadata as verified.
    if type(author) is str and author.strip():
        return author.strip().lstrip("@").casefold()==handle
    return True


def analyze_single_profile(username, selection, videos, raw_records, *,
                           frozen_evidence=None, now=None):
    """Compute bounded single-profile C diagnostics from the SAME snapshot.

    Raises on changed ranking, duplicate canonical post IDs, view-counter
    conflict, or changed pinned D1 scope. Does NOT create metric_snapshots.
    Each returned per-post number retains the source identity / uncertainty.
    """
    handle,profile=canonical_tiktok_profile(username)
    if (type(selection) is not dict or selection.get("username")!=handle
        or type(videos) is not list or len(videos)!=100
        or type(raw_records) is not list or len(raw_records)>MAX_POSTS
        or any(type(r) is not dict for r in raw_records)):
        raise ValueError("single_profile_c_invalid_inputs")
    selected=selection.get("posts")
    if type(selected) is not list or len(selected)!=100:
        raise ValueError("single_profile_c_selection_not_exact_100")
    if rank_profile_posts(handle,raw_records,limit=100)["posts"]!=selected:
        raise ValueError("single_profile_c_profile_snapshot_does_not_match_pinned_selection")
    if (len({v.get("video_id") for v in videos})!=100
        or any(p["native_id"]!=v.get("native_id") or
               p["url"]!=v.get("url") or
               v.get("state")!="analysis_partial" for p,v in zip(selected,videos))):
        raise ValueError("single_profile_c_d1_scope_or_analysis_changed")

    if now is None: now=datetime.now(timezone.utc)
    if not isinstance(now,datetime) or now.tzinfo is None:
        raise ValueError("single_profile_c_invalid_report_time")
    now=now.astimezone(timezone.utc)

    pool={}
    for record in raw_records:
        normalized=normalize_record("tiktok",record)
        if normalized is None:continue
        url=normalized["url"]
        kind=_post_kind(url,record)
        if kind is None:continue
        try:
            plat,native,canonical=canonical_video_url(url)
        except (CandidateError,ValueError,TypeError):
            continue
        if plat!="tiktok" or urlsplit(canonical).path.split("/")[1].casefold()!="@"+handle:
            continue
        if not _exact_author(handle,normalized):
            continue
        views=normalized.get("views")
        if type(views) is not int:continue
        normalized={**normalized,
                    "content_kind":"photo_carousel" if kind=="photo" else "video",
                    "numeric_fields":numeric_fields(record),
                    "author_metadata_present":bool(normalized.get("author"))}
        if native in pool:
            # Exact same record repeated is okay; disagreement is NOT.
            first=pool[native]
            if any(first.get(key)!=normalized.get(key) for key in
                   ("views","likes","comments","shares","saves",
                    "published_at","content_kind")):
                raise ValueError("single_profile_c_conflicting_duplicate_provider_record")
        else:
            pool[native]=normalized

    pinned_ids={p["native_id"] for p in selected}
    if not pinned_ids.issubset(pool.keys()):
        raise ValueError("single_profile_c_selected_provider_rows_missing")
    for p in selected:
        raw=pool[p["native_id"]]
        if raw["views"]!=p["views"] or raw["content_kind"]!=p["content_kind"]:
            raise ValueError("single_profile_c_pinned_counts_or_kind_changed")

    # Only age-matched evidence is eligible for an exploratory C1 baseline.
    # Missing age information means no age-adjusted C1; never substitute
    # 'all Top 100 median' and call it an independent creator baseline.
    age={}
    for native,raw in pool.items():
        t=_iso_datetime(raw.get("published_at"))
        if t and t<=now:
            age[native]=(now-t).total_seconds()/3600
    evidence_by_id={}
    if frozen_evidence is not None:
        if (type(frozen_evidence) is not list or len(frozen_evidence)!=100
            or len({r.get("video_id") for r in frozen_evidence})!=100):
            raise ValueError("single_profile_c_frozen_ab_evidence_not_100")
        evidence_by_id={r["video_id"]:r for r in frozen_evidence}
        if set(evidence_by_id)!={v["video_id"] for v in videos}:
            raise ValueError("single_profile_c_frozen_ab_evidence_ids_changed")

    results=[]
    counts=Counter()
    for p,v in zip(selected,videos):
        native=p["native_id"]
        raw=pool[native]
        views=p["views"]
        target_age=age.get(native)
        peers=[]
        if target_age is not None and target_age>=1:
            for other_native,other in pool.items():
                if other_native==native or other["content_kind"]!=p["content_kind"]:
                    continue
                other_age=age.get(other_native)
                if (other_age is None or other_age<1
                    or not target_age/2<=other_age<=target_age*2):
                    continue
                peers.append(other["views"])
        c1=None
        if len(peers)>=MIN_CREATOR_PEERS and median(peers)>0:
            baseline=Decimal(str(median(peers)))
            c1={
                "state":"diagnostic_same_profile_age_matched_not_certified",
                "creator_baseline_views":str(baseline),
                "comparison_posts":len(peers),
                "creator_ratio":str(compute_creator_ratio(views,baseline,EPSILON)),
                "age_matching":"publication_age_0.5x_to_2x_as_of_report_time",
                "source_full_author_history_verified":False,
                "approved_creator_cohort":False,
            }
            counts["C1_diagnostic"]+=1
        counters={key:raw.get(key) for key in
                  ("views","likes","comments","shares","saves")}
        c4=None
        if views>0 and all(type(counters[key]) is int
                           for key in ("likes","comments","shares")):
            calc=compute_raw_engagement(
                views,counters["likes"],counters["comments"],counters["shares"])
            c4={
                "state":"source_reported_counters_exact_math_time_unverified",
                "like_rate_fraction":str(calc.like_rate_fraction),
                "comment_rate_fraction":str(calc.comment_rate_fraction),
                "share_rate_fraction":str(calc.share_rate_fraction),
                "engagement_rate_fraction":str(calc.engagement_rate_fraction),
                "weighted_engagement_fraction":str(calc.weighted_engagement_raw_fraction),
                "engagement_rate_percent":str(calc.as_percentages().engagement_rate_percent),
                "saves_included":False,
                "c0_approved":False,
            }
            counts["C4_diagnostic"]+=1

        # Descriptive age at report time is not original C6, which requires
        # the *source observation instant*, not the worker/report clock.
        publication_age=(
            {"state":"descriptive_as_of_report_not_C6",
             "published_at":raw["published_at"],
             "publication_age_hours_as_of_report":round(target_age,4)}
            if target_age is not None else None)
        if publication_age:counts["publication_age_descriptive"]+=1
        ab=evidence_by_id.get(v["video_id"],{})
        result={
            "video_id":v["video_id"],
            "platform_native_id":native,
            "url":p["url"],
            "content_kind":p["content_kind"],
            "views":views,
            "likes":counters["likes"],
            "comments":counters["comments"],
            "shares":counters["shares"],
            "saves":counters["saves"],
            "provider_numeric_fields":raw["numeric_fields"],
            "provider_author_metadata_present":raw["author_metadata_present"],
            "a_report_present":bool(ab.get("group_a")),
            "b_report_present":bool(ab.get("group_b")),
            "format_family":ab.get("format_family"),
            "carousel_format":ab.get("carousel_format"),
            "C0":{"state":"source_observation_time_not_verified","official_eligible":False},
            "C1":c1,
            "C2":None,
            "C3":None,
            "C4":c4,
            "C5":None,
            "C6":None,
            "C7":None,
            "publication_age":publication_age,
            "not_calculable":{
                "C2":"independent_niche_authors_not_supplied",
                "C3":"two_verified_source_observations_not_supplied",
                "C5":"independent_peer_prior_and_approval_not_supplied",
                "C6":"verified_metric_observed_at_not_supplied",
                "C7":"C3_C5_C6_and_approval_not_available",
            },
        }
        results.append(result)
    counts["profile_views"]=len(results)
    counts["C0_official"]=0
    counts["C2_official"]=0
    counts["C3_official"]=0
    counts["C4_official"]=0
    counts["C5_official"]=0
    counts["C6_official"]=0
    counts["C7_official"]=0
    counts["A_reports"]=sum(r["a_report_present"] for r in results)
    counts["B_reports"]=sum(r["b_report_present"] for r in results)
    document={
        "schema_version":VERSION,
        "scope":"one_tiktok_profile_same_immutable_provider_snapshot",
        "profile":profile,
        "profile_snapshot_id":selection["profile_snapshot_id"],
        "profile_dataset_id":selection.get("profile_dataset_id"),
        "analysis_utc":now.isoformat(),
        "source_observation_time_verified":False,
        "full_author_history_verified":False,
        "source_record_count":len(raw_records),
        "validated_author_post_count":len(pool),
        "selected":len(results),
        "coverage":dict(counts),
        "official_c0_to_c7_certified":False,
        "external_requests_in_pure_calculation":0,
        "limitations":[
            "no_fabricated_source_observation_instant",
            "profile_data_not_source_verified_C0",
            "C1_diagnostic_uses_unverified_profile_history_and_as_of_report_age",
            "C4_math_valid_but_source_observation_clock_unverified",
            "C2_C5_excluded_multi_author",
            "C3_requires_two_real_same_source_observation_times",
            "C6_cannot_use_report_clock_as_source_observation_time",
            "C7_requires_real_C3_C5_C6_and_approval",
        ],
        "posts":results,
    }
    # Stable identity fingerprint excluding retrieval date, output location and
    # API bearer details. No raw captions, authors' private data or comments.
    stable=[{k:r[k] for k in
            ("video_id","platform_native_id","views","likes","comments","shares","saves")}
            for r in results]
    document["counter_evidence_sha256"]=sha256(
        json.dumps(stable,ensure_ascii=False,separators=(",",":"),
                   sort_keys=True).encode("utf-8")).hexdigest()
    return document
