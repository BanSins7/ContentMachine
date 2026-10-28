"""SP-03: no-spend config/run/quote/budget facade and owner-consent guard.

Tests use injected trusted DB function doubles; they MUST NOT simulate that
frontend JSON is authentic local-owner consent. Actual SQL transaction
semantics remain covered by full PostgreSQL engine checks on final branch.
"""
from __future__ import annotations

import copy
import unittest
from unittest import mock

from intelligence.search_planner import SearchPlannerService
from intelligence.search_planner.generation_workflow import GenerationWorkflow
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from test_search_planner_engine_contracts import config

PID = "a" * 32
FOREIGN = "b" * 32
PUB = "22222222-2222-4222-8222-222222222222"
GEN = "55555555-5555-4555-8555-555555555555"
CFG = "88888888-8888-4888-8888-888888888888"
QUOTE = "99999999-9999-4999-8999-999999999999"
CONSENT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
KEY = "sp-stage03-idempotent-run-0001"


class Recorder:
    def __init__(self):
        self.events = []
        self.quote_calls = 0
        self.authorized = False
        self.last_config = None

    def register_config(self, pid, cfg):
        self.events.append(("register", pid))
        self.last_config = copy.deepcopy(cfg)
        return {"project_id": pid, "config_id": CFG, "revision": 1,
                "config_sha256": "a" * 64,
                "status": "approved_config_no_spending_consent"}

    def create_plan(self, pid, pub, cid, key):
        self.events.append(("plan", pid, pub, cid, key))
        return {"project_id": pid, "generation_id": GEN, "status": "awaiting_authorization",
                "input_sha256": "b" * 64, "reused": self.events.count(
                    ("plan", pid, pub, cid, key)) > 1}

    def create_quote(self, pid, gid):
        self.quote_calls += 1
        self.events.append(("quote", pid, gid))
        return {"project_id": pid, "quote_id": QUOTE, "generation_id": gid,
                "high_usd": "1.25000000", "currency": "USD",
                "reused": self.quote_calls > 1}

    def authorize_budget(self, pid, gid, qid, amount):
        self.events.append(("authorize", pid, gid, qid, amount))
        if self.authorized:
            return {"project_id": pid, "consent_id": CONSENT,
                    "generation_id": gid, "authorized_usd": "1.25000000",
                    "status": "queued", "reused": True}
        self.authorized = True
        return {"project_id": pid, "consent_id": CONSENT,
                "generation_id": gid, "authorized_usd": "1.25000000",
                "status": "queued", "reused": False}

    def revoke_budget(self, pid, cid):
        self.events.append(("revoke", pid, cid))
        return {"project_id": pid, "consent_id": cid, "generation_id": GEN,
                "revoked": True, "reused": False}

    def read_budget(self, pid, gid):
        self.events.append(("budget", pid, gid))
        return {"project_id": pid, "generation_id": gid,
                "status": "awaiting_authorization", "consent": None}


def workflow(rec=None, guard=None):
    rec = rec if rec is not None else Recorder()
    return GenerationWorkflow(
        register_config=rec.register_config,
        create_plan=rec.create_plan,
        create_quote=rec.create_quote,
        authorize_budget=rec.authorize_budget,
        revoke_budget=rec.revoke_budget,
        read_budget=rec.read_budget,
        owner_action_guard=guard,
    )


