"""PG17-only mocked RTB Deep V2 expansion ledger / two-expansion cap tests.

No real OpenRouter calls. Each expansion gets a NEW independent user consent.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import json
import unittest

from test_ke_stage02_intake_db import DSN
from test_taxonomy_deep_v2_pass2_db import DeepV2Pass2PG
from intelligence.taxonomy_builder.deep_v2_expansion import RTBDeepV2ExpansionRepository
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError


@unittest.skipUnless(DSN, "PostgreSQL17 required for RTB explicit Deep V2 expansions")
class DeepV2ExpansionPG(unittest.TestCase):
    def setUp(self):
        self.base = DeepV2Pass2PG(methodName="runTest")
        self.base.setUp()
        first_nodes = self.base.review["approved_nodes"]
        self.duplicates = [{
            "a": first_nodes[0]["id"],
            "b": first_nodes[1]["id"],
            "reason": "Two content ideas overlap pending separate validation",
        }]
        out = self.base._advance(
            self.base._sender(duplicates=self.duplicates))
        self.assertEqual(out["status"], "expand_pending")
        self.repo = RTBDeepV2ExpansionRepository(
            connection_factory=self.base.fx.repo.connection_factory)
        self.pid, self.rid, self.actor = (
            self.base.pid, self.base.rid, self.base.actor)

    def tearDown(self):
        self.base.tearDown()

    def _sender(self, *, known=True, wrong_h1=False, fault=False):
        def send(payload):
            if fault:
                raise TimeoutError("test transport after possible charge")
            request = json.loads(payload["messages"][-1]["content"])
            nodes = deepcopy(request["last_approved_or_generated_tree"])
            if wrong_h1:
                nodes[0]["name"] = "Unexpected rewritten direction"
            response = {
                "id": "mock-expansion-only",
                "model": payload["model"],
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({
                        "nodes": nodes,
                        "duplicates": self.duplicates,
                    })},
                }],
                "usage": {
                    "prompt_tokens": 6000, "completion_tokens": 2100,
                    "cost": "0.031",
                },
            }
            if not known:
                del response["usage"]["cost"]
            return response
        return send

    def _budget(self):
        with self.base.fx.repo.connection_factory() as conn:
            return conn.execute(
                """SELECT authorized_usd,spent_usd,reserved_usd,
                          expansion_count,status
                   FROM tb_deep_v2_runs WHERE project_id=%s AND id=%s""",
                (self.pid, self.rid),
            ).fetchone()

    def _approved(self):
        quote = self.repo.quote_expansion(self.pid, self.rid, self.actor)
        consent = self.repo.authorize_expansion(
            self.pid, quote["quote_id"], self.actor,
            quote["required_usd"],
            acknowledged_quote_sha256=quote["quote_sha256"],
        )
        return quote, consent

    def _advance(self, consent, sender):
        return self.repo.advance_expansion(
            self.pid, self.rid, consent["consent_id"],
            self.actor, sender=sender)

    def test_quote_never_makes_paid_attempt_or_amends_budget(self):
        before = self._budget()
        q = self.repo.quote_expansion(self.pid, self.rid, self.actor)
        after = self._budget()
        self.assertEqual(before, after)
        self.assertTrue(q["explicit_consent_required"])
        self.assertEqual(q["provider_calls"], 0)
        self.assertEqual(q["expansion_no"], 1)
        with self.base.fx.repo.connection_factory() as conn:
            self.assertEqual(conn.execute(
                """SELECT count(*) FROM tb_deep_v2_attempts
                   WHERE project_id=%s AND run_id=%s
                     AND stage LIKE 'deep_expand_%'""",
                (self.pid, self.rid),
            ).fetchone()[0], 0)

    def test_each_consent_adds_exact_audited_budget_once(self):
        before = self._budget()
        q, c = self._approved()
        after = self._budget()
        self.assertEqual(after[0]-before[0], Decimal(c["newly_authorized_usd"]))
        self.assertGreaterEqual(Decimal(c["newly_authorized_usd"]),
                                Decimal(q["required_usd"]))
        self.assertEqual(c["expansion_no"], 1)
        with self.assertRaisesRegex(PipelineError, "expansion_already_authorized"):
            self.repo.authorize_expansion(
                self.pid, q["quote_id"], self.actor,
                q["required_usd"], acknowledged_quote_sha256=q["quote_sha256"])
        self.assertEqual(self._budget(), after)

    def test_wrong_sha_or_unauthorized_budget_blocks_consent(self):
        q = self.repo.quote_expansion(self.pid, self.rid, self.actor)
        for bad in ("0", "100.000000", ""):
            with self.subTest(budget=bad), self.assertRaises(PipelineError):
                self.repo.authorize_expansion(
                    self.pid, q["quote_id"], self.actor, bad,
                    acknowledged_quote_sha256=q["quote_sha256"])
        with self.assertRaisesRegex(PipelineError, "consent_hash_mismatch"):
            self.repo.authorize_expansion(
                self.pid, q["quote_id"], self.actor, q["required_usd"],
                acknowledged_quote_sha256="0"*64)
        self.assertEqual(self._budget()[3], 0)

    def test_two_paid_expansions_and_never_a_third(self):
        q1, c1 = self._approved()
        first = self._advance(c1, self._sender())
        self.assertEqual(first["expansion_count"], 1)
        self.assertEqual(first["status"], "expand_pending")
        q2, c2 = self._approved()
        self.assertEqual(q2["expansion_no"], 2)
        self.assertNotEqual(q1["quote_id"], q2["quote_id"])
        second = self._advance(c2, self._sender())
        self.assertEqual(second["expansion_count"], 2)
        self.assertEqual(second["status"], "blocked_quality")
        self.assertEqual(self._budget()[3], 2)
        with self.assertRaisesRegex(PipelineError, "expansion_not_pending"):
            self.repo.quote_expansion(self.pid, self.rid, self.actor)
        with self.base.fx.repo.connection_factory() as conn:
            rows = conn.execute(
                """SELECT stage,charged_usd FROM tb_deep_v2_attempts
                   WHERE project_id=%s AND run_id=%s AND
                         stage IN('deep_expand_1','deep_expand_2')
                   ORDER BY stage""",
                (self.pid, self.rid),
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual([x[0] for x in rows], ["deep_expand_1", "deep_expand_2"])
        self.assertEqual([x[1] for x in rows], [Decimal("0.031")]*2)

    def test_one_expansion_writes_finalized_single_attempt(self):
        _, consent = self._approved()
        out = self._advance(consent, self._sender())
        self.assertEqual(out["provider_calls"], 1)
        budget = self._budget()
        self.assertEqual(budget[2], 0)
        self.assertGreater(budget[1], Decimal("0.031"))
        with self.assertRaisesRegex(PipelineError, "expansion_not_pending"):
            self._advance(consent, self._sender())

    def test_unchanged_h1_all_14_fields_and_original_a_b(self):
        _, consent = self._approved()
        captured = []
        original = self._sender()
        def sender(payload):
            captured.append(payload)
            return original(payload)
        self._advance(consent, sender)
        self.assertEqual(len(captured), 1)
        body = json.loads(captured[0]["messages"][-1]["content"])
        self.assertEqual(body["review1"], self.base.review["approved_nodes"])
        self.assertTrue(body["coverage"]["h1_is_locked"])
        self.assertTrue(body["coverage"]["last_tree_is_locked"])
        self.assertEqual(body["coverage"]["explicit_expansion_number"], 1)
        self.assertIn("approved_b9_facts", body)
        self.assertIn("original_a_observations", body)
        self.assertEqual(len(body["last_approved_or_generated_tree"][0]), 14)

    def test_charged_rewritten_h1_is_rejected_without_retry(self):
        _, consent = self._approved()
        out = self._advance(consent, self._sender(wrong_h1=True))
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["charged_usd"], "0.031")
        self.assertEqual(self._budget()[2], 0)
        with self.assertRaises(PipelineError):
            self._advance(consent, self._sender())

    def test_unknown_cost_holds_reserve_and_prevents_reuse(self):
        _, consent = self._approved()
        out = self._advance(consent, self._sender(known=False))
        self.assertEqual(out["status"], "uncertain")
        self.assertFalse(out["automatic_retry_allowed"])
        self.assertGreater(self._budget()[2], 0)
        self.assertEqual(self._budget()[3], 0)
        with self.assertRaises(PipelineError):
            self._advance(consent, self._sender())

    def test_revoked_expansion_consent_prevents_network(self):
        _, consent = self._approved()
        self.repo.revoke_expansion(self.pid, consent["consent_id"], self.actor)
        calls = []
        with self.assertRaisesRegex(PipelineError, "expansion_revoked"):
            self._advance(consent, lambda payload: calls.append(payload))
        self.assertEqual(calls, [])
        self.assertEqual(self._budget()[4], "expand_pending")

    def test_double_paid_advance_is_at_most_one_request(self):
        _, consent = self._approved()
        captured, lock = [], Lock()
        mock = self._sender()
        def sender(payload):
            with lock:
                captured.append(payload)
            return mock(payload)
        def execute():
            try:
                return self._advance(consent, sender)["status"]
            except PipelineError:
                return "blocked"
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: execute(), (1, 2)))
        self.assertEqual(len(captured), 1)
        self.assertEqual(len(results), 2)
        self.assertIn("blocked", results)


if __name__ == "__main__":
    unittest.main()
