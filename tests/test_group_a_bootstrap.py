"""Standalone GA-MOD-00 contract and HTTP/worker no-op regression tests."""
from __future__ import annotations

import importlib
import io
from types import SimpleNamespace
import unittest

from intelligence.group_a.capabilities import (
    BOOTSTRAP_REASON, capability_snapshot, live_handoff_ready,
    require_live_handoff,
)
from intelligence.group_a.contracts import GroupAError
from intelligence.group_a import http_api, worker


class Handler:
    def __init__(self):
        self.responses = []
        self.headers = {"Content-Length": "2"}
        self.rfile = io.BytesIO(b"{}")

    def json_response(self, status, payload):
        self.responses.append((status, payload))


class BootstrapContractTests(unittest.TestCase):
    def test_package_loads_without_qualified_data_module(self):
        ga = importlib.import_module("intelligence.group_a")
        self.assertTrue(callable(ga.parse_config))
        self.assertTrue(callable(ga.work_once))

    def test_handoff_is_explicitly_unavailable(self):
        snapshot = capability_snapshot()
        self.assertEqual(snapshot["schema_version"], "group-a-capabilities/v1")
        self.assertEqual(snapshot["qdp_handoff"]["contract"], "qdp-handoff/v1")
        self.assertEqual(snapshot["qdp_handoff"]["state"], "unavailable")
        self.assertEqual(snapshot["qdp_handoff"]["reason_code"], BOOTSTRAP_REASON)
        self.assertEqual(snapshot["runtime"]["state"], "disabled")
        self.assertIn(snapshot["technical_a1"]["state"],
                      ("tools_detected", "tools_unavailable"))
        self.assertFalse(live_handoff_ready())
        with self.assertRaises(GroupAError) as ctx:
            require_live_handoff()
        self.assertEqual(ctx.exception.http_status, 503)

    def test_http_exposes_capabilities_without_database(self):
        h = Handler()
        ok = http_api.handle_get(
            h, SimpleNamespace(path="/api/projects/" + "a" * 32
                               + "/group-a/capabilities", query=""))
        self.assertTrue(ok)
        self.assertEqual(h.responses[0][0], 200)
        self.assertEqual(h.responses[0][1]["qdp_handoff"]["state"], "unavailable")

    def test_http_start_and_read_never_touch_qdp(self):
        project = "a" * 32
        h = Handler()
        self.assertTrue(http_api.handle_post(h, f"/api/projects/{project}/group-a/tasks"))
        self.assertEqual(h.responses[0][0], 503)
        self.assertEqual(h.responses[0][1]["error"]["code"], BOOTSTRAP_REASON)
        h2 = Handler()
        self.assertTrue(http_api.handle_get(
            h2, SimpleNamespace(path=f"/api/projects/{project}/group-a/tasks",
                                query="qdp_run_id=" + "0" * 36)))
        self.assertEqual(h2.responses[0][0], 503)

    def test_worker_is_disabled_until_handoff(self):
        self.assertFalse(worker.start_worker())
        self.assertEqual(worker.serve(once=True)["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
