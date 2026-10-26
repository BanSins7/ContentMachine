"""PostgreSQL17 RTB Deep V2 quote/consent/start acceptance; NO paid provider call."""
from __future__ import annotations

import unittest
from uuid import uuid4

from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb
from test_ke_stage02_intake_db import DSN
from test_taxonomy_deep_v2_ledger_db import DeepV2LedgerPG

from intelligence.taxonomy_builder.deep_v2_quote_repository import (
    RTBDeepV2QuoteRepository,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError


@unittest.skipUnless(DSN, "PostgreSQL17 required for RTB Deep V2 quote service")
class DeepV2QuotePG(unittest.TestCase):
    def setUp(self):
        self.fx = DeepV2LedgerPG(methodName="runTest")
        self.fx.setUp()
        self.repository = RTBDeepV2QuoteRepository(
            connection_factory=self.fx.factory)

    def tearDown(self):
        self.fx.tearDown()

    @property
    def pid(self):
        return self.fx.base.pid

    @property
    def reviewer(self):
        return self.fx.base.reviewer

    @property
    def bid(self):
        return self.fx.binding["binding_id"]

    def _quote(self):
        return self.repository.quote(self.pid, self.bid, self.reviewer)

    def _consent(self, q):
        return self.repository.authorize(
            self.pid, q["quote_id"], self.reviewer, q["required_usd"],
            acknowledged_quote_sha256=q["quote_sha256"])

    def test_real_quote_is_replayable_and_starts_no_paid_activity(self):
        q = self._quote()
        self.assertTrue(q["consent_required"])
        self.assertEqual(q["provider_calls"], 0)
        self.assertFalse(q["authorized"])
        with self.fx.factory() as conn:
            row = conn.execute(
                """SELECT quote,request_sha256,prompt_sha256,quoted_usd
                   FROM tb_deep_v2_quotes WHERE project_id=%s AND id=%s""",
                (self.pid, q["quote_id"]),
            ).fetchone()
            counts = conn.execute(
                """SELECT
                     (SELECT count(*) FROM tb_deep_v2_consents WHERE project_id=%s),
                     (SELECT count(*) FROM tb_deep_v2_runs WHERE project_id=%s),
                     (SELECT count(*) FROM tb_deep_v2_attempts WHERE project_id=%s)""",
                (self.pid, self.pid, self.pid),
            ).fetchone()
        self.assertEqual(counts, (0, 0, 0))
        self.assertEqual(row[0]["request_sha256"], row[1])
        self.assertEqual(row[0]["prompt_sha256"], row[2])
        self.assertEqual(row[0]["quoted_usd"], str(row[3]))
        self.assertTrue(row[0]["pass1_priced_exact_payload"])
        self.assertTrue(row[0]["pass2_priced_conservative_bounded_capacity"])

    def test_requires_correct_quote_hash_and_sufficient_budget(self):
        q = self._quote()
        with self.assertRaisesRegex(PipelineError, "quote_consent_hash_mismatch"):
            self.repository.authorize(
                self.pid, q["quote_id"], self.reviewer,
                q["required_usd"], acknowledged_quote_sha256="0" * 64)
        with self.assertRaises(PipelineError):
            self.repository.authorize(
                self.pid, q["quote_id"], self.reviewer,
                "0.000001", acknowledged_quote_sha256=q["quote_sha256"])
        valid = self._consent(q)
        self.assertTrue(valid["created"])
        again = self._consent(q)
        self.assertEqual(valid["consent_id"], again["consent_id"])
        self.assertFalse(again["created"])

    def test_exact_binding_quote_and_consent_launches_unpaid_pending_run(self):
        q = self._quote()
        c = self._consent(q)
        key = uuid4().hex
        first = self.repository.start(
            self.pid, q["quote_id"], c["consent_id"], self.reviewer, key)
        duplicate = self.repository.start(
            self.pid, q["quote_id"], c["consent_id"], self.reviewer, key)
        self.assertTrue(first["created"])
        self.assertFalse(duplicate["created"])
        self.assertEqual(first["run_id"], duplicate["run_id"])
        self.assertEqual(first["status"], "pass1_pending")
        self.assertEqual(first["provider_calls"], 0)
        with self.fx.factory() as conn:
            rows = conn.execute(
                """SELECT status,reserved_usd,spent_usd,authorized_usd
                   FROM tb_deep_v2_runs WHERE project_id=%s AND id=%s""",
                (self.pid, first["run_id"]),
            ).fetchone()
            attempts = conn.execute(
                "SELECT count(*) FROM tb_deep_v2_attempts WHERE run_id=%s",
                (first["run_id"],),
            ).fetchone()[0]
        self.assertEqual(rows[0], "pass1_pending")
        self.assertEqual(rows[1], 0)
        self.assertEqual(rows[2], 0)
        self.assertGreater(rows[3], 0)
        self.assertEqual(attempts, 0)

    def test_revocation_blocks_new_run_and_consumed_consent(self):
        q = self._quote()
        c = self._consent(q)
        self.repository.revoke(self.pid, c["consent_id"], self.reviewer)
        with self.assertRaises(PipelineError):
            self.repository.start(
                self.pid, q["quote_id"], c["consent_id"],
                self.reviewer, uuid4().hex)
        with self.assertRaises(PipelineError):
            self._consent(q)

    def test_idempotency_conflict_refuses_different_consent(self):
        q = self._quote()
        c = self._consent(q)
        key = uuid4().hex
        self.repository.start(
            self.pid, q["quote_id"], c["consent_id"], self.reviewer, key)
        next_quote = self._quote()
        next_consent = self._consent(next_quote)
        with self.assertRaisesRegex(PipelineError, "start_idempotency_conflict"):
            self.repository.start(
                self.pid, next_quote["quote_id"], next_consent["consent_id"],
                self.reviewer, key)
        with self.assertRaisesRegex(PipelineError, "consent_already_used"):
            self.repository.start(
                self.pid, q["quote_id"], c["consent_id"],
                self.reviewer, uuid4().hex)

    def test_requote_required_after_catalog_price_changes(self):
        quote = self._quote()
        self.assertIsNotNone(quote["quote_id"])
        with self.fx.factory() as conn:
            new_id = conn.execute(
                """INSERT INTO tb_model_snapshots(
                    model_id,catalog_sha256,capabilities,pricing)
                   VALUES(%s,%s,%s,%s) RETURNING id""",
                ("anthropic/claude-opus-5.5", uuid4().hex + uuid4().hex,
                 Jsonb(self.fx.model["capabilities"]),
                 Jsonb({**self.fx.model["pricing"], "prompt": "0.000009"})),
            ).fetchone()[0]
            refresh = conn.execute(
                """INSERT INTO tb_catalog_refreshes(models_present)
                   VALUES(%s) RETURNING id""",
                (Jsonb(["anthropic/claude-opus-5.5"]),),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO tb_catalog_refresh_models(
                     refresh_id,model_id,snapshot_id) VALUES(%s,%s,%s)""",
                (refresh, "anthropic/claude-opus-5.5", new_id),
            )
        with self.assertRaises(PipelineError):
            self._consent(quote)

    def test_unauthorized_actor_cannot_quote(self):
        with self.assertRaises(ValueError):
            self.repository.quote(self.pid, self.bid, "not-authorized")


if __name__ == "__main__":
    unittest.main()
