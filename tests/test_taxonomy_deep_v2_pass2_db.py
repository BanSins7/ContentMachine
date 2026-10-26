"""Mocked PostgreSQL17 RTB Deep V2 Pass2 after immutable Human Review1.

One explicitly invoked Deep V2 Pass2 only; no paid provider network calls.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from copy import deepcopy
from threading import Lock
from unittest.mock import patch
import json
import unittest

from test_ke_stage02_intake_db import DSN
from test_taxonomy_deep_v2_review_db import RTBDeepV2Review1PG

from intelligence.taxonomy_builder.deep_v2_pass2 import RTBDeepV2Pass2Repository
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError


@unittest.skipUnless(DSN, "PostgreSQL17 required for RTB Deep V2 Pass2")
class DeepV2Pass2PG(unittest.TestCase):
    def setUp(self):
        self.fx = RTBDeepV2Review1PG(methodName="runTest")
        self.fx.setUp()
        self.review = self.fx.repo.submit_review1(
            self.fx.pid, self.fx.run_id, self.fx.actor, self.fx._approve())
        self.repo = RTBDeepV2Pass2Repository(
            connection_factory=self.fx.repo.connection_factory)
        self.pid, self.rid, self.actor = self.fx.pid, self.fx.run_id, self.fx.actor

    def tearDown(self):
        self.fx.tearDown()

    def _sender(self, *, cost="0.025", tree=None, known_usage=True,
                fail=False, duplicates=None):
        def sender(payload):
            if fail:
                raise TimeoutError("synthetic uncertain provider response")
            content = json.dumps({
                "nodes": deepcopy(tree if tree is not None
                                   else self.review["approved_nodes"]),
                "duplicates": deepcopy(duplicates or []),
            })
            result = {
                "id": "mock-second-pass",
                "model": payload["model"],
                "choices": [{"finish_reason": "stop",
                             "message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 5200,
                    "completion_tokens": 2600,
                    "cost": cost,
                },
            }
            if not known_usage:
                del result["usage"]["cost"]
            return result
        return sender

    def _advance(self, sender):
        return self.repo.advance_pass2(
            self.pid, self.rid, self.actor, sender=sender)

    def _attempt(self):
        with self.fx.repo.connection_factory() as conn:
            attempt = conn.execute(
                """SELECT status,reservation_usd,charged_usd,output,error_code
                   FROM tb_deep_v2_attempts
                   WHERE project_id=%s AND run_id=%s AND stage='deep_second'""",
                (self.pid, self.rid),
            ).fetchone()
            record = conn.execute(
                """SELECT status,spent_usd,reserved_usd,quality_report
                   FROM tb_deep_v2_runs WHERE project_id=%s AND id=%s""",
                (self.pid, self.rid),
            ).fetchone()
        return attempt, record

    def test_approved_h1_one_paid_pass_retains_all_14_keys(self):
        called = []
        send = self._sender()
        def track(payload):
            called.append(payload)
            return send(payload)
        result = self._advance(track)
        self.assertEqual(len(called), 1)
        self.assertIn(result["status"], ("review2", "expand_pending"))
        self.assertEqual(result["charged_usd"], "0.025")
        attempt, run = self._attempt()
        self.assertEqual(attempt[0], "completed")
        self.assertEqual(Decimal(attempt[2]), Decimal("0.025"))
        self.assertEqual(attempt[3]["nodes"], self.review["approved_nodes"])
        self.assertTrue(all(len(n) == 14 for n in attempt[3]["nodes"]))
        self.assertEqual(Decimal(run[2]), 0)
        self.assertGreater(Decimal(run[1]), Decimal("0.025"))
        with self.assertRaisesRegex(PipelineError, "pass2_not_pending"):
            self._advance(track)
        self.assertEqual(len(called), 1)

    def test_rewritten_h1_node_is_charged_but_rejected(self):
        changed = deepcopy(self.review["approved_nodes"])
        changed[0]["name"] = "Unapproved changed H1 node"
        out = self._advance(self._sender(tree=changed))
        self.assertEqual(out["status"], "failed")
        attempt, run = self._attempt()
        self.assertEqual(attempt[0], "error")
        self.assertEqual(attempt[4], "rtb_v2_pass2_charged_tree_rejected")
        self.assertEqual(Decimal(run[2]), 0)
        self.assertEqual(Decimal(run[1]), Decimal("0.05"))

    def test_unknown_charge_holds_money_and_forbids_retry(self):
        calls = []
        send = self._sender(known_usage=False)
        def track(payload):
            calls.append(payload)
            return send(payload)
        out = self._advance(track)
        self.assertEqual(out["status"], "uncertain")
        self.assertFalse(out["automatic_retry_allowed"])
        attempt, run = self._attempt()
        self.assertEqual(attempt[0], "uncertain")
        self.assertGreater(Decimal(run[2]), 0)
        self.assertEqual(Decimal(run[1]), Decimal("0.025"))
        with self.assertRaises(PipelineError):
            self._advance(track)
        self.assertEqual(len(calls), 1)

    def test_transport_timeout_never_retries(self):
        out = self._advance(self._sender(fail=True))
        self.assertEqual(out["status"], "uncertain")
        attempt, run = self._attempt()
        self.assertEqual(attempt[0], "uncertain")
        self.assertGreater(Decimal(run[2]), 0)

    def test_pass2_cannot_dispatch_without_h1_or_if_cost_exceeds_cap(self):
        calls = []
        with patch(
            "intelligence.taxonomy_builder.deep_v2_pass2.reservation_cost",
            return_value=Decimal("9999.000000000000")
        ):
            with self.assertRaisesRegex(
                PipelineError, "exceeds_authorized_reservation"
            ):
                self._advance(lambda body: calls.append(body))
        self.assertEqual(calls, [])
        attempt, run = self._attempt()
        self.assertIsNone(attempt)
        self.assertEqual(run[0], "pass2_pending")
        self.assertEqual(Decimal(run[2]), 0)

    def test_concurrent_double_second_pass_is_exactly_one_request(self):
        lock, calls = Lock(), []
        send = self._sender()
        def track(payload):
            with lock:
                calls.append(payload)
            return send(payload)
        def attempt():
            try:
                return self._advance(track)["status"]
            except PipelineError:
                return "blocked"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), (1, 2)))
        self.assertEqual(calls.__len__(), 1)
        self.assertIn("blocked", results)
        self.assertEqual(len(results), 2)

    def test_revocation_before_second_pass_never_calls_opus(self):
        with self.fx.repo.connection_factory() as conn:
            consent_id = conn.execute(
                """SELECT consent_id FROM tb_deep_v2_runs
                   WHERE project_id=%s AND id=%s""",
                (self.pid, self.rid),
            ).fetchone()[0]
        self.repo.revoke(self.pid, str(consent_id), self.actor)
        calls = []
        with self.assertRaisesRegex(PipelineError, "consent_revoked"):
            self._advance(lambda body: calls.append(body))
        self.assertEqual(calls, [])
        attempt, run = self._attempt()
        self.assertIsNone(attempt)
        self.assertEqual(run[0], "pass2_pending")

    def test_proposed_duplicates_trigger_nonautomatic_expansion_pending(self):
        a, b = self.review["approved_nodes"][:2]
        result = self._advance(self._sender(duplicates=[{
            "a": a["id"], "b": b["id"],
            "reason": "Manually investigate overlapping formats",
        }]))
        self.assertEqual(result["status"], "expand_pending")
        self.assertGreater(result["quality"]["duplicate_count"], 0)
        attempt, _ = self._attempt()
        self.assertEqual(attempt[0], "completed")


if __name__ == "__main__":
    unittest.main()
