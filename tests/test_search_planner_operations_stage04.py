"""SP-04 zero-network controller and read-only PostgreSQL operations tests.

No live transport, no paid call; injected executor models the server-owned
single step while checking that consent never implicitly permits execution.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
import unittest

from intelligence.search_planner import (
    SearchPlannerService, GenerationExecutionController,
)
from intelligence.search_planner.operations import (
    execution_snapshot, unsubmitted_stale_attempt_ids,
)
from intelligence.search_planner.ai_query.transport import OpenRouterQueryTransport
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

PID = "a" * 32
OTHER = "b" * 32
GEN = "55555555-5555-4555-8555-555555555555"
ATTEMPT = "66666666-6666-4666-8666-666666666666"


class Result:
    def __init__(self, one=None, many=()):
        self.one, self.many = one, many
    def fetchone(self):
        return self.one
    def fetchall(self):
        return self.many


class FakeConn:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.queries = []
    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        return next(self.replies)
    def transaction(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


def dummy_trans():
    return OpenRouterQueryTransport(sender=lambda *_: {"not": "called"})


def fixture(**updates):
    log = []
    cfg = {
        "owner_action_guard": None,
        "transport_factory": dummy_trans,
        "paid_executor": lambda pid, gid, **kwargs: log.append(
            ("execute", pid, gid, kwargs["explicit_paid_execution"],
             type(kwargs["transport"]).__name__)) or {"generation_id": gid, "status": "completed"},
        "preflight_reader": lambda pid, gid: log.append(("preflight", pid, gid)) or {
            "plan": SimpleNamespace(generation_id=gid, batch_id=ATTEMPT),
            "exact_request_sha256": "a"*64,
            "quoted_reserve_usd": "1.25000000",
            "calculated_reserve_usd": "0.25000000",
            "payload": {"secret_prompt": "do not return in preview"},
        },
        "status_reader": lambda pid, gid: log.append(("status", pid, gid)) or {
            "project_id": pid, "generation_id": gid, "status": "queued"},
        "stale_dispatch_recovery": lambda pid, gid, **kwargs: log.append(
            ("stale", kwargs["older_than_seconds"])) or {
                "generation_id": gid, "marked_uncertain": 1, "paid_retries": 0},
        "stale_reservation_reader": lambda pid, gid, **kwargs: log.append(
            ("list", kwargs["older_than_seconds"], kwargs.get("limit"))) or [ATTEMPT],
        "unsubmitted_release": lambda pid, aid: log.append(
            ("release", pid, aid)) or {"attempt_id": aid, "status": "abandoned_before_dispatch"},
        "cancel_action": lambda pid, gid: log.append(
            ("cancel", pid, gid)) or {"generation_id": gid, "status": "cancelled"},
    }
    cfg.update(updates)
    return GenerationExecutionController(**cfg), log


class SP04ExecutionControllerTests(unittest.TestCase):
    def test_status_is_read_only(self):
        ctl, calls = fixture()
        self.assertEqual(ctl.status(PID, GEN)["project_id"], PID)
        self.assertEqual(calls, [("status", PID, GEN)])

    def test_preflight_does_not_leak_payload_or_execute(self):
        ctl, calls = fixture()
        p = ctl.preflight(PID, GEN)
        self.assertTrue(p["eligible"])
        self.assertEqual(p["paid_calls"], 0)
        self.assertNotIn("payload", p)
        self.assertNotIn("secret_prompt", str(p))
        self.assertEqual(calls, [("preflight", PID, GEN)])

    def test_none_preflight_never_launches_worker(self):
        ctl, calls = fixture(preflight_reader=lambda *_: None)
        p = ctl.preflight(PID, GEN)
        self.assertFalse(p["eligible"])
        self.assertEqual(p["reason"], "no_eligible_batch")
        self.assertEqual(calls, [])

    def test_wrong_generation_in_preflight_blocks(self):
        ctl, calls = fixture(preflight_reader=lambda *_: {
            "plan": SimpleNamespace(generation_id=OTHER, batch_id=ATTEMPT),
            "exact_request_sha256": "a"*64,
        })
        with self.assertRaises(PipelineError):
            ctl.preflight(PID, GEN)
        self.assertEqual(calls, [])

    def test_denied_paid_execution_never_creates_transport(self):
        calls = []
        def create():
            calls.append("transport")
            return dummy_trans()
        ctl, _ = fixture(transport_factory=create)
        with self.assertRaisesRegex(PipelineError, "Verified local owner"):
            ctl.execute_one(PID, GEN)
        self.assertEqual(calls, [])

    def test_paid_step_requires_exact_true_owner_result(self):
        for truthy in (False, None, 1, "yes", {}, []):
            ctl, calls = fixture(owner_action_guard=lambda *_: truthy)
            with self.subTest(truthy=truthy), self.assertRaises(PipelineError):
                ctl.execute_one(PID, GEN)
            self.assertEqual(calls, [])

    def test_one_authorized_step_and_no_automatic_drain(self):
        calls = []
        def gate(action, project, obj):
            calls.append(("gate", action, project, obj))
            return action == "search_planner.generation.execute_paid_batch"
        ctl, work = fixture(owner_action_guard=gate)
        got = ctl.execute_one(PID, GEN)
        self.assertEqual(got["status"], "completed")
        self.assertEqual(len(work), 1)
        self.assertEqual(work[0], ("execute", PID, GEN, True, "OpenRouterQueryTransport"))
        self.assertEqual(calls, [
            ("gate", "search_planner.generation.execute_paid_batch", PID, GEN)
        ])

    def test_incorrect_transport_fails_before_paid_executor(self):
        ctl, calls = fixture(owner_action_guard=lambda *_: True,
                             transport_factory=lambda: object())
        with self.assertRaises(PipelineError):
            ctl.execute_one(PID, GEN)
        self.assertEqual(calls, [])

    def test_stale_dispatches_are_uncertain_and_never_replayed(self):
        ctl, calls = fixture(owner_action_guard=lambda *_: True)
        result = ctl.recover_stale_dispatches(PID, GEN, older_than_seconds=3600)
        self.assertEqual(result["marked_uncertain"], 1)
        self.assertEqual(result["paid_retries"], 0)
        self.assertEqual(calls, [("stale", 3600)])
        with self.assertRaises(PipelineError):
            ctl.recover_stale_dispatches(PID, GEN, older_than_seconds=True)
        self.assertEqual(len(calls), 1)

    def test_uncertain_recovery_rejects_unsafe_retry_counter(self):
        ctl, calls = fixture(
            owner_action_guard=lambda *_: True,
            stale_dispatch_recovery=lambda *a, **k: {"paid_retries": 1},
        )
        with self.assertRaises(PipelineError):
            ctl.recover_stale_dispatches(PID, GEN)

    def test_manual_release_guard_cannot_be_implicit(self):
        ctl, calls = fixture()
        with self.assertRaises(PipelineError):
            ctl.release_unsubmitted(PID, GEN, ATTEMPT)
        self.assertEqual(calls, [])

    def test_stale_reservation_listing_is_non_mutating(self):
        ctl, calls = fixture()
        self.assertEqual(ctl.list_stale_reservations(PID, GEN), [ATTEMPT])
        self.assertEqual(calls, [("list", 900, None)])

    def test_manual_release_checks_identity_and_stale_membership(self):
        ctl, calls = fixture(owner_action_guard=lambda *_: True)
        result = ctl.release_unsubmitted(PID, GEN, ATTEMPT)
        self.assertEqual(result["attempt_id"], ATTEMPT)
        self.assertEqual(calls, [
            ("list", 900, 100), ("release", PID, ATTEMPT)
        ])
        ctl, calls = fixture(
            owner_action_guard=lambda *_: True,
            stale_reservation_reader=lambda *a, **k: [])
        with self.assertRaises(PipelineError):
            ctl.release_unsubmitted(PID, GEN, ATTEMPT)
        self.assertEqual(calls, [])

    def test_stale_attempt_must_not_be_refunded_after_dispatch(self):
        def unsafe_release(*_):
            raise PipelineError("Cannot refund a possibly-dispatched attempt", 409)
        ctl, calls = fixture(owner_action_guard=lambda *_: True,
                             unsubmitted_release=unsafe_release)
        with self.assertRaisesRegex(PipelineError, "possibly-dispatched"):
            ctl.release_unsubmitted(PID, GEN, ATTEMPT)
        self.assertEqual(calls, [("list", 900, 100)])

    def test_cancel_is_separately_owner_gated(self):
        def permit(action, *_):
            return action.endswith(".cancel")
        ctl, calls = fixture(owner_action_guard=permit)
        with self.assertRaises(PipelineError):
            ctl.execute_one(PID, GEN)
        self.assertEqual(ctl.cancel(PID, GEN)["status"], "cancelled")
        self.assertEqual(calls, [("cancel", PID, GEN)])

    def test_bad_ids_block_before_any_backend(self):
        ctl, calls = fixture(owner_action_guard=lambda *_: True)
        for method, args in (
            (ctl.execute_one, ("bad-project", GEN)),
            (ctl.status, (PID, "bad-uuid")),
            (ctl.cancel, (PID, "not-a-uuid")),
            (ctl.release_unsubmitted, (PID, GEN, "invalid")),
        ):
            with self.subTest(method=method), self.assertRaises(PipelineError):
                method(*args)
        self.assertEqual(calls, [])

    def test_actual_status_sql_scoped_readonly_aggregation(self):
        class Expiration:
            def isoformat(self):
                return "2030-01-01T00:00:00+00:00"
        replies = [
            Result(),  # read-only transaction
            Result(one=("running", False, object(), object(),
                        Decimal("2.0"), Decimal("0.3"), Decimal("0.5"),
                        None, Expiration())),
            Result(many=[("completed", 2), ("pending", 1)]),
            Result(many=[("completed", 2), ("dispatch_intent", 1)]),
        ]
        conn = FakeConn(replies)
        got = execution_snapshot(PID, GEN, connect=lambda: conn)
        self.assertEqual(got["generation_status"], "running")
        self.assertEqual(got["budget"]["available_usd"], "1.2")
        self.assertEqual(got["batch_states"], {"completed": 2, "pending": 1})
        self.assertEqual(got["attempt_states"], {"completed": 2, "dispatch_intent": 1})
        self.assertEqual(got["provider_requests_in_this_read"], 0)
        self.assertIn("REPEATABLE READ READ ONLY", conn.queries[0][0])
        self.assertEqual(conn.queries[1][1], (PID, GEN))
        self.assertEqual(conn.queries[2][1], (GEN,))

    def test_actual_status_handles_no_consent(self):
        conn = FakeConn([
            Result(), Result(one=("awaiting_authorization", False, None, None,
                                  None, None, None, None, None)),
            Result(many=[("pending", 3)]), Result(many=[]),
        ])
        result = execution_snapshot(PID, GEN, connect=lambda: conn)
        self.assertIsNone(result["budget"])
        self.assertFalse(result["has_consent"])

    def test_real_stale_detection_filters_reserved_without_intent(self):
        conn = FakeConn([Result(many=[(ATTEMPT,)])])
        data = unsubmitted_stale_attempt_ids(
            PID, GEN, older_than_seconds=1200, limit=25, connect=lambda: conn,
        )
        self.assertEqual(data, [ATTEMPT])
        sql, params = conn.queries[0]
        self.assertEqual(params, (PID, GEN, 1200, 25))
        self.assertIn("a.status='reserved'", sql)
        self.assertIn("a.dispatch_intent_at IS NULL", sql)
        self.assertIn("a.started_at < NOW()", sql)
        self.assertNotIn("UPDATE ", sql)

    def test_public_service_exposes_status_but_not_implicit_paid_step(self):
        ctl, calls = fixture()
        svc = SearchPlannerService(execution_controller=ctl)
        self.assertEqual(svc.generation_execution_status(PID, GEN)["status"], "queued")
        with self.assertRaises(PipelineError):
            svc.execute_generation_batch(PID, GEN)
        self.assertEqual(calls, [("status", PID, GEN)])
        with self.assertRaises(PipelineError):
            SearchPlannerService(execution_controller={})


if __name__ == "__main__":
    unittest.main()
