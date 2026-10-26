# GB-MOD-03 — Group B standalone runtime schema

**Branch:** `feature/group-b-full-module`; **migration:** `intelligence/migrations/055_group_b_runtime.sql`.

## Ordering and ownership

Baseline `main` contains migrations through `053_ke_c_qualified_execution.sql`. The independent Group A branch already uses `054_group_a_module_core.sql`, so Group B owns **055**. Migration 055 does **not** depend on 054 and operates against baseline main. Verify new migration filenames at merge time, because peer branches evolve independently.

The existing installer `intelligence.migrate.apply_migrations` sorts filenames, uses a PostgreSQL transaction advisory lock and stores SQL SHA-256 in `schema_migrations`. Previously deployed migrations cannot be edited. The original `005_content_features_b.sql` remains unchanged (Git blob `e203aab384af65ae11e9960860bee4715bb21e4d`).

## Owned runtime tables

| Table | Purpose | Integrity |
|---|---|---|
| `group_b_inbox` | Durable QDP event envelope/pins | `project_id` FK, canonical `video_id/source_id`, unique `(project_id,event_key)`, event and preparation SHA |
| `group_b_runs` | Versioned B run state, lease and exact A/analysis pointers | Composite FK matching project/inbox/video/source, per-project idempotency, source and A lineage trigger |
| `group_b_block_states` | Per-run B1–B8 readiness/execution states | Defaults to `not_run`; composite project/run FK |
| `group_b_model_quotes` | Immutable provider-pricing quote snapshot | Exact hashes, nonzero quote, expiry and scoped FK |
| `group_b_model_approvals` | Immutable approval record | Nonzero authorized amount; scoped quote/run FK |
| `group_b_model_attempts` | Ledger for individual attempts | Scoped approval/quote/run FK, distinct `uncertain` charge state, unique request key |
| `group_b_review_events` | Append-only human decision revisions | Revision unique per project/run; immutable trigger |
| `group_b_outbox` | Versioned delivery queue for Analytics | Scoped run FK, deduplicated event key, delivery state |

The records are infrastructure, **not** proof a paid model has been authorized, content observed, or B1–B8 produced. No worker, provider, HTTP route, QDP ACK or Analytics publication runs in stage 03. Group A rows do not have a reliable project foreign key in schema 005; verifying real project ownership of those observations is specifically delegated to the future QDP/A/media adapters. The 005 content classification tables are referenced, never recreated.

Quotes and human approvals/reviews have immutable database guards. Actual consent authentication, quote expiration validation, atomic reservation, charge uncertainty recovery, worker lease fencing and state transition guards belong to stages GB-MOD-10/11/21–24 and must remain fail-closed until implemented.

## Schema checker and tests

`intelligence/group_b/repository.py` provides **read-only** `inspect_runtime_schema(connection)` and `require_runtime_schema(connection)`. It verifies 8 tables, installed SQL SHA-256 and legacy 005 blob. Schema installation is explicitly handled by the existing migration CLI rather than running automatically during import.

`tests/test_group_b_db.py` includes pure checks and optional disposable PG17 tests: full migration apply, second-application no-op, FK project scoping, replay collision, mismatched Group A pointer, immutable history, SHA tamper rejection and concurrent repeat application.

```bash
python -m unittest discover -s tests -p 'test_group_b_db.py' -v
CM_TEST_PG_DSN='postgresql://gb_test:gb_test@127.0.0.1:5432/cm_group_b_test' python -m unittest discover -s tests -p 'test_group_b_db.py' -v
```

Without `CM_TEST_PG_DSN`, PG tests are SKIP (not PASS). Tests reject databases without “test” or “_ci” in the database name. The concurrency test exercises repeat application to an installed catalog, **not** first-install race across fresh databases. Full first-install concurrency and production rollout remain release-gate evidence requirements.

**Next:** GB-MOD-04 — concrete QDP inbox adapter and ACK strictly after durable receipt/job commit.
