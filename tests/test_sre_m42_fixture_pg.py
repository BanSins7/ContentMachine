"""M42 isolated PG17 smoke; no main migrations and no network/provider calls.

Run only against an explicitly disposable CI postgres service. The test creates
its own named DB and nonprivileged role and drops both after the session.
This is NOT the full C9->D1->M30 E2E or a migration-062 workaround.
"""
import os
import unittest
from uuid import uuid4

from intelligence.relevance.m42_fixture_isolation import (
    PROVENANCE, record_simulated_step,
)


@unittest.skipUnless(os.environ.get("CM_SRE_M42_PG_CI") == "1",
                     "M42 isolated CI postgres not explicitly configured")
class IsolatedPG17Test(unittest.TestCase):
    def test_actual_pg17_shadow_persistence_and_no_live_export(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo

        admin_dsn = os.environ["CM_SRE_M42_ADMIN_DSN"]
        if ("CM_SRE_OPENROUTER_API_KEY" in os.environ or
                "OPENROUTER_API_KEY" in os.environ):
            self.fail("provider credentials forbidden in M42 simulation")
        db = "sre_m42_ci"
        role = "sre_m42_ci_runner"
        password = os.environ["CM_SRE_M42_RUNNER_PASSWORD"]
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            identity = admin.execute(
                "SELECT current_database(), current_setting('server_version_num')::int"
            ).fetchone()
            self.assertEqual(identity[0], "contentmachine")
            self.assertGreaterEqual(identity[1], 170000)
            self.assertIn(admin.info.host, ("127.0.0.1", "localhost"))
            self.assertIsNone(admin.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (db,)
            ).fetchone())
            self.assertIsNone(admin.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)
            ).fetchone())
            admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)))
            try:
                admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(db), sql.Identifier(role)))
                try:
                    connection = make_conninfo(
                        admin_dsn, dbname=db, user=role, password=password,
                        application_name="contentmachine-sre-m42-isolated")
                    with psycopg.connect(connection, autocommit=True) as fixture:
                        # In the fresh disposable fixture database, forensics
                        # must read (not create) schema_migrations and flag 062
                        # before any migration runner can claim readiness.
                        from intelligence.relevance.m42_migration_062_audit import (
                            inspect_062_installed,
                        )
                        forensic = inspect_062_installed(fixture)
                        self.assertEqual(forensic["ledger_state"], "ledger_absent")
                        self.assertFalse(forensic["safe_to_apply"])
                        self.assertTrue(forensic["corrupted_evidence_constraint"])
                        self.assertFalse(forensic["schema_changed"])
                        self.assertIsNone(fixture.execute(
                            "SELECT to_regclass('public.schema_migrations')"
                        ).fetchone()[0])
                        fixture.execute("""CREATE TABLE public.sre_m42_disposable_marker (
                          marker text PRIMARY KEY CHECK(marker='fixture_database_only'))""")
                        fixture.execute("""INSERT INTO public.sre_m42_disposable_marker
                          (marker) VALUES('fixture_database_only')""")
                        run = str(uuid4())
                        project = "c" * 32
                        for stage in ("approved_c9_read", "authorized_d1_read",
                                      "local_rank_all", "fake_opus_response",
                                      "fake_receipt", "simulated_verified_pairs",
                                      "simulated_human_review", "synthetic_outbox"):
                            first = record_simulated_step(
                                fixture, project, run, stage, "a" * 64)
                            again = record_simulated_step(
                                fixture, project, run, stage, "a" * 64)
                            self.assertFalse(first["replayed"])
                            self.assertTrue(again["replayed"])
                        persisted = fixture.execute("""SELECT count(*),
                          bool_and(provenance=%s),bool_and(payload->>'exportable_to_live_consumers'='false')
                          FROM public.sre_m42_fixture_journal""", (PROVENANCE,)).fetchone()
                        self.assertEqual(persisted, (8, True, True))
                        self.assertIsNone(fixture.execute(
                            "SELECT to_regclass('public.sre_live_result_outbox')"
                        ).fetchone()[0])
                        # M20/M27/M21 technical protocol is executed for real,
                        # but the C9+content fixture itself is deliberately fake.
                        # Real approved C9/D1 PostgreSQL flow remains M42 BLOCKED.
                        import json
                        from intelligence.relevance.m42_fake_opus_shadow import (
                            verify_and_journal_fake_opus,
                        )
                        from test_sre_opus_transport import M27OpusTransport, RUN
                        from test_sre_opus_evidence_verifier import make_reply

                        M27OpusTransport.setUpClass()
                        m20 = M27OpusTransport.plan
                        envelopes = [
                            M27OpusTransport().envelope(i)
                            for i in range(m20["request_count"])
                        ]
                        scripted = {
                            request["request_sha256"]: json.dumps(
                                make_reply(request), ensure_ascii=False,
                            )
                            for request in m20["requests"]
                        }
                        verified = verify_and_journal_fake_opus(
                            fixture, RUN, m20, envelopes, scripted
                        )
                        repeat = verify_and_journal_fake_opus(
                            fixture, RUN, m20, envelopes, scripted
                        )
                        self.assertEqual(verified["validated_categories"], 3)
                        self.assertEqual(verified["validated_requests"], 3)
                        self.assertTrue(repeat["replayed"])
                        self.assertFalse(verified["provider_receipts_authenticated"])
                        self.assertFalse(verified["exportable_to_live_consumers"])
                        self.assertEqual(verified["paid_calls"], 0)
                        stored = fixture.execute("""SELECT count(*),
                          bool_and((payload->>'paid_calls')::int=0),
                          bool_and(payload->>'exportable_to_live_consumers'='false')
                          FROM public.sre_m42_fixture_journal""").fetchone()
                        self.assertEqual(stored, (10, True, True))
                        self.assertIsNone(fixture.execute(
                            "SELECT to_regclass('public.sre_live_opus_receipts')"
                        ).fetchone()[0])
                        self.assertIsNone(fixture.execute(
                            "SELECT to_regclass('public.sre_live_result_outbox')"
                        ).fetchone()[0])

                finally:
                    admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(db)))
            finally:
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(
                    sql.Identifier(role)))


if __name__ == "__main__":
    unittest.main()
