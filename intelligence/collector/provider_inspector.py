"""Strict, zero-network TikTok DTK v5.1.7 result inspector.

Pinned contract: src/dtk/models/content.py and Page[T], DTK v5.1.7.
Consumes the already-unwrapped dtk.task() response, NOT a raw HTTP envelope.
No SQL, provider calls, raw responses, media downloads or metrics persistence.
All normalized candidates exist in memory for the next (separately gated)
Discovery ingestion stage. Current Worker stores only bounded QA aggregates.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any

from intelligence.discovery.normalize import (
    CandidateError, canonical_video_url, datetime_utc, optional_count, TT_ID,
)
from .contracts import (
    CandidateEnvelope, CollectorError, ProviderPage, PROJECT_RE,
    require_id, require_int, require_uuid,
)

INSPECTOR_VERSION = "dtk-tiktok-search/v5.1.7-inspector-v1"
MAX_PROVIDER_ITEMS = 100
HANDLE = re.compile(r"[A-Za-z0-9._]{1,40}\Z")
TEXT_LIMIT = 500
MAX_CURSOR_LEN = 320
KNOWN_KINDS = {"video", "image_album", "live"}
REJECT_KINDS = {"image_album", "live"}
METRIC_NAMES = {
    "views": "play_count", "likes": "digg_count",
    "comments": "comment_count", "shares": "share_count",
    "saves": "collect_count",
}


@dataclass(frozen=True, slots=True)
class InspectedPage:
    page: ProviderPage
    raw_item_count: int
    examined_count: int
    rejected_count: int
    overflow_count: int
    quality_reasons: tuple[tuple[str, int], ...]
    rejected: tuple[dict, ...]
    pagination_reasons: tuple[str, ...] = ()

    def summary(self) -> dict:
        """Safe, bounded telemetry; no account names, captions, media or IDs."""
        accepted = len(self.page.items)
        partial = sum(c.quality == "partial" for c in self.page.items)
        return {
            "inspector_version": INSPECTOR_VERSION,
            "inspection_status": "completed",
            "raw_result_persisted": False,
            "candidate_metadata_persisted": False,
            "metrics_persisted": False,
            "inspection_pending": False,
            "ingestion_pending": True,
            "raw_item_count": self.raw_item_count,
            "examined_count": self.examined_count,
            "accepted_count": accepted,
            "valid_count": accepted - partial,
            "partial_count": partial,
            "rejected_count": self.rejected_count,
            "overflow_count": self.overflow_count,
            "reason_counts": dict(self.quality_reasons),
            "pagination": {
                "has_more": self.page.has_more,
                "cursor_present": self.page.next_cursor is not None,
                "warnings": list(self.pagination_reasons),
                # Do not persist the cursor: stage 11 controls provenance and quota.
                "next_page_scheduled": False,
            },
        }


def _text(value: Any, *, limit: int = TEXT_LIMIT) -> str | None:
    if value is None or value == "":
        return None
    if type(value) is not str or len(value) > limit or any(
        ord(c) < 32 or 127 <= ord(c) <= 159 for c in value
    ):
        raise CandidateError("invalid_text")
    stripped = value.strip()
    return stripped or None


def _published(value: Any) -> datetime | None:
    # DTK normalized Content.created_at is an ISO timestamp or null,
    # not platform create_time Unix seconds. Never guess milliseconds.
    return datetime_utc(value)


def _duration(value: Any) -> float | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 86_400_000:
        raise CandidateError("invalid_duration")
    return value / 1000


def _kind(value: Any) -> str:
    if value is None:
        return "unknown"
    if type(value) is not str or value not in KNOWN_KINDS:
        raise CandidateError("unknown_content_kind")
    if value in REJECT_KINDS:
        raise CandidateError("not_video")
    return value


def _metrics(item: dict, *, allow_metrics: bool) -> dict:
    if not allow_metrics:
        return {k: None for k in METRIC_NAMES}
    stats = item.get("stats")
    if stats is None:
        return {k: None for k in METRIC_NAMES}
    if type(stats) is not dict:
        raise CandidateError("invalid_metrics")
    return {
        name: optional_count(stats.get(remote))
        for name, remote in METRIC_NAMES.items()
    }


def _normalize_item(item: Any, task: dict, provider_task_id: str,
                    fetched_at: datetime, allow_metrics: bool) -> CandidateEnvelope:
    if type(item) is not dict:
        raise CandidateError("invalid_record")
    if item.get("platform") != "tiktok":
        raise CandidateError("platform_mismatch")
    kind = _kind(item.get("kind"))
    if item.get("is_private") is True:
        raise CandidateError("private_content")
    if item.get("is_deleted") is True:
        raise CandidateError("deleted_content")
    if item.get("is_private") not in (None, False) or item.get("is_deleted") not in (None, False):
        raise CandidateError("invalid_visibility")
    claimed = item.get("content_id")
    if type(claimed) is not str or TT_ID.fullmatch(claimed) is None:
        raise CandidateError("invalid_video_id")
    author = item.get("author")
    if type(author) is not dict:
        raise CandidateError("invalid_author")
    if author.get("platform") not in (None, "tiktok"):
        raise CandidateError("author_platform_mismatch")
    handle = _text(author.get("unique_id"), limit=40)
    if handle is not None and HANDLE.fullmatch(handle) is None:
        raise CandidateError("invalid_author")
    uid = _text(author.get("uid"), limit=130)
    # Normalized upstream contract carries Content.web_url. Do NOT invent
    # a canonical URL from a bare video id if both URL and handle are absent.
    link = item.get("web_url")
    if link is None and handle is not None:
        link = "https://www.tiktok.com/@" + handle + "/video/" + claimed
    try:
        platform, video_id, canonical = canonical_video_url(link)
    except CandidateError:
        raise CandidateError("invalid_url") from None
    if platform != "tiktok" or video_id != claimed or "/photo/" in canonical:
        raise CandidateError("identity_mismatch")
    title = _text(item.get("title"))
    description = _text(item.get("description"), limit=500)
    if title is None and description is not None:
        title = description.split("\n", 1)[0][:TEXT_LIMIT].strip() or None
    published = _published(item.get("created_at"))
    duration = _duration(item.get("duration_ms"))
    counts = _metrics(item, allow_metrics=allow_metrics)
    # Provider fetched_at may be stale or missing, but the local observation
    # time is the clock of the current safe GET; future metrics need a
    # separately approved observation policy.
    reasons = []
    if kind != "video":
        reasons.append("content_kind_unverified")
    if title is None:
        reasons.append("missing_title")
    if published is None:
        reasons.append("missing_publication_date")
    if duration is None:
        reasons.append("missing_duration")
    if handle is None:
        reasons.append("missing_author_handle")
    return CandidateEnvelope(
        project_id=task["project_id"],
        taxonomy_publication_id=task["taxonomy_publication_id"],
        taxonomy_version=task["taxonomy_version"],
        generation_id=task["generation_id"],
        source_id=task["source_id"],
        provider_task_id=provider_task_id,
        query_ids=tuple(task["query_ids"]),
        hypothesis_ids=tuple(task["hypothesis_ids"]),
        platform="tiktok",
        platform_video_id=video_id,
        canonical_url=canonical,
        fetched_at=fetched_at,
        published_at=published,
        observed_at=fetched_at if allow_metrics and any(
            v is not None for v in counts.values()
        ) else None,
        title=title,
        content_kind=kind,
        creator_platform_id=uid,
        creator_handle=handle,
        duration_seconds=duration,
        quality="partial" if reasons else "valid",
        quality_reasons=tuple(reasons),
        **counts,
    )


def _pagination(payload: dict, meta: Any) -> tuple[bool, str | None, tuple[str, ...]]:
    has_more = payload.get("has_more", False)
    if type(has_more) is not bool:
        raise CollectorError("invalid_provider_page", "Invalid DTK has_more")
    cursor = payload.get("cursor")
    if cursor is not None and (
        type(cursor) is not str or not 1 <= len(cursor) <= MAX_CURSOR_LEN or
        any(ord(c) < 33 or 127 <= ord(c) <= 159 for c in cursor)
    ):
        raise CollectorError("invalid_provider_page", "Invalid DTK cursor")
    # DTK v5.1.7 may return cursor in result_meta.cursor.next. Only
    # use it when the page's own cursor is absent and metadata is valid.
    if cursor is None and type(meta) is dict and type(meta.get("cursor")) is dict:
        alternate = meta["cursor"].get("next")
        if type(alternate) is str and 1 <= len(alternate) <= MAX_CURSOR_LEN and all(
            33 <= ord(ch) <= 126 for ch in alternate
        ):
            cursor = alternate
    warnings = []
    if has_more and cursor is None:
        warnings.append("pagination_cursor_missing")
    if not has_more:
        cursor = None
    return has_more, cursor, tuple(warnings)


def inspect_dtk_search_result(
    answer: dict, task: dict, provider_task_id: str, *,
    fetched_at: datetime | None = None,
    allow_metrics: bool = False,
) -> InspectedPage:
    """Inspect one *done* DTK task response, never submit/poll internally.

    Unknown top-level/content fields (including media, raw, signed links,
    avatar, music, tags, explain, PII) are ignored, not persisted. No policy
    claims or user-provided attestation can make this function write to DB.
    """
    from intelligence.connectors import dtk
    from .adapters.dtk_tiktok import prepare_keyword_task
    prepare_keyword_task(task)
    require_uuid(provider_task_id, "provider_task_id")
    if type(answer) is not dict or answer.get("state") != "done":
        raise CollectorError("provider_not_done", "A completed DTK task response is required")
    if answer.get("task_id") != provider_task_id:
        raise CollectorError("task_identity_mismatch", "DTK task ID mismatch")
    if type(allow_metrics) is not bool:
        raise CollectorError("invalid_policy", "Metrics flag must be boolean")
    stamp = fetched_at or datetime.now(timezone.utc)
    if (not isinstance(stamp,datetime) or stamp.tzinfo is None
        or stamp.utcoffset() is None):
        raise CollectorError("invalid_datetime", "Timezone-aware fetched_at is required")
    stamp = stamp.astimezone(timezone.utc)
    content = answer.get("data")
    if type(content) is not dict or type(content.get("items")) is not list:
        raise CollectorError("invalid_provider_page", "Expected DTK Page[Content] with items")
    items = content["items"]
    if len(items) > MAX_PROVIDER_ITEMS:
        raise CollectorError("provider_page_too_large", "DTK page exceeds 100 items")
    limit = require_int(task["max_results_per_page"], "max_results_per_page", 1, 50)
    has_more, cursor, pagination_reasons = _pagination(content,answer.get("result_meta"))
    accepted = []
    reasons = Counter()
    rejected = []
    seen = set()
    for pos, item in enumerate(items):
        if pos >= limit:
            reasons["local_result_cap"] += 1
            rejected.append({"index":pos, "reason":"local_result_cap"})
            continue
        try:
            normalized = _normalize_item(item,task,provider_task_id,stamp,allow_metrics)
            if normalized.platform_video_id in seen:
                raise CandidateError("duplicate_page_video")
            seen.add(normalized.platform_video_id)
            accepted.append(normalized)
            reasons.update(normalized.quality_reasons)
        except (CandidateError,CollectorError) as exc:
            code = exc.code if isinstance(exc,(CandidateError,CollectorError)) else "invalid_record"
            reasons[code] += 1
            rejected.append({"index":pos,"reason":code})
    page = ProviderPage(
        project_id=task["project_id"],
        taxonomy_publication_id=task["taxonomy_publication_id"],
        taxonomy_version=task["taxonomy_version"],
        generation_id=task["generation_id"],
        query_ids=tuple(task["query_ids"]),
        hypothesis_ids=tuple(task["hypothesis_ids"]),
        source_id="dtk_tiktok",
        provider_task_id=provider_task_id,
        items=tuple(accepted),
        fetched_at=stamp,
        has_more=has_more,
        next_cursor=cursor,
    )
    return InspectedPage(
        page=page,
        raw_item_count=len(items),
        examined_count=min(len(items),limit),
        rejected_count=len(rejected),
        overflow_count=max(0,len(items)-limit),
        quality_reasons=tuple(sorted(reasons.items())),
        rejected=tuple(rejected),
        pagination_reasons=pagination_reasons,
    )
