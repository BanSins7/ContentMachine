"""Zero-cost Generation Engine contracts. No LLM calls, billing or provider routing."""
from __future__ import annotations

from copy import deepcopy
from enum import Enum
import re

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.search_planner.contracts import validate_search_policy

HASH_RE = re.compile(r"[0-9a-f]{64}\Z")


class RunState(str, Enum):
    CREATED = "created"
    AWAITING_AUTHORIZATION = "awaiting_authorization"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED_BUDGET = "paused_budget"
    PAUSED_PRICE = "paused_price"
    BLOCKED_CONFIG = "blocked_config"
    UNCERTAIN = "uncertain"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class BatchState(str, Enum):
    PENDING = "pending"
    RESERVED = "reserved"
    DISPATCHING = "dispatching"
    COMPLETED = "completed"
    FAILED_KNOWN = "failed_known"
    UNCERTAIN = "uncertain"
    CANCELLED = "cancelled"


class AttemptState(str, Enum):
    RESERVED = "reserved"
    DISPATCH_INTENT = "dispatch_intent"
    COMPLETED = "completed"
    FAILED_KNOWN = "failed_known"
    UNCERTAIN = "uncertain"
    ABANDONED_BEFORE_DISPATCH = "abandoned_before_dispatch"
    RECONCILED_COMPLETED = "reconciled_completed"
    RECONCILED_FAILED = "reconciled_failed"


RUN_TRANSITIONS = {
    RunState.CREATED: {RunState.AWAITING_AUTHORIZATION, RunState.BLOCKED_CONFIG, RunState.CANCELLED},
    RunState.AWAITING_AUTHORIZATION: {RunState.QUEUED, RunState.CANCELLED},
    RunState.QUEUED: {RunState.RUNNING, RunState.PAUSED_BUDGET, RunState.PAUSED_PRICE,
                      RunState.BLOCKED_CONFIG, RunState.CANCELLED},
    RunState.RUNNING: {RunState.COMPLETED, RunState.PAUSED_BUDGET, RunState.PAUSED_PRICE,
                       RunState.UNCERTAIN, RunState.FAILED, RunState.CANCELLED},
    RunState.PAUSED_BUDGET: {RunState.QUEUED, RunState.CANCELLED},
    RunState.PAUSED_PRICE: {RunState.CANCELLED},
    RunState.BLOCKED_CONFIG: {RunState.CANCELLED},
    RunState.UNCERTAIN: {RunState.RUNNING, RunState.FAILED, RunState.CANCELLED},
    RunState.FAILED: set(),
    RunState.COMPLETED: set(),
    RunState.CANCELLED: set(),
}
BATCH_TRANSITIONS = {
    BatchState.PENDING: {BatchState.RESERVED, BatchState.CANCELLED},
    BatchState.RESERVED: {BatchState.DISPATCHING, BatchState.CANCELLED},
    BatchState.DISPATCHING: {BatchState.COMPLETED, BatchState.FAILED_KNOWN, BatchState.UNCERTAIN},
    BatchState.UNCERTAIN: {BatchState.COMPLETED, BatchState.FAILED_KNOWN},
    BatchState.COMPLETED: set(),
    BatchState.FAILED_KNOWN: set(),
    BatchState.CANCELLED: set(),
}
ATTEMPT_TRANSITIONS = {
    AttemptState.RESERVED: {AttemptState.DISPATCH_INTENT, AttemptState.ABANDONED_BEFORE_DISPATCH},
    AttemptState.DISPATCH_INTENT: {AttemptState.COMPLETED, AttemptState.FAILED_KNOWN, AttemptState.UNCERTAIN},
    AttemptState.UNCERTAIN: {AttemptState.RECONCILED_COMPLETED, AttemptState.RECONCILED_FAILED},
    AttemptState.COMPLETED: set(),
    AttemptState.FAILED_KNOWN: set(),
    AttemptState.ABANDONED_BEFORE_DISPATCH: set(),
    AttemptState.RECONCILED_COMPLETED: set(),
    AttemptState.RECONCILED_FAILED: set(),
}


def require_transition(current: str, next_status: str, graph: dict) -> None:
    """Fail closed for unknown states and backwards transitions."""
    allowed = graph.get(current)
    if allowed is None or next_status not in {state.value for state in allowed}:
        raise PipelineError("Invalid engine state transition", 409)


def validate_generation_config(raw: dict) -> dict:
    """Strict structural validation; catalog price/rights verified separately.

    Config approval grants NO right to generate/spend. The generation created
    from it remains awaiting_authorization until stage Engine-B.
    """
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version", "search_policy", "model", "execution"
    } or raw["schema_version"] != "search-generation-config/v1":
        raise PipelineError("Invalid generation configuration", 422)
    policy = validate_search_policy(raw["search_policy"])
    model = raw["model"]
    if not isinstance(model, dict) or set(model) != {
        "provider", "model_id", "catalog_snapshot_id",
        "model_catalog_sha256", "prompt_version", "prompt_sha256",
        "raw_schema_version", "raw_schema_sha256", "strategy_version",
        "max_output_tokens"
    }:
        raise PipelineError("Invalid generation model specification", 422)
    if model["provider"] != "openrouter":
        raise PipelineError("Only OpenRouter models are configured for AI Query", 422)
    if type(model["catalog_snapshot_id"]) is not int or model["catalog_snapshot_id"] < 1:
        raise PipelineError("Invalid catalog snapshot ID", 422)
    for field in ("model_catalog_sha256", "prompt_sha256", "raw_schema_sha256"):
        if not isinstance(model[field], str) or HASH_RE.fullmatch(model[field]) is None:
            raise PipelineError("Invalid model/prompt/schema digest: " + field, 422)
    for field in ("model_id", "prompt_version", "raw_schema_version", "strategy_version"):
        if not isinstance(model[field], str) or not 1 <= len(model[field].strip()) <= 160:
            raise PipelineError("Invalid model version field: " + field, 422)
    if type(model["max_output_tokens"]) is not int or not 1 <= model["max_output_tokens"] <= 131072:
        raise PipelineError("Invalid max_output_tokens", 422)

    execution = raw["execution"]
    if not isinstance(execution, dict) or set(execution) != {
        "target_hypotheses_per_batch", "hard_max_hypotheses_per_batch",
        "max_input_bytes_per_batch", "max_concurrent_provider_calls",
        "automatic_paid_retries", "samples_per_batch"
    }:
        raise PipelineError("Invalid execution policy", 422)
    for field, low, high in (
        ("target_hypotheses_per_batch", 1, 5),
        ("hard_max_hypotheses_per_batch", 1, 5),
        ("max_input_bytes_per_batch", 1024, 1000000),
        ("max_concurrent_provider_calls", 1, 1),
        ("automatic_paid_retries", 0, 0),
        ("samples_per_batch", 1, 1),
    ):
        value = execution[field]
        if type(value) is not int or not low <= value <= high:
            raise PipelineError("Invalid execution setting: " + field, 422)
    if execution["target_hypotheses_per_batch"] > execution["hard_max_hypotheses_per_batch"]:
        raise PipelineError("Target batch size exceeds hard maximum", 422)
    return {
        "schema_version": raw["schema_version"],
        "search_policy": policy,
        "model": deepcopy(model),
        "execution": deepcopy(execution),
    }


def model_snapshot_hash(config: dict) -> str:
    return sha256(config["model"])
