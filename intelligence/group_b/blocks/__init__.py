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

from .b5_editing import (
    B5MeasurementUnavailable, B5Metrics, B5EditingExecutor, B5EditingVerifier,
    analyze_b5, read_shot_timeline,
)

__all__ = (
    "B3SamplingError", "B3SamplingPlan", "SamplingPolicy",
    "build_b3_sampling_plan", "verify_b3_plan_against_manifest",
    "B2FormatError", "B2FormatProposal", "B2PreparedRequest",
    "validate_b2_response", "unknown_b2", "prepare_b2_request",
    "B2_VIDEO_FORMATS", "B2_CAROUSEL_FORMATS", "B2_OUTPUT_SCHEMA",
    "B1EvidenceError", "B1HookProposal", "B1PreparedRequest",
    "validate_b1_response", "unknown_b1", "prepare_b1_request",
    "B1_MECHANISMS", "B1_DELIVERY", "B1_FLAGS", "B1_OUTPUT_SCHEMA",
    "B5MeasurementUnavailable", "B5Metrics", "B5EditingExecutor",
    "B5EditingVerifier", "analyze_b5", "read_shot_timeline",
)
