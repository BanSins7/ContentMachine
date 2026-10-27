"""SP integration preflight against peer branch contracts; offline only.

No Collector import, paid/model calls or database writes. Structural checks
cannot be used to claim an authenticated Collector acceptance.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
import unittest
from unittest.mock import patch

from intelligence.search_planner.integration import (
    read_integration_readiness, assess_collector_v1_projection,
)
from intelligence.search_planner.integration.readiness import REQUIRED_TABLES
from intelligence.search_planner.integration.collector_v1 import _collector_canonical_chunk
from intelligence.search_planner.handoff import project_verified_query_handoff
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from test_search_planner_stage10_task_planner import PID, QV
from test_search_planner_stage11_handoff import synthetic_trusted_source


class _Row:
    def __init__(self, value):
        self.value = value
    def fetchone(self):
        return self.value


class _ReadOnlyConn:
    def __init__(self, installed=(), project_exists=True):
        self.installed = set(installed)
        self.project_exists = project_exists
        self.calls = []
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def transaction(self):
        return nullcontext()
    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith("SELECT 1 FROM project_registry"):
            return _Row((1,) if self.project_exists else None)
        if sql.startswith("SELECT to_regclass(%s)::text"):
            table = params[0].removeprefix("public.")
            return _Row((params[0] if table in self.installed else None,))
        if sql.startswith("SET TRANSACTION"):
            return _Row(None)
        raise AssertionError("Unexpected SQL in integration diagnostic: " + sql)


class PeerIntegrationTests(unittest.TestCase):
    def test_absent_optional_branches_fail_closed_without_writes(self):
        installed = set(REQUIRED_TABLES["rtb_deep_v1"]) | set(REQUIRED_TABLES["sqg_qv"])
        conn = _ReadOnlyConn(installed)
        report = read_integration_readiness(PID, connect=lambda: conn)
        self.assertEqual(report["status"], "integration_blocked")
        self.assertEqual(report["systems"]["rtb_deep_v1"]["schema_state"],
                         "schema_detected_only")
        self.assertEqual(report["systems"]["rtb_deep_v2"]["schema_state"], "absent")
        self.assertEqual(report["systems"]["collector_planning"]["schema_state"],
                         "absent")
        self.assertFalse(report["collector_operationally_ready"])
        self.assertFalse(report["source_dispatch_authorized"])
        self.assertFalse(report["rtb_v2_context_enabled"])
        self.assertEqual((report["paid_provider_calls"], report["external_source_calls"]), (0, 0))
        self.assertTrue(all(
            sql.startswith("SELECT ") or sql.startswith("SET TRANSACTION")
            for sql, _ in conn.calls
        ))

    def test_all_peer_tables_present_still_do_not_prove_external_acceptance(self):
        installed = {x for rows in REQUIRED_TABLES.values() for x in rows}
        conn = _ReadOnlyConn(installed)
        report = read_integration_readiness(PID, connect=lambda: conn)
        self.assertTrue(all(v["schema_state"] == "schema_detected_only"
                            for v in report["systems"].values()))
        self.assertIn("collector_durable_receipt_readback_not_attested",
                      report["blocking_reasons"])
        self.assertTrue(report["schema_probe_is_not_end_to_end_certification"])
        self.assertFalse(report["independent_collector_receipt_verified"])

    def test_partial_peer_schema_is_not_accepted(self):
        conn = _ReadOnlyConn({"collector_query_sets", "sp_handoff_events"})
        report = read_integration_readiness(PID, connect=lambda: conn)
        self.assertEqual(report["systems"]["collector_planning"]["schema_state"], "partial")
        self.assertIn("missing_schema:collector_planning", report["blocking_reasons"])

    def test_invalid_project_and_missing_project_fail_closed(self):
        with self.assertRaises(PipelineError):
            read_integration_readiness("invalid", connect=lambda: _ReadOnlyConn())
        conn = _ReadOnlyConn(project_exists=False)
        with self.assertRaises(PipelineError) as err:
            read_integration_readiness(PID, connect=lambda: conn)
        self.assertEqual(err.exception.http_status, 404)

    def test_117_qv_rows_have_three_collector_compatible_chunks(self):
        source = synthetic_trusted_source(117)
        projected = project_verified_query_handoff(source)
        before = deepcopy(projected)
        report = assess_collector_v1_projection(projected)
        self.assertEqual(projected, before)
        self.assertEqual(report["selected_query_count"], 117)
        self.assertEqual([p["selected_query_count"] for p in report["chunks"]], [50, 50, 17])
        self.assertTrue(report["all_chunks_structurally_parseable"])
        self.assertFalse(report["trusted_collector_publisher_installed"])
        self.assertFalse(report["collector_accepted"])
        self.assertEqual(report["provider_calls"], 0)
        self.assertTrue(all(p["same_query_ids"] for p in report["chunks"]))

    def test_collector_canonical_query_order_differs_from_sp_scientific_rank(self):
        projected = project_verified_query_handoff(synthetic_trusted_source(3))
        chunk = projected["chunks"][0]
        # Same queries, deliberately different order. Rehash every actual
        # SP envelope field; report remains structural-only, NOT a trust proof.
        for key in ("query_ids", "source_provenance"):
            chunk[key][0], chunk[key][1] = chunk[key][1], chunk[key][0]
        queries = chunk["collector_query_set"]["queries"]
        queries[0], queries[1] = queries[1], queries[0]
        projected["selected_query_ids"] = list(chunk["query_ids"])
        chunk["collector_query_set_sha256"] = sha256(chunk["collector_query_set"])
        chunk["chunk_sha256"] = sha256({k:v for k,v in chunk.items() if k!="chunk_sha256"})
        projected["projection_sha256"] = sha256({
            k:v for k,v in projected.items() if k!="projection_sha256"
        })
        report = assess_collector_v1_projection(projected)
        self.assertTrue(report["requires_collector_query_sort_normalization"])
        self.assertFalse(report["chunks"][0]["byte_fingerprint_equal"])
        self.assertNotEqual(report["chunks"][0]["sp_query_set_sha256"],
                            report["chunks"][0]["collector_v1_canonical_sha256"])
        self.assertFalse(report["external_dispatch_authorized"])

    def test_collector_parser_rejects_invalid_ids_modes_text_and_language(self):
        chunk = project_verified_query_handoff(synthetic_trusted_source(2))["chunks"][0]
        invalid = (
            ("query_id", "not-a-uuid"),
            ("hypothesis_id", "invalid space"),
            ("direction_id", chunk["collector_query_set"]["queries"][0]["hypothesis_id"]),
            ("mode", "semantic_unsafe"),
            ("language", "ru_XX"),
            ("text", "bad\ntext"),
        )
        for key, val in invalid:
            with self.subTest(key=key):
                copied = deepcopy(chunk)
                copied["collector_query_set"]["queries"][0][key] = val
                with self.assertRaises(PipelineError):
                    _collector_canonical_chunk(copied)

    def test_no_work_is_not_published_to_collector(self):
        zero = project_verified_query_handoff(synthetic_trusted_source(0))
        result = assess_collector_v1_projection(zero)
        self.assertEqual(result["chunk_count"], 0)
        self.assertEqual(result["chunks"], [])
        self.assertFalse(result["collector_accepted"])

    def test_facade_and_http_are_read_only_and_owner_project_scoped(self):
        from intelligence.search_planner import SearchPlannerService
        from intelligence.search_planner.http_api import _dispatch_get
        from urllib.parse import urlparse
        from test_search_planner_http_api import RouterFactory, get
        service = SearchPlannerService()
        with patch("intelligence.search_planner.service.read_integration_readiness",
                   return_value={"status":"integration_blocked"}) as reader:
            self.assertEqual(service.integration_readiness(PID)["status"], "integration_blocked")
            reader.assert_called_once_with(PID)
        factory = RouterFactory()
        _, handler = get("/integration-readiness", factory=factory)
        self.assertEqual(handler.last[0], 200)
        self.assertEqual(factory.services[-1].calls[0][0], "integration_readiness")
        _, handler = get("/handoff/"+QV+"/compatibility", factory=factory)
        self.assertEqual(handler.last[0], 200)
        self.assertEqual(factory.services[-1].calls[0][0], "collector_wire_compatibility")
        self.assertTrue(all(guard is None for guard in factory.guards))
        _, handler = get("/integration-readiness?force=1", factory=factory)
        self.assertEqual(handler.last[0], 400)


if __name__ == "__main__":
    unittest.main()
