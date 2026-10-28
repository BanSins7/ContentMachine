# PIE-MOD-04 Verification Instructions

Run unit/static on Windows/Linux:

```bash
python -m unittest discover -s tests -p 'test_pie_*.py' -v
python -m compileall -q intelligence/patterns_insights
```

PostgreSQL integration runs **only against two disposable PostgreSQL 17 databases**, and requires `psycopg` v3 installed. Never set these DSNs to real ContentMachine data. The test suite explicitly rejects database names that do not contain `test` and several known production database names; the operator must still verify isolation.

```bash
export CM_TEST_PG_DSN='postgresql://pie_test:pie_test@localhost:5432/pie_test'
export CM_TEST_PG_UPGRADE_DSN='postgresql://pie_test:pie_test@localhost:5432/pie_upgrade_test'
python -m unittest discover -s tests -p 'test_pie_db_schema.py' -v
```

When `CM_TEST_PG_DSN` is absent, PostgreSQL tests are intentionally **SKIPPED**, not PASSED. GitHub workflow `PIE standalone module` configures PostgreSQL 17 for the dedicated `postgres17-schema` job. A queued workflow is not a verification of SQL execution. The database changes use the existing versioned/checksummed core migrator without modifying it.
