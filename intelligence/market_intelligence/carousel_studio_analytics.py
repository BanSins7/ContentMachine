"""Pure, evidence-bounded TikTok carousel cohort analytics.

No provider requests and no additional LLM calls. Groups use VERIFIED image
counts, not estimated post metadata. Account denominator uses the same saved
profile snapshot for all posts (videos and carousels), never mixes refreshed
post-level counts with the snapshot. Missing counters are missing, not zero.
"""
from __future__ import annotations

import json
import math
from statistics import mean, median

SCHEMA="studio-carousel-cohorts/v1"
BUCKETS=(("1-2","1–2 слайда"),("3-5","3–5 слайдов"),
         ("6-8","6–8 слайдов"),("9+","9+ слайдов"))
COUNTERS=("views","likes","comments","shares","saves")
REACTIONS=("likes","comments","shares","saves")
BATCH_SIZE=10


def _json(value):
    if isinstance(value,dict):
        return value
    if isinstance(value,str) and value:
        try:
            parsed=json.loads(value)
            return parsed if isinstance(parsed,dict) else {}
        except ValueError:
            return {}
    return {}


def _number(value):
    if type(value) is int and 0<=value<2**63:
        return value
    return None


def _counts(value):
    raw=_json(value)
    counts=raw.get("observed_counters") or raw.get("counts") or raw
    if not isinstance(counts,dict):
        counts={}
    return {key:_number(counts.get(key)) for key in COUNTERS}


def _er(counts):
    if (counts["views"] is None or counts["views"]<=0
        or any(counts[key] is None for key in REACTIONS)):
        return None
    return 100*sum(counts[key] for key in REACTIONS)/counts["views"]


def _percent(n,d):
    return round(100*n/d,3) if d else None


def _round(value,decimals=3):
    return round(value,decimals) if value is not None else None


def _summary(rows):
    """Ratios use only posts whose complete numerator and denominator are known."""
    counts={key:[x["counts"][key] for x in rows if x["counts"][key] is not None]
            for key in COUNTERS}
    engagements=[x for x in rows if _er(x["counts"]) is not None]
    total_views=sum(x["counts"]["views"] for x in engagements)
    total_actions=sum(sum(x["counts"][key] for key in REACTIONS)
                      for x in engagements)
    ers=[_er(x["counts"]) for x in engagements]
    return {
        "post_count":len(rows),
        "observed":{key:sum(v) if v else None for key,v in counts.items()},
        "coverage":{key:len(v) for key,v in counts.items()},
        "mean_views":_round(mean(counts["views"])) if counts["views"] else None,
        "median_views":_round(median(counts["views"])) if counts["views"] else None,
        "weighted_er_by_views_pct":_round(100*total_actions/total_views)
                                  if total_views>0 else None,
        "median_post_er_pct":_round(median(ers)) if ers else None,
        "mean_post_er_pct":_round(mean(ers)) if ers else None,
        "er_eligible_posts":len(ers),
        "observed_actions_on_er_eligible_posts":total_actions if ers else None,
    }


def _bucket(slides):
    if not isinstance(slides,int) or slides<1:
        return None
    if slides<=2:return "1-2"
    if slides<=5:return "3-5"
    if slides<=8:return "6-8"
    return "9+"


def _verified_slide_count(item):
    manifest=_json(item.get("manifest_json"))
    slides=manifest.get("slides")
    if (not isinstance(slides,list) or not slides
        or len(slides)>1000):
        return None
    indices=[s.get("slide_index") for s in slides if isinstance(s,dict)]
    if indices!=list(range(1,len(slides)+1)):
        return None
    # Must be from a downloaded, ordered manifest, not scraped photo_count.
    return len(slides)


def _signals(rows, group_median_views, group_median_er):
    """Flags are descriptive relative comparisons, not diagnosed causes."""
    low_views=0
    low_er=0
    for row in rows:
        v=row["counts"]["views"]
        er=row["er_pct"]
        if (len(rows)>=5 and group_median_views is not None
            and group_median_views>0 and v is not None
            and v<0.5*group_median_views):
            low_views+=1
        if (len(rows)>=5 and group_median_er is not None
            and group_median_er>0 and er is not None
            and er<0.5*group_median_er):
            low_er+=1
    flags=[]
    if low_views:
        flags.append({
            "type":"below_group_median_views",
            "post_count":low_views,
            "description":"Просмотры ниже половины медианы своей группы",
        })
    if low_er:
        flags.append({
            "type":"below_group_median_er",
            "post_count":low_er,
            "description":"ER ниже половины медианы своей группы",
        })
    return flags


