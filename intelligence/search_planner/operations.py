"""SP-04 — supervised one-step worker with explicit authorization and recovery.

Preserves the existing transactional SQG ledger. No daemon starts on import or
construction. No automatic paid retries, no Collector imports, no GET spending.

The paid executor records the durable dispatch_intent BEFORE the one provider
POST. Crashes after that transition have UNKNOWN billing and must be marked
uncertain, never blindly replayed. A crash while still 'reserved' is safe to
release only after an independently authorized operator review.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid

from .contracts import publication_uuid
from .generation_workflow import OwnerActionGuard, _guard
from .ai_query.live_worker import preflight_live_batch, execute_one_paid_batch
from .ai_query.transport import OpenRouterQueryTransport
from .generation_engine.recovery_manager import (
    request_cancel, mark_stale_dispatches,
)
from .generation_engine.repository import release_unsubmitted_attempt


MAX_STALE_SECONDS = 86400
DEFAULT_STALE_SECONDS = 900


def _age(seconds: int) -> int:
    if type(seconds) is not int or not 1 <= seconds <= MAX_STALE_SECONDS:
        raise PipelineError("Invalid SP recovery age", 422)
    return seconds


def execution_snapshot(project_id: str, generation_id: str, *,
                       connect=None) -> dict[str, Any]:
    """Read-only, project-scoped status/batches/attempts and ledger totals.

    Does not change dispatch state, reveal raw prompts, provider credentials,
    response contents or authorize an external call.
    """
    pid, gid = _pid(project_id), publication_uuid(generation_id)
    factory = connect if connect is not None else _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            run = conn.execute(
                """SELECT g.status,g.cancel_requested,g.quote_id,g.consent_id,
                          c.authorized_usd,c.spent_usd,c.reserved_usd,c.revoked_at,
                          c.expires_at
                     FROM sqg_generations g
                     LEFT JOIN sqg_consents c
                       ON c.id=g.consent_id AND c.project_id=g.project_id
                    WHERE g.project_id=%s AND g.id=%s::uuid""",
                (pid, gid),
            ).fetchone()
            if run is None:
                raise PipelineError("Search generation not found in project", 404)
            batches = conn.execute(
                """SELECT b.status,COUNT(*)
                     FROM sqg_batches b
                    WHERE b.generation_id=%s::uuid
                    GROUP BY b.status ORDER BY b.status""", (gid,),
            ).fetchall()
            attempts = conn.execute(
                """SELECT a.status,COUNT(*)
                     FROM sqg_attempts a
                    WHERE a.generation_id=%s::uuid
                    GROUP BY a.status ORDER BY a.status""", (gid,),
            ).fetchall()
    if run[3] is None:
        money = None
    else:
        if any(x is None for x in run[4:7]):
            raise PipelineError("Search Planner spending ledger incomplete", 409)
        money = {
            "authorized_usd": str(run[4]),
            "spent_usd": str(run[5]),
            "reserved_usd": str(run[6]),
            "available_usd": str(run[4] - run[5] - run[6]),
            "revoked": run[7] is not None,
            "expires_at": run[8].isoformat() if run[8] else None,
        }
    return {
        "schema_version": "search-planner-execution-status/v1",
        "project_id": pid,
        "generation_id": gid,
        "generation_status": run[0],
        "cancel_requested": bool(run[1]),
        "has_quote": run[2] is not None,
        "has_consent": run[3] is not None,
        "budget": money,
        "batch_states": {k: int(v) for k, v in batches},
        "attempt_states": {k: int(v) for k, v in attempts},
        "provider_requests_in_this_read": 0,
    }


def unsubmitted_stale_attempt_ids(project_id: str, generation_id: str, *,
                                  older_than_seconds: int = DEFAULT_STALE_SECONDS,
                                  limit: int = 50, connect=None) -> list[str]:
    """Inspect pre-dispatch reservations only. No writes or funding release.

    These records have not reached dispatch_intent. A human/supervisor must
    separately authorize each cleanup. A currently running reservation can
    appear here if its worker stalls, so listing alone is NOT cleanup consent.
    """
    pid, gid = _pid(project_id), publication_uuid(generation_id)
    seconds = _age(older_than_seconds)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise PipelineError("Invalid SP recovery page size", 422)
    factory = connect if connect is not None else _connect
    with factory() as conn:
        rows = conn.execute(
            """SELECT a.id FROM sqg_attempts a
               JOIN sqg_generations g ON g.id=a.generation_id
              WHERE g.project_id=%s AND g.id=%s::uuid
                AND a.status='reserved'
                AND a.dispatch_intent_at IS NULL
                AND a.started_at < NOW() - (%s * INTERVAL '1 second')
              ORDER BY a.started_at,a.id LIMIT %s""",
            (pid, gid, seconds, limit),
        ).fetchall()
    return [str(r[0]) for r in rows]


class GenerationExecutionController:
    """Deliberately manually stepped, no hidden background paid scheduler.

    OwnerActionGuard MUST originate from trusted application assembly; never
    from request JSON. SP-13 still needs to implement real session/project ACL.
    Test doubles are accepted only as injected server-owned Python callables.
    """

    def __init__(
        self, *,
        owner_action_guard: OwnerActionGuard | None = None,
        transport_factory: Callable[[], OpenRouterQueryTransport] = OpenRouterQueryTransport,
        paid_executor: Callable = execute_one_paid_batch,
        preflight_reader: Callable = preflight_live_batch,
        status_reader: Callable = execution_snapshot,
        stale_dispatch_recovery: Callable = mark_stale_dispatches,
        stale_reservation_reader: Callable = unsubmitted_stale_attempt_ids,
        unsubmitted_release: Callable = release_unsubmitted_attempt,
        cancel_action: Callable = request_cancel,
    ) -> None:
        ports = (transport_factory, paid_executor, preflight_reader, status_reader,
                 stale_dispatch_recovery, stale_reservation_reader,
                 unsubmitted_release, cancel_action)
        if any(not callable(fn) for fn in ports):
            raise PipelineError("Search Planner operation port is not callable", 422)
        if owner_action_guard is not None and not callable(owner_action_guard):
            raise PipelineError("Search Planner owner guard is not callable", 422)
        self._owner = owner_action_guard
        self._factory = transport_factory
        self._execute = paid_executor
        self._preflight = preflight_reader
        self._status = status_reader
        self._stale_dispatch = stale_dispatch_recovery
        self._stale_reservations = stale_reservation_reader
        self._release = unsubmitted_release
        self._cancel = cancel_action

    def status(self, project_id: str, generation_id: str) -> dict:
        """Unprivileged readonly status; project scope is checked by DB."""
        return self._status(_pid(project_id), publication_uuid(generation_id))

    def preflight(self, project_id: str, generation_id: str) -> dict:
        """Safe price/consent check without exposing the full model prompt."""
        pid, gid = _pid(project_id), publication_uuid(generation_id)
        detail = self._preflight(pid, gid)
        if detail is None:
            return {"generation_id": gid, "eligible": False,
                    "reason": "no_eligible_batch", "paid_calls": 0}
        if (type(detail) is not dict or "plan" not in detail or
                "exact_request_sha256" not in detail):
            raise PipelineError("Invalid paid execution preflight result", 409)
        plan = detail["plan"]
        if plan.generation_id != gid:
            raise PipelineError("Preflight generation mismatch", 409)
        return {
            "generation_id": gid, "eligible": True,
            "batch_id": plan.batch_id,
            "exact_request_sha256": detail["exact_request_sha256"],
            "quoted_reserve_usd": detail["quoted_reserve_usd"],
            "calculated_reserve_usd": detail["calculated_reserve_usd"],
            "paid_calls": 0,
        }

    def execute_one(self, project_id: str, generation_id: str) -> dict:
        """Exactly one paid batch after authenticated *execution* action.

        A generation budget consent is not sufficient authorization to set
        explicit_paid_execution. No worker starts automatically; the genuine
        HTTP owner/session boundary must be connected in SP-13.
        """
        pid, gid = _pid(project_id), publication_uuid(generation_id)
        _guard(self._owner, "search_planner.generation.execute_paid_batch", pid, gid)
        transport = self._factory()
        if type(transport) is not OpenRouterQueryTransport:
            raise PipelineError("Trusted OpenRouter transport is required", 422)
        return self._execute(pid, gid, transport=transport,
                             explicit_paid_execution=True)

    def recover_stale_dispatches(self, project_id: str, generation_id: str, *,
                                 older_than_seconds: int = DEFAULT_STALE_SECONDS) -> dict:
        """Mark old dispatch_intent attempts uncertain, never resubmit POST."""
        pid, gid = _pid(project_id), publication_uuid(generation_id)
        seconds = _age(older_than_seconds)
        _guard(self._owner, "search_planner.generation.recover", pid, gid)
        result = self._stale_dispatch(pid, gid, older_than_seconds=seconds)
        if type(result) is not dict or result.get("paid_retries") != 0:
            raise PipelineError("Unsafe SP recovery response", 409)
        return result

    def list_stale_reservations(self, project_id: str, generation_id: str, *,
                                older_than_seconds: int = DEFAULT_STALE_SECONDS) -> list[str]:
        """Read-only operator diagnostic; ids are never proof of safe release."""
        pid, gid = _pid(project_id), publication_uuid(generation_id)
        return self._stale_reservations(pid, gid,
                                        older_than_seconds=_age(older_than_seconds))

    def release_unsubmitted(self, project_id: str, generation_id: str,
                            attempt_id: str, *, older_than_seconds: int =
                            DEFAULT_STALE_SECONDS) -> dict:
        """Manual recheck then transactional release of reserved-only funds.

        Any race with dispatch_intent must fail closed in the existing
        release_unsubmitted_attempt() SQL transaction.
        """
        pid, gid = _pid(project_id), publication_uuid(generation_id)
        aid = publication_uuid(attempt_id)
        seconds = _age(older_than_seconds)
        _guard(self._owner, "search_planner.generation.release_unsubmitted", pid, gid)
        listed = self._stale_reservations(pid, gid,
                                          older_than_seconds=seconds, limit=100)
        if aid not in listed:
            raise PipelineError("Attempt is not verified stale and undispatched", 409)
        result = self._release(pid, aid)
        if (type(result) is not dict or result.get("attempt_id") != aid or
                result.get("status") != "abandoned_before_dispatch"):
            raise PipelineError("Unexpected SP reserve release result", 409)
        return result

    def cancel(self, project_id: str, generation_id: str) -> dict:
        """Independent owner-approved cancellation; dispatched cost stays held."""
        pid, gid = _pid(project_id), publication_uuid(generation_id)
        _guard(self._owner, "search_planner.generation.cancel", pid, gid)
        return self._cancel(pid, gid)
