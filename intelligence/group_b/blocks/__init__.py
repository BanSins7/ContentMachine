"""Independently auditable Group B domain blocks; importing is side-effect free."""
from .b1_hook import (
    B1EvidenceError, B1HookProposal, validate_b1_response, unknown_b1,
    MECHANISMS as B1_MECHANISMS, DELIVERY as B1_DELIVERY,
    FLAGS as B1_FLAGS,
)
from .b1_schema import B1_OUTPUT_SCHEMA

from .b5_editing import (
    B5MeasurementUnavailable, B5Metrics, B5EditingExecutor, B5EditingVerifier,
    analyze_b5, read_shot_timeline,
)

__all__ = (
    "B1EvidenceError", "B1HookProposal", "validate_b1_response", "unknown_b1",
    "B1_MECHANISMS", "B1_DELIVERY", "B1_FLAGS", "B1_OUTPUT_SCHEMA",
    "B5MeasurementUnavailable", "B5Metrics", "B5EditingExecutor",
    "B5EditingVerifier", "analyze_b5", "read_shot_timeline",
)