def build(items,channel_posts,*,source_kind,batch_size=BATCH_SIZE):
    """Return deterministic complete drill-down with an explicit denominator."""
    if source_kind not in ("profile","post"):
        raise ValueError("studio_segment_invalid_source")
    if type(batch_size) is not int or batch_size<1 or batch_size>500:
        raise ValueError("studio_segment_invalid_batch_size")
    by_native={}
    for raw in channel_posts:
        native=raw.get("native_id")
        kind=raw.get("kind")
        if not isinstance(native,str) or kind not in ("photo_carousel","video"):
            continue
        if native in by_native:
            raise ValueError("studio_segment_duplicate_channel_post")
        by_native[native]={
            "native_id":native,"kind":kind,
            "counts":_counts(raw.get("counts_json")),
        }
    has_channel=source_kind=="profile" and bool(by_native)
    channel=list(by_native.values()) if has_channel else []
    all_channel=_summary(channel) if has_channel else None
    video_rows=[x for x in channel if x["kind"]=="video"]
    photo_rows=[x for x in channel if x["kind"]=="photo_carousel"]
    classified={key:[] for key,_ in BUCKETS}
    without_manifest=[]
    all_items=[]
    for item in items:
        native=str(item.get("native_id",""))
        slides=_verified_slide_count(item)
        local=_counts(item.get("metrics_json"))
        snapshot=by_native.get(native)
        # For account share and relative ER use the one same-time profile
        # snapshot. Post-detail counters may have a different observation time.
        row={
            "native_id":native,
            "url":item.get("canonical_url"),
            "state":item.get("state"),
            "slide_count":slides,
            "counts":snapshot["counts"] if has_channel and snapshot else (
                {key:None for key in COUNTERS} if has_channel else local),
            "post_detail_counts":local,
            "has_gemini":bool(_json(item.get("analysis_json"))),
        }
        row["er_pct"]=_er(row["counts"])
        all_items.append(row)
        bucket=_bucket(slides)
        if bucket is None:
            without_manifest.append(row)
        else:
            classified[bucket].append(row)
    classified_count=sum(len(x) for x in classified.values())
    account_posts=len(channel) if has_channel else None
    known_views=all_channel["observed"]["views"] if all_channel else None
    channel_view_coverage=all_channel["coverage"]["views"] if all_channel else None
    summaries=[]
    for key,label in BUCKETS:
        rows=classified[key]
        stats=_summary(rows)
        complete_views=stats["observed"]["views"]
        pub_share=_percent(len(rows),account_posts) if has_channel else None
        view_share=(_percent(complete_views,known_views) if has_channel and
                    complete_views is not None and known_views else None)
        # A contribution *ratio* requires comparable complete populations.
        # If some channel posts have unknown views, a normalised ratio using
        # all publication counts would inflate the apparent impact.
        complete_population=(has_channel and
            all_channel["coverage"]["views"]==account_posts and
            stats["coverage"]["views"]==len(rows))
        impact=(round(view_share/pub_share,3) if complete_population
                and view_share is not None and pub_share else None)
        known_account_actions=(all_channel["observed_actions_on_er_eligible_posts"]
                               if all_channel else None)
        actions_share=_percent(stats["observed_actions_on_er_eligible_posts"],
                               known_account_actions) if (
            has_channel and stats["observed_actions_on_er_eligible_posts"] is not None
            and known_account_actions) else None
        account_er=all_channel["weighted_er_by_views_pct"] if all_channel else None
        er_index=(round(stats["weighted_er_by_views_pct"]/account_er,3)
                  if account_er and stats["weighted_er_by_views_pct"] is not None
                  else None)
        ranked=sorted(
            rows,key=lambda row:(row["counts"]["views"] is None,
                                 -(row["counts"]["views"] or 0),row["native_id"]))
        batches=[]
        for start in range(0,len(ranked),batch_size):
            entries=ranked[start:start+batch_size]
            bsummary=_summary(entries)
            batches.append({
                "batch_id":f"{key}-{start//batch_size+1}",
                "label":f"Места {start+1}–{start+len(entries)}",
                "start_rank":start+1,
                "end_rank":start+len(entries),
                "metrics":bsummary,
                "relative_signals":_signals(
                    entries,stats["median_views"],stats["median_post_er_pct"]),
                "posts":[{
                    "native_id":r["native_id"],"url":r["url"],
                    "views":r["counts"]["views"],
                    "er_pct":_round(r["er_pct"]),
                    "slide_count":r["slide_count"],
                    "state":r["state"],"has_gemini":r["has_gemini"],
                } for r in entries],
            })
        summaries.append({
            "id":key,"label":label,
            "metrics":stats,
            "share_of_verified_carousels_pct":_percent(len(rows),classified_count),
            "share_of_observed_channel_posts_pct":pub_share,
            "share_of_observed_channel_views_pct":view_share,
            "view_impact_index":impact,
            "share_of_observed_channel_actions_pct":actions_share,
            "er_index_vs_channel":er_index,
            "relative_signals":_signals(rows,stats["median_views"],
                                         stats["median_post_er_pct"]),
            "batches":batches,
        })
    # The ranking is deliberately data-driven, not predetermined by slide count.
    leaders=sorted(
        [g for g in summaries if g["view_impact_index"] is not None],
        key=lambda g:(-g["view_impact_index"],g["id"]))
    summary={
        "schema_version":SCHEMA,
        "source_kind":source_kind,
        "scope":"provider_returned_publications_only" if has_channel
                else "selected_post_only_no_channel_comparison",
        "profile_history_complete":False,
        "batch_size":batch_size,
        "channel":{
            "all_posts":all_channel,
            "ordinary_videos":_summary(video_rows) if has_channel else None,
            "photo_posts":_summary(photo_rows) if has_channel else None,
            "publications_with_unknown_kind_excluded":True,
            "known_views_coverage_pct":(
                _percent(channel_view_coverage,account_posts) if has_channel else None),
        },
        "carousel_classification":{
            "with_verified_slide_count":classified_count,
            "without_verified_slide_count":len(without_manifest),
            "number_of_processed_carousels":len(all_items),
            "photo_posts_found_in_profile":len(photo_rows) if has_channel else None,
            "coverage_of_profile_photo_posts_pct":(
                _percent(classified_count,len(photo_rows)) if has_channel else None),
            "unclassified_ids":[r["native_id"] for r in without_manifest],
        },
        "best_relative_impact_group":leaders[0]["id"] if leaders else None,
        "groups":summaries,
        "interpretation_notes":[
            "Доли аккаунта относятся только к публикациям в полученном снимке Bright Data, не к гарантированно всей истории TikTok.",
            "Доля каруселей по длине считается только среди каруселей с проверенным количеством исходных слайдов.",
            "Для ER требуются просмотры >0 и все четыре счётчика: лайки, комментарии, репосты, сохранения. Если хоть один отсутствует, ER=null.",
            "ER weighted = 100 * сумма четырёх реакций / сумма просмотров по публикациям с полными данными. Mean и median ER рассчитываются отдельно.",
            "Для сопоставления с аккаунтом используются счётчики ОДНОГО снимка профиля, без смешивания с поздними снимками отдельных постов.",
            "Индекс вклада = доля наблюдаемых просмотров / доля наблюдаемых публикаций; он выводится только при полной доступности просмотров в снимке и группе, не доказывает причинное влияние длины карусели.",
            "Доля реакций рассчитывается только по постам с полным набором четырёх реакций и просмотрами >0; ER не суммируется по группам.",
            "ER-индекс = взвешенный ER группы / взвешенный ER аккаунта (для доступных публикаций с полными счётчиками).",
            "Батчи отсортированы по просмотрам; публикации без числа просмотров идут последними.",
            "Сигналы являются гипотезами для ручной проверки, а не выявленными причинами низких просмотров.",
            "Категория 9+ сохраняет длинные карусели; однофотографические посты могут не проходить проверку downloader как карусели.",
        ],
    }
    return summary
