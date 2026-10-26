"""M03 real PostgreSQL17 runtime schema preflight (read only, disposable DB)."""
from __future__ import annotations

import os
import unittest

from intelligence import migrate
from intelligence.relevance.config import load_settings
from intelligence.relevance.health import check_read_only_schema, diagnose


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"), "disposable PostgreSQL required")
class RuntimePreflightPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.dsn=os.environ["CM_TEST_PG_DSN"]
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            migrate.apply_migrations(conn)

    def test_actual_migrated_schema_is_readable_and_probe_does_not_write(self):
        import psycopg
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            before=conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
            self.assertTrue(check_read_only_schema(conn))
            after=conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
            self.assertEqual(before,after)

    def test_enabled_health_not_available_without_explicit_project_policy(self):
        import psycopg
        settings=load_settings({
            "CM_SRE_ENABLED":"1",
            "CM_SRE_OFFLINE_WORKER":"1",
            "CM_DB_ENABLED":"1",
            "CM_DB_DSN":self.dsn,
        })
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            result=diagnose(settings,dependency_probe=lambda _:True,
                            db_probe=lambda:check_read_only_schema(conn))
        self.assertEqual(result["status"],"blocked_policy")
        self.assertFalse(result["worker_enabled"])
        self.assertFalse(result["can_dispatch_provider"])


if __name__=="__main__":
    unittest.main()
