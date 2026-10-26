"""Patterns & Insights Engine (PIE), standalone bootstrap only.

PIE-MOD-00 exposes only validated local input pointers. This module does NOT
contain a live UAE adapter, statistical inference engine, LLM or real insights.
"""
from .contracts import (
    PIEBoundaryError, PIEDatasetPointer, canonical_hash,
)
__all__ = ["PIEBoundaryError", "PIEDatasetPointer", "canonical_hash"]
