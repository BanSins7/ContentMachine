"""SP-15C: explicitly enabled foreground supervisor for NON-BILLABLE QV->SP inbox.

This process has no LLM transport, Collector adapter, provider POST or recovery
permissions. It only drives SP12's trusted PostgreSQL QV outbox -> SP inbox
transaction. A dedicated heartbeat thread uses separate short-lived DB
connections to keep the fenced SP15B lease alive even during slow projections.

Importing this module has no side effects. Running it requires an explicit
--enable-safe-inbox flag. The lease is NOT authorization to spend money.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from dataclasses import dataclass, field
from uuid import uuid4

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.pipeline_repository import _pid
from .lease_ops import (
    acquire_inbox_lease, heartbeat_inbox_lease, release_inbox_lease,
)
from .supervision import run_safe_inbox_cycle

SCHEMA = "search-planner-safe-supervisor/v1"


def _bounded_int(value, lower, upper, label):
    if type(value) is not int or not lower <= value <= upper:
        raise PipelineError("Invalid " + label, 422)
    return value


def _public_event(project_id, event, *, cycle_count=0, ingested_count=0,
                  no_work_count=0, reason=None):
    """Fixed-schema operational output. Never include exception strings/secrets."""
    return {
        "schema_version": SCHEMA,
        "project_id": project_id,
        "event": event,
        "cycles": cycle_count,
        "ingested_events": ingested_count,
        "no_work_events": no_work_count,
        "reason_code": reason,
        "paid_http_calls": 0,
        "collector_accept_calls": 0,
        "provider_search_calls": 0,
    }


def _json_report(event):
    print(json.dumps(event, ensure_ascii=False, separators=(",", ":")), flush=True)


@dataclass(frozen=True)
class SupervisorSettings:
    """Server/operator-only settings; nothing comes from query or browser JSON."""

    limit: int = 5
    lease_seconds: int = 120
    heartbeat_seconds: int = 20
    poll_seconds: int = 10
    max_cycles: int = 0  # zero means run until operator/process stop

    def __post_init__(self):
        _bounded_int(self.limit, 1, 50, "QV inbox page size")
        _bounded_int(self.lease_seconds, 30, 300, "lease seconds")
        _bounded_int(self.heartbeat_seconds, 5, 60, "heartbeat seconds")
        _bounded_int(self.poll_seconds, 1, 3600, "poll seconds")
        _bounded_int(self.max_cycles, 0, 100000, "max cycles")
        if self.heartbeat_seconds * 3 > self.lease_seconds:
            raise PipelineError("Heartbeat interval must fit in lease TTL", 422)


@dataclass
class _LeaseHealth:
    lost: threading.Event = field(default_factory=threading.Event)
    ended: threading.Event = field(default_factory=threading.Event)


class SafeInboxSupervisor:
    """Single fenced foreground session with independent lease heartbeat.

    Every cycle is an SP12 idempotent QV->SP inbox operation. Even if the
    process dies while that transaction is running, replay never results
    in external POST, and QV ACK is atomic with durable inbox insertion.
    """

    def __init__(self, project_id, *, settings=None, service=None,
                 acquirer=acquire_inbox_lease,
                 heartbeater=heartbeat_inbox_lease,
                 releaser=release_inbox_lease,
                 processor=run_safe_inbox_cycle,
                 reporter=_json_report):
        self.project_id = _pid(project_id)
        self.settings = settings if settings is not None else SupervisorSettings()
        if type(self.settings) is not SupervisorSettings:
            raise PipelineError("Untrusted supervisor settings", 422)
        if any(not callable(fn) for fn in (
            acquirer, heartbeater, releaser, processor, reporter
        )):
            raise PipelineError("Invalid supervisor backend port", 422)
        self._acquire = acquirer
        self._heartbeat = heartbeater
        self._release = releaser
        self._processor = processor
        self._reporter = reporter
        self._service = service

    def run(self, *, stop_event=None):
        """Foreground operation only. One database lease per process session.

        Caller controls graceful stop via a threading.Event. No signal
        handlers or threads are registered by constructing this class.
        """
        stop = stop_event if stop_event is not None else threading.Event()
        if not callable(getattr(stop, "is_set", None)) or not callable(
            getattr(stop, "wait", None)
        ) or not callable(getattr(stop, "set", None)):
            raise PipelineError("Invalid stop signal", 422)
        pid = self.project_id
        cfg = self.settings
        owner_id = str(uuid4())
        lease = self._acquire(pid, owner_id, lease_seconds=cfg.lease_seconds)
        if lease is None:
            outcome = _public_event(pid, "lease_busy", reason="active_owner")
            self._reporter(outcome)
            return outcome
        if (type(lease) is not dict or lease.get("owner_id") != owner_id or
                lease.get("project_id") != pid or
                type(lease.get("fence_generation")) is not int or
                lease["fence_generation"] <= 0):
            raise PipelineError("Untrusted supervisor lease", 409)

        fence = lease["fence_generation"]
        health = _LeaseHealth()
        heartbeat_stop = threading.Event()
        heartbeat_thread = None
        count = ingested = no_work = 0
        reason = None
        released = False

        def pulse():
            try:
                while not heartbeat_stop.wait(cfg.heartbeat_seconds):
                    if self._heartbeat(
                        pid, owner_id, fence, lease_seconds=cfg.lease_seconds
                    ) is not True:
                        health.lost.set()
                        stop.set()
                        break
            except Exception:
                # No raw SQL/network/credential exception content in logs.
                health.lost.set()
                stop.set()
            finally:
                health.ended.set()

        try:
            # Verify ownership *before* the first QV transaction.
            if self._heartbeat(
                pid, owner_id, fence, lease_seconds=cfg.lease_seconds
            ) is not True:
                raise PipelineError("Supervisor lease not renewable", 409)
            heartbeat_thread = threading.Thread(
                target=pulse, name="cm-sp-safe-inbox-heartbeat", daemon=True
            )
            heartbeat_thread.start()
            self._reporter(_public_event(pid, "started"))
            while not stop.is_set() and not health.lost.is_set():
                if cfg.max_cycles and count >= cfg.max_cycles:
                    reason = "bounded_cycles_completed"
                    break
                # Fenced precondition for each new cycle. A lost lease
                # prevents any *new* work even if a prior cycle completed.
                if self._heartbeat(
                    pid, owner_id, fence, lease_seconds=cfg.lease_seconds
                ) is not True:
                    health.lost.set()
                    raise PipelineError("Supervisor lease lost before cycle", 409)
                if stop.is_set() or health.lost.is_set():
                    break
                result = self._processor(
                    pid, limit=cfg.limit, service=self._service
                )
                if (type(result) is not dict or
                        result.get("schema_version") != "search-planner-inbox-cycle/v1" or
                        result.get("project_id") != pid or
                        type(result.get("ingested_events")) is not int or
                        type(result.get("no_work_events")) is not int or
                        not 0 <= result["no_work_events"] <= result["ingested_events"] <= cfg.limit or
                        any(type(result.get(k)) is not int or result[k] != 0
                            for k in ("paid_http_requests",
                                      "collector_accept_calls",
                                      "provider_search_calls"))):
                    raise PipelineError("Unsafe supervisor cycle output", 409)
                # Count only after actual SP12 commit returns; a lease loss
                # during work is surfaced as a failure, not a clean success.
                count += 1
                ingested += result["ingested_events"]
                no_work += result["no_work_events"]
                if health.lost.is_set():
                    raise PipelineError("Supervisor heartbeat failed during work", 409)
                if self._heartbeat(
                    pid, owner_id, fence, lease_seconds=cfg.lease_seconds
                ) is not True:
                    health.lost.set()
                    raise PipelineError("Supervisor lease lost after cycle", 409)
                self._reporter(_public_event(
                    pid, "cycle", cycle_count=count, ingested_count=ingested,
                    no_work_count=no_work,
                ))
                if cfg.max_cycles and count >= cfg.max_cycles:
                    reason = "bounded_cycles_completed"
                    break
                if stop.wait(cfg.poll_seconds):
                    reason = "operator_stop"
                    break
            if health.lost.is_set():
                raise PipelineError("Supervisor heartbeat lost; operator inspection required", 409)
            if reason is None:
                reason = "operator_stop" if stop.is_set() else "bounded_cycles_completed"
            outcome = _public_event(
                pid, "stopped", cycle_count=count, ingested_count=ingested,
                no_work_count=no_work, reason=reason,
            )
            return outcome
        finally:
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                # Never release a lease while its heartbeat thread may still
                # be renewing it. An unresponsive DB heartbeat is left to TTL
                # expiry (and next session fenced takeover), not force-unlocked.
                heartbeat_thread.join(timeout=cfg.heartbeat_seconds + 5)
            if heartbeat_thread is None or not heartbeat_thread.is_alive():
                try:
                    released = self._release(pid, owner_id, fence) is True
                except Exception:
                    released = False
            self._reporter(_public_event(
                pid, "session_closed",
                cycle_count=count, ingested_count=ingested,
                no_work_count=no_work,
                reason="released" if released else "lease_expiry_required",
            ))
            if not released:
                # A stuck heartbeat or failed release is NOT clean shutdown.
                # Never force-delete another owner's lease; let TTL expire.
                raise PipelineError("Supervisor lease release unverified", 409)


def _cli_number(low, high):
    def convert(value):
        try:
            n = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("Expected integer") from exc
        if str(n) != value or not low <= n <= high:
            raise argparse.ArgumentTypeError("Invalid bounded value")
        return n
    return convert


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Explicit, non-paid Content Machine QV->SP inbox supervisor"
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--enable-safe-inbox", action="store_true")
    parser.add_argument("--limit", type=_cli_number(1, 50), default=5)
    parser.add_argument("--lease-seconds", type=_cli_number(30, 300), default=120)
    parser.add_argument("--heartbeat-seconds", type=_cli_number(5, 60), default=20)
    parser.add_argument("--poll-seconds", type=_cli_number(1, 3600), default=10)
    parser.add_argument("--max-cycles", type=_cli_number(0, 100000), default=0)
    args = parser.parse_args(argv)
    if not args.enable_safe_inbox:
        print(json.dumps({
            "schema_version": SCHEMA, "error_code": "explicit_enable_required"
        }), file=sys.stderr, flush=True)
        return 2
    stop = threading.Event()
    original_handlers = {}
    try:
        settings = SupervisorSettings(
            limit=args.limit, lease_seconds=args.lease_seconds,
            heartbeat_seconds=args.heartbeat_seconds,
            poll_seconds=args.poll_seconds, max_cycles=args.max_cycles,
        )
        supervisor = SafeInboxSupervisor(args.project, settings=settings)
        # Only main-thread CLI execution installs OS handlers; app.py/import
        # never acquires a lease, changes signal handling, or creates threads.
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                original_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, lambda _signum, _frame: stop.set())
        result = supervisor.run(stop_event=stop)
        return 0 if result["event"] in ("stopped", "lease_busy") else 1
    except KeyboardInterrupt:
        stop.set()
        return 130
    except Exception:
        print(json.dumps({
            "schema_version": SCHEMA, "error_code": "safe_supervisor_failed",
            "paid_http_calls": 0, "provider_search_calls": 0,
        }), file=sys.stderr, flush=True)
        return 1
    finally:
        for sig, handler in original_handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
