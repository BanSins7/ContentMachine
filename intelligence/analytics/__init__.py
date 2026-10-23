"""Qualified Group A + Group B + Group C -> Unified Analytics.

No collector, media extractor or external network side effects.
"""
from .contracts import AnalyticsError, canonical_hash, evaluate_qualification
from .metrics import compute_c_metrics, MetricError

__all__ = ["AnalyticsError", "canonical_hash", "evaluate_qualification",
           "compute_c_metrics", "MetricError"]
