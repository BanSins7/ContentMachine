"""SP-15E: read-only Collector policy/quota status; never source readiness."""
from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest import mock

from intelligence.search_planner.capabilities.collector_db_port import (
    CollectorPolicySnapshotPort, SCHEMA, _safe_declarations
)
from intelligence.search_planner.capabilities.service import match_capabilities
from intelligence.search_planner.service import SearchPlannerService
from intelligence.search_planner.http_api import _dispatch_get
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

PID = "a" * 32
OTHER = "b" * 32
NOW = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)


def declaration(pid=PID, **updates):
    source = {
        "source_id": "dtk_tiktok",
        "platform": "tiktok",
        "integration_state": "local_rest_connector_present_manual_only",
        "capabilities": [{"mode": "keyword", "operation": "search_posts"}],
        "operationally_verified": True,  # Spoofed from old registry
        "source_policy_verified": True,
        "pricing_status": "verified_free",
    }
    source.update(updates)
    return {
        "schema_version": "collector-source-declarations/v1",
        "project_id": pid,
        "registry_version": "collector-source-registry/v2-dtk-installed",
        "sources": [source],
    }


def ok_policy():
    return (
        "enabled", True, True, 365, 3,
        {"keyword_search": True},
        {"can_search": True, "can_store_metadata": True},
        {"retention_days": 30},
        NOW-timedelta(hours=1),
    )


class Conn:
    def __init__(self, policy=None, quota=None, *, fail=False):
        self.policy = ok_policy() if policy is None else policy
        self.quota = (100, 10, 5) if quota is None else quota
        self.fail = fail
        self.queries = []
        self.cursor = None
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def transaction(self): return nullcontext()
    def execute(self, sql, args=None):
        self.queries.append((sql, args))
        if self.fail: raise RuntimeError("db host and db-password MUST_NOT_LEAK")
        self.cursor = sql
        return self
    def fetchone(self):
        return self.policy if "FROM sources" in self.cursor else self.quota


