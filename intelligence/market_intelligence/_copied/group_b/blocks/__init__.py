"""Independently auditable Group B domain blocks; importing is side-effect free."""
from .b1_hook import (
    B1EvidenceError, B1HookProposal, B1PreparedRequest,
    validate_b1_response, unknown_b1, prepare_b1_request,
    MECHANISMS as B1_MECHANISMS, DELIVERY as B1_DELIVERY,
    FLAGS as B1_FLAGS,
)
from .b1_schema import B1_OUTPUT_SCHEMA

from .b2_format import (
    B2FormatError, B2FormatProposal, B2PreparedRequest,
    validate_b2_response, unknown_b2, prepare_b2_request,
    VIDEO_FORMATS as B2_VIDEO_FORMATS,
    CAROUSEL_FORMATS as B2_CAROUSEL_FORMATS,
)
from .b2_schema import B2_OUTPUT_SCHEMA

from .b3_sampling import (
    B3SamplingError, B3SamplingPlan, SamplingPolicy,
    build_b3_sampling_plan, verify_b3_plan_against_manifest,
)

from .b3_story import (
    B3StoryError, B3StoryProposal, B3PreparedRequest, ROLES as B3_ROLES,
    validate_b3_story, unknown_b3, prepare_b3_story_request,
)
from .b3_schema import B3_STORY_SCHEMA
from .b3_storage import persist_b3_story

from .b4_visual import (
    B4VisualError, B4VisualProposal, B4PreparedRequest,
    validate_b4_visual, unknown_b4, prepare_b4_request,
)
from .b4_schema import B4_OUTPUT_SCHEMA
from .b4_storage import persist_b4_visual

from .b6_subtitles import (
    B6SubtitleError, B6SubtitleProposal, TYPES as B6_SUBTITLE_TYPES,
    STYLE_FIELDS as B6_STYLE_FIELDS, validate_b6_subtitles, unknown_b6,
)
from .b6_storage import persist_b6_subtitles

from .b7_cta import (
    B7CTAError, B7CTAProposal, B7PreparedRequest,
    CTA_TYPES as B7_CTA_TYPES, DESTINATIONS as B7_DESTINATIONS,
    validate_b7_cta, unknown_b7, prepare_b7_request,
)
from .b7_schema import B7_OUTPUT_SCHEMA
from .b7_storage import persist_b7_cta

from .b8_topic import (
    B8TopicError, B8TopicProposal, B8PreparedRequest,
    validate_b8_topic, unknown_b8, prepare_b8_request,
)
from .b8_schema import B8_OUTPUT_SCHEMA
from .b8_storage import persist_b8_topic

from .b5_editing import (
    B5MeasurementUnavailable, B5Metrics, B5EditingExecutor, B5EditingVerifier,
    analyze_b5, read_shot_timeline,
)

__all__ = (
    "B4VisualError", "B4VisualProposal", "B4PreparedRequest",
    "validate_b4_visual", "unknown_b4", "prepare_b4_request",
    "B4_OUTPUT_SCHEMA", "persist_b4_visual",
    "B3StoryError", "B3StoryProposal", "B3PreparedRequest",
    "B3_ROLES", "B3_STORY_SCHEMA",
    "validate_b3_story", "unknown_b3", "prepare_b3_story_request",
    "persist_b3_story",
    "B3SamplingError", "B3SamplingPlan", "SamplingPolicy",
    "build_b3_sampling_plan", "verify_b3_plan_against_manifest",
    "B2FormatError", "B2FormatProposal", "B2PreparedRequest",
    "validate_b2_response", "unknown_b2", "prepare_b2_request",
    "B2_VIDEO_FORMATS", "B2_CAROUSEL_FORMATS", "B2_OUTPUT_SCHEMA",
    "B1EvidenceError", "B1HookProposal", "B1PreparedRequest",
    "validate_b1_response", "unknown_b1", "prepare_b1_request",
    "B1_MECHANISMS", "B1_DELIVERY", "B1_FLAGS", "B1_OUTPUT_SCHEMA",
    "B6SubtitleError", "B6SubtitleProposal", "B6_SUBTITLE_TYPES",
    "B6_STYLE_FIELDS", "validate_b6_subtitles", "unknown_b6",
    "persist_b6_subtitles",
    "B7CTAError", "B7CTAProposal", "B7PreparedRequest",
    "B7_CTA_TYPES", "B7_DESTINATIONS", "B7_OUTPUT_SCHEMA",
    "validate_b7_cta", "unknown_b7", "prepare_b7_request",
    "persist_b7_cta",
    "B8TopicError", "B8TopicProposal", "B8PreparedRequest",
    "validate_b8_topic", "unknown_b8", "prepare_b8_request",
    "B8_OUTPUT_SCHEMA", "persist_b8_topic",
    "B5MeasurementUnavailable", "B5Metrics", "B5EditingExecutor",
    "B5EditingVerifier", "analyze_b5", "read_shot_timeline",
)
