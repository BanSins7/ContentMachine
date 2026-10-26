"""SP-03: trusted zero-provider generation planning, quote and consent facade.

Reuse existing production PostgreSQL transactions: never create parallel
run/quote/consent tables or implement an untracked money ledger. There is no
model invocation here. Owner authorization is checked by an injected,
trusted server-side callback; without it paid budget consent fails closed.
Do not let HTTP request JSON provide a callback or return its own approval.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Protocol

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.pipeline_repository import _pid
from .contracts import publication_uuid
from .generation_engine.contracts import validate_generation_config
from .generation_engine.cost_estimator import money
from .generation_engine.run_manager import (
    register_config_version, create_planned_generation,
)
from .generation_engine.budget_manager import (
    create_generation_quote, authorize_generation_budget,
    revoke_generation_budget, get_generation_budget,
)


class OwnerActionGuard(Protocol):
    """Trusted, server-constructed gate; user-supplied booleans are NOT identity."""

    def __call__(self, action: str, project_id: str,
                 object_id: str) -> bool: ...


def _guard(guard: OwnerActionGuard | None, action: str,
           project_id: str, object_id: str) -> None:
    # No gate => no spending consent (or revocation), even in a unit test.
    if guard is None:
        raise PipelineError("Verified local owner action required", 403)
    if not callable(guard):
        raise PipelineError("Owner action guard unavailable", 503)
    try:
        permitted = guard(action, project_id, object_id)
    except Exception as exc:
        # Do not leak tokens, headers, user identity or auth error details.
        raise PipelineError("Owner action verification failed", 403) from exc
    if permitted is not True:
        raise PipelineError("Verified local owner action required", 403)


def _response(raw: Any, *, project_id: str, operation: str,
              required: tuple[str, ...]) -> dict[str, Any]:
    if type(raw) is not dict or not all(k in raw for k in required):
        raise PipelineError("Incomplete Search Planner " + operation + " result", 409)
    # Underlying storage functions remain authoritative. We return a defensive
    # copy rather than passing mutable DB or injected fake values downstream.
    if "project_id" in raw and raw["project_id"] != project_id:
        raise PipelineError("Search Planner result belongs to another project", 409)
    return deepcopy(raw)


class GenerationWorkflow:
    """SP-03 service port. No HTTP endpoints, provider worker, Collector imports.

    All mutators are explicitly called. Registration and planning cost $0;
    quote persists a conservative estimate without consent. approve() is
    absent from a default unauthenticated assembly by design.
    """

    def __init__(
        self, *,
        owner_action_guard: OwnerActionGuard | None = None,
        register_config: Callable = register_config_version,
        create_plan: Callable = create_planned_generation,
        create_quote: Callable = create_generation_quote,
        authorize_budget: Callable = authorize_generation_budget,
        revoke_budget: Callable = revoke_generation_budget,
        read_budget: Callable = get_generation_budget,
    ) -> None:
        calls = (register_config, create_plan, create_quote, authorize_budget,
                 revoke_budget, read_budget)
        if any(not callable(x) for x in calls):
            raise PipelineError("Invalid Search Planner GenerationWorkflow port", 422)
        if owner_action_guard is not None and not callable(owner_action_guard):
            raise PipelineError("Owner action guard must be trusted and callable", 422)
        self._guard = owner_action_guard
        self._register = register_config
        self._plan = create_plan
        self._quote = create_quote
        self._authorize = authorize_budget
        self._revoke = revoke_budget
        self._budget = read_budget

    def register_configuration(self, project_id: str,
                               configuration: dict[str, Any]) -> dict[str, Any]:
        """Approve pinned config version, not provider spending."""
        pid = _pid(project_id)
        validated = validate_generation_config(configuration)
        return _response(self._register(pid, validated), project_id=pid,
                         operation="configuration",
                         required=("config_id", "config_sha256", "revision", "status"))

    def plan(self, project_id: str, publication_id: str, config_id: str,
             idempotency_key: str) -> dict[str, Any]:
        """Durable context/batch registration, idempotent on exact input key."""
        pid = _pid(project_id)
        pubid = publication_uuid(publication_id)
        cid = publication_uuid(config_id)
        if (type(idempotency_key) is not str or
                not 12 <= len(idempotency_key) <= 128):
            raise PipelineError("Invalid Search Planner idempotency key", 422)
        result = self._plan(pid, pubid, cid, idempotency_key)
        return _response(result, project_id=pid, operation="planned generation",
                         required=("generation_id", "status", "input_sha256"))

    def quote(self, project_id: str, generation_id: str) -> dict[str, Any]:
        """Persistent indicative quote; no consent and zero provider work."""
        pid = _pid(project_id)
        gid = publication_uuid(generation_id)
        result = self._quote(pid, gid)
        answer = _response(result, project_id=pid, operation="generation quote",
                           required=("quote_id", "generation_id", "high_usd", "currency"))
        if answer["generation_id"] != gid or answer["currency"] != "USD":
            raise PipelineError("Generation quote identity mismatch", 409)
        publication_uuid(answer["quote_id"])
        money(answer["high_usd"], "high_usd", positive=True)
        return answer

    def budget(self, project_id: str, generation_id: str) -> dict[str, Any]:
        """Read-only budget/status projection, no owner check/no money changes."""
        pid = _pid(project_id)
        gid = publication_uuid(generation_id)
        result = self._budget(pid, gid)
        answer = _response(result, project_id=pid, operation="generation budget",
                           required=("generation_id", "status", "consent"))
        if answer["generation_id"] != gid:
            raise PipelineError("Generation budget identity mismatch", 409)
        return answer

    def authorize(self, project_id: str, generation_id: str,
                  quote_id: str, authorized_usd: str) -> dict[str, Any]:
        """Separate explicit owner consent, NOT provider dispatch.

        Calls the trusted guard BEFORE touching PostgreSQL or its money ledger.
        No caller-supplied `approved=True` bypass is accepted. SP-13 HTTP
        must inject a real authenticated local owner gate.
        """
        pid = _pid(project_id)
        gid = publication_uuid(generation_id)
        qid = publication_uuid(quote_id)
        budget = money(authorized_usd, "authorized_usd", positive=True)
        _guard(self._guard, "search_planner.generation.authorize", pid, gid)
        response = self._authorize(pid, gid, qid, format(budget, "f"))
        answer = _response(response, project_id=pid, operation="generation consent",
                           required=("consent_id", "generation_id", "authorized_usd", "status"))
        if answer["generation_id"] != gid:
            raise PipelineError("Generation consent identity mismatch", 409)
        publication_uuid(answer["consent_id"])
        return answer

    def revoke(self, project_id: str, consent_id: str) -> dict[str, Any]:
        """Owner-only revocation; backend pauses worker via existing ledger."""
        pid = _pid(project_id)
        cid = publication_uuid(consent_id)
        _guard(self._guard, "search_planner.generation.revoke", pid, cid)
        response = self._revoke(pid, cid)
        answer = _response(response, project_id=pid, operation="generation revocation",
                           required=("consent_id", "generation_id", "revoked"))
        if answer["consent_id"] != cid:
            raise PipelineError("Generation consent identity mismatch", 409)
        return answer
