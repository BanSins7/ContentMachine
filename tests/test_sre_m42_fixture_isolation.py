"""M42 negative security and durable replay tests (no DB, provider or network)."""
import socket
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

from intelligence.relevance.m42_fixture_isolation import (
    M42FixtureBlocked, PROVENANCE,
    require_disposable_m42 as real_require_disposable_m42,
    record_simulated_step as real_record_simulated_step,
)

BASE_ENV = {"CM_SRE_M42_TEST_ONLY": "1", "CM_SRE_M42_DISPOSABLE": "1"}
SHA = "a" * 64

# Test-only wrappers configure real process variables; the runtime public API
# deliberately does NOT accept a caller-injected environment override.
def require_disposable_m42(conn, *, environ=None):
    with patch.dict("os.environ", {} if environ is None else environ, clear=True):
        return real_require_disposable_m42(conn)


def record_simulated_step(conn, project_id, run_id, step, source_sha256, *, environ=None):
    with patch.dict("os.environ", {} if environ is None else environ, clear=True):
        return real_record_simulated_step(conn, project_id, run_id, step, source_sha256)



class Result:
    def __init__(self, one=None, many=None):
        self.one, self.many = one, many

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class FakePG:
    autocommit = True

    def __init__(self, *, identity=None, marker=True):
        self.identity = identity or (
            "sre_m42_unit", "contentmachine-sre-m42-isolated",
            170005, False, False, False, False, False,
        )
        self.marker = marker
        self.rows = {}
        self.queries = []
        self.transaction_count = 0

    @contextmanager
    def transaction(self):
        self.transaction_count += 1
        yield

    def execute(self, query, params=None):
        self.queries.append((query, params))
        if "FROM pg_roles" in query:
            return Result(one=self.identity)
        if "to_regclass" in query:
            return Result(one=("sre_m42_disposable_marker",) if self.marker else (None,))
        if "FROM public.sre_m42_disposable_marker" in query:
            return Result(many=[("fixture_database_only",)] if self.marker else [])
        if query.startswith("CREATE TABLE"):
            return Result()
        if "INSERT INTO public.sre_m42_fixture_journal" in query:
            project, run, step, source, blob, digest = params
            key = (project, run, step, source)
            if key in self.rows:
                return Result(one=None)
            import json
            self.rows[key] = (json.loads(blob), digest, PROVENANCE)
            return Result(one=(digest,))
        if "FROM public.sre_m42_fixture_journal" in query:
            return Result(one=self.rows.get(tuple(params)))
        raise AssertionError("unexpected SQL")


