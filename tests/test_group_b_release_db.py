"""GB-MOD-28 PostgreSQL17 clean/upgrade and immutability release gate.

This is a disposable synthetic-database TEST. No provider, QDP/Group A
source authorization, reviewer ID or production model quality is implied.
"""
from __future__ import annotations
from pathlib import Path
import hashlib
import os
import unittest

from intelligence.migrate import apply_migrations, MIGRATIONS


def disposable():
    from psycopg.conninfo import conninfo_to_dict
    dsn=os.environ["CM_TEST_PG_DSN"]
    name=conninfo_to_dict(dsn).get("dbname","").lower()
    if "test" not in name and "_ci" not in name:
        raise RuntimeError("QA release refuses non-test database")
    return dsn


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"Requires disposable PG17")
class TestB28ReleasePG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg=psycopg
        cls.dsn=disposable()

    def test_exact_postgres17_and_frozen_migration_checksums(self):
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(int(conn.execute(
                "SHOW server_version_num").fetchone()[0])//10000,17)
            apply_migrations(conn)
            rows=dict(conn.execute(
                "SELECT version,sha256 FROM schema_migrations").fetchall())
            for name in (
                "005_content_features_b.sql","055_group_b_runtime.sql",
                "056_group_b_qdp_receipts.sql","057_group_b_budget_ledger.sql",
                "058_group_b_worker_jobs.sql","059_group_b_observability.sql",
            ):
                with self.subTest(migration=name):
                    path=MIGRATIONS/name
                    expected=hashlib.sha256(path.read_bytes()).hexdigest()
                    self.assertIn(name,rows)
                    self.assertEqual(rows[name],expected)

    def test_full_upgrade_idempotent_no_drift_or_schema_rewrite(self):
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(apply_migrations(conn),[])
            self.assertEqual(apply_migrations(conn),[])

    def test_tampered_005_checksum_rejected_with_transaction_rollback(self):
        with self.psycopg.connect(self.dsn) as conn:
            original=conn.execute(
                """SELECT sha256 FROM schema_migrations
                   WHERE version='005_content_features_b.sql'""",
            ).fetchone()[0]
            # A savepoint isolates the intentionally forged migration
            # checksum and rolls back the test tampering immediately.
            with self.assertRaises(RuntimeError):
                with conn.transaction():
                    conn.execute(
                        """UPDATE schema_migrations SET sha256=%s
                           WHERE version='005_content_features_b.sql'""",
                        ("0"*64,)
                    )
                    apply_migrations(conn)
            actual=conn.execute(
                """SELECT sha256 FROM schema_migrations
                   WHERE version='005_content_features_b.sql'""",
            ).fetchone()[0]
            self.assertEqual(original,actual)

    def test_telemetry_trigger_and_index_installed(self):
        with self.psycopg.connect(self.dsn) as conn:
            apply_migrations(conn)
            triggers=conn.execute(
                """SELECT tgname FROM pg_trigger
                   WHERE tgrelid='group_b_execution_metrics'::regclass
                     AND NOT tgisinternal""",
            ).fetchall()
            names={x[0] for x in triggers}
            self.assertTrue({
                "trg_group_b_execution_metrics_immutable",
                "trg_group_b_execution_metric_identity",
            }.issubset(names))
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM group_b_execution_metrics").fetchone()[0],0)


if __name__=="__main__":
    unittest.main()
