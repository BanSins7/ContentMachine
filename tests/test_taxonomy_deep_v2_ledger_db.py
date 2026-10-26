"""RTB-FIX-02-B2/C SQL ledger integration, PostgreSQL17 only.

Synthetic local B9 approved snapshot, DB-only Opus catalog; no provider calls.
Tests enforce quote/consent/run immutability and stage reservation guards.
"""
from __future__ import annotations

from decimal import Decimal
import unittest
from uuid import uuid4

from psycopg.errors import RaiseException, CheckViolation, UniqueViolation
from psycopg.types.json import Jsonb
from test_ke_stage02_intake_db import DSN
from test_ke_stage13_knowledge_snapshot import Stage13PG

from intelligence.taxonomy_builder.deep_v2_binding_repository import RTBDeepV2BindingRepository


@unittest.skipUnless(DSN, "PostgreSQL17 required for RTB Deep V2 ledger")
class DeepV2LedgerPG(unittest.TestCase):
    def setUp(self):
        self.base = Stage13PG(methodName="runTest")
        self.base.setUp()
        sid = self.base._published()
        repo = self.base._repo()
        self.snapshot = repo.publish(self.base.pid, sid, self.base.reviewer)
        repo.prepare_c_handoff(self.base.pid, sid, self.base.reviewer)
        self.factory = self.base._factory()
        self.binding = RTBDeepV2BindingRepository(
            connection_factory=self.factory).pin(
                self.base.pid, self.snapshot["snapshot_id"], self.base.reviewer)
        with self.factory() as conn:
            self.catalog_id = conn.execute(
                """INSERT INTO tb_model_snapshots(
                    model_id,catalog_sha256,capabilities,pricing)
                   VALUES(%s,%s,%s,%s) RETURNING id""",
                ("anthropic/claude-opus-5.5", "d"*64,
                 Jsonb({"model_id": "anthropic/claude-opus-5.5",
                        "text_output": True, "supports_structured_outputs": True,
                        "supported_parameters": ["structured_outputs"],
                        "context_length": 200000, "max_output_tokens": 12000}),
                 Jsonb({"prompt": "0.000005", "completion": "0.000025",
                        "request": "0.001", "internal_reasoning": "0.000002"})),
            ).fetchone()[0]
        self.model = {
            "model_id": "anthropic/claude-opus-5.5",
            "catalog_sha256": "d"*64,
            "catalog_version_id": self.catalog_id,
            "capabilities": {"model_id": "anthropic/claude-opus-5.5",
                             "text_output": True,
                             "supports_structured_outputs": True,
                             "supported_parameters": ["structured_outputs"],
                             "context_length": 200000, "max_output_tokens": 12000},
            "pricing": {"prompt": "0.000005", "completion": "0.000025",
                        "request": "0.001", "internal_reasoning": "0.000002"},
        }

    def tearDown(self):
        self.base.doCleanups()

    def _quote(self, *, binding_sha=None, expiry_sql=None):
        sha = binding_sha or self.binding["research_input_sha256"]
        binding_id = self.binding["binding_id"]
        qsha, request_sha, prompt_sha = "e"*64, "f"*64, "c"*64
        body = {
            "schema_version": "rtb-deep-v2-quote/v1",
            "binding_id": binding_id,
            "research_input_sha256": sha,
            "quote_sha256": qsha,
            "request_sha256": request_sha,
            "prompt_sha256": prompt_sha,
            "prompt_version": "deep-v2/1",
            "model_id": "anthropic/claude-opus-5.5",
            "catalog_sha256": "d"*64,
            "quoted_usd": "3",
        }
        with self.factory() as conn:
            sql = """INSERT INTO tb_deep_v2_quotes(
                project_id,binding_id,research_input_sha256,quote_sha256,
                request_sha256,prompt_sha256,prompt_version,model_id,
                catalog_version_id,catalog_sha256,model_snapshot,stage_config,
                quote,required_pass1_reservation_usd,required_pass2_reservation_usd,
                quoted_usd,created_by"""
            params = [
                self.base.pid, binding_id, sha, qsha, request_sha, prompt_sha,
                "deep-v2/1", "anthropic/claude-opus-5.5",
                self.catalog_id, "d"*64, Jsonb(self.model),
                Jsonb({"prompt_version":"deep-v2/1",
                       "model_id":"anthropic/claude-opus-5.5"}),
                Jsonb(body), Decimal("1"), Decimal("2"), Decimal("3"),
                self.base.reviewer,
            ]
            if expiry_sql:
                sql += ",expires_at) VALUES("+",".join(["%s"]*17)+","+expiry_sql+") RETURNING id"
            else:
                sql += ") VALUES("+",".join(["%s"]*17)+") RETURNING id"
            return str(conn.execute(sql, tuple(params)).fetchone()[0])

    def _consent(self, quote_id, *, amount="3", acknowledged="e"*64):
        with self.factory() as conn:
            return str(conn.execute(
                """INSERT INTO tb_deep_v2_consents(
                     project_id,quote_id,authorized_usd,approved_by,
                     acknowledged_quote_sha256)
                   VALUES(%s,%s,%s,%s,%s) RETURNING id""",
                (self.base.pid, quote_id, Decimal(amount),
                 self.base.reviewer, acknowledged),
            ).fetchone()[0])

    def _run(self, quote_id, consent_id, *, budget="3", key=None):
        with self.factory() as conn:
            return str(conn.execute(
                """INSERT INTO tb_deep_v2_runs(
                     project_id,binding_id,quote_id,consent_id,
                     idempotency_key,start_request_sha256,authorized_usd)
                   VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (self.base.pid, self.binding["binding_id"], quote_id,
                 consent_id, key or uuid4().hex, "b"*64, Decimal(budget)),
            ).fetchone()[0])

    def test_quote_requires_real_B9_binding_and_verified_catalog(self):
        quote = self._quote()
        self.assertTrue(quote)
        with self.assertRaises((RaiseException, CheckViolation)):
            self._quote(binding_sha="0"*64)
        with self.assertRaises(RaiseException):
            self._quote(expiry_sql="clock_timestamp()-interval '1 second'")

    def test_quote_is_immutable(self):
        quote = self._quote()
        with self.assertRaises(RaiseException):
            with self.factory() as conn:
                conn.execute("DELETE FROM tb_deep_v2_quotes WHERE id=%s", (quote,))
        with self.assertRaises(RaiseException):
            with self.factory() as conn:
                conn.execute("UPDATE tb_deep_v2_quotes SET quoted_usd=4 WHERE id=%s", (quote,))

    def test_consent_uses_exact_quote_budget_and_sha(self):
        quote = self._quote()
        with self.assertRaises(RaiseException):
            self._consent(quote, amount="2.99")
        with self.assertRaises(RaiseException):
            self._consent(quote, acknowledged="0"*64)
        valid = self._consent(quote, amount="3")
        self.assertTrue(valid)
        with self.assertRaises(RaiseException):
            with self.factory() as conn:
                conn.execute("DELETE FROM tb_deep_v2_consents WHERE id=%s", (valid,))

    def test_quote_has_no_implicit_consent_or_run(self):
        quote = self._quote()
        with self.factory() as conn:
            count = conn.execute(
                """SELECT
                 (SELECT count(*) FROM tb_deep_v2_consents WHERE project_id=%s),
                 (SELECT count(*) FROM tb_deep_v2_runs WHERE project_id=%s),
                 (SELECT count(*) FROM tb_deep_v2_attempts WHERE project_id=%s)""",
                (self.base.pid,self.base.pid,self.base.pid),
            ).fetchone()
        self.assertEqual(count,(0,0,0))

    def test_revocation_blocks_run_start(self):
        quote = self._quote()
        consent = self._consent(quote)
        with self.factory() as conn:
            conn.execute(
                """INSERT INTO tb_deep_v2_revocations(
                  project_id,consent_id,revoked_by) VALUES(%s,%s,%s)""",
                (self.base.pid, consent, self.base.reviewer),
            )
        with self.assertRaises(RaiseException):
            self._run(quote,consent)

    def test_run_identity_locked_and_cannot_reuse_consent(self):
        quote = self._quote()
        consent = self._consent(quote)
        run = self._run(quote,consent)
        with self.assertRaises((UniqueViolation,RaiseException)):
            self._run(quote,consent,key="other-key-0001")
        with self.assertRaises(RaiseException):
            with self.factory() as conn:
                conn.execute("UPDATE tb_deep_v2_runs SET authorized_usd=99 WHERE id=%s", (run,))
        with self.assertRaises(RaiseException):
            self._run(quote,consent,budget="1")

    def test_attempt_requires_reserved_running_stage_and_single_slot(self):
        quote = self._quote()
        consent = self._consent(quote)
        run = self._run(quote,consent)
        query = """INSERT INTO tb_deep_v2_attempts(
                    project_id,run_id,stage,status,request_sha256,
                    prompt_sha256,reservation_usd)
                   VALUES(%s,%s,%s,'reserved',%s,%s,%s) RETURNING id"""
        args = (self.base.pid,run,"deep_first","f"*64,"c"*64,Decimal("1"))
        with self.assertRaises(RaiseException):
            with self.factory() as conn:
                conn.execute(query,args)
        with self.factory() as conn:
            conn.execute("""UPDATE tb_deep_v2_runs
                        SET status='pass1_running',reserved_usd=1
                        WHERE project_id=%s AND id=%s""",(self.base.pid,run))
            attempt = conn.execute(query,args).fetchone()[0]
        with self.assertRaises((RaiseException,UniqueViolation)):
            with self.factory() as conn:
                conn.execute(query,args)
        with self.factory() as conn:
            conn.execute("""UPDATE tb_deep_v2_attempts
                            SET status='uncertain',error_code='unknown_spend',
                                finished_at=clock_timestamp()
                            WHERE id=%s""",(attempt,))
            conn.execute("""UPDATE tb_deep_v2_runs SET status='uncertain'
                            WHERE project_id=%s AND id=%s""",(self.base.pid,run))
        with self.assertRaises(RaiseException):
            with self.factory() as conn:
                conn.execute("""UPDATE tb_deep_v2_attempts SET status='completed'
                             WHERE id=%s""",(attempt,))
        with self.factory() as conn:
            record = conn.execute(
                "SELECT reserved_usd,status FROM tb_deep_v2_runs WHERE id=%s",(run,)
            ).fetchone()
        self.assertEqual(record,(Decimal("1"),"uncertain"))

    def test_budget_cannot_exceed_consent(self):
        q=self._quote()
        c=self._consent(q)
        r=self._run(q,c)
        with self.assertRaises(CheckViolation):
            with self.factory() as conn:
                conn.execute("""UPDATE tb_deep_v2_runs
                                SET reserved_usd=4 WHERE id=%s""",(r,))


if __name__ == "__main__":
    unittest.main()
