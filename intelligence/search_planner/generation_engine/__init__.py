"""Generation Engine: planning and authorized budget ledger, NO provider calls."""
from .run_manager import register_config_version, create_planned_generation, get_planned_generation
from .batch_manager import make_batches
from .contracts import RunState, BatchState, AttemptState, validate_generation_config, require_transition
from .budget_manager import (
    create_generation_quote, authorize_generation_budget,
    revoke_generation_budget, get_generation_budget,
)
from .repository import (
    reserve_batch, record_dispatch_intent, settle_known_failure,
    mark_uncertain_attempt, release_unsubmitted_attempt,
)

__all__ = (
    "register_config_version", "create_planned_generation", "get_planned_generation",
    "make_batches", "RunState", "BatchState", "AttemptState",
    "validate_generation_config", "require_transition",
    "create_generation_quote", "authorize_generation_budget",
    "revoke_generation_budget", "get_generation_budget",
    "reserve_batch", "record_dispatch_intent", "settle_known_failure",
    "mark_uncertain_attempt", "release_unsubmitted_attempt",
)
