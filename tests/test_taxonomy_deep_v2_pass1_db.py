"""RTB Deep V2 Pass1 reserve/charge PostgreSQL17 integration; MOCK provider only.

The test calls internal advance_pass1() explicitly. It never contacts
OpenRouter or grants permission for UI automatic dispatch.
"""
from __future__ import annotations

from decimal import Decimal
import json
import unittest
from uuid import uuid4

from test_ke_stage02_intake_db import DSN
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
        fid = self.source["c1_approved_facts"][0]["rtb_fact_id"]
        def node(nid, parent, kind):
            return {
                "id": nid, "parent_id": parent, "kind": kind,
                "relation": "direct", "source_fact_ids": [fid],
                "name": nid.replace("_", " ").title(),
                "rationale": "Grounded short-form research direction",
                "definition": "Videos that show the approved real function",
                "include": "Comparisons of real demonstrated functions",
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
        with self.fx.fx.fx.factory() as conn:
            conn.execute(
                """UPDATE tb_catalog_refreshes SET checked_at =
                   checked_at-interval '2 days'""")
        sent = []
        with self.assertRaises(PipelineError):
            self._advance(lambda payload: sent.append(payload))
        self.assertEqual(sent, [])
        self.assertIsNone(self.repository.inspect_pass1(
            self.fx.pid, self.started["run_id"], self.fx.reviewer)["attempt"])


if __name__ == "__main__":
    unittest.main()