class SP03GenerationWorkflowTests(unittest.TestCase):
    def test_register_config_pins_and_defensively_copies_without_money(self):
        r = Recorder(); w = workflow(r)
        raw = config(); before = copy.deepcopy(raw)
        x = w.register_configuration(PID, raw)
        self.assertEqual(x["config_id"], CFG)
        self.assertEqual(x["status"], "approved_config_no_spending_consent")
        self.assertEqual(raw, before)
        x["status"] = "tampered"
        self.assertEqual(r.last_config, before)
        self.assertEqual(r.events, [("register", PID)])

    def test_invalid_config_and_foreign_project_fail_before_register(self):
        r = Recorder(); w = workflow(r)
        for pid, cfg in (
            ("foreign", config()),
            (PID, {**config(), "unexpected": True}),
            (PID, {**config(), "schema_version": "not-v1"}),
        ):
            with self.subTest(pid=pid, cfg=cfg.get("schema_version")):
                with self.assertRaises(PipelineError):
                    w.register_configuration(pid, cfg)
        self.assertEqual(r.events, [])

    def test_plan_reuses_exact_idempotency_key_and_does_not_quote(self):
        r = Recorder(); w = workflow(r)
        first = w.plan(PID, PUB, CFG, KEY)
        second = w.plan(PID, PUB, CFG, KEY)
        self.assertEqual(first["generation_id"], second["generation_id"])
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(first["status"], "awaiting_authorization")
        self.assertEqual([t[0] for t in r.events], ["plan", "plan"])
        self.assertFalse(r.authorized)

    def test_invalid_run_inputs_do_not_touch_postgres_port(self):
        r = Recorder(); w = workflow(r)
        for pid, pub, cid, key in (
            (FOREIGN[:-1], PUB, CFG, KEY),
            (PID, "wrong", CFG, KEY),
            (PID, PUB, "bad-uuid", KEY),
            (PID, PUB, CFG, "short"),
            (PID, PUB, CFG, True),
        ):
            with self.subTest(value=(pid, pub, cid, key)):
                with self.assertRaises(PipelineError):
                    w.plan(pid, pub, cid, key)
        self.assertEqual(r.events, [])

    def test_quote_idempotency_and_no_consent(self):
        r = Recorder(); w = workflow(r)
        a, b = w.quote(PID, GEN), w.quote(PID, GEN)
        self.assertEqual(a["quote_id"], b["quote_id"])
        self.assertFalse(a["reused"])
        self.assertTrue(b["reused"])
        self.assertEqual(a["high_usd"], "1.25000000")
        self.assertEqual([t[0] for t in r.events], ["quote", "quote"])
        self.assertFalse(r.authorized)

    def test_quote_rejects_invalid_server_reply(self):
        r = Recorder()
        cases = (
            {"generation_id": GEN, "quote_id": QUOTE, "high_usd": "1", "currency": "EUR"},
            {"generation_id": PUB, "quote_id": QUOTE, "high_usd": "1", "currency": "USD"},
            {"generation_id": GEN, "quote_id": QUOTE, "high_usd": "NaN", "currency": "USD"},
            {"generation_id": GEN, "quote_id": "notuuid", "high_usd": "1", "currency": "USD"},
            {"generation_id": GEN, "quote_id": QUOTE, "high_usd": "1", "currency": "USD", "project_id": FOREIGN},
        )
        for case in cases:
            with self.subTest(case=case):
                w = GenerationWorkflow(create_quote=lambda *a, value=case: value)
                with self.assertRaises(PipelineError):
                    w.quote(PID, GEN)

    def test_budget_read_does_not_authorize_money(self):
        r = Recorder(); w = workflow(r)
        b = w.budget(PID, GEN)
        self.assertEqual(b["consent"], None)
        self.assertEqual(r.events, [("budget", PID, GEN)])
        self.assertFalse(r.authorized)

    def test_default_facade_cannot_authorize_without_server_gate(self):
        r = Recorder(); w = workflow(r)
        with self.assertRaisesRegex(PipelineError, "Verified local owner"):
            w.authorize(PID, GEN, QUOTE, "1.25")
        with self.assertRaisesRegex(PipelineError, "Verified local owner"):
            w.revoke(PID, CONSENT)
        self.assertEqual(r.events, [])

    def test_owner_guard_must_return_true_exactly(self):
        for result in (None, False, 1, "true", {}):
            r = Recorder(); w = workflow(r, guard=lambda *a, output=result: output)
            with self.subTest(result=result):
                with self.assertRaises(PipelineError):
                    w.authorize(PID, GEN, QUOTE, "1.25")
                self.assertEqual(r.events, [])
                self.assertFalse(r.authorized)

    def test_owner_guard_exception_is_redacted(self):
        r = Recorder()
        def guard(*args):
            raise RuntimeError("Bearer TOKEN SHOULD NEVER BE ECHOED")
        with self.assertRaisesRegex(PipelineError, "verification failed") as err:
            workflow(r, guard=guard).authorize(PID, GEN, QUOTE, "1.25")
        self.assertNotIn("TOKEN", str(err.exception))
        self.assertEqual(r.events, [])

    def test_explicit_owner_consent_is_idempotent_without_provider_dispatch(self):
        r = Recorder(); approvals = []
        def guard(action, pid, obj):
            approvals.append((action, pid, obj))
            return True
        w = workflow(r, guard=guard)
        first = w.authorize(PID, GEN, QUOTE, "1.25000000")
        second = w.authorize(PID, GEN, QUOTE, "1.25000000")
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(first["status"], "queued")
        self.assertEqual(approvals, [
            ("search_planner.generation.authorize", PID, GEN),
            ("search_planner.generation.authorize", PID, GEN),
        ])
        self.assertEqual(len(r.events), 2)
        self.assertTrue(all(e[0] == "authorize" for e in r.events))

    def test_revoke_requires_independent_owner_action(self):
        r = Recorder(); approvals = []
        def guard(action, pid, obj):
            approvals.append((action, pid, obj))
            return action.endswith(".revoke")
        w = workflow(r, guard=guard)
        with self.assertRaises(PipelineError):
            w.authorize(PID, GEN, QUOTE, "1.25000000")
        x = w.revoke(PID, CONSENT)
        self.assertTrue(x["revoked"])
        self.assertEqual(approvals[-1], ("search_planner.generation.revoke", PID, CONSENT))
        self.assertEqual(r.events, [("revoke", PID, CONSENT)])

    def test_invalid_amount_and_id_do_not_reach_guard_or_ledger(self):
        r = Recorder(); seen = []
        w = workflow(r, guard=lambda *a: seen.append(a) or True)
        for amount in ("NaN", "0", "-1", True, 0.1):
            with self.subTest(amount=amount), self.assertRaises(PipelineError):
                w.authorize(PID, GEN, QUOTE, amount)
        with self.assertRaises(PipelineError):
            w.authorize(PID, GEN, "bad", "1.25")
        with self.assertRaises(PipelineError):
            w.revoke("not-project", CONSENT)
        self.assertEqual(seen, [])
        self.assertEqual(r.events, [])

    def test_facade_exposes_full_preflight_but_never_spends_without_gate(self):
        r = Recorder()
        facade = SearchPlannerService(generation_workflow=workflow(r))
        config_out = facade.register_generation_config(PID, config())
        self.assertEqual(config_out["config_id"], CFG)
        plan = facade.plan_generation(PID, PUB, CFG, KEY)
        self.assertEqual(plan["status"], "awaiting_authorization")
        quote = facade.quote_generation(PID, GEN)
        self.assertEqual(quote["quote_id"], QUOTE)
        self.assertIsNone(facade.generation_budget(PID, GEN)["consent"])
        with self.assertRaises(PipelineError):
            facade.authorize_generation(PID, GEN, QUOTE, "1.25")
        self.assertEqual([x[0] for x in r.events],
                         ["register", "plan", "quote", "budget"])
        with self.assertRaises(PipelineError):
            SearchPlannerService(generation_workflow={})

    def test_owner_gate_may_not_be_untyped_or_false(self):
        for invalid in (False, "yes", {}, 1):
            with self.subTest(invalid=invalid):
                with self.assertRaises(PipelineError):
                    GenerationWorkflow(owner_action_guard=invalid)

    def test_no_provider_or_collector_calls_in_generation_workflow(self):
        from pathlib import Path
        module = Path(__file__).resolve().parents[1] / "intelligence" / "search_planner" / "generation_workflow.py"
        source = module.read_text(encoding="utf-8")
        for forbidden in ("requests.post(", "urlopen(", "openrouter.ai", "intelligence.collector", "record_dispatch_intent("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
