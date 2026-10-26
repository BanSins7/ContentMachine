# SRE M11 — Core LIVE PostgreSQL Tables / Immutable Identity Pins

**2026-10-26 · Branch:** `feature/sre-production-v1-20261023` · [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22)

**Implementation:** committed. **Validation:** unit and PostgreSQL17 tests committed; GitHub Actions still QUEUED / success NOT confirmed. Do not mark M11 QA complete until test runs finish on the final SHA. No paid requests, worker startup or main merge.

## Additive migration

New file: `intelligence/migrations/054_sre_live_core.sql`, next after the frozen main migration `053_ke_c_qualified_execution.sql`. **Historical SRE migrations `016_sre_d1_intake_rights.sql` and `017_sre_offline_run_engine.sql` are unchanged**, and all other main migrations remain untouched.

Three new domain-specific tables, initially empty:

| Table | Identity / pinned evidence | Mutable runtime state |
|---|---|---|
| `sre_live_runs` | UUID run + project, exact C9 publication/taxonomy version, taxonomy/pipeline/request SHA, immutable JSON manifest and SHA, per-project idempotency key, `execution_mode='live'` | typed M10 run state; item counters, cancellation, fencing generation and lease |
| `sre_live_items` | project/run/video, exact S3 version and content SHA, actual D1 source/policy/discovery/observation, revision, origin JSON, expected leaf manifest+context SHA | typed item state, retry count, lease token/expiration and monotonically increasing generation |
| `sre_live_batches` | UUID batch, exact project/run, ordinal, immutable batch JSON manifest/input SHA | typed batch state, lease token/expiration and generation |

The new tables do **not** store model verdicts, paid dispatch attempts, receipts or billing. Existing `sre_offline_runs`, `sre_offline_items`, `sre_classification_runs` and `sre_live_classification_results` are left intact. No fake S4 run is promoted to LIVE. Creating the schema does not run any classifier.

## SQL-level authority and immutable pins

- `sre_live_runs` includes composite FK `(project_id,publication_id,taxonomy_version_id)` to the actual `tb_sre_taxonomy_links` and `(pipeline_config_id,project_id)` to SRE pipeline config. Another project's C9 publication/pipeline cannot be attached.
- `sre_live_items` references `(run_id,project_id)` and the exact composite `(id,project_id,video_id,source_id,source_policy_version_id,content_sha256)` in `sre_content_versions`; legacy content with missing SHA cannot satisfy this pinned reference. Source discovery must belong to the same project.
- An INSERT trigger verifies that the content row has a real S3 intake fingerprint, exactly the declared D1 provenance JSON, discovery ID, metadata observation and content revision; it checks the original discovery actually belongs to the pinned video/source/project.
- Three UPDATE triggers forbid mutation of input identities, SHA and JSON manifests. Lease generations cannot decrease or skip values; acquiring/changing a non-null worker token requires an increment. Explicit no-delete triggers prevent accidental SQL hard-delete of LIVE lineage; project retention and controlled purge require a later dedicated design (M34).
- The schema explicitly revokes PUBLIC table/sequence permissions, but this **does not replace** trusted host authentication/authorization and does not claim to protect against DB owners who can disable triggers. No row-level security policy is assumed.
- Counts and enum checks cannot replace future transactional, audited M12/M13/M15/M30 service logic. A SHA column alone is not proof that caller-supplied JSON was canonicalized; future run planning must compute/verify SHA from the exact C9/D1 context, and current M08 read gates remain mandatory before any external transmission or result publication.

## Fresh/upgrade compatibility

Existing `intelligence.migrate.apply_migrations` is the sole migration executor. The preflight manifest automatically adds `054` and reports next free prefix `055`. Its historical 016/017 Git blob pins remain immutable.

The existing `tests/test_sre_migration_guard.py` and `tests/test_sre_migration_guard_pg.py` now distinguish three SRE-owned files from the simulated upstream-main-only 053 baseline. They assert fresh install of all migrations, upgrade from main-only (with historical 016/017 as explicit backfills and new 054 appended), re-apply no-op and unchanged old `schema_migrations` SHA records.

## Tests / commands

- `tests/test_sre_live_schema.py`: offline SQL manifest/DDL audit, three LIVE-only tables, composite FKs, immutable triggers, no finance/paid dispatch.
- `tests/test_sre_offline_pg.py`: PostgreSQL17 fixture with actual two-human-approved C9 and actual D1 observations; creates real LIVE run/items/batch and rejects forged project, taxonomy version, pipeline, content SHA, source discovery, S3 origin JSON, revision, attempt to modify hash/manifest, unauthorized DELETE, stale/rollback/skip lease generation. Confirms no legacy classification run/result is created.
- `tests/test_sre_migration_guard_pg.py`: disposable PostgreSQL17 isolated fresh and main-only upgrade schema with installed ledger checksum guarantees.
- `.github/workflows/sre-contracts.yml`: unit tests on Linux+Windows; existing PG17 S4 tests and migration tests extended to M11. Full repository checks remain required.

```sh
python -m unittest discover -s tests -p test_sre_live_schema.py -v
python -m unittest discover -s tests -p test_sre_migration_guard.py -v
CM_TEST_PG_DSN='<DISPOSABLE_POSTGRES_17>' python -m unittest discover -s tests -p test_sre_migration_guard_pg.py -v
CM_TEST_PG_DSN='<DISPOSABLE_POSTGRES_17>' python -m unittest discover -s tests -p test_sre_offline_pg.py -v
```

**QA status:** code and test sources implemented; PostgreSQL17 success on exact code SHA is NOT confirmed. No historical SQL edit, no automatic paid POST, no merging to `main`.

**Next M12:** verified LIVE run planning/idempotent enqueue with exact S2 C9 and M08 D1 source input digest, own transaction and collision handling — only when user asks.
