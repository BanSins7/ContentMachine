"""Stage07: zero-real-network facts-v3 execution and audited PostgreSQL ledger."""
from __future__ import annotations

import json
import unittest
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb

from test_ke_stage02_intake_db import ReadOnlyIntakePG, DSN
from test_ke_stage04_full_pack import CFG
from test_ke_stage05_multi_pack import MULTI
from test_ke_stage06_request_db import Stage06RequestPG
from intelligence.knowledge_extraction.fact_execution import (
    FactExecutionRepository, _provider_response,
)
from intelligence.knowledge_extraction.fact_request_repository import FactRequestRepository
from intelligence.knowledge_extraction.full_pack_repository import FullContextPackRepository
from intelligence.knowledge_extraction.multi_pack_repository import MultiPackRepository
from intelligence.knowledge_extraction.intake import IntakeError


def successful_sender(body):
    """Simulate an OpenRouter response using the ACTUAL pinned JSONL request."""
    content = body["messages"][1]["content"]
    row = json.loads(content.split("FACTS_V3_SOURCE_JSONL_BEGIN\n", 1)[1].splitlines()[1])
    quote = row["text"][:min(20, len(row["text"]))]
    proposal = {
        "kind": "external_claim", "text": "Claim explicitly in source",
        "source_chunk_id": row.get("chunk_id", row.get("fragment_id")),
        "source_quote": quote, "quote_start": 0, "quote_end": len(quote),
    }
    return {
        "id": "fake-provider-id", "model": "xiaomi/mimo-v2.6-flash",
        "usage": {"cost": 0.0005, "prompt_tokens": 900, "completion_tokens": 160},
        "choices": [{"finish_reason": "stop",
                     "message": {"content": json.dumps({"facts": [proposal]}, ensure_ascii=False)}}],
    }


class Stage07TransportTests(unittest.TestCase):
    def test_no_api_call_without_sender_or_key(self):
        with self.assertRaises(IntakeError) as captured:
            _provider_response({"model": "test"})
        self.assertEqual(captured.exception.code, "facts_openrouter_key_missing")

    def test_usage_and_content_are_returned_only_after_response(self):
        result = _provider_response({"messages": [{"role": "system"}, {"role": "user",
                          "content": 'FACTS_V3_SOURCE_JSONL_BEGIN\n{}\n{"text":"abc","fragment_id":"a"}'}]},
                                   sender=lambda _: {
             "id":"test-id","model":"xiaomi/mimo-v2.6-flash",
             "usage":{"cost":"0.00042","prompt_tokens":12,"completion_tokens":4},
             "choices":[{"finish_reason":"stop","message":{"content":'{"facts":[]}'}}]})
        self.assertEqual(result["charged"], Decimal("0.000420000000"))
        self.assertEqual(result["content"], '{"facts":[]}')
        self.assertEqual(result["usage"]["prompt_tokens"], 12)

    def test_unknown_spend_never_treated_as_free(self):
        for bad in ({"usage":{}}, {"usage":{"cost":0}}, ["not a response"],
                    {"usage":{"cost":"0.1","prompt_tokens":True,"completion_tokens":2}}):
            with self.subTest(bad=bad):
                with self.assertRaises(IntakeError):
                    _provider_response({}, sender=lambda _: bad)


