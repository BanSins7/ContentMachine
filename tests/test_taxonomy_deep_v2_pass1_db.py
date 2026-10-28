"""RTB Deep V2 Pass1 reserve/charge PostgreSQL17 integration; MOCK provider only.

The test calls internal advance_pass1() explicitly. It never contacts
OpenRouter or grants permission for UI automatic dispatch.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Lock
import json
import unittest
from uuid import uuid4

from test_ke_stage02_intake_db import DSN
from psycopg.types.json import Jsonb
from test_taxonomy_deep_v2_quote_db import DeepV2QuotePG

from intelligence.taxonomy_builder.deep_v2_pass1 import RTBDeepV2Pass1Repository
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError


@unittest.skipUnless(DSN, "PostgreSQL17 required for RTB Deep V2 Pass1")
class DeepV2Pass1PG(unittest.TestCase):

    def setUp(self):
        self.fx = DeepV2QuotePG(methodName="runTest")
        self.fx.setUp()
        self.repository = RTBDeepV2Pass1Repository(
            connection_factory=self.fx.fx.factory)
        self.quote = self.fx._quote()
        self.consent = self.fx._consent(self.quote)
        self.started = self.repository.start(
            self.fx.pid, self.quote["quote_id"], self.consent["consent_id"],
            self.fx.reviewer, uuid4().hex)
        pinned = self.repository.bindings.get(
            self.fx.pid, self.fx.bid, self.fx.reviewer)["research_input"]
        self.source = pinned

    def tearDown(self):
        self.fx.tearDown()

    def _nodes(self):
        fact = self.source["c1_approved_facts"][0]
        fid = fact["rtb_fact_id"]
        # This fixture imports the approved B9 fact from Stage13. The source
        # can be "other", not an own-product evidence grant. A "direct"
        # taxonomy relation would be rightly rejected by the V2 validator.
        # Keep the fake provider response semantically valid without relaxing
        # production source-role/product-claim protections.
        relation = (
            "direct" if fact["may_support_own_product_claim"] is True
            else "problem_related" if fact["kind"] == "problem"
            else "adjacent"
        )
        def node(nid, parent, kind):
            return {
                "id": nid, "parent_id": parent, "kind": kind,
                "relation": relation, "source_fact_ids": [fid],
                "name": nid.replace("_", " ").title(),
                "rationale": "Grounded short-form research direction",
                # V2 readiness detects near-duplicate names+definitions.
                # All four model nodes must express distinct search intents:
                # an identical generic definition would correctly block
                # publication as a semantic duplicate even with valid rights.
                "definition": {
                    "direction_one": "Study narrated workflow breakdown formats.",
                    "hypothesis_one": "Test concise side-by-side process comparisons.",
                    "direction_two": "Explore practical educational clip structures.",
                    "hypothesis_two": "Test audience-led explanatory question clips.",
                }[nid],
                "include": "Comparisons with observable source-grounded context",
                "exclude": "Unsupported claims not backed by approved sources",
                "examples": ["A brief functional demonstration"],
                "source_observation_ids": [],
                "search_signals": (["Demonstration format"] if kind=="hypothesis" else []),
                "validation_focus": (["Check actual demonstrator evidence"] if kind=="hypothesis" else []),
            }
        return [
            node("direction_one", None, "direction"),
            node("hypothesis_one", "direction_one", "hypothesis"),
            node("direction_two", None, "direction"),
            node("hypothesis_two", "direction_two", "hypothesis"),
        ]

    def _sender(self, *, cost="0.025", valid=True, known_usage=True, fail=False):
        def sender(payload):
            if fail:
                raise TimeoutError("synthetic connection drop")
            result = {
                "id": "mock-response-1", "model": payload["model"],
                "choices": [{"finish_reason": "stop", "message": {
                    "content": json.dumps({"nodes": self._nodes()}) if valid else "{bad"
                }}],
                "usage": {
                    "prompt_tokens": 4300, "completion_tokens": 2000,
                    "cost": cost,
                },
            }
            if not known_usage:
                del result["usage"]["cost"]
            return result
        return sender

    def _advance(self, sender):
        return self.repository.advance_pass1(
            self.fx.pid, self.started["run_id"],
            self.fx.reviewer, sender=sender)

    def test_exactly_one_mock_call_persists_paid_review1(self):
        sent = []
        good = self._sender()
        def sender(payload):
            sent.append(payload)
            return good(payload)
        result = self._advance(sender)
        self.assertEqual(len(sent), 1)
        self.assertEqual(result["status"], "review1")
        self.assertEqual(result["charged_usd"], "0.025")
        record = self.repository.inspect_pass1(
            self.fx.pid, self.started["run_id"], self.fx.reviewer)
        self.assertEqual(record["status"], "review1")
        self.assertEqual(Decimal(record["spent_usd"]), Decimal("0.025"))
        self.assertEqual(Decimal(record["reserved_usd"]), 0)
        self.assertEqual(record["attempt"]["status"], "completed")
        self.assertEqual(len(record["attempt"]["output"]["nodes"]), 4)
        with self.assertRaises(PipelineError):
            self._advance(sender)
        self.assertEqual(len(sent), 1)

    def test_unknown_provider_usage_keeps_reservation_and_stops_retries(self):
        calls = []
        f = self._sender(known_usage=False)
        def sender(payload):
            calls.append(payload)
            return f(payload)
        result = self._advance(sender)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(len(calls), 1)
        record = self.repository.inspect_pass1(
            self.fx.pid, self.started["run_id"], self.fx.reviewer)
        self.assertEqual(record["attempt"]["status"], "uncertain")
        self.assertGreater(Decimal(record["reserved_usd"]), 0)
        self.assertEqual(Decimal(record["spent_usd"]), 0)
        with self.assertRaises(PipelineError):
            self._advance(sender)
        self.assertEqual(len(calls), 1)

    def test_timeout_is_unconfirmed_cost_and_never_automatic_retry(self):
        out = self._advance(self._sender(fail=True))
        self.assertEqual(out["status"], "uncertain")
        self.assertEqual(
            self.repository.inspect_pass1(
                self.fx.pid, self.started["run_id"], self.fx.reviewer)["status"],
            "uncertain",
        )

    def test_known_charge_with_invalid_json_settles_and_fails(self):
        result = self._advance(self._sender(valid=False))
        self.assertEqual(result["status"], "failed")
        record = self.repository.inspect_pass1(
            self.fx.pid, self.started["run_id"], self.fx.reviewer)
        self.assertEqual(Decimal(record["spent_usd"]), Decimal("0.025"))
        self.assertEqual(Decimal(record["reserved_usd"]), 0)
        self.assertEqual(record["attempt"]["status"], "error")
        self.assertEqual(record["attempt"]["error_code"],
                         "rtb_v2_charged_response_invalid")

    def test_revocation_before_dispatch_never_calls_provider(self):
        self.repository.revoke(
            self.fx.pid, self.consent["consent_id"], self.fx.reviewer)
        called = []
        with self.assertRaisesRegex(PipelineError, "consent_revoked"):
            self._advance(lambda payload: called.append(payload))
        self.assertEqual(called, [])
        record = self.repository.inspect_pass1(
            self.fx.pid, self.started["run_id"], self.fx.reviewer)
        self.assertIsNone(record["attempt"])
        self.assertEqual(record["status"], "pass1_pending")

    def test_concurrent_double_advance_performs_at_most_one_provider_call(self):
        calls = []
        guard = Lock()
        good = self._sender()
        def sender(payload):
            with guard:
                calls.append(payload)
            return good(payload)
        def one():
            try:
                return self._advance(sender)["status"]
            except PipelineError:
                return "blocked_repeated_attempt"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: one(), range(2)))
        self.assertEqual(len(calls), 1)
        self.assertEqual(sorted(results), ["blocked_repeated_attempt", "review1"])

    def test_revoked_during_one_provider_call_still_records_known_charge(self):
        good = self._sender()
        def sender(payload):
            self.repository.revoke(
                self.fx.pid, self.consent["consent_id"], self.fx.reviewer)
            return good(payload)
        result = self._advance(sender)
        self.assertEqual(result["status"], "cancelled")
        record = self.repository.inspect_pass1(
            self.fx.pid, self.started["run_id"], self.fx.reviewer)
        self.assertEqual(Decimal(record["spent_usd"]), Decimal("0.025"))
        self.assertEqual(Decimal(record["reserved_usd"]), 0)
        self.assertEqual(record["attempt"]["status"], "completed")

    def test_charge_over_budget_is_audited_and_paused(self):
        big = str(Decimal(self.quote["required_usd"]) + Decimal("2"))
        result = self._advance(self._sender(cost=big))
        self.assertEqual(result["status"], "paused_budget")
        record = self.repository.inspect_pass1(
            self.fx.pid, self.started["run_id"], self.fx.reviewer)
        self.assertEqual(Decimal(record["spent_usd"]), Decimal(big))
        self.assertEqual(Decimal(record["reserved_usd"]), 0)
        self.assertEqual(record["attempt"]["status"], "completed")

    def test_price_drift_blocks_dispatch_before_attempt(self):
        with self.fx.fx.factory() as conn:
            new_snapshot = conn.execute(
                """INSERT INTO tb_model_snapshots(model_id,catalog_sha256,
                          capabilities,pricing)
                   VALUES(%s,%s,%s,%s) RETURNING id""",
                ("anthropic/claude-opus-5.5", uuid4().hex+uuid4().hex,
                 Jsonb(self.fx.fx.model["capabilities"]),
                 Jsonb({**self.fx.fx.model["pricing"], "prompt": "0.000011"})),
            ).fetchone()[0]
            refresh = conn.execute(
                """INSERT INTO tb_catalog_refreshes(models_present)
                   VALUES(%s) RETURNING id""",
                (Jsonb(["anthropic/claude-opus-5.5"]),),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO tb_catalog_refresh_models(
                     refresh_id,model_id,snapshot_id) VALUES(%s,%s,%s)""",
                (refresh, "anthropic/claude-opus-5.5", new_snapshot),
            )
        sent = []
        with self.assertRaises(PipelineError):
            self._advance(lambda payload: sent.append(payload))
        self.assertEqual(sent, [])
        self.assertIsNone(self.repository.inspect_pass1(
            self.fx.pid, self.started["run_id"], self.fx.reviewer)["attempt"])


if __name__ == "__main__":
    unittest.main()