class M42FixtureIsolationTest(unittest.TestCase):
    def setUp(self):
        self.db = FakePG()
        self.run = str(uuid4())

    def test_default_deny_no_flags_and_no_database_mutation(self):
        for env in ({}, {"CM_SRE_M42_TEST_ONLY": "1"},
                    {"CM_SRE_M42_DISPOSABLE": "1"}):
            with self.subTest(env=env), self.assertRaises(M42FixtureBlocked):
                record_simulated_step(self.db, 1, self.run, "fake_receipt", SHA,
                                      environ=env)
        self.assertEqual(self.db.queries, [])

    def test_prod_live_qdp_provider_configuration_denied_before_sql(self):
        for key in (
            "CM_SRE_LIVE_CLASSIFICATION", "CM_SRE_QDP_EVENT_DELIVERY",
            "CM_SRE_COLLECTOR_EVENT_INGEST", "CM_SRE_OPENROUTER_API_KEY",
            "OPENROUTER_API_KEY", "SRE_ENABLE_LIVE_PAID",
        ):
            with self.subTest(key=key), self.assertRaises(M42FixtureBlocked):
                record_simulated_step(self.db, 1, self.run, "fake_opus_response",
                                      SHA, environ={**BASE_ENV, key: "1"})
        self.assertEqual(self.db.queries, [])

    def test_production_db_and_privileged_role_denied(self):
        original = self.db.identity
        bad = [("contentmachine", *original[1:]),
               ("sre_m42_unit", "app-prod", *original[2:]),
               (*original[:2], 160000, *original[3:]),
               (*original[:4], True, *original[5:]),
               (*original[:5], True, *original[6:]),
               (*original[:6], True, *original[7:]),
               (*original[:7], True)]
        for identity in bad:
            db = FakePG(identity=identity)
            with self.subTest(identity=identity), self.assertRaises(M42FixtureBlocked):
                require_disposable_m42(db, environ=BASE_ENV)
            self.assertEqual(db.transaction_count, 0)

    def test_missing_marker_or_uncommitted_connection_denied(self):
        with self.assertRaises(M42FixtureBlocked):
            require_disposable_m42(FakePG(marker=False), environ=BASE_ENV)
        self.db.autocommit = False
        with self.assertRaises(M42FixtureBlocked):
            require_disposable_m42(self.db, environ=BASE_ENV)

    def test_durable_idempotent_replay_and_only_shadow_table(self):
        a = record_simulated_step(self.db, 31, self.run,
                                  "fake_receipt", SHA, environ=BASE_ENV)
        b = record_simulated_step(self.db, 31, self.run,
                                  "fake_receipt", SHA, environ=BASE_ENV)
        self.assertFalse(a["replayed"])
        self.assertTrue(b["replayed"])
        self.assertEqual(a["payload_sha256"], b["payload_sha256"])
        self.assertEqual(a["provenance"], PROVENANCE)
        self.assertFalse(a["exportable_to_live_consumers"])
        self.assertEqual(a["paid_calls"], 0)
        self.assertEqual(self.db.transaction_count, 2)
        self.assertEqual(len(self.db.rows), 1)
        self.assertEqual(self.db.rows[(31, self.run, "fake_receipt", SHA)][0]
                         ["financial_settlement_authorized"], False)
        mutation_sql = "\n".join(q for q, _ in self.db.queries if
                                 ("CREATE TABLE" in q or "INSERT INTO" in q))
        self.assertNotIn("INSERT INTO sre_live_", mutation_sql)
        self.assertNotIn("sre_live_opus_receipts", mutation_sql)
        self.assertNotIn("sre_live_result_outbox", mutation_sql)
        self.assertIn("sre_m42_fixture_journal", mutation_sql)

    def test_conflict_or_external_tamper_is_not_replayed(self):
        record_simulated_step(self.db, 31, self.run,
                              "fake_receipt", SHA, environ=BASE_ENV)
        key = (31, self.run, "fake_receipt", SHA)
        saved = self.db.rows[key]
        self.db.rows[key] = (saved[0], "f" * 64, PROVENANCE)
        with self.assertRaisesRegex(M42FixtureBlocked, "replay mismatch"):
            record_simulated_step(self.db, 31, self.run, "fake_receipt",
                                  SHA, environ=BASE_ENV)

    def test_invalid_stage_project_run_and_sha_no_writes(self):
        cases = [(0, self.run, "fake_receipt", SHA),
                 (1, "not-a-uuid", "fake_receipt", SHA),
                 (1, self.run, "production_receipt", SHA),
                 (1, self.run, "fake_receipt", "xyz"),
                 (True, self.run, "fake_receipt", SHA),
                 (1, self.run.upper(), "fake_receipt", SHA)]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(M42FixtureBlocked):
                record_simulated_step(self.db, *args, environ=BASE_ENV)
        self.assertEqual(self.db.transaction_count, 0)

    def test_stages_are_distinct_and_no_fake_export_api(self):
        for stage in ("approved_c9_read", "authorized_d1_read", "local_rank_all",
                      "fake_quote", "fake_consent", "fake_reserve",
                      "fake_dispatch_intent", "fake_opus_response",
                      "fake_receipt", "simulated_verified_pairs",
                      "simulated_human_review", "synthetic_outbox", "rights_revoked"):
            result = record_simulated_step(self.db, 7, self.run, stage,
                                           SHA, environ=BASE_ENV)
            self.assertFalse(result["exportable_to_live_consumers"])
        self.assertEqual(len(self.db.rows), 13)

    def test_no_network_even_when_fake_opus_stage_used(self):
        with patch.object(socket.socket, "connect", side_effect=AssertionError("NETWORK")):
            result = record_simulated_step(self.db, 7, self.run,
                                           "fake_opus_response", SHA,
                                           environ=BASE_ENV)
        self.assertEqual(result["paid_calls"], 0)


if __name__ == "__main__":
    unittest.main()
