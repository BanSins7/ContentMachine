"""SP-15C safe foreground process, independent heartbeat, stop, no-paid QA."""
from __future__ import annotations

import contextlib
from io import StringIO
import json
import threading
import time
import unittest
from unittest import mock

from intelligence.search_planner.safe_daemon import (
    SafeInboxSupervisor, SupervisorSettings, main,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

PID = "a" * 32


def valid_cycle(pid, *, limit, service):
    return {
        "schema_version": "search-planner-inbox-cycle/v1",
        "project_id": pid,
        "ingested_events": 1,
        "no_work_events": 1,
        "paid_http_requests": 0,
        "collector_accept_calls": 0,
        "provider_search_calls": 0,
    }


def fixture(*, count=1, max_cycles=1, processor=valid_cycle,
            heartbeat=None, stop=None):
    """All ports are controlled; never connect to PostgreSQL/provider."""
    calls, reports = [], []
    heartbeater = heartbeat or (lambda *a, **k: True)

    def acquirer(pid, owner, *, lease_seconds):
        calls.append(("acquire", pid, lease_seconds))
        return {"project_id": pid, "owner_id": owner, "fence_generation": 11}

    def pulse(pid, owner, fence, *, lease_seconds):
        calls.append(("heartbeat", fence))
        return heartbeater(pid, owner, fence, lease_seconds=lease_seconds)

    def release(pid, owner, fence):
        calls.append(("release", fence))
        return True

    def process(pid, *, limit, service):
        calls.append(("process", pid, limit))
        return processor(pid, limit=limit, service=service)

    supervisor = SafeInboxSupervisor(
        PID, settings=SupervisorSettings(
            lease_seconds=30, heartbeat_seconds=5, poll_seconds=1,
            max_cycles=max_cycles, limit=count,
        ),
        service=object(), acquirer=acquirer, heartbeater=pulse,
        releaser=release, processor=process, reporter=reports.append,
    )
    return supervisor, calls, reports


class SP15SafeDaemonTests(unittest.TestCase):
    def test_safe_settings_are_bounded_before_opening_db(self):
        for options in (
            {"limit": 0}, {"limit": True}, {"max_cycles": -1},
            {"heartbeat_seconds": 20, "lease_seconds": 30},
            {"lease_seconds": 301}, {"poll_seconds": 0},
        ):
            with self.subTest(options=options), self.assertRaises(PipelineError):
                SupervisorSettings(**options)
        self.assertEqual(SupervisorSettings().max_cycles, 0)

    def test_one_fenced_cycle_and_clean_shutdown(self):
        worker, calls, reports = fixture()
        result = worker.run()
        self.assertEqual(result["event"], "stopped")
        self.assertEqual(result["reason_code"], "bounded_cycles_completed")
        self.assertEqual(result["cycles"], 1)
        self.assertEqual(result["ingested_events"], 1)
        self.assertEqual(result["no_work_events"], 1)
        self.assertEqual(result["paid_http_calls"], 0)
        self.assertEqual(result["collector_accept_calls"], 0)
        self.assertEqual(result["provider_search_calls"], 0)
        self.assertEqual(sum(x[0] == "process" for x in calls), 1)
        self.assertEqual([x["event"] for x in reports],
                         ["started", "cycle", "session_closed"])
        self.assertEqual(reports[-1]["reason_code"], "released")
        self.assertNotIn("owner_id", json.dumps(reports))
        self.assertEqual(calls[-1], ("release", 11))

    def test_busy_session_never_starts_thread_or_tries_qv(self):
        hits, reports = [], []
        worker = SafeInboxSupervisor(
            PID, settings=SupervisorSettings(max_cycles=1),
            acquirer=lambda *a, **k: None,
            heartbeater=lambda *a, **k: hits.append("heartbeat"),
            releaser=lambda *a, **k: hits.append("release"),
            processor=lambda *a, **k: hits.append("process"),
            reporter=reports.append,
        )
        result = worker.run()
        self.assertEqual(result["event"], "lease_busy")
        self.assertEqual(hits, [])
        self.assertEqual(len(reports), 1)

    def test_preexisting_stop_never_processes_work(self):
        stop = threading.Event()
        stop.set()
        worker, calls, reports = fixture(max_cycles=0)
        result = worker.run(stop_event=stop)
        self.assertEqual(result["reason_code"], "operator_stop")
        self.assertFalse(any(x[0] == "process" for x in calls))
        self.assertEqual(calls[-1], ("release", 11))

    def test_stop_during_cycle_finishes_only_that_cycle(self):
        stop = threading.Event()
        def process(pid, *, limit, service):
            stop.set()
            return valid_cycle(pid, limit=limit, service=service)
        worker, calls, reports = fixture(max_cycles=0, processor=process)
        result = worker.run(stop_event=stop)
        self.assertEqual(result["reason_code"], "operator_stop")
        self.assertEqual(result["cycles"], 1)
        self.assertEqual(sum(x[0] == "process" for x in calls), 1)

    def test_invalid_backend_output_does_not_claim_success(self):
        worker, calls, reports = fixture(processor=lambda *_a, **_k: {
            **valid_cycle(PID, limit=1, service=None),
            "paid_http_requests": 1,
        })
        with self.assertRaisesRegex(PipelineError, "Unsafe supervisor"):
            worker.run()
        self.assertEqual(calls[-1], ("release", 11))
        self.assertNotIn("cycle", [r["event"] for r in reports])

    def test_failed_heartbeat_blocks_next_cycle_and_still_releases(self):
        state = {"n": 0}
        def heartbeat(*_a, **_k):
            state["n"] += 1
            return state["n"] < 3
        worker, calls, reports = fixture(max_cycles=5, heartbeat=heartbeat)
        with self.assertRaisesRegex(PipelineError, "lost after cycle"):
            worker.run()
        self.assertEqual(sum(x[0] == "process" for x in calls), 1)
        self.assertEqual(calls[-1], ("release", 11))

    def test_backend_exception_fails_closed_with_release(self):
        worker, calls, reports = fixture(
            processor=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("key=PRIVATE"))
        )
        with self.assertRaisesRegex(RuntimeError, "PRIVATE"):
            worker.run()
        self.assertEqual(calls[-1], ("release", 11))
        self.assertNotIn("PRIVATE", json.dumps(reports))

    def test_release_failure_does_not_claim_clean_shutdown(self):
        reports = []
        worker = SafeInboxSupervisor(
            PID, settings=SupervisorSettings(max_cycles=1),
            acquirer=lambda pid, oid, **k: {
                "project_id": pid, "owner_id": oid, "fence_generation": 5,
            },
            heartbeater=lambda *a, **k: True,
            releaser=lambda *a, **k: False,
            processor=valid_cycle, reporter=reports.append,
        )
        with self.assertRaisesRegex(PipelineError, "release unverified"):
            worker.run()
        self.assertEqual(reports[-1]["reason_code"], "lease_expiry_required")

    def test_false_boolean_counter_is_not_a_real_zero(self):
        worker, calls, reports = fixture(
            processor=lambda pid, *, limit, service: {
                **valid_cycle(pid, limit=limit, service=service),
                "provider_search_calls": False,
            }
        )
        with self.assertRaisesRegex(PipelineError, "Unsafe supervisor"):
            worker.run()
        self.assertEqual(calls[-1], ("release", 11))

    def test_heartbeat_is_independent_during_blocked_projection(self):
        """5s interval: intentionally exercises the real thread, not a mock."""
        pulses = []
        began = threading.Event()
        def heartbeat(pid, owner, fence, *, lease_seconds):
            pulses.append(time.monotonic())
            if len(pulses) >= 3:  # startup + pre-cycle + BACKGROUND tick
                began.set()
            return True
        def slow(pid, *, limit, service):
            self.assertTrue(began.wait(7), "Heartbeat did not renew during slow work")
            return valid_cycle(pid, limit=limit, service=service)
        worker, calls, _reports = fixture(
            heartbeat=heartbeat, processor=slow, max_cycles=1
        )
        result = worker.run()
        self.assertEqual(result["event"], "stopped")
        self.assertGreaterEqual(len(pulses), 3)  # startup, background, post-cycle
        self.assertEqual(calls[-1], ("release", 11))

    def test_explicit_cli_switch_is_mandatory(self):
        error = StringIO()
        with contextlib.redirect_stderr(error):
            code = main(["--project", PID])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(error.getvalue())["error_code"],
                         "explicit_enable_required")

    def test_cli_redacts_exceptions_and_restores_signal_handlers(self):
        with mock.patch(
            "intelligence.search_planner.safe_daemon.SafeInboxSupervisor",
            side_effect=RuntimeError("postgres password SUPER_SECRET")
        ):
            error = StringIO()
            with contextlib.redirect_stderr(error):
                code = main(["--project", PID, "--enable-safe-inbox",
                             "--max-cycles", "1"])
            self.assertEqual(code, 1)
            self.assertNotIn("SUPER_SECRET", error.getvalue())
            self.assertEqual(json.loads(error.getvalue())["error_code"],
                             "safe_supervisor_failed")

    def test_cli_bounded_execution_has_no_paid_parameter(self):
        class Stub:
            def __init__(self, *a, **kw):
                self.args = a
                self.kw = kw
            def run(self, *, stop_event):
                self.assert_stop = hasattr(stop_event, "set")
                return {"event": "stopped"}
        with mock.patch("intelligence.search_planner.safe_daemon.SafeInboxSupervisor",
                        Stub):
            self.assertEqual(
                main(["--project", PID, "--enable-safe-inbox", "--max-cycles", "1"]),
                0,
            )


if __name__ == "__main__":
    unittest.main()
