"""Deterministic readiness gate; deny by default and never manufacture observations."""
from __future__ import annotations
import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

ENGINE_VERSION = "analytics-qualified/v1"
PROJECT_RE = re.compile(r"^[0-9a-f]{32}$")
_VIDEO_KINDS = frozenset(("video", "slideshow_video"))
_CAROUSEL_KINDS = frozenset(("photo_carousel", "mixed_carousel"))
_ALLOWED_KINDS = _VIDEO_KINDS | _CAROUSEL_KINDS | {"image_post"}
_RIGHTS = ("can_store_metadata", "can_analyze_media", "can_store_metrics", "can_derive_scores")

class AnalyticsError(ValueError):
    def __init__(self, code: str, detail: str, http_status: int = 422):
        super().__init__(detail)
        self.code = code
        self.http_status = http_status

def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(v) for v in value]
    return value

def canonical_hash(value: dict) -> str:
    data = json.dumps(_json_value(value), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()

def validate_request(project_id: str, video_id: int, request_key: str) -> None:
    if not isinstance(project_id, str) or not PROJECT_RE.fullmatch(project_id):
        raise AnalyticsError("invalid_project", "Expected canonical project id")
    if type(video_id) is not int or video_id < 1:
        raise AnalyticsError("invalid_video", "Expected positive video id")
    if (type(request_key) is not str or not 8 <= len(request_key) <= 180
            or not re.fullmatch(r"[A-Za-z0-9_.:-]+", request_key)):
        raise AnalyticsError("invalid_request_key", "Invalid immutable request key")

def evaluate_qualification(e: dict) -> dict:
    """Pure gate over trustworthy repository-loaded observations.

    status=qualified requires source rights, SRE relevance, complete compatible A/B,
    and COMPLETE persisted C. Partial is *not* usable for ranked analytics.
    """
    blockers = []
    def block(code):
        if code not in blockers:
            blockers.append(code)
    source = e.get("source") or {}
    policy = e.get("policy") or {}
    perms = policy.get("permissions") or {}
    source_id = source.get("id")
    if source.get("status") != "enabled" or not policy.get("is_active") or policy.get("source_id") != source_id:
        block("source_policy_inactive")
    for right in _RIGHTS:
        if source.get(right) is not True or perms.get(right) is not True:
            block("rights_" + right)
    if not e.get("project_link"):
        block("not_in_project")
    kind = e.get("content_kind")
    if kind not in _ALLOWED_KINDS:
        block("unknown_content_kind")
    if kind == "image_post":
        block("b2_unsupported_image_post")
    a = e.get("a") or {}
    b = e.get("b") or {}
    c = e.get("c") or {}
    metric = e.get("metric") or {}
    relevance = e.get("relevance") or {}
    b_features = e.get("features_b") or {}
    if (not a or a.get("state") != "completed"
        or a.get("video_id") != e.get("video_id") or a.get("source_id") != source_id
        or a.get("analysis_id") is None):
        block("a_missing_or_incomplete")
    if (not b or b.get("state") != "completed" or b.get("needs_review") is not False
        or b.get("video_id") != e.get("video_id") or b.get("source_id") != source_id):
        block("b_missing_or_unreviewed")
    expected = "video" if kind in _VIDEO_KINDS else "carousel"
    if b and b.get("content_type") != expected:
        block("b_kind_mismatch")
    if a and b and (
        (expected == "video" and b.get("video_a_analysis_id") != a.get("analysis_id"))
        or (expected == "carousel" and b.get("carousel_a_analysis_id") != a.get("analysis_id"))):
        block("a_b_lineage_mismatch")
    if a and kind in _CAROUSEL_KINDS and a.get("content_kind") != kind:
        block("carousel_kind_mismatch")
    if kind in _VIDEO_KINDS and a and (
        a.get("duration_sec") is None or a.get("scene_status") != "complete"
        or a.get("speech_status") != "complete"
        or a.get("screen_text_status") != "complete"
        or a.get("has_subtitles") == "unknown"):
        block("a_incomplete_measurement_coverage")
    if kind in _CAROUSEL_KINDS and a and (
        a.get("slide_status") != "complete" or a.get("text_status") != "complete"
        or (a.get("slide_count") or 0) < 2):
        block("a_incomplete_slide_coverage")
    if b:
        hook, fmt = b_features.get("hook") or {}, b_features.get("format") or {}
        if hook.get("classification_status") != "inferred":
            block("b1_hook_unresolved")
        if fmt.get("classification_status") != "inferred":
            block("b2_format_unresolved")
        story = b_features.get("story") or {}
        story_units = ((story.get("frames") or []) + (story.get("intervals") or [])
                       if expected == "video" else (story.get("slides") or []))
        if not story_units or any(part.get("needs_review") for part in story_units):
            block("b3_story_unresolved")
        if b_features.get("visual") is None:
            block("b4_visual_not_measured")
        if expected == "video" and not b_features.get("editing"):
            block("b5_editing_not_measured")
        if expected == "video" and not b_features.get("subtitles"):
            block("b6_subtitles_not_measured")
        cta = b_features.get("cta_summary") or {}
        if cta.get("analysis_status") != "complete":
            block("b7_cta_unresolved")
        topic = b_features.get("topic") or {}
        if not topic.get("topic_id") or topic.get("needs_review") is not False:
            block("b8_topic_unresolved")
    if not relevance.get("match") or not (relevance.get("label_id") or relevance.get("result_id")):
        block("no_approved_relevance")
    if not metric:
        block("c_metric_missing")
    else:
        if metric.get("video_id") != e.get("video_id") or metric.get("source_id") != source_id:
            block("metric_source_mismatch")
        if metric.get("data_quality_status") != "ok" or not metric.get("valid_retention"):
            block("metric_quality_or_retention")
    if c.get("score_status") != "complete" or c.get("breakout_score") is None:
        block("c_score_not_complete")
    if c and (c.get("post_id") != e.get("video_id") or c.get("snapshot_id") != metric.get("id")):
        block("c_lineage_mismatch")
    rights_block = any(x.startswith("rights_") or x == "source_policy_inactive" for x in blockers)
    hard_block = rights_block or "not_in_project" in blockers or "metric_quality_or_retention" in blockers
    status = "blocked" if hard_block else ("qualified" if not blockers else "partial")
    readiness = {
        "source": not rights_block,
        "project": "not_in_project" not in blockers,
        "a": not any(x.startswith("a_") or x.startswith("carousel_") for x in blockers),
        "b": not any(x.startswith("b_") for x in blockers),
        "c": not any(x.startswith("c_") or x.startswith("metric_") for x in blockers),
        "relevance": "no_approved_relevance" not in blockers,
    }
    return {"status": status, "blockers": blockers, "readiness": readiness}
