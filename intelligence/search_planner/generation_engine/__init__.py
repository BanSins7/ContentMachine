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


from .provider_port import FakeProviderPort, BatchRequest, ProviderResponse, PossiblyBilled, KnownProviderFailure
from .worker import planned_next_request, process_one_batch, drain_generation
from .output_manager import settle_successful_batch, get_candidates_bundle
from .recovery_manager import request_cancel, mark_stale_dispatches, pending_outbox, ack_outbox

__all__ = (
    "register_config_version", "create_planned_generation", "get_planned_generation",
    "make_batches", "RunState", "BatchState", "AttemptState",
    "validate_generation_config", "require_transition",
    "create_generation_quote", "authorize_generation_budget",
    "revoke_generation_budget", "get_generation_budget",
    "reserve_batch", "record_dispatch_intent", "settle_known_failure",
    "mark_uncertain_attempt", "release_unsubmitted_attempt",
    "FakeProviderPort", "BatchRequest", "ProviderResponse",
    "PossiblyBilled", "KnownProviderFailure", "planned_next_request",
    "process_one_batch", "drain_generation", "settle_successful_batch",
    "get_candidates_bundle", "request_cancel", "mark_stale_dispatches",
    "pending_outbox", "ack_outbox",
)
