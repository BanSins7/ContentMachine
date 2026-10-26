"""GA-MOD-03: PostgreSQL adapter stays importable and deny-by-default."""
from __future__ import annotations

import unittest

from intelligence.group_a.contracts import GroupAError
from intelligence.group_a.contracts_v1 import ContractViolation
from intelligence.group_a.storage_pg import (
    STORAGE_SCHEMA, TABLES, VIEWS, PostgreSQLGroupAStore,
    PostgreSQLGroupATaskQueue, migration_snapshot,
)


class FakeConnection:
    def __init__(self, missing=()):
        self.missing = frozenset(missing)
        self.lookups = []

    def execute(self, sql, args):
        if sql != "SELECT to_regclass(%s)" or len(args) != 1:
            raise AssertionError("Unexpected SQL in schema inspection")
        self.lookups.append(args[0])
        return self

    def fetchone(self):
        return (None if self.lookups[-1] in self.missing else self.lookups[-1],)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def partial_result():
    status = {
        "status": "not_run", "evidence_count": None,
        "verified_absence": False, "reason_code": "not_run",
    }
    return {
        "schema_version": "group-a-to-b/v1",
        "project_id": "a" * 32,
        "qdp_run_id": "00000000-0000-0000-0000-000000000000",
        "video_id": 1, "qdp_input_sha256": "b" * 64,
        "manifest_sha256": "c" * 64, "content_kind": "video",
        "analysis_state": "partial", "video_a_analysis_id": 1,
        "carousel_a_analysis_id": None,
        "method_version": "group-a-local-video-v1",
        "streams": {name: dict(status) for name in
                    ("technical", "speech", "screen_text", "audio", "subtitles")},
        "evidence": [],
    }


class GA03StoragePorts(unittest.TestCase):
    def test_diagnostics_readonly_and_schema_is_explicit(self):
        conn = FakeConnection()
        info = migration_snapshot(conn)
        self.assertEqual(info["schema_version"], STORAGE_SCHEMA)
        self.assertEqual(info["state"], "installed")
        self.assertEqual(conn.lookups, list(TABLES + VIEWS))
        self.assertFalse(info["live_handoff_ready"])
        self.assertFalse(info["legacy_qdp_required"])
        absent = migration_snapshot(FakeConnection(missing=("ga_inbox","ga_steps")))
        self.assertEqual(absent["state"], "incomplete")
        self.assertEqual(absent["missing_relations"], ["ga_inbox","ga_steps"])

    def test_writer_queue_not_claim_live_capability(self):
        calls = []
        def open_db():
            calls.append(True)
            raise AssertionError("Cannot connect without handoff")
        store = PostgreSQLGroupAStore(open_db)
        queue = PostgreSQLGroupATaskQueue(open_db)
        self.assertEqual(store.capability().state, "unavailable")
        self.assertEqual(queue.capability().state, "unavailable")
        with self.assertRaises(GroupAError) as ctx:
            store.write_immutable(partial_result())
        self.assertEqual(ctx.exception.http_status, 503)
        with self.assertRaises(GroupAError):
            store.read_result("a"*32,
                              "00000000-0000-0000-0000-000000000000", 1)
        with self.assertRaises(GroupAError):
            queue.enqueue_once({"task":"forged"})
        self.assertEqual(calls, [])

    def test_invalid_writer_shape_rejected_before_gate(self):
        store = PostgreSQLGroupAStore(lambda: None)
        with self.assertRaises(ContractViolation):
            store.write_immutable({"schema_version":"group-a-to-b/v1"})
        with self.assertRaises(ContractViolation):
            PostgreSQLGroupATaskQueue(lambda: None).enqueue_once("not-an-object")
        with self.assertRaises(TypeError):
            PostgreSQLGroupAStore(None)
        with self.assertRaises(ValueError):
            migration_snapshot(None)


if __name__ == "__main__":
    unittest.main()
