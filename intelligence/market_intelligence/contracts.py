"""Pure, deterministic Market Intelligence contracts. No DB or external calls."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from statistics import median

from intelligence.discovery.normalize import canonical_video_url, CandidateError
from intelligence.project_sources.normalize import normalize_url, ReferenceError

SCHEMA = "market-intelligence-snapshot/v1"
MAX_SEEDS = 200
MAX_VIDEOS = 2000


class MarketError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.status = code, status


def digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def validate_seeds(videos: object, authors: object) -> dict:
    """Only supported public platform URLs, with canonical identity and strict bounds."""
    if not isinstance(videos, list) or not isinstance(authors, list):
        raise MarketError("invalid_seeds", "Нужны массивы ссылок на видео и авторов.")
    if not 1 <= len(videos) + len(authors) <= MAX_SEEDS:
        raise MarketError("seed_limit", "Допустимо от 1 до 200 ссылок.")
    result = {"videos": [], "authors": []}
    seen = set()
    for kind, entries in (("videos", videos), ("authors", authors)):
        for raw in entries:
            if not isinstance(raw, str) or not raw.strip() or len(raw) > 2048:
                raise MarketError("invalid_url", "Обнаружена пустая или слишком длинная ссылка.")
            try:
                ref = normalize_url(raw, "user_video_reference" if kind == "videos" else "competitor_account")
            except ReferenceError as exc:
                raise MarketError("invalid_url", str(exc)) from exc
            if ref.platform not in ("tiktok", "instagram", "youtube") or ref.link_kind != ("video" if kind == "videos" else "account"):
                raise MarketError("unsupported_source", "Нужна прямая ссылка на видео или профиль TikTok, Instagram, YouTube.")
            if kind == "videos":
                try:
                    platform, native_id, url = canonical_video_url(ref.canonical_url)
                except CandidateError as exc:
                    raise MarketError("unsupported_video", "Неподдерживаемая ссылка на видео для Discovery.") from exc
            else:
                platform, native_id, url = ref.platform, ref.native_id, ref.canonical_url
            key = (kind, platform, native_id, url)
            if key in seen:
                continue
            seen.add(key)
            result[kind].append({"url": url, "platform": platform, "native_id": native_id})
    result["videos"].sort(key=lambda r: (r["platform"], r["native_id"]))
    result["authors"].sort(key=lambda r: (r["platform"], r["url"]))
    return result


def _positive_ratio(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        if number < 0 or number != number or number == float("inf"):
            return None
        return number
    except (ValueError, TypeError, OverflowError):
        return None


def summarize_market(project_id: str, seeds: dict, observations: list[dict],
                     *, truncated: bool = False) -> dict:
    """Group independently measured D1 performance and MI media evidence.

    A caption-derived signature is only a preliminary creative hypothesis.
    Source metrics and trend directions are never inferred from seed URLs.
    """
    if not isinstance(observations, list) or len(observations) > MAX_VIDEOS:
        raise MarketError("observation_limit", "Слишком много наблюдений.", 422)
    formats = defaultdict(list)
    evidence = []
    seen = set()
    for row in observations:
        vid = row.get("video_id")
        if not isinstance(vid, int) or isinstance(vid, bool) or vid <= 0 or vid in seen:
            raise MarketError("invalid_observation", "Видео должно иметь уникальный действительный Discovery ID.")
        seen.add(vid)
        base = {
            "video_id": vid,
            "url": row.get("url"),
            "platform": row.get("platform"),
            "creator_id": row.get("creator_id"),
            "published_at": row.get("published_at"),
            "metric_snapshot_id": row.get("metric_snapshot_id"),
            "metric_observed_at": row.get("metric_observed_at"),
            "views": row.get("views"),
            "mi_analysis_id": row.get("mi_analysis_id"),
            "format_family": row.get("format_family"),
            "creator_ratio": _positive_ratio(row.get("creator_ratio")),
            "creator_baseline_views": _positive_ratio(row.get("creator_baseline_views")),
            "creator_baseline_post_count": row.get("creator_baseline_post_count", 0),
            "views_growth_per_hour": _positive_ratio(row.get("views_growth_per_hour")),
            "engagement_rate": _positive_ratio(row.get("engagement_rate")),
            "freshness_score": _positive_ratio(row.get("freshness_score")),
            "metric_source_id": row.get("metric_source_id"),
            "metric_definition": row.get("metric_definition"),
            "technical": row.get("technical"),
            "group_c": row.get("group_c"),
            "creative_state": row.get("creative_state","unavailable"),
            "cta_type":row.get("cta_type"),
            "cta_text":row.get("cta_text"),
            "visual_subjects":row.get("visual_subjects",[]),
            "frame_evidence_refs":row.get("frame_evidence_refs",[]),
            "visual_evidence":row.get("visual_evidence"),
            "hook_mechanisms": list(row.get("hook_mechanisms") or []),
            "hook_text": (row.get("hook_text") or "")[:320],
            "hook_visual_description": (row.get("hook_visual_description") or "")[:480],
            "story_roles": list(row.get("story_roles") or [])[:12],
        }
        if base["metric_snapshot_id"] is None:
            base["views"] = None
            base["creator_ratio"] = None
            base["views_growth_per_hour"] = None
            base["engagement_rate"] = None
        if base["mi_analysis_id"] is None:
            base["format_family"] = None
            base["hook_mechanisms"] = []
            base["hook_text"] = ""
            base["hook_visual_description"] = ""
            base["story_roles"] = []
        evidence.append(base)
        if base["format_family"]:
            signature = (tuple(sorted(base["hook_mechanisms"])), tuple(base["story_roles"]))
            formats[(base["platform"], base["format_family"], signature)].append(base)
    found = []
    for (platform, family, signature), rows in sorted(formats.items()):
        creators = {r["creator_id"] for r in rows if r["creator_id"] is not None}
        measured = [r for r in rows if r["metric_snapshot_id"] is not None and r["views"] is not None]
        ratios = [r["creator_ratio"] for r in measured if r["creator_ratio"] is not None]
        growth = [r["views_growth_per_hour"] for r in measured
                  if r["views_growth_per_hour"] is not None]
        # Evidence-based preliminary format grouping, not causal proof.
        structural = bool(signature[0] and signature[1])
        readiness = "preliminary_observed" if (structural and len(measured) >= 5
                     and len(creators) >= 3 and len(ratios) >= 5) else "insufficient_evidence"
        found.append({
            "format_key": platform + ":" + family + (":" + digest(signature)[:12] if structural else ""),
            "platform": platform,
            "format_family": family,
            "classification_granularity": "mi_timed_caption_heuristic" if structural else "mi_creative_unknown_or_weak",
            "observed_creative_structure": {
                "hook_mechanisms": list(signature[0]),
                "story_roles": list(signature[1]),
                "hook_examples": [
                    {"video_id": r["video_id"], "text": r["hook_text"],
                     "visual": r["hook_visual_description"]}
                    for r in rows if r["hook_text"] or r["hook_visual_description"]
                ][:5],
            } if structural else None,
            "observed_videos": len(rows),
            "independent_creators": len(creators),
            "videos_with_eligible_metrics": len(measured),
            "videos_with_verified_creator_ratio": len(ratios),
            "median_creator_ratio": round(median(ratios), 4) if ratios else None,
            "median_views_growth_per_hour": round(median(growth),4) if growth else None,
            "trend_stage": "unknown_insufficient_time_series",
            "evidence_video_ids": [r["video_id"] for r in rows],
            "readiness": readiness,
        })
    # Rank only groups supported by comparable creator baselines; never
    # substitute raw popularity for evidence quality. Non-ready groups last.
    found.sort(key=lambda f: (
        f["readiness"] != "preliminary_observed",
        -(f["median_creator_ratio"] if f["median_creator_ratio"] is not None else -1),
        -f["independent_creators"], f["format_key"]
    ))
    coverage = {
        "seed_videos": len(seeds["videos"]),
        "seed_authors": len(seeds["authors"]),
        "matched_discovery_videos": len(evidence),
        "eligible_metric_videos": sum(r["metric_snapshot_id"] is not None for r in evidence),
        "analyzed_media_videos": sum(r["mi_analysis_id"] is not None for r in evidence),
        "creator_ratio_videos": sum(r["creator_ratio"] is not None for r in evidence),
        "views_growth_videos": sum(r["views_growth_per_hour"] is not None for r in evidence),
        "c4_raw_engagement_videos": sum((r.get("group_c") or {}).get("c4") is not None for r in evidence),
        "c5_diagnostic_videos": sum((r.get("group_c") or {}).get("c5") is not None for r in evidence),
        "c6_freshness_videos": sum((r.get("group_c") or {}).get("c6_freshness") is not None for r in evidence),
        "c7_uncertified_preview_videos": sum((r.get("group_c") or {}).get("c7_breakout_preview") is not None for r in evidence),
        "truncated": bool(truncated),
    }
    status = "partial" if evidence else "no_discovery_data"
    if coverage["eligible_metric_videos"] == 0:
        limitations = ["Нет разрешённых измерений метрик: эффективность не определена."]
    else:
        limitations = []
    if not found:
        limitations.append("Нет самостоятельных подтверждённых текстовых структур видео Market Intelligence: форматы не определены.")
    if any(r["readiness"] == "insufficient_evidence" for r in found):
        limitations.append("Некоторые группы имеют мало сопоставимых измерений и независимых авторов.")
    if truncated:
        limitations.append("Выборка усечена до лимита: оценка покрытия неполная.")
    limitations.append("Рост тренда пока не классифицируется по агрегированной динамике публикаций.")
    if seeds["authors"]:
        limitations.append("Профили проверяются в отдельном ограниченном задании; число найденных роликов не гарантирует полный архив автора.")
    return {
        "schema_version": SCHEMA,
        "project_id": project_id,
        "status": status,
        "source_mode": "user_seeds_plus_existing_d1",
        "seeds": seeds,
        "coverage": coverage,
        "format_families": found,
        "ranking_method": "mi-creator-relative-median/v1",
        "video_evidence": evidence,
        "limitations": limitations,
        "market_hypotheses_verified": False,
        "provider_requests": 0,
        "llm_requests": 0,
    }


def verified_rtb_context(snapshot: dict) -> dict:
    """A strictly evidence-bounded read-only DTO for a *future* RTB v3 adapter."""
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != SCHEMA:
        raise MarketError("market_schema_mismatch", "Неизвестная версия рыночного отчёта.")
    if snapshot.get("market_hypotheses_verified") is not False:
        raise MarketError("false_verification", "Рыночный отчёт не подтверждает причинную эффективность.")
    return {
        "schema_version": "rtb-market-evidence-context/v1",
        "project_id": snapshot["project_id"],
        "market_snapshot_sha256": digest(snapshot),
        "format_families": snapshot["format_families"],
        "video_evidence": snapshot["video_evidence"],
        "coverage": snapshot["coverage"],
        "limitations": snapshot["limitations"],
        "instruction_boundary": "Evidence only; no guarantees, no fabricated creator performance, no hypothesis approval.",
    }
