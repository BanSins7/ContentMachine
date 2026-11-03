"""Minimal Group B-owned, dependency-free input boundary (bootstrap v0).

This is NOT the QDP producer contract. Concrete versioned adapter translators
and authoritative DB checks are introduced in GB-MOD-04/05.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID

_PROJECT = re.compile(r"[0-9a-f]{32}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_KINDS = {
    "video": "video",
    "slideshow_video": "video",
    "photo_carousel": "carousel",
    "mixed_carousel": "carousel",
}

class ContractError(ValueError):
    """Typed bootstrap boundary failure; never silently infer missing evidence."""
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code

def content_type_for_kind(kind: str) -> str:
    if type(kind) is not str or kind not in _KINDS:
        raise ContractError("unsupported_content_kind", "Only approved B video/carousel types are supported")
    return _KINDS[kind]

def canonical_hash(payload: dict) -> str:
    if type(payload) is not dict:
        raise ContractError("invalid_payload", "Canonical payload must be an object")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise ContractError("invalid_payload", "Payload is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()

@dataclass(frozen=True)
class GroupBInputPointer:
    """Local normalized pointer, NOT evidence that QDP/Group A is connected."""
    project_id: str
    qdp_run_id: str
    video_id: int
    source_id: str
    content_kind: str
    group_a_analysis_id: int
    qdp_input_sha256: str
    group_a_snapshot_sha256: str
    media_manifest_sha256: str
    schema_version: str = "group-b-input/v1"

    def __post_init__(self):
        if type(self.project_id) is not str or not _PROJECT.fullmatch(self.project_id):
            raise ContractError("invalid_project_id", "Expected 32 lower-hex project ID")
        if type(self.qdp_run_id) is not str:
            raise ContractError("invalid_qdp_run", "Expected canonical UUID string")
        try:
            parsed = UUID(self.qdp_run_id)
        except (ValueError, AttributeError) as exc:
            raise ContractError("invalid_qdp_run", "Expected UUID") from exc
        if str(parsed) != self.qdp_run_id:
            raise ContractError("invalid_qdp_run", "UUID must be canonical lower-case")
        if type(self.video_id) is not int or self.video_id <= 0:
            raise ContractError("invalid_video_id", "Canonical D1 video ID must be positive integer")
        if type(self.group_a_analysis_id) is not int or self.group_a_analysis_id <= 0:
            raise ContractError("invalid_a_analysis_id", "Group A analysis ID must be positive integer")
        if type(self.source_id) is not str or not self.source_id.strip() or len(self.source_id) > 250:
            raise ContractError("invalid_source_id", "Source ID must be bounded nonblank text")
        content_type_for_kind(self.content_kind)
        for name in ("qdp_input_sha256", "group_a_snapshot_sha256", "media_manifest_sha256"):
            val = getattr(self, name)
            if type(val) is not str or not _SHA.fullmatch(val):
                raise ContractError("invalid_sha256", f"{name} must be 64 lower-hex characters")
        if self.schema_version != "group-b-input/v1":
            raise ContractError("unsupported_schema_version", "Unsupported local input schema")


# GB-MOD-01. These are the exact storage vocabularies from immutable migration
# 005_content_features_b.sql, not model outputs or evidence that a job ran.
# Keep ordered tuples for deterministic model schemas and comparisons to SQL.
HOOK_MECHANISMS = (
    "problem", "promise", "curiosity", "myth_busting", "mistake", "surprise",
    "story", "challenge", "comparison", "proof",
)
HOOK_DELIVERY = ("speech", "on_screen_text", "visual_action", "sound")
VIDEO_FORMATS = (
    "talking_head", "dialogue_interview", "skit", "demonstration",
    "product_showcase", "screen_recording", "pov", "reaction",
    "voiceover_montage", "animation_motion_graphics", "slideshow_video", "vlog_bts",
)
CAROUSEL_FORMATS = (
    "explainer", "listicle", "step_by_step", "comparison", "storytelling",
    "case_study", "product_catalog", "visual_gallery",
)
STORY_ROLES = (
    "hook", "context", "problem", "consequence", "goal", "myth", "mistake",
    "cause", "explanation", "fact", "tip", "step", "demonstration",
    "comparison", "example", "proof", "testimonial", "product_intro",
    "product_demo", "before_after", "result", "reveal", "summary", "offer",
    "cta", "other",
)
BEFORE_AFTER_PHASES = ("before", "after", "both")
EDITING_TYPES = ("single_take", "slow_cuts", "medium_cuts", "fast_cuts")
SUBTITLE_TYPES = ("none", "standard", "dynamic")
SUBTITLE_STYLE_ENUMS = {
    "position": ("top", "center", "bottom"),
    "font_size": ("small", "medium", "large"),
    "font_style": ("regular", "bold", "extra_bold"),
    "background_style": ("none", "outline", "shadow", "box"),
    "display_mode": ("line", "phrase", "word_by_word"),
}
SUBTITLE_STYLE_FIELDS = (
    "position", "font_size", "font_style", "text_color", "highlight_color",
    "background_style", "display_mode",
)
CTA_TYPES = (
    "follow", "like", "comment", "share", "save", "profile_visit",
    "link_click", "app_install", "purchase", "sign_up", "dm", "other",
)
CTA_DELIVERY = ("speech", "on_screen_text")
CTA_DESTINATIONS = (
    "current_post", "profile", "link_in_bio", "website", "app_store",
    "comments", "dm", "other", "unknown",
)
CTA_SERVICES = (
    "tiktok", "instagram", "youtube", "telegram", "whatsapp",
    "apple_app_store", "google_play", "other",
)
ANALYSIS_STATES = ("pending", "partial", "completed", "failed")
CTA_SCAN_STATES = ("not_run", "complete", "partial", "failed")
B1_CLASSIFICATION_STATES = ("inferred", "unknown")
B2_CLASSIFICATION_STATES = ("inferred", "unknown", "needs_review")

# These are the actual 005 tables, not a claim that analysis runtime exists.
B_BLOCK_TABLES = {
    "B1": ("content_b_hooks", "content_b_hook_parts"),
    "B2": ("content_b_formats",),
    "B3": ("content_b_story_sampling", "content_b_story_frames",
           "content_b_story_intervals", "content_b_story_slides"),
    "B4": ("content_b_visual_content",),
    "B5": ("content_b_editing",),
    "B6": ("content_b_subtitles",),
    "B7": ("content_b_cta_analyses", "content_b_ctas"),
    "B8": ("content_b_categories", "content_b_topics",
           "content_b_subtopics", "content_b_topic_assignments"),
}


def _enum(value: str, values: tuple[str, ...], field: str) -> str:
    if type(value) is not str or value not in values:
        raise ContractError("invalid_" + field, f"{field} is outside frozen Group B vocabulary")
    return value


def validate_unique_labels(labels: object, allowed: tuple[str, ...], *, field: str) -> tuple[str, ...]:
    """Match b_valid_labels: non-null, supported, no repeated label or null."""
    if type(labels) not in (list, tuple) or any(type(x) is not str for x in labels):
        raise ContractError("invalid_" + field, f"{field} must be a list of strings")
    result = tuple(labels)
    if len(result) != len(set(result)) or any(label not in allowed for label in result):
        raise ContractError("invalid_" + field, f"{field} contains duplicates/unknown labels")
    return result


def validate_placement(content_type: str, *, start_sec=None, end_sec=None, slide_index=None) -> None:
    """Enforce SQL-style video seconds versus carousel slide-index separation.

    Unknown timestamps are allowed by migration 005. This helper is NOT a
    complete evidence/timeline verification; that belongs to GB-MOD-21.
    """
    from decimal import Decimal, InvalidOperation

    _enum(content_type, ("video", "carousel"), "content_type")
    for label, number in (("start_sec", start_sec), ("end_sec", end_sec)):
        if number is None:
            continue
        if type(number) not in (int, float, str, Decimal):
            raise ContractError("invalid_position", f"{label} must be a nonnegative finite number")
        try:
            d = Decimal(str(number))
        except InvalidOperation as exc:
            raise ContractError("invalid_position", f"Invalid {label}") from exc
        if not d.is_finite() or d < 0:
            raise ContractError("invalid_position", f"Invalid {label}")
    if start_sec is not None and end_sec is not None:
        if Decimal(str(end_sec)) <= Decimal(str(start_sec)):
            raise ContractError("invalid_position", "end_sec must exceed start_sec")
    if content_type == "video":
        if slide_index is not None:
            raise ContractError("invalid_position", "Video evidence cannot use carousel slide indices")
    elif (type(slide_index) is not int or slide_index < 1 or
          start_sec is not None or end_sec is not None):
        raise ContractError("invalid_position", "Carousel position requires positive slide_index only")


def validate_b1(*, mechanisms: object, delivery: object, classification_status: str,
                is_question=None, has_number=None, direct_address=None,
                has_negation=None, is_command=None) -> None:
    validate_unique_labels(mechanisms, HOOK_MECHANISMS, field="mechanisms")
    validate_unique_labels(delivery, HOOK_DELIVERY, field="delivery")
    _enum(classification_status, B1_CLASSIFICATION_STATES, "b1_status")
    for name, value in (("is_question", is_question), ("has_number", has_number),
                        ("direct_address", direct_address), ("has_negation", has_negation),
                        ("is_command", is_command)):
        if value is not None and type(value) is not bool:
            raise ContractError("invalid_" + name, f"{name} is nullable boolean")


def validate_b2(content_type: str, *, video_primary=None, video_secondary=None,
                carousel_format=None, classification_status="unknown") -> None:
    _enum(content_type, ("video", "carousel"), "content_type")
    _enum(classification_status, B2_CLASSIFICATION_STATES, "b2_status")
    if content_type == "video":
        if carousel_format is not None or (video_secondary is not None and video_primary is None):
            raise ContractError("invalid_b2_format", "Video format cannot use carousel or secondary-only")
        if video_primary is not None:
            _enum(video_primary, VIDEO_FORMATS, "video_primary")
        if video_secondary is not None:
            _enum(video_secondary, VIDEO_FORMATS, "video_secondary")
        if video_primary is not None and video_primary == video_secondary:
            raise ContractError("invalid_b2_format", "Primary/secondary must differ")
        if classification_status == "inferred" and video_primary is None:
            raise ContractError("invalid_b2_format", "Inferred video requires primary")
    else:
        if video_primary is not None or video_secondary is not None:
            raise ContractError("invalid_b2_format", "Carousel format excludes video labels")
        if carousel_format is not None:
            _enum(carousel_format, CAROUSEL_FORMATS, "carousel_format")
        if classification_status == "inferred" and carousel_format is None:
            raise ContractError("invalid_b2_format", "Inferred carousel requires its format")


def validate_b3_role(role: str, *, before_after_phase=None, needs_review=False) -> None:
    _enum(role, STORY_ROLES, "story_role")
    if type(needs_review) is not bool:
        raise ContractError("invalid_review_state", "needs_review must be boolean")
    if before_after_phase is not None:
        _enum(before_after_phase, BEFORE_AFTER_PHASES, "before_after_phase")
        if role != "before_after":
            raise ContractError("invalid_before_after_phase", "Only before_after roles have phase")
    if role == "other" and not needs_review:
        raise ContractError("review_required", "Other story role requires manual review")


def validate_b4(*, ai_generated=None, person_visible, product_visible, logo_visible,
                visual_evidence_present=False, ai_origin_verified=False) -> None:
    """Admission guard, not a provenance verifier or a model result."""
    if visual_evidence_present is not True:
        raise ContractError("missing_visual_evidence", "No B4 record without visual pass")
    for name, value in (("person_visible", person_visible),
                        ("product_visible", product_visible), ("logo_visible", logo_visible)):
        if type(value) is not bool:
            raise ContractError("invalid_" + name, f"{name} requires measured boolean")
    if ai_generated is not None:
        if type(ai_generated) is not bool or ai_origin_verified is not True:
            raise ContractError("unverified_ai_origin", "AI origin must remain null until verified")


def validate_b7(*, cta_type: str, delivery: object, destination_type: str,
                destination_service=None, destination_other_desc=None) -> None:
    _enum(cta_type, CTA_TYPES, "cta_type")
    channels = validate_unique_labels(delivery, CTA_DELIVERY, field="cta_delivery")
    if not 1 <= len(channels) <= 2:
        raise ContractError("invalid_cta_delivery", "CTA requires 1 or 2 channels")
    _enum(destination_type, CTA_DESTINATIONS, "destination_type")
    if destination_service is not None:
        _enum(destination_service, CTA_SERVICES, "destination_service")
    if destination_type != "other" and destination_other_desc is not None:
        raise ContractError("invalid_destination", "Only destination 'other' may have description")


def cta_presence(scan_status: str, occurrence_count: int) -> bool | None:
    """Same tri-state as content_b_cta_summary view; not a CTA classifier."""
    _enum(scan_status, CTA_SCAN_STATES, "cta_scan_status")
    if type(occurrence_count) is not int or occurrence_count < 0:
        raise ContractError("invalid_cta_count", "CTA count is nonnegative integer")
    return occurrence_count > 0 if scan_status == "complete" else None
