"""Search Planner — public, standalone ContentMachine subsystem API.

Historical V1 functions remain import-compatible. SP-01 adds only a read-only
service facade and typed contracts; paid execution and Collector handoff are
separate explicitly authorized stages. No Collector dependency on import.
"""
from .input_loader import load_approved_publication
from .context_builder import build_hypothesis_contexts
from .service import SearchPlannerService
from .configuration import SearchPlannerOptions, SERVICE_CONTRACT_VERSION
from .api_contracts import (
    ContextPreview, GenerationStatus, ValidatedQueriesView, ServiceStatus,
)

__all__ = (
    "load_approved_publication",
    "build_hypothesis_contexts",
    "SearchPlannerService",
    "SearchPlannerOptions",
    "SERVICE_CONTRACT_VERSION",
    "ContextPreview",
    "GenerationStatus",
    "ValidatedQueriesView",
    "ServiceStatus",
)
