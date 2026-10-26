"""SP-09: independent read-only source capability snapshot and matcher.

All fixtures here are manually constructed SERVER-side. They are not proof of
real Collector readiness; no external provider calls, SQL writes or secrets.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from intelligence.search_planner import SearchPlannerService
from intelligence.search_planner.capabilities import (
    CAPABILITY_SCHEMA, MATCH_SCHEMA, CapabilityMatcher,
    validate_capability_snapshot, project_collector_declarations,
)
from intelligence.search_planner.capabilities.service import match_capabilities
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

PID = "a" * 32
FOREIGN = "b" * 32
NOW = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)


def snapshot():
    return {
        "schema_version": CAPABILITY_SCHEMA,
        "project_id": PID,
        "registry_version": "external-catalog/v1",
        "captured_at": (NOW - timedelta(minutes=1)).isoformat(),
        "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
        "sources": [{
            "source_id": "verified_test_source",
            "platform": "tiktok",
            "integration_state": "mock_verified_injected_port",
            "enabled": True,
            "operationally_verified": True,
            "source_policy_verified": True,
            "credentials_verified": True,
            "pricing_status": "verified_free",
            "cost_usd_per_request": "0.00000000",
            "quota_available_requests": 17,
            "capabilities": [{
                "mode": "keyword",
                "operation": "search_posts",
                "languages": ["ru", "en"],
                "markets": ["BY", "US"],
                "max_query_length": 120,
                "max_pages": 1,
                "pagination_verified": False,
            }],
        }],
    }


def query(*, mode="keyword", text="домашняя тренировка", language="ru",
          market="BY"):
    return {"query_mode": mode, "text": text, "language": language,
            "market": market}


class FakePort:
    def __init__(self, doc):
        self.doc = doc
        self.calls = []
    def snapshot(self, project_id):
        self.calls.append(project_id)
        return deepcopy(self.doc)


class CapabilitiesStage09(unittest.TestCase):
    def test_service_exposes_optional_port_but_never_claims_execution(self):
        svc = SearchPlannerService(
            capability_matcher=CapabilityMatcher(
                FakePort(snapshot()), clock=lambda: NOW,
            )
        )
        status = svc.status().to_dict()
        self.assertEqual(status["collector"]["state"], "capability_port_configured")
        self.assertFalse(status["collector"]["external_search_ready"])
        self.assertEqual(
            svc.source_capabilities(PID)["registry_version"], "external-catalog/v1"
        )
        matched = svc.match_query_capabilities(PID, query())
        self.assertEqual(matched["compatible_source_ids"], ["verified_test_source"])
        self.assertFalse(matched["external_dispatch_authorized"])
        self.assertEqual(matched["provider_calls_in_this_match"], 0)
        with self.assertRaises(PipelineError):
            SearchPlannerService(capability_matcher={})

    def test_service_without_port_cannot_invent_executable_search(self):
        svc = SearchPlannerService()
        catalog = svc.source_capabilities(PID)
        self.assertEqual(catalog["sources"], [])
        self.assertEqual(catalog["registry_version"], "collector-adapter-unavailable/v1")
        result = svc.match_query_capabilities(PID, query())
        self.assertFalse(result["external_dispatch_authorized"])
        self.assertEqual(result["catalog_status"], "unavailable")
        with self.assertRaises(PipelineError):
            svc.source_capabilities("wrong-project-id")

    def test_no_collector_port_is_truthfully_unavailable_and_cost_free(self):
        match = CapabilityMatcher(clock=lambda: NOW)
        result = match.match(PID, query())
        self.assertEqual(result["schema_version"], MATCH_SCHEMA)
        self.assertEqual(result["catalog_status"], "unavailable")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["compatible_source_ids"], [])
        self.assertEqual(result["provider_calls_in_this_match"], 0)
        self.assertFalse(result["external_dispatch_authorized"])
        self.assertEqual(match.snapshot(PID)["sources"], [])

    def test_verified_snapshot_is_plan_compatible_not_authorized(self):
        doc = snapshot()
        port = FakePort(doc)
        matcher = CapabilityMatcher(port=port, clock=lambda: NOW)
        output = matcher.match(PID, query())
        self.assertEqual(port.calls, [PID])
        self.assertEqual(output["compatible_source_ids"], ["verified_test_source"])
        match = output["matches"][0]
        self.assertTrue(match["capability_declared"])
        self.assertTrue(match["operationally_ready"])
        self.assertTrue(match["policy_and_budget_verified"])
        self.assertTrue(match["planning_compatible"])
        self.assertEqual(match["declared_operation"], "search_posts")
        self.assertEqual(match["estimated_single_request_usd"], "0.00000000")
        self.assertFalse(match["external_dispatch_authorized"])
        self.assertEqual(doc, snapshot())
        self.assertEqual(output, matcher.match(PID, query()))
        self.assertEqual(matcher.snapshot(PID)["sources"], doc["sources"])

    def test_strict_project_identity_and_unknown_keys(self):
        for mutate in (
            lambda x: x.__setitem__("project_id", FOREIGN),
            lambda x: x.__setitem__("schema_version", "collector_registry/v2"),
            lambda x: x.__setitem__("unexpected", True),
            lambda x: x.__setitem__("registry_version", ""),
            lambda x: x.__setitem__("sources", "bad"),
        ):
            doc = snapshot()
            mutate(doc)
            with self.subTest(doc=doc), self.assertRaises(PipelineError):
                validate_capability_snapshot(doc, project_id=PID, now=NOW)

    def test_source_quota_price_and_readiness_never_inferred(self):
        for field, value, expected in (
            ("enabled", False, "source_disabled"),
            ("operationally_verified", False, "source_not_operationally_verified"),
            ("credentials_verified", False, "credentials_not_verified"),
            ("source_policy_verified", False, "source_policy_not_verified"),
            ("quota_available_requests", None, "quota_not_verified"),
            ("quota_available_requests", 0, "quota_exhausted"),
            ("pricing_status", "unknown", "pricing_not_verified"),
        ):
            doc = snapshot()
            doc["sources"][0][field] = value
            if field == "pricing_status":
                doc["sources"][0]["cost_usd_per_request"] = None
            with self.subTest(field=field):
                result = match_capabilities(doc, query(), project_id=PID, now=NOW)
                entry = result["matches"][0]
                self.assertFalse(entry["planning_compatible"])
                self.assertIn(expected, entry["block_reasons"])
                self.assertEqual(result["compatible_source_ids"], [])
                self.assertFalse(result["external_dispatch_authorized"])

    def test_paid_price_is_visible_but_never_grants_execution_consent(self):
        doc = snapshot()
        doc["sources"][0]["pricing_status"] = "verified_paid"
        doc["sources"][0]["cost_usd_per_request"] = "0.08000000"
        result = match_capabilities(doc, query(), project_id=PID, now=NOW)
        self.assertEqual(result["compatible_source_ids"], ["verified_test_source"])
        self.assertEqual(result["matches"][0]["estimated_single_request_usd"], "0.08000000")
        self.assertFalse(result["external_dispatch_authorized"])

    def test_bad_decimal_and_quota_data_fail_closed(self):
        for price in ("NaN", "-0.01", "Infinity", "0", "0.000000001", 1, True):
            doc = snapshot()
            doc["sources"][0]["pricing_status"] = "verified_paid"
            doc["sources"][0]["cost_usd_per_request"] = price
            with self.subTest(price=price), self.assertRaises(PipelineError):
                validate_capability_snapshot(doc, project_id=PID, now=NOW)
        for quota in (-1, True, 1.1, "0"):
            doc = snapshot()
            doc["sources"][0]["quota_available_requests"] = quota
            with self.subTest(quota=quota), self.assertRaises(PipelineError):
                validate_capability_snapshot(doc, project_id=PID, now=NOW)

    def test_stale_snapshot_has_no_eligible_source_even_if_verified(self):
        doc = snapshot()
        output = match_capabilities(
            doc, query(), project_id=PID, now=NOW + timedelta(minutes=10),
        )
        self.assertEqual(output["catalog_status"], "expired")
        self.assertFalse(output["matches"][0]["planning_compatible"])
        self.assertIn("capability_snapshot_expired", output["matches"][0]["block_reasons"])

    def test_future_dated_or_excessive_lifetime_is_invalid(self):
        doc = snapshot()
        doc["captured_at"] = (NOW + timedelta(hours=1)).isoformat()
        with self.assertRaises(PipelineError):
            validate_capability_snapshot(doc, project_id=PID, now=NOW)
        doc = snapshot()
        doc["expires_at"] = (NOW + timedelta(days=3)).isoformat()
        with self.assertRaises(PipelineError):
            validate_capability_snapshot(doc, project_id=PID, now=NOW)

    def test_only_verified_query_language_and_market_match(self):
        for kwargs, reason in (
            ({"language": "de"}, "language_not_verified"),
            ({"market": "NL"}, "market_not_verified"),
            ({"market": None}, "market_not_specified"),
            ({"text": "x" * 121}, "Untrusted"),
        ):
            with self.subTest(kwargs=kwargs):
                q = query(**kwargs)
                if len(q["text"]) > 120:
                    with self.assertRaises(PipelineError):
                        match_capabilities(snapshot(), q, project_id=PID, now=NOW)
                else:
                    r = match_capabilities(snapshot(), q, project_id=PID, now=NOW)
                    self.assertIn(reason, r["matches"][0]["block_reasons"])

    def test_hashtag_does_not_masquerade_as_keyword_capability(self):
        r = match_capabilities(snapshot(), query(mode="hashtag"),
                               project_id=PID, now=NOW)
        self.assertFalse(r["matches"][0]["capability_declared"])
        self.assertIn("unsupported_query_mode", r["matches"][0]["block_reasons"])

    def test_verified_wildcard_scope_explicitly_matches(self):
        doc = snapshot()
        c = doc["sources"][0]["capabilities"][0]
        c["languages"], c["markets"] = ["*"], ["*"]
        r = match_capabilities(doc, query(language="ru", market=None),
                               project_id=PID, now=NOW)
        self.assertEqual(r["compatible_source_ids"], ["verified_test_source"])

    def test_duplicate_source_and_mode_are_rejected(self):
        doc = snapshot()
        doc["sources"].append(deepcopy(doc["sources"][0]))
        with self.assertRaises(PipelineError):
            validate_capability_snapshot(doc, project_id=PID, now=NOW)
        doc = snapshot()
        doc["sources"][0]["capabilities"].append(
            deepcopy(doc["sources"][0]["capabilities"][0]))
        with self.assertRaises(PipelineError):
            validate_capability_snapshot(doc, project_id=PID, now=NOW)

    def test_invalid_scope_value_types_cannot_crash_with_type_error(self):
        for langs in ([], ["ru", "ru"], ["*","ru"], [[]], [1], "ru"):
            doc = snapshot()
            doc["sources"][0]["capabilities"][0]["languages"] = langs
            with self.subTest(langs=langs), self.assertRaises(PipelineError):
                validate_capability_snapshot(doc, project_id=PID, now=NOW)
        doc = snapshot()
        doc["sources"][0]["capabilities"][0]["mode"] = []
        with self.assertRaises(PipelineError):
            validate_capability_snapshot(doc, project_id=PID, now=NOW)

    def test_unknown_source_pricing_never_gets_a_synthetic_zero(self):
        doc = snapshot()
        src = doc["sources"][0]
        src.update(pricing_status="unknown", cost_usd_per_request=None)
        r = match_capabilities(doc, query(), project_id=PID, now=NOW)
        self.assertIsNone(r["matches"][0]["estimated_single_request_usd"])
        self.assertIn("pricing_not_verified", r["matches"][0]["block_reasons"])

    def test_legacy_collector_declared_keyword_is_not_ready(self):
        desc = [{
            "source_id": "dtk_tiktok", "platform": "tiktok",
            "integration_state": "local_rest_connector_present_manual_only",
            "capabilities": [{"mode": "keyword", "operation": "search_posts"}],
            "operationally_verified": False,
            "source_policy_verified": False,
            "pricing_status": "not_quoted",
        }, {
            "source_id": "apify_tiktok", "platform": "tiktok",
            "integration_state": "future_adapter_not_implemented",
            "capabilities": [], "operationally_verified": False,
            "source_policy_verified": False, "pricing_status": "unknown_paid_provider",
        }]
        projected = project_collector_declarations(
            PID, registry_version="collector-source-registry/v2-dtk-installed",
            descriptors=desc, observed_at=NOW,
        )
        self.assertEqual(projected["sources"][0]["source_id"], "dtk_tiktok")
        r = match_capabilities(projected, query(), project_id=PID, now=NOW)
        match = next(x for x in r["matches"] if x["source_id"] == "dtk_tiktok")
        self.assertTrue(match["capability_declared"])
        self.assertEqual(match["declared_operation"], "search_posts")
        self.assertFalse(match["planning_compatible"])
        self.assertIn("pricing_not_verified", match["block_reasons"])
        self.assertIn("quota_not_verified", match["block_reasons"])
        self.assertIn("language_not_verified", match["block_reasons"])
        self.assertFalse(match["external_dispatch_authorized"])
        self.assertFalse(
            next(x for x in r["matches"] if x["source_id"] == "apify_tiktok")
            ["capability_declared"]
        )

    def test_legacy_source_flags_can_never_upgrade_themselves_to_ready(self):
        desc = [{
            "source_id": "dtk_tiktok", "platform": "tiktok",
            "integration_state": "manual",
            "capabilities": [{"mode": "keyword", "operation": "search_posts"}],
            "operationally_verified": True, "source_policy_verified": True,
            "pricing_status": "verified_free",
        }]
        catalog = project_collector_declarations(
            PID, registry_version="collector-source-registry/v2",
            descriptors=desc, observed_at=NOW,
        )
        source = catalog["sources"][0]
        self.assertFalse(source["operationally_verified"])
        self.assertFalse(source["source_policy_verified"])
        self.assertFalse(source["credentials_verified"])
        self.assertIsNone(source["quota_available_requests"])
        self.assertEqual(source["pricing_status"], "unknown")

    def test_no_collector_import_required_even_in_fresh_process(self):
        root = Path(__file__).resolve().parents[1]
        cmd = r"""
