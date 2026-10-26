"""Search Planner — public, standalone ContentMachine subsystem API.

Historical V1 functions remain import-compatible. SP-01 adds only a read-only
service facade and typed contracts; paid execution and Collector handoff are
separate explicitly authorized stages. No Collector dependency on import.
"""
from .input_loader import load_approved_publication
from .context_builder import build_hypothesis_contexts
from .service import SearchPlannerService
from .generation_workflow import GenerationWorkflow, OwnerActionGuard
from .operations import GenerationExecutionController, execution_snapshot, unsubmitted_stale_attempt_ids
from .configuration import SearchPlannerOptions, SERVICE_CONTRACT_VERSION
from .api_contracts import (
    ContextPreview, GenerationStatus, ValidatedQueriesView, ServiceStatus,
)

__all__ = (
    "load_approved_publication",
    "build_hypothesis_contexts",
    "SearchPlannerService",
    "GenerationWorkflow",
    "OwnerActionGuard",
    "GenerationExecutionController",
    "execution_snapshot",
    "unsubmitted_stale_attempt_ids",
    "SearchPlannerOptions",
    "SERVICE_CONTRACT_VERSION",
    "ContextPreview",
    "GenerationStatus",
    "ValidatedQueriesView",
    "ServiceStatus",
)
