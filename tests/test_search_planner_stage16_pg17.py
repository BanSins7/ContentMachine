"""SP-16A REAL PostgreSQL 17 gates: migration replay, fenced lease and invoice DDL.

Must run against an explicitly named DISPOSABLE database, never production.
Without CM_SP16_RUN_PG17=1 this test suite skips; the dedicated CI job sets
the flag and checks the exact database name/engine, preventing a false green
via an absent PostgreSQL service.

The isolated fixture runs the *actual* 057/058 SQL migrations in its own
ephemeral schema, on minimal upstream FK stubs. The separate migration test
verifies the complete project's real fresh/replay migrator in public schema.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import unittest
from uuid import uuid4

from intelligence.search_planner.lease_ops import (
    acquire_inbox_lease, heartbeat_inbox_lease, release_inbox_lease,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

MIGRATIONS = Path(__file__).resolve().parents[1] / "intelligence" / "migrations"
APPROVED_DB = "cm_sp16_test"
ACCOUNT = "b" * 64


class SP16ARealPostgres17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("CM_SP16_RUN_PG17") != "1":
            raise unittest.SkipTest("SP16 real PostgreSQL gate not explicitly enabled")
        dsn = os.getenv("CM_TEST_PG_DSN", "")
        if not dsn:
            raise AssertionError("SP16 CI requires CM_TEST_PG_DSN; never silently skip DB")
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        cls.pg = psycopg
        cls.dsn = dsn
        parsed = conninfo_to_dict(dsn)
        if parsed.get("dbname") != APPROVED_DB:
            raise AssertionError(
                "SP16 refuses any database except the dedicated cm_sp16_test"
            )
        cls.schema = "sp16_" + uuid4().hex[:20]
        cls.pid_a = uuid4().hex
        cls.pid_b = uuid4().hex
        cls.gen_a, cls.gen_b = uuid4(), uuid4()
        cls.created_schema = False
        # Full migrations are executed separately by CI before this suite.
        # Fail loudly rather than silently replacing failures with a fake DB.
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            version = int(conn.execute("SHOW server_version_num").fetchone()[0])
            if not 170000 <= version < 180000:
                raise AssertionError("SP16 requires real PostgreSQL major version 17")
            versions = {
                row[0] for row in conn.execute(
                    "SELECT version FROM public.schema_migrations"
                ).fetchall()
            }
            if not {"057_sp_supervisor_leases.sql",
                    "058_sp_invoice_reconciliations.sql"}.issubset(versions):
                raise AssertionError(
                    "SP16 full migrator must install genuine 057 and 058 first"
                )
            conn.execute(f"CREATE SCHEMA {cls.schema}")
            cls.created_schema = True
        try:
            with cls.connect() as conn:
                conn.execute("""CREATE TABLE project_registry (
                                    id TEXT PRIMARY KEY
                                )""")
                conn.execute("""CREATE TABLE sqg_generations (
                                    id UUID PRIMARY KEY,
                                    project_id TEXT NOT NULL REFERENCES project_registry(id),
                                    UNIQUE(id,project_id)
                                )""")
                conn.execute("""CREATE TABLE sqg_attempts (id UUID PRIMARY KEY)""")
                conn.execute("""CREATE FUNCTION sqg_reject_immutable_write()
                                RETURNS trigger LANGUAGE plpgsql AS $$
                                BEGIN
                                    RAISE EXCEPTION 'Immutable evidence';
                                END $$""")
                for name in ("057_sp_supervisor_leases.sql",
                             "058_sp_invoice_reconciliations.sql"):
                    conn.execute((MIGRATIONS / name).read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO project_registry(id) VALUES (%s),(%s)",
                    (cls.pid_a, cls.pid_b),
                )
                conn.execute(
                    """INSERT INTO sqg_generations(id,project_id)
                       VALUES (%s,%s),(%s,%s)""",
                    (cls.gen_a, cls.pid_a, cls.gen_b, cls.pid_b),
                )
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def connect(cls):
        return cls.pg.connect(
            cls.dsn, connect_timeout=5,
            options=f"-c search_path={cls.schema} -c statement_timeout=10000",
        )

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "created_schema", False):
            # Safe only inside the explicitly verified disposable SP16 DB.
            with cls.pg.connect(cls.dsn, connect_timeout=5) as conn:
                conn.execute(f"DROP SCHEMA IF EXISTS {cls.schema} CASCADE")
            cls.created_schema = False

    def test_a_full_migration_checksum_replay_is_noop(self):
        from intelligence.migrate import apply_migrations
        with self.pg.connect(self.dsn, connect_timeout=5) as conn:
            installed = apply_migrations(conn)
            self.assertEqual(installed, [])
            count = conn.execute(
                """SELECT COUNT(*) FROM public.schema_migrations
                   WHERE version IN (%s,%s)""",
                ("057_sp_supervisor_leases.sql", "058_sp_invoice_reconciliations.sql"),
            ).fetchone()[0]
            self.assertEqual(count, 2)

    def test_b_atomic_eight_worker_race_only_one_lease_wins(self):
        pid = self.pid_a
        owners = [str(uuid4()) for _ in range(8)]
        def claim(owner):
            return acquire_inbox_lease(
                pid, owner, lease_seconds=60, connect=self.connect,
            )
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(claim, owners))
        wins = [r for r in results if r is not None]
        self.assertEqual(len(wins), 1)
        self.assertEqual(wins[0]["fence_generation"], 1)
        owner = wins[0]["owner_id"]
        self.assertTrue(heartbeat_inbox_lease(
            pid, owner, 1, lease_seconds=60, connect=self.connect
        ))
        with self.assertRaises(PipelineError):
            heartbeat_inbox_lease(
                pid, str(uuid4()), 1, connect=self.connect
            )
        self.assertTrue(release_inbox_lease(pid, owner, 1, connect=self.connect))
        replacement = acquire_inbox_lease(
            pid, str(uuid4()), lease_seconds=60, connect=self.connect
        )
        self.assertEqual(replacement["fence_generation"], 2)
        self.assertFalse(release_inbox_lease(
            pid, owner, 1, connect=self.connect
        ))
        with self.assertRaises(PipelineError):
            heartbeat_inbox_lease(pid, owner, 1, connect=self.connect)
        self.assertTrue(release_inbox_lease(
            pid, replacement["owner_id"], 2, connect=self.connect
        ))

    def test_c_cross_project_lease_isolation(self):
        first = acquire_inbox_lease(
            self.pid_b, str(uuid4()), connect=self.connect
        )
        self.assertEqual(first["fence_generation"], 1)
        self.assertIsNone(acquire_inbox_lease(
            self.pid_b, str(uuid4()), connect=self.connect
        ))
        with self.assertRaises(PipelineError):
            heartbeat_inbox_lease(
                self.pid_a, first["owner_id"], 1, connect=self.connect
            )
        self.assertTrue(release_inbox_lease(
            self.pid_b, first["owner_id"], 1, connect=self.connect
        ))

    def _insert_invoice(self, *, pid, gid, aid, invoice_line, response_id):
        with self.connect() as conn:
            conn.execute("INSERT INTO sqg_attempts(id) VALUES (%s)", (aid,))
            conn.execute(
                """INSERT INTO sp_invoice_reconciliations (
                   project_id,generation_id,attempt_id,provider_response_id,
                   request_sha256,invoice_line_id,invoice_sha256,
                   provider_account_sha256,charge_usd,verification_method,
                   provider_verified_at,decision,owner_action
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,
                     'account_billing_export_exact_request',now(),
                     'reconciled_failed',
                     'search_planner.generation.reconcile_invoice')""",
                (pid, gid, aid, response_id, "a" * 64, invoice_line,
                 "c" * 64, ACCOUNT, "0.12500000"),
            )

    def test_d_invoice_is_immutable_and_double_charge_globally_blocked(self):
        first = uuid4()
        second = uuid4()
        self._insert_invoice(
            pid=self.pid_a, gid=self.gen_a, aid=first,
            invoice_line="line-real-pg-a", response_id="gen-real-pg-a",
        )
        # The same account may serve both projects; scope alone cannot
        # prevent billing twice. Actual 058 unique constraints must reject it.
        with self.assertRaises(self.pg.Error):
            self._insert_invoice(
                pid=self.pid_b, gid=self.gen_b, aid=second,
                invoice_line="line-real-pg-a", response_id="gen-real-pg-b",
            )
        with self.assertRaises(self.pg.Error):
            with self.connect() as conn:
                conn.execute(
                    """UPDATE sp_invoice_reconciliations SET charge_usd=0
                       WHERE attempt_id=%s""", (first,),
                )
        with self.assertRaises(self.pg.Error):
            with self.connect() as conn:
                conn.execute(
                    "DELETE FROM sp_invoice_reconciliations WHERE attempt_id=%s",
                    (first,),
                )
        with self.connect() as conn:
            stored = conn.execute(
                """SELECT charge_usd FROM sp_invoice_reconciliations
                   WHERE attempt_id=%s""", (first,),
            ).fetchone()
        self.assertEqual(str(stored[0]), "0.12500000")

    def test_e_one_provider_response_cannot_pay_two_attempts(self):
        first, second = uuid4(), uuid4()
        self._insert_invoice(
            pid=self.pid_a, gid=self.gen_a, aid=first,
            invoice_line="line-real-pg-c", response_id="gen-real-pg-c",
        )
        with self.assertRaises(self.pg.Error):
            self._insert_invoice(
                pid=self.pid_b, gid=self.gen_b, aid=second,
                invoice_line="line-real-pg-d", response_id="gen-real-pg-c",
            )

    def test_f_invoice_project_fk_and_decimal_precision_enforced(self):
        with self.assertRaises(self.pg.Error):
            self._insert_invoice(
                pid=self.pid_a, gid=self.gen_b, aid=uuid4(),
                invoice_line="line-real-pg-foreign", response_id="gen-foreign",
            )
        # NUMERIC(18,8) intentionally quantizes; malformed values rejected
        # at typed Python boundary in SP15D, while DB rejects negatives.
        negative_attempt = uuid4()
        with self.connect() as conn:
            conn.execute("INSERT INTO sqg_attempts(id) VALUES (%s)", (negative_attempt,))
        with self.assertRaises(self.pg.Error):
            with self.connect() as conn:
                conn.execute(
                    """INSERT INTO sp_invoice_reconciliations(
                      project_id,generation_id,attempt_id,provider_response_id,
                      request_sha256,invoice_line_id,invoice_sha256,
                      provider_account_sha256,charge_usd,verification_method,
                      provider_verified_at,decision,owner_action)
                       VALUES (%s,%s,%s,'gen-real-pg-neg',%s,'line-real-pg-neg',
                         %s,%s,-0.00000001,
                         'account_billing_export_exact_request',now(),
                         'reconciled_failed',
                         'search_planner.generation.reconcile_invoice')""",
                    (self.pid_a, self.gen_a, negative_attempt, "a"*64, "c"*64, ACCOUNT),
                )


if __name__ == "__main__":
    unittest.main()
