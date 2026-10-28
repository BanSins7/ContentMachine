"""Immutable, side-effect-free SP service configuration (not spending consent).

Operator workflow configuration, model credentials and worker execution are
separate responsibilities. No environment reads, model downloads or HTTP IO
occur on import or construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

SERVICE_CONTRACT_VERSION = "search-planner-service/v1"
MAX_C9_HYPOTHESES = 48


@dataclass(frozen=True, slots=True)
class SearchPlannerOptions:
    """Bound context preview size; no enable_paid/enable_collector override."""

    max_preview_hypotheses: int = MAX_C9_HYPOTHESES

    def __post_init__(self) -> None:
        if (type(self.max_preview_hypotheses) is not int or
                not 1 <= self.max_preview_hypotheses <= MAX_C9_HYPOTHESES):
            raise PipelineError("Invalid Search Planner preview hypothesis limit", 422)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "SearchPlannerOptions":
        if type(raw) is not dict or set(raw) - {"max_preview_hypotheses"}:
            raise PipelineError("Unknown Search Planner runtime options", 422)
        return cls(max_preview_hypotheses=raw.get("max_preview_hypotheses",
                                                 MAX_C9_HYPOTHESES))
