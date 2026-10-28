"""SP-15B fenced durable lease and fail-closed supervisor contracts (offline)."""
from __future__ import annotations

from io import StringIO
from pathlib import Path
import contextlib
import json
import unittest
from unittest import mock

from intelligence.search_planner.lease_ops import (
    acquire_inbox_lease, heartbeat_inbox_lease, release_inbox_lease,
    run_fenced_inbox_cycle,
)
from intelligence.search_planner.supervision import main
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

PID = "a" * 32
OID = "11111111-1111-4111-8111-111111111111"


class Answer:
    def __init__(self, one=None):
        self.one = one
    def fetchone(self):
        return self.one


class DB:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.statements = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def transaction(self):
        return self
    def execute(self, sql, args=None):
        self.statements.append((sql, args))
        return Answer(next(self.rows))


class SafeService:
    def __init__(self):
        self.calls = []
    def ingest_pending_qv_handoffs(self, pid, *, limit):
        self.calls.append((pid, limit))
        return [{"status": "no_work"}]


class SP15LeaseTests(unittest.TestCase):
    def test_lease_migration_is_additive_and_fenced(self):
        path = (Path(__file__).resolve().parents[1] /
                "intelligence/migrations/057_sp_supervisor_leases.sql")
        sql = path.read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE sp_supervisor_leases", sql)
        self.assertIn("PRIMARY KEY(project_id,worker_kind)", sql)
        self.assertIn("fence_generation BIGINT NOT NULL", sql)
        self.assertNotIn("DROP TABLE", sql.upper())
        self.assertNotIn("ALTER TABLE sqg_", sql)

    def test_atomic_acquire_fenced_upsert_and_project_scope(self):
        db = DB([(1,)])
        lease = acquire_inbox_lease(PID, OID, connect=lambda: db)
        self.assertEqual(lease["fence_generation"], 1)
        sql, params = db.statements[0]
        self.assertIn("ON CONFLICT (project_id,worker_kind)", sql)
        self.assertIn("lease_until <= now()", sql)
        self.assertIn("fence_generation+1", sql)
        self.assertEqual(params, (PID, OID, 120))

    def test_contention_does_not_take_over_active_lease(self):
        db = DB([None])
        self.assertIsNone(acquire_inbox_lease(PID, OID, connect=lambda: db))
        self.assertEqual(len(db.statements), 1)

    def test_fenced_heartbeat_and_stale_release(self):
        db = DB([(7,)])
        self.assertTrue(heartbeat_inbox_lease(PID, OID, 7, connect=lambda: db))
        sql, args = db.statements[0]
        self.assertIn("fence_generation=%s", sql)
        self.assertIn("lease_until>now()", sql)
        self.assertEqual(args, (120, PID, OID, 7))
        with self.assertRaisesRegex(PipelineError, "Lost supervisor lease"):
            heartbeat_inbox_lease(PID, OID, 8, connect=lambda: DB([None]))
        self.assertFalse(release_inbox_lease(PID, OID, 8, connect=lambda: DB([None])))
        self.assertTrue(release_inbox_lease(PID, OID, 7, connect=lambda: DB([(7,)])))

    def test_invalid_owner_fence_ttl_do_not_hit_db(self):
        factory = mock.Mock()
        for fn, args, kwargs in (
            (acquire_inbox_lease, (PID, "forged"), {}),
            (acquire_inbox_lease, (PID, OID), {"lease_seconds": True}),
            (heartbeat_inbox_lease, (PID, OID, 0), {}),
            (release_inbox_lease, (PID, OID, "1"), {}),
        ):
            with self.subTest(fn=fn), self.assertRaises(PipelineError):
                fn(*args, connect=factory, **kwargs)
        factory.assert_not_called()

    def test_busy_cycle_never_touches_qv_or_paid_ports(self):
        svc = SafeService()
        called = []
        result = run_fenced_inbox_cycle(
            PID, service=svc, acquirer=lambda *args: None,
            heartbeater=lambda *args: called.append("heartbeat"),
            releaser=lambda *args: called.append("release"),
        )
        self.assertEqual(result["lease_status"], "busy")
        self.assertEqual(result["provider_search_calls"], 0)
        self.assertEqual(svc.calls, [])
        self.assertEqual(called, [])

    def test_successful_explicit_cycle_renews_and_releases_correct_fence(self):
        svc = SafeService()
        steps = []
        def acquirer(pid, oid):
            steps.append(("acquire", pid, oid))
            return {"project_id": pid, "owner_id": oid, "fence_generation": 7}
        def heartbeat(pid, oid, generation):
            steps.append(("heartbeat", generation))
            return True
        def release(pid, oid, generation):
            steps.append(("release", generation))
            return True
        result = run_fenced_inbox_cycle(
            PID, limit=2, service=svc, acquirer=acquirer,
            heartbeater=heartbeat, releaser=release,
        )
        self.assertEqual(result["lease_status"], "released")
        self.assertEqual(result["ingested_events"], 1)
        self.assertEqual(result["no_work_events"], 1)
        self.assertEqual(result["paid_http_requests"], 0)
        self.assertEqual(svc.calls, [(PID, 2)])
        self.assertEqual([x[0] for x in steps],
                         ["acquire", "heartbeat", "heartbeat", "release"])
        self.assertEqual([x[1] for x in steps[1:]], [7, 7, 7])

    def test_lost_heartbeat_fails_closed_and_releases(self):
        svc = SafeService()
        released = []
        with self.assertRaises(PipelineError):
            run_fenced_inbox_cycle(
                PID, service=svc,
                acquirer=lambda pid, oid: {
                    "project_id": pid, "owner_id": oid, "fence_generation": 4,
                },
                heartbeater=lambda *args: False,
                releaser=lambda *args: released.append(args),
            )
        self.assertEqual(svc.calls, [])
        self.assertEqual(len(released), 1)

    def test_cli_leased_inbox_is_opt_in_and_one_cycle(self):
        with mock.patch("intelligence.search_planner.lease_ops.run_fenced_inbox_cycle",
                        return_value={"lease_status": "busy"}) as fn:
            stdout = StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--project", PID, "--mode", "leased-inbox", "--limit", "6"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["lease_status"], "busy")
            fn.assert_called_once_with(PID, limit=6)


if __name__ == "__main__":
    unittest.main()