@unittest.skipUnless(DSN, "PostgreSQL17 required for Stage07 live SQL ledger QA")
class Stage07FactPG(ReadOnlyIntakePG):
    _catalog = Stage06RequestPG._catalog
    _bundle = Stage06RequestPG._bundle
    _factory = Stage06RequestPG._factory

    def setUp(self):
        super().setUp()
        self._catalog(window=100000)

    def _ready(self, *, multi=False):
        bundle_id = self._bundle(large=multi)
        factory = self._factory()
        if multi:
            self._catalog(window=16000)
            pack = MultiPackRepository(connection_factory=factory).prepare(
                self.pid, bundle_id, MULTI)
            self.assertEqual(pack["status"], "fits_multi")
            batch_id = FactRequestRepository(connection_factory=factory).prepare(
                self.pid, multi_plan_id=pack["plan_id"])["batch_id"]
        else:
            pack = FullContextPackRepository(connection_factory=factory).prepare(
                self.pid, bundle_id, CFG)
            self.assertEqual(pack["status"], "fits")
            batch_id = FactRequestRepository(connection_factory=factory).prepare(
                self.pid, full_pack_id=pack["pack_id"])["batch_id"]
        repo = FactExecutionRepository(connection_factory=factory)
        quote = repo.quote(self.pid, batch_id)
        consent = repo.authorize(self.pid, quote["quote_id"], quote["quoted_usd"],
                                 approved_by="test-user")
        run = repo.start(self.pid, quote["quote_id"], consent["consent_id"],
                         "ke-stage07-"+uuid4().hex)
        return repo, batch_id, quote, consent, run

    def test_exact_quote_consent_one_run_and_review_pending(self):
        repo, batch_id, quote, consent, run = self._ready()
        self.assertFalse(run["provider_calls"])
        with self.psycopg.connect(DSN) as c:
            count = c.execute(
                "SELECT count(*) FROM knowledge_fact_execution_attempts WHERE project_id=%s",
                (self.pid,),
            ).fetchone()[0]
        self.assertEqual(count, 0)
        def asserted_sender(body):
            with self.psycopg.connect(DSN) as conn:
                attempt = conn.execute(
                    """SELECT status FROM knowledge_fact_execution_attempts
                       WHERE project_id=%s AND run_id=%s""",
                    (self.pid, run["run_id"]),
                ).fetchone()[0]
            self.assertEqual(attempt, "reserved")
            self.assertEqual(body["response_format"]["type"], "json_schema")
            self.assertFalse(body["provider"]["allow_fallbacks"])
            return successful_sender(body)
        result = repo.advance(self.pid, run["run_id"], sender=asserted_sender)
        self.assertEqual(result["status"], "ready_for_normalization")
        self.assertEqual(result["candidate_count"], 1)
        inspected = repo.inspect(self.pid, run["run_id"], include_drafts=True)
        self.assertEqual(inspected["spent_usd"], "0.000500000000")
        self.assertEqual(inspected["reserved_usd"], "0E-12")
        self.assertFalse(inspected["approved"])
        self.assertEqual(inspected["attempts"][0]["draft"]["candidates"][0]["grounding_status"],
                         "pending_b5")
        with self.assertRaises(IntakeError):
            repo.advance(self.pid, run["run_id"], sender=asserted_sender)
        same = repo.start(self.pid, quote["quote_id"], consent["consent_id"],
                          self._existing_idempotency(run["run_id"]))
        self.assertFalse(same["created"])

    def _existing_idempotency(self, run_id):
        with self.psycopg.connect(DSN) as conn:
            return conn.execute(
                "SELECT idempotency_key FROM knowledge_fact_execution_runs WHERE id=%s",
                (run_id,),
            ).fetchone()[0]

    def test_uncertain_transport_stops_forever_and_reservation_remains(self):
        repo, _, _, _, run = self._ready()
        def timeout(_):
            raise TimeoutError("possibly billed")
        result = repo.advance(self.pid, run["run_id"], sender=timeout)
        self.assertEqual(result["status"], "uncertain")
        inspected = repo.inspect(self.pid, run["run_id"])
        self.assertEqual(inspected["attempts"][0]["status"], "uncertain")
        self.assertEqual(Decimal(inspected["spent_usd"]), 0)
        self.assertGreater(Decimal(inspected["reserved_usd"]), 0)
        with self.assertRaises(IntakeError):
            repo.advance(self.pid, run["run_id"], sender=timeout)

    def test_invalid_paid_content_is_billed_and_never_approved(self):
        repo, _, _, _, run = self._ready()
        def invalid(body):
            r = successful_sender(body)
            r["choices"][0]["message"]["content"] = '{"facts":[{"not_allowed":true}]}'
            return r
        result = repo.advance(self.pid, run["run_id"], sender=invalid)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["charged_usd"], "0.000500000000")
        inspected = repo.inspect(self.pid, run["run_id"])
        self.assertEqual(inspected["attempts"][0]["status"], "error")
        self.assertEqual(inspected["attempts"][0]["error_code"], "facts_provider_draft_invalid")
        self.assertIsNone(inspected["attempts"][0]["candidate_count"])

    def test_multi_pack_sequential_and_no_second_spending_consent(self):
        repo, batch_id, quote, consent, run = self._ready(multi=True)
        info = repo.requests.materialize(self.pid, batch_id)
        self.assertGreater(len(info["parts"]), 1)
        for i in range(len(info["parts"])):
            result = repo.advance(self.pid, run["run_id"], sender=successful_sender)
            self.assertEqual(result["pack_index"], i)
        self.assertEqual(result["status"], "ready_for_normalization")
        self.assertEqual(repo.inspect(self.pid, run["run_id"])["provider_calls"],
                         len(info["parts"]))
        with self.assertRaises(IntakeError):
            repo.start(self.pid, quote["quote_id"], consent["consent_id"],
                       "ke-stage07-another-idempotency-key")

    def test_revocation_blocks_next_pack_and_no_paid_call(self):
        repo, _, _, consent, run = self._ready()
        repo.revoke(self.pid, consent["consent_id"], revoked_by="test-user")
        invoked = []
        result = repo.advance(self.pid, run["run_id"],
                              sender=lambda body: invoked.append(body))
        self.assertEqual(result["status"], "revoked")
        self.assertEqual(invoked, [])

    def test_missing_key_before_reservation_and_foreign_scope(self):
        repo, _, _, _, run = self._ready()
        with patch("intelligence.knowledge_extraction.fact_execution.resolve_key",
                   return_value=None):
            with self.assertRaises(IntakeError):
                repo.advance(self.pid, run["run_id"])
        self.assertEqual(repo.inspect(self.pid, run["run_id"])["attempts"], [])
        with self.assertRaises(IntakeError):
            repo.inspect(self.other, run["run_id"])

    def test_single_consent_and_immutable_quote(self):
        repo, batch_id, quote, consent, run = self._ready()
        # Once an explicit consent is consumed, a subsequent intentional
        # run requires a brand-new quote/consent even within the TTL.
        next_quote = repo.quote(self.pid, batch_id)
        self.assertTrue(next_quote["created"])
        self.assertNotEqual(next_quote["quote_id"], quote["quote_id"])
        with self.assertRaises(IntakeError):
            repo.authorize(self.pid, quote["quote_id"], quote["quoted_usd"],
                           approved_by="test-user")
        with self.assertRaises(RaiseException):
            with self.psycopg.connect(DSN) as conn:
                conn.execute("DELETE FROM knowledge_fact_cost_quotes WHERE id=%s",
                             (quote["quote_id"],))
        with self.assertRaises(RaiseException):
            with self.psycopg.connect(DSN) as conn:
                conn.execute("UPDATE knowledge_fact_spending_consents SET authorized_usd=1 WHERE id=%s",
                             (consent["consent_id"],))

    def test_profile_change_after_quote_blocks_dispatch(self):
        repo, _, _, _, run = self._ready()
        with self.psycopg.connect(DSN) as conn:
            conn.execute(
                """INSERT INTO project_profile_versions(
                     project_id,revision,schema_version,profile_hash,profile)
                   VALUES(%s,2,1,%s,%s)""",
                (self.pid, "9"*64, Jsonb({"goal": "changed"})),
            )
            conn.execute(
                """UPDATE project_registry SET profile_revision=2,profile_hash=%s
                   WHERE id=%s""",
                ("9"*64, self.pid),
            )
        called = []
        with self.assertRaises(IntakeError):
            repo.advance(self.pid, run["run_id"],
                         sender=lambda body: called.append(body))
        self.assertEqual(called, [])
        self.assertEqual(repo.inspect(self.pid, run["run_id"])["attempts"], [])


    def test_known_overcharge_is_recorded_and_pauses_remaining_packs(self):
        repo, _, _, _, run = self._ready()
        def overcharged(body):
            result = successful_sender(body)
            result["usage"]["cost"] = "90"
            return result
        result = repo.advance(self.pid, run["run_id"], sender=overcharged)
        self.assertEqual(result["status"], "paused_budget")
        self.assertEqual(result["charged_usd"], "90.000000000000")
        entry = repo.inspect(self.pid, run["run_id"])
        self.assertEqual(entry["spent_usd"], "90.000000000000")
        self.assertEqual(entry["reserved_usd"], "0E-12")
        self.assertEqual(entry["attempts"][0]["status"], "error")
        with self.assertRaises(IntakeError):
            repo.advance(self.pid, run["run_id"], sender=overcharged)

    def test_known_billed_wrong_model_rejected(self):
        repo, _, _, _, run = self._ready()
        def wrong_model(body):
            result = successful_sender(body)
            result["model"] = "other/provider-model"
            return result
        result = repo.advance(self.pid, run["run_id"], sender=wrong_model)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["charged_usd"], "0.000500000000")
        self.assertEqual(result["error_code"], "facts_provider_model_mismatch")
        self.assertFalse(repo.inspect(self.pid, run["run_id"])["approved"])

    def test_missing_cost_usage_preserves_unknown_billing_reservation(self):
        repo, _, _, _, run = self._ready()
        def missing_usage(body):
            result = successful_sender(body)
            result["usage"].pop("cost")
            return result
        outcome = repo.advance(self.pid, run["run_id"], sender=missing_usage)
        self.assertEqual(outcome["status"], "uncertain")
        self.assertEqual(outcome["error_code"], "facts_provider_usage_uncertain")
        entry = repo.inspect(self.pid, run["run_id"])
        self.assertGreater(Decimal(entry["reserved_usd"]), 0)
        self.assertEqual(Decimal(entry["spent_usd"]), 0)
        self.assertIsNone(entry["attempts"][0]["charged_usd"])



if __name__ == "__main__":
    unittest.main()