class CollectorPreflightTests(unittest.TestCase):
    def test_absent_collector_does_not_open_database(self):
        port = CollectorPolicySnapshotPort(
            connect=lambda: self.fail("DB should not be read"),
            clock=lambda: NOW,
        )
        r = port.inspection(PID)
        self.assertEqual(r["status"], "declarations_unavailable")
        self.assertEqual(r["sources"], [])
        self.assertFalse(r["external_dispatch_authorized"])
        self.assertEqual(port.snapshot(PID)["sources"], [])
        self.assertEqual(r["paid_provider_calls"], 0)

    def test_sql_reads_policy_and_quota_without_any_provider_or_write(self):
        conn = Conn()
        port = CollectorPolicySnapshotPort(
            declaration_supplier=declaration,
            connect=lambda: conn, clock=lambda: NOW,
        )
        r = port.inspection(PID)
        self.assertEqual(r["schema_version"], SCHEMA)
        self.assertEqual(r["project_id"], PID)
        self.assertEqual(r["status"], "policy_preflight_only")
        self.assertEqual(r["sources"][0]["quota_available_requests"], 85)
        self.assertTrue(r["sources"][0]["global_policy_quota_preflight_passed"])
        self.assertFalse(r["sources"][0]["operationally_ready"])
        self.assertFalse(r["external_dispatch_authorized"])
        self.assertIn("credentials_not_verified", r["sources"][0]["block_reasons"])
        self.assertIn("project_owner_source_consent_not_verified", r["sources"][0]["block_reasons"])
        self.assertEqual(len(conn.queries), 3)
        self.assertIn("REPEATABLE READ READ ONLY", conn.queries[0][0])
        self.assertTrue(all(q[0].strip().startswith(("SET", "SELECT")) for q in conn.queries))
        self.assertEqual(conn.queries[1][1], ("dtk_tiktok",))
        self.assertEqual(conn.queries[2][1], ("dtk_tiktok",))

    def test_old_collector_verified_flags_never_upgrade_to_executable_source(self):
        port = CollectorPolicySnapshotPort(
            declaration_supplier=declaration,
            connect=lambda: Conn(), clock=lambda: NOW,
        )
        catalog = port.snapshot(PID)
        source = catalog["sources"][0]
        self.assertFalse(source["enabled"])
        self.assertFalse(source["operationally_verified"])
        self.assertFalse(source["source_policy_verified"])
        self.assertFalse(source["credentials_verified"])
        self.assertEqual(source["pricing_status"], "unknown")
        self.assertIsNone(source["cost_usd_per_request"])
        q = {"query_mode": "keyword", "text": "running",
             "language": "en", "market": "US"}
        match = match_capabilities(catalog, q, project_id=PID, now=NOW)
        self.assertEqual(match["compatible_source_ids"], [])
        self.assertFalse(match["external_dispatch_authorized"])

    def test_missing_db_schema_fails_closed_without_leaking_database_errors(self):
        port = CollectorPolicySnapshotPort(
            declaration_supplier=declaration,
            connect=lambda: Conn(fail=True), clock=lambda: NOW,
        )
        r = port.inspection(PID)
        self.assertEqual(r["status"], "policy_preflight_unavailable")
        self.assertIsNone(r["sources"][0]["quota_available_requests"])
        self.assertFalse(r["sources"][0]["global_policy_quota_preflight_passed"])
        self.assertNotIn("MUST_NOT_LEAK", repr(r))
        self.assertIn("policy_or_quota_database_unavailable",
                      r["sources"][0]["block_reasons"])

    def test_missing_source_never_invents_quota(self):
        port = CollectorPolicySnapshotPort(
            declaration_supplier=declaration,
            connect=lambda: Conn(policy=()), clock=lambda: NOW,
        )
        r = port.inspection(PID)
        self.assertEqual(r["status"], "policy_preflight_unavailable")
        self.assertIsNone(r["sources"][0]["quota_available_requests"])

    def test_missing_policy_is_detected_even_if_quota_exists(self):
        row = list(ok_policy())
        row[4] = None
        row[8] = None
        port = CollectorPolicySnapshotPort(
            declaration_supplier=declaration,
            connect=lambda: Conn(policy=tuple(row)), clock=lambda: NOW,
        )
        r = port.inspection(PID)
        self.assertIn("source_policy_not_reviewed", r["sources"][0]["block_reasons"])
        self.assertFalse(r["sources"][0]["global_policy_quota_preflight_passed"])

    def test_bad_quota_and_expired_policy_fail_closed(self):
        for bad_quota in ((100, 60, 60), (True, 1, 1), (10, -1, 0)):
            with self.subTest(quota=bad_quota):
                port = CollectorPolicySnapshotPort(
                    declaration_supplier=declaration,
                    connect=lambda: Conn(quota=bad_quota), clock=lambda: NOW,
                )
                r = port.inspection(PID)
                self.assertEqual(r["status"], "policy_preflight_unavailable")
        row = list(ok_policy())
        row[8] = NOW - timedelta(days=40)
        port = CollectorPolicySnapshotPort(
            declaration_supplier=declaration,
            connect=lambda: Conn(policy=tuple(row)), clock=lambda: NOW,
        )
        r = port.inspection(PID)
        self.assertIn("source_policy_review_expired", r["sources"][0]["block_reasons"])

    def test_cross_project_declaration_and_duplicate_ids_rejected_before_db(self):
        port = CollectorPolicySnapshotPort(
            declaration_supplier=lambda pid: declaration(OTHER),
            connect=lambda: self.fail("no SQL"), clock=lambda: NOW,
        )
        with self.assertRaises(PipelineError):
            port.inspection(PID)
        bad = declaration()
        bad["sources"].append(deepcopy(bad["sources"][0]))
        with self.assertRaises(PipelineError):
            _safe_declarations(bad, PID, observed_at=NOW)

    def test_service_default_preserves_no_source_and_works_without_collector(self):
        service = SearchPlannerService()
        r = service.collector_policy_preflight(PID)
        self.assertEqual(r["sources"], [])
        self.assertEqual(service.source_capabilities(PID)["sources"], [])

    def test_installed_bridge_is_visible_but_still_not_execution_ready(self):
        port = CollectorPolicySnapshotPort(
            declaration_supplier=declaration,
            connect=lambda: Conn(), clock=lambda: NOW,
        )
        service = SearchPlannerService(collector_policy_port=port)
        self.assertEqual(service.collector_policy_preflight(PID)["sources"][0]
                         ["quota_available_requests"], 85)
        self.assertFalse(service.source_capabilities(PID)["sources"][0]
                         ["operationally_verified"])
        self.assertFalse(service.status().external_search_ready)

    def test_get_endpoint_rejects_query_parameters_and_calls_one_read(self):
        class API:
            calls = 0
            def collector_policy_preflight(self, pid):
                self.calls += 1
                self.last_pid = pid
                return {"schema_version": SCHEMA, "project_id": pid}
        svc = API()
        self.assertEqual(_dispatch_get(svc, PID, "collector-readiness", {}),
                         {"schema_version": SCHEMA, "project_id": PID})
        self.assertEqual(svc.last_pid, PID)
        self.assertEqual(svc.calls, 1)
        with self.assertRaises(PipelineError):
            _dispatch_get(svc, PID, "collector-readiness", {"arbitrary": ["1"]})
        self.assertEqual(svc.calls, 1)


if __name__ == "__main__":
    unittest.main()
