"""Unified Analytics standalone module bootstrap (no live adapters or writes).

Owns verified joins/qualification and descriptive outputs in future stages.
This module deliberately does NOT calculate Group C performance scores.
"""
from .contracts import (
    AnalyticsBoundaryError,
    UnifiedAnalyticsInputPointer,
    canonical_hash,
    content_type_for_kind,
)
__all__ = [
    "AnalyticsBoundaryError", "UnifiedAnalyticsInputPointer",
    "canonical_hash", "content_type_for_kind",
]
