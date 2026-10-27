# Search Planner — SP-16 release gate (living checklist)

**Branch:** `feature/search-planner-full-integration` · **PR:** #34 (Draft) · **updated:** 2026-10-27. This document distinguishes **code written** from **tests actually executed**. Never merge to `main` automatically.

## Current decision

**NOT RELEASE-READY.** Individual SP01–15 software slices have been authored, but real external OpenRouter billing and Collector readiness remain unavailable, Deep C9 V2 still depends on a separate module, and exact-head full QA is not confirmed. This page is a gate, not a fabricated green report.

## SP-16A — PostgreSQL 17 fresh + upgrade + invariants

- [x] ~~Added real PostgreSQL 17 service job `sp-16a-postgres17-real` in `.github/workflows/sp-stage01-fast.yml`. Database is **disposable** `cm_sp16_test`, with the exact major-version assertion and `CM_SP16_RUN_PG17=1`.~~
- [x] ~~CI runs `python -m intelligence.migrate` twice against fresh PostgreSQL 17: complete fresh installation, then migrator checksum validation and no-op replay. Uses the **existing production migrator**, never a copied custom SQL path.~~
- [x] ~~Added `tests/test_search_planner_stage16_pg17.py` with real PostgreSQL driver, external connections, eight-worker lease race, exact monotonic fence takeover, stale owner rejection, cross-project lease isolation, immutable invoice evidence, global invoice line/response uniqueness, project FK and negative bill rejection.~~
- [x] ~~Added `tests/test_search_planner_stage16_upgrade_pg17.py` for a separate empty disposable sibling DB `cm_sp16_upgrade`: apply original migrations through 056, verify the 057/058 rows absent, upgrade with the same migrator, verify unchanged historical SHA-256, assert two new tables exist, then demand a no-op replay.~~
- [ ] Exact-head GitHub Actions **successful** real PostgreSQL 17 fresh/upgrade runs. Do not turn this green while a run is QUEUED, missing or failed.
- [ ] Production-like crash/SIGKILL/WAL recovery, concurrent SP12 QV outbox ACK+inbox chunk transactions, real SP13 paid request idempotency, and real invoice state mutation with complete upstream fixtures.
- [ ] PostgreSQL 17 real compatibility with Collector's own registered source-policy migrations under least-privilege read-only role.

### Safe local reproduction

Run only against a newly provisioned dedicated disposable PostgreSQL 17 database called **`cm_sp16_test`**. The schema/upgrade tests refuse any other database name. The upgrade test creates a separate `cm_sp16_upgrade` database and will fail if that database already exists rather than overwrite it.

In Bash:

```bash
export CM_DB_ENABLED=1
export CM_TEST_PG_DSN='postgresql://<ci_user>:<ci_password>@127.0.0.1:5432/cm_sp16_test'
export CM_DB_DSN="$CM_TEST_PG_DSN"
export CM_SP16_RUN_PG17=1

python -m pip install -r intelligence/requirements.txt
python -m intelligence.migrate
python -m intelligence.migrate
python -m unittest discover -s tests -p 'test_search_planner_stage16_pg17.py' -v
python -m unittest discover -s tests -p 'test_search_planner_stage16_upgrade_pg17.py' -v
```

Use PowerShell `$env:VARIABLE=...` equivalents on Windows; use a disposable DB only. These tests do not call OpenRouter, DTK, Apify, YouTube, Instagram or Collector search. If `CM_SP16_RUN_PG17` is not set, the DB suite explicitly reports SKIP for ordinary unit runs; in the dedicated CI job it is set and absence of the database is a **failure**, never SKIP.

## SP-16B — PostgreSQL17 SP12 QV→SP inbox transaction (code only)

- [x] ~~Added `test_search_planner_stage16_inbox_pg17.py`: actual `055_sp_handoff_inbox.sql` in isolated disposable PG17 schema plus real production `ingest_qv_to_sp_inbox`/replay/READ API over psycopg.~~
- [x] ~~Added tests for 117→50/50/17 chunks and 117 source/hypothesis lineage hashes, 0→durable no_work, cross-project reject, forged QV pointer reject, two rollback failpoints around QV ACK, eight-concurrent-ingestor exactly-once ACK, immutable chunk triggers, absent Collector awaiting without fake receipts.~~
- [x] ~~CI `sp-16a-postgres17-real` now executes the SP-16B test file after migration fresh/replay and 056→latest upgrade.~~
- [ ] Actual current-head PostgreSQL17 test success, not a queued or in-progress workflow.
- [ ] **Authentic** end-to-end C9→real semantic QV→SP across real upstream tables (rather than deterministic trusted QV fixture); provider budget HTTP idempotency, SIGKILL/WAL and real browser/security smoke remain open.

## SP-16C — outstanding system E2E

- [ ] Approved C9 (and authentic Deep V2 once present) → generation plan/quote/owner consent → mocked single paid completion → durable SQG candidates → local production semantic QV → immutable outbox → HELD task plan → SP durable inbox. No fake provider results counted as production facts.
- [ ] Re-run real SP-16B PostgreSQL17 QV inbox/failpoint/117→50+50+17 suite to confirmed SUCCESS on exact final HEAD; cover process SIGKILL/WAL separately (test-only synchronous rollback is not SIGKILL).
- [ ] Absent Collector → honest `awaiting_collector`; trusted mocked *durable* receipt used only in offline E2E; real Collector acceptance remains external until separately audited.
- [ ] Full Windows+Linux app/browser/CLI smoke, owner token lifecycle, query/Host/origin ACL, local security, 0 paid POST unless separate explicit consent.
- [ ] Independent real provider billing evidence reconciler plus independently authenticated Collector readiness/receipts; never invent readiness, USD 0 invoice or provider acceptance.

## Pull request release checks

- [ ] Latest `main` comparison for merge conflicts and missing upstream migrations.
- [ ] One exact PR head SHA used for **all** required GitHub workflows, Windows and Linux; no stale-commit greens.
- [ ] All mandatory checks show explicit **success** (queued/pending are NOT PASS). If runner infrastructure remains blocked, state the blocker and do not merge.
- [ ] User explicitly approves merge of PR #34 into `main`. Existing Draft status is not approval.

**Next stage:** SP-16B real PostgreSQL QV→SP atomic transaction + replay/crash and full system mock-provider E2E, followed by SP-16C operational Windows/Linux gating.
