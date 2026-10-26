"""Search Planner — public, standalone ContentMachine subsystem API.

Historical V1 functions remain import-compatible. SP-01 offers read-only
services; SP-03 and SP-04 add explicitly gated ledger mutations and one paid
worker invocation at a time. No Collector dependency or automatic dispatch
on import; downstream Collector handoff remains separately versioned.
"""
from .input_loader import load_approved_publication
from .context_builder import build_hypothesis_contexts
from .service import SearchPlannerService
from .generation_workflow import GenerationWorkflow, OwnerActionGuard
from .operations import GenerationExecutionController, execution_snapshot, unsubmitted_stale_attempt_ids
from .capabilities import CapabilityMatcher, validate_capability_snapshot
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
    "CapabilityMatcher",
    "validate_capability_snapshot",
    "SearchPlannerOptions",
    "SERVICE_CONTRACT_VERSION",
    "ContextPreview",
    "GenerationStatus",
    "ValidatedQueriesView",
    "ServiceStatus",
)
