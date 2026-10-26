"""SP-15A safety and project-scoped telemetry (offline, no PostgreSQL/server)."""
from __future__ import annotations

from decimal import Decimal
from io import StringIO
from unittest import TestCase, mock
import contextlib
import json

from intelligence.search_planner.supervision import (
    read_operational_snapshot, run_safe_inbox_cycle, main,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

PID = "a" * 32


class Rows:
    def __init__(self, one=None, many=()):
        self.one, self.many = one, many
    def fetchone(self):
        return self.one
    def fetchall(self):
        return self.many


class FakeConn:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.sql = []
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False
    def transaction(self):
        return self
    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        return next(self.replies)


def conn_for(*, pending=(2, Decimal("900.9")), fixture=1, money=True,
             gen=(("running", 1), ("uncertain", 1)),
             inbox=(("needs_attention", 1),), attempts=(("uncertain", 1),)):
    return FakeConn([
        Rows(),
        Rows(many=gen),
        Rows(one=(Decimal("10.00"), Decimal("2.50"), Decimal("1.25"))
             if money else (None, None, None)),
        Rows(one=pending),
        Rows(one=(fixture,)),
        Rows(many=inbox),
        Rows(many=attempts),
        Rows(one=(2, True, Decimal("2.8"))),
    ])


class SP15ObservabilityTests(TestCase):
    def test_read_only_project_scope_and_financial_decimal_strings(self):
        c = conn_for()
        s = read_operational_snapshot(PID, connect=lambda: c)
        self.assertEqual(s["project_id"], PID)
        self.assertEqual(s["qv_pending_production_events"], 2)
        self.assertEqual(s["qv_oldest_pending_age_seconds"], 900)
        self.assertEqual(s["qv_quarantined_fixture_events"], 1)
        self.assertEqual(s["consent_totals_usd"], {
            "authorized": "10.00", "spent": "2.50", "reserved": "1.25",
        })
        self.assertEqual(len(s["warning_codes"]), 4)
        self.assertEqual(s["paid_http_requests_in_snapshot"], 0)
        self.assertEqual(s["provider_calls_in_snapshot"], 0)
        self.assertEqual(s["qv_inbox_worker_lease"]["fence_generation"], 2)
        self.assertEqual(s["qv_inbox_worker_lease"]["last_heartbeat_age_seconds"], 2)
        self.assertIn("REPEATABLE READ READ ONLY", c.sql[0][0])
        self.assertEqual(len(c.sql), 8)
        self.assertTrue(all(params == (PID,) for _, params in c.sql[1:]))
        self.assertFalse(any("UPDATE " in sql or "INSERT " in sql for sql, _ in c.sql))
        self.assertNotIn("key", json.dumps(s))

    def test_missing_metrics_remain_unknown_not_fake_zero(self):
        c = conn_for(pending=(0, None), fixture=0, money=False,
                     gen=(), inbox=(), attempts=())
        s = read_operational_snapshot(PID, connect=lambda: c)
        self.assertIsNone(s["qv_oldest_pending_age_seconds"])
        self.assertIsNone(s["consent_totals_usd"])
        self.assertEqual(s["warning_codes"], [])

    def test_fail_closed_on_corrupted_aggregation(self):
        for changes in (
            {"pending": (2, None)},
            {"pending": (-1, None)},
            {"fixture": -1},
            {"gen": (("unrecognized", 1),)},
            {"attempts": (("accepted", -1),)},
        ):
            with self.subTest(changes=changes), self.assertRaises(PipelineError):
                read_operational_snapshot(PID, connect=lambda: conn_for(**changes))
        with self.assertRaises(PipelineError):
            read_operational_snapshot("wrong", connect=lambda: conn_for())

    def test_bounded_safe_cycle_only_ingests_internal_qv_event(self):
        class Service:
            def __init__(self):
                self.calls = []
            def ingest_pending_qv_handoffs(self, project_id, *, limit):
                self.calls.append((project_id, limit))
                return [{"status": "no_work"}, {"status": "awaiting_collector"}]
        s = Service()
        result = run_safe_inbox_cycle(PID, limit=3, service=s)
        self.assertEqual(s.calls, [(PID, 3)])
        self.assertEqual(result["ingested_events"], 2)
        self.assertEqual(result["no_work_events"], 1)
        self.assertEqual(result["collector_accept_calls"], 0)
        self.assertEqual(result["provider_search_calls"], 0)

    def test_cycle_rejects_unsafe_limit_and_untrusted_backend_result(self):
        class Service:
            def ingest_pending_qv_handoffs(self, *_args, **_kwargs):
                return "fake accepted"
        for limit in (True, 0, 51, "3"):
            with self.subTest(limit=limit), self.assertRaises(PipelineError):
                run_safe_inbox_cycle(PID, limit=limit, service=Service())
        with self.assertRaises(PipelineError):
            run_safe_inbox_cycle(PID, service=Service())

    def test_foreground_cli_observation_never_drains_without_explicit_mode(self):
        with mock.patch("intelligence.search_planner.supervision.read_operational_snapshot",
                        return_value={"status": "read_only"}) as reader, mock.patch(
                        "intelligence.search_planner.supervision.run_safe_inbox_cycle"
                        ) as drain:
            out = StringIO()
            with contextlib.redirect_stdout(out):
                exit_code = main(["--project", PID])
            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(out.getvalue()), {"status": "read_only"})
            reader.assert_called_once_with(PID)
            drain.assert_not_called()

    def test_cli_failed_operations_log_no_exception_text_or_secret(self):
        with mock.patch("intelligence.search_planner.supervision.read_operational_snapshot",
                        side_effect=RuntimeError("api_key=SECRET123")):
            err = StringIO()
            with contextlib.redirect_stderr(err):
                code = main(["--project", PID])
            self.assertEqual(code, 1)
            self.assertNotIn("SECRET123", err.getvalue())
            self.assertEqual(json.loads(err.getvalue())["error_code"],
                             "operational_cycle_failed")

    def test_cli_mode_inbox_is_explicit_and_bounded(self):
        with mock.patch("intelligence.search_planner.supervision.run_safe_inbox_cycle",
                        return_value={"ingested_events": 0}) as fn:
            out = StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["--project", PID, "--mode", "inbox", "--limit", "5"])
            self.assertEqual(code, 0)
            fn.assert_called_once_with(PID, limit=5)
            self.assertEqual(json.loads(out.getvalue())["ingested_events"], 0)

    def test_operations_http_route_only_calls_readonly_project_method(self):
        from urllib.parse import urlparse
        from intelligence.search_planner.http_api import _dispatch_get
        class FakeService:
            def __init__(self):
                self.calls = []
            def operational_snapshot(self, pid):
                self.calls.append(pid)
                return {"project_id": pid, "provider_calls_in_snapshot": 0}
        svc = FakeService()
        url = urlparse("/api/projects/" + PID + "/search-planner/operations")
        result = _dispatch_get(svc, PID, "operations", url)
        self.assertEqual(result["provider_calls_in_snapshot"], 0)
        self.assertEqual(svc.calls, [PID])
        bad = urlparse("/api/projects/" + PID + "/search-planner/operations?dispatch=true")
        with self.assertRaises(PipelineError):
            _dispatch_get(svc, PID, "operations", bad)
        self.assertEqual(svc.calls, [PID])