import builtins, sys
orig = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == "intelligence.collector" or name.startswith("intelligence.collector."):
        raise AssertionError("Unexpected Collector runtime import")
    return orig(name, *args, **kwargs)
builtins.__import__ = blocked
from intelligence.search_planner.capabilities import CapabilityMatcher
assert CapabilityMatcher().snapshot("a"*32)["sources"] == []
assert "intelligence.collector" not in sys.modules
"""
        p = subprocess.run([sys.executable, "-c", cmd], cwd=root,
                           capture_output=True, text=True, check=False, timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + "\n" + p.stderr)

    def test_bad_query_types_and_browser_ready_claims_are_rejected(self):
        matcher = CapabilityMatcher(FakePort(snapshot()), clock=lambda: NOW)
        for replacement in ({"ready": True}, {"mode": "keyword"}, {"query_mode": []},
                            {"query_mode": "keyword", "text": "q", "language": "ru",
                             "market": "BY", "approved": True}):
            with self.subTest(replacement=replacement), self.assertRaises(PipelineError):
                matcher.match(PID, replacement)

    def test_source_catalog_order_is_stable_for_hash_and_match(self):
        doc = snapshot()
        second = deepcopy(doc["sources"][0])
        second["source_id"] = "verified_other_source"
        doc["sources"].append(second)
        first = match_capabilities(doc, query(), project_id=PID, now=NOW)
        doc["sources"].reverse()
        reordered = match_capabilities(doc, query(), project_id=PID, now=NOW)
        self.assertEqual(first["compatible_source_ids"], reordered["compatible_source_ids"])
        # Hash covers the exact catalog payload; changing provider ordering
        # changes the underlying byte snapshot and must be detected.
        self.assertNotEqual(first["catalog_sha256"], reordered["catalog_sha256"])


if __name__ == "__main__":
    unittest.main()
