"""SP-15A operational snapshots and explicitly invoked, non-paid QV inbox cycles.

This module never starts a daemon on import, never calls an LLM or Collector
adapter, and does not auto-retry uncertain external requests. PostgreSQL is
the durable source of truth: replay safety belongs to the SP-12 QV row lock
and atomic inbox insert/ACK, not to this process's memory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import sys
import time

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid

GENERATION_STATES = frozenset((
    "created", "awaiting_authorization", "queued", "running",
    "paused_budget", "paused_price", "blocked_config", "uncertain",
    "failed", "completed", "cancelled",
))
INBOX_STATES = frozenset((
    "pending_planning", "awaiting_owner_commit", "committed_held",
    "awaiting_collector", "delivering", "collector_accepted",
    "incompatible", "needs_attention", "no_work", "cancelled",
))
ATTEMPT_STATES = frozenset(("uncertain", "accepted", "incompatible", "needs_attention"))


def _counts(rows, allowed):
    counts = {}
    for state, count in rows:
        if state not in allowed or type(count) is not int or count < 0 or state in counts:
            raise PipelineError("Invalid Search Planner operational state/count", 409)
        counts[state] = count
    return dict(sorted(counts.items()))


def read_operational_snapshot(project_id, *, connect=None):
    """One project-scoped REPEATABLE READ, READ ONLY snapshot; no raw query data.

    A missing metric remains unavailable rather than being invented as zero.
    The only emitted monetary numbers are aggregated Decimal strings.
    """
    pid = _pid(project_id)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            generations = conn.execute(
                """SELECT status,COUNT(*) FROM sqg_generations
                   WHERE project_id=%s GROUP BY status ORDER BY status""",
                (pid,),
            ).fetchall()
            spend = conn.execute(
                """SELECT SUM(authorized_usd),SUM(spent_usd),SUM(reserved_usd)
                   FROM sqg_consents WHERE project_id=%s""",
                (pid,),
            ).fetchone()
            pending = conn.execute(
                """SELECT COUNT(*),
                          EXTRACT(EPOCH FROM (now()-MIN(o.created_at)))
                   FROM qv_outbox o JOIN qv_runs r ON r.id=o.run_id
                   WHERE r.project_id=%s AND r.embedding_mode='semantic_local'
                     AND o.delivered_at IS NULL""",
                (pid,),
            ).fetchone()
            fixture = conn.execute(
                """SELECT COUNT(*) FROM qv_outbox o
                   JOIN qv_runs r ON r.id=o.run_id
                   WHERE r.project_id=%s AND r.embedding_mode='test_fixture'
                     AND o.delivered_at IS NULL""",
                (pid,),
            ).fetchone()
            inbox = conn.execute(
                """SELECT status,COUNT(*) FROM sp_handoff_events
                   WHERE project_id=%s GROUP BY status ORDER BY status""",
                (pid,),
            ).fetchall()
            delivery = conn.execute(
                """SELECT a.status,COUNT(*) FROM sp_delivery_attempts a
                   JOIN sp_handoff_events e ON e.id=a.event_id
                   WHERE e.project_id=%s GROUP BY a.status ORDER BY a.status""",
                (pid,),
            ).fetchall()

    if spend is None or len(spend) != 3 or any(
        v is not None and type(v) is not Decimal for v in spend
    ):
        raise PipelineError("Invalid SP financial aggregation", 409)
    if (pending is None or len(pending) != 2 or
            type(pending[0]) is not int or pending[0] < 0 or
            fixture is None or len(fixture) != 1 or
            type(fixture[0]) is not int or fixture[0] < 0):
        raise PipelineError("Invalid SP outbox accounting", 409)
    if pending[0] == 0 and pending[1] is not None:
        raise PipelineError("Invalid empty outbox age", 409)
    if pending[0] and (pending[1] is None or not isinstance(pending[1], (int, float, Decimal))):
        raise PipelineError("Missing pending outbox age", 409)
    ages = max(0, int(pending[1])) if pending[0] else None

    gen = _counts(generations, GENERATION_STATES)
    box = _counts(inbox, INBOX_STATES)
    tasks = _counts(delivery, ATTEMPT_STATES)
    warning_codes = []
    if gen.get("uncertain", 0) or tasks.get("uncertain", 0):
        warning_codes.append("unknown_external_outcome_manual_reconciliation")
    if box.get("needs_attention", 0) or tasks.get("needs_attention", 0):
        warning_codes.append("handoff_needs_operator_attention")
    if ages is not None and ages >= 900:
        warning_codes.append("qv_inbox_lag_over_15m")
    if fixture[0]:
        warning_codes.append("test_fixture_quarantined_not_dispatchable")

    return {
        "schema_version": "search-planner-operational-snapshot/v1",
        "project_id": pid,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "generation_states": gen,
        "sp_inbox_states": box,
        "collector_delivery_attempt_states": tasks,
        "qv_pending_production_events": pending[0],
        "qv_oldest_pending_age_seconds": ages,
        "qv_quarantined_fixture_events": fixture[0],
        "consent_totals_usd": (None if spend[0] is None else {
            "authorized": str(spend[0]),
            "spent": str(spend[1]),
            "reserved": str(spend[2]),
        }),
        "warning_codes": warning_codes,
        "paid_http_requests_in_snapshot": 0,
        "provider_calls_in_snapshot": 0,
    }


def run_safe_inbox_cycle(project_id, *, limit=10, service=None):
    """Explicitly consume QV -> SP durable inbox; never execute paid/search work."""
    pid = _pid(project_id)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise PipelineError("Invalid SP safe inbox cycle limit", 422)
    if service is None:
        from .service import SearchPlannerService
        service = SearchPlannerService()
    result = service.ingest_pending_qv_handoffs(pid, limit=limit)
    if type(result) is not list or len(result) > limit or any(type(x) is not dict for x in result):
        raise PipelineError("Invalid supervised inbox cycle result", 409)
    return {
        "schema_version": "search-planner-inbox-cycle/v1",
        "project_id": pid,
        "ingested_events": len(result),
        "no_work_events": sum(x.get("status") == "no_work" for x in result),
        "paid_http_requests": 0,
        "collector_accept_calls": 0,
        "provider_search_calls": 0,
    }


def _int_between(lower, upper):
    def parse(value):
        try:
            n = int(value)
        except (TypeError, ValueError) as exc:
            raise argparse.ArgumentTypeError("Expected an integer") from exc
        if str(n) != value or not lower <= n <= upper:
            raise argparse.ArgumentTypeError("Integer is outside allowed range")
        return n
    return parse


def main(argv=None):
    """Bounded foreground operator command: --mode observe by default.

    --mode inbox is explicit. Multi-cycle mode requires a finite cycle count;
    no auto-start or hidden background paid worker exists.
    """
    parser = argparse.ArgumentParser(description="Search Planner safe supervisor")
    parser.add_argument("--project", required=True)
    parser.add_argument("--mode", choices=("observe", "inbox"), default="observe")
    parser.add_argument("--limit", type=_int_between(1, 50), default=10)
    parser.add_argument("--cycles", type=_int_between(1, 100), default=1)
    parser.add_argument("--interval-seconds", type=_int_between(5, 3600), default=30)
    args = parser.parse_args(argv)
    try:
        pid = _pid(args.project)
        for i in range(args.cycles):
            result = (read_operational_snapshot(pid) if args.mode == "observe"
                      else run_safe_inbox_cycle(pid, limit=args.limit))
            # Deliberately no raw exceptions, provider payloads, API keys or queries.
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
            if i + 1 < args.cycles:
                time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        return 130
    except Exception:
        print(json.dumps({
            "schema_version": "search-planner-supervisor-error/v1",
            "error_code": "operational_cycle_failed",
            "provider_calls": 0,
        }), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
