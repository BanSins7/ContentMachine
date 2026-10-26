# SRE M05 — Migration Compatibility and Forward-Only Upgrade

**2026-10-26 · implementation committed, GitHub Actions verification pending.**
**Branch:** `feature/sre-production-v1-20261023` · **PR:** #22 (Draft).
No changes to `main`, another feature branch, existing SQL files, or the common migration writer.

## What is implemented

- `intelligence/relevance/migration_guard.py`: no-import-side-effect, read-only migration manifest and explicit PostgreSQL ledger preflight. Historical SRE SQL `016_sre_d1_intake_rights.sql` and `017_sre_offline_run_engine.sql` are pinned to their existing Git blob identities and cannot be silently re-baselined. Installed rows are compared using the **same SHA-256 of normalized UTF-8 SQL** as `intelligence/migrate.py`.
- Main compatibility baseline includes `016_query_validation.sql`, PSI `030–041` and Knowledge B `042–053`, without colliding by numeric prefixes: version is the **full filename**. The actual migration runner remains `intelligence.migrate.apply_migrations`, whose install order is sorted filenames.
- When applied entries contain an unknown filename or a mismatched/invalid SHA-256, preflight returns `blocked` with reason codes `unknown_applied_version` / `applied_checksum_mismatch` and the affected filenames. No automatic repair, overwrite or checksum re-seeding.
- On a **fresh DB**, preflight reports all files pending and never creates `schema_migrations`. On a DB upgraded to current main first, it reports two previously missing, lexically earlier SRE `016/017` as `retroactive_additions` for operator review; it does not mistake those additions for modified historical SQL.
- New SRE migration prefixes are chosen *after* the maximum current migration prefix; at this M05 baseline the next available is **054**. Recompute before authoring future migrations if main/another module advances.
- No provider/worker/DB/network access at Python import or during `--manifest`. Explicit `--dry-run` opens a read-only transaction against the administrator-selected `CM_DB_DSN`. It cannot apply, delete or mutate SQL. Connection errors are redacted; credentials are not printed.

## Operator commands

Offline source integrity check (no PostgreSQL driver required):

```bash
python -m intelligence.relevance.migration_guard --manifest
```

Read-only database preflight (operator explicitly sets the target DSN; **do not** use on an unknown production environment):

```bash
CM_DB_DSN="postgresql://..." python -m intelligence.relevance.migration_guard --dry-run
```

The JSON report has `status`, `reasons`, `pending`, `retroactive_additions`, `checksum_mismatch`, `unknown_applied`, `manifest_fingerprint`, `next_sre_migration_prefix`, and ledger counts. Exit code 0 means `ready` for migration review, **not** deployment approval. Exit code 2 means `blocked`. The command **never runs migrations**.

Before a real upgrade, stop writers, back up the DB, verify the target database/search path and applied schema hashes, inspect the `retroactive_additions` list, and only then separately execute the existing migration entrypoint with an authorized operator. Preflight must be rerun on the actual target database; CI cannot prove what is deployed.

## Tests and acceptance gate

- `tests/test_sre_migration_guard.py`: historical pin/manifest, all main migrations, fresh and fully applied ledgers, main-only upgrade report, unknown version, mismatched checksum, edited immutable SQL, missing baseline, future version allocation, read-only database stub, and manifest CLI without DB.
- `tests/test_sre_migration_guard_pg.py`: real **disposable PostgreSQL 17 schemas**; fresh application of the full union + second application no-op; main-only application (everything except SRE 016/017), additive SRE upgrade and second application no-op, preserved original main checksum rows, and forced corrupted installed checksum blocked without writes.
- `.github/workflows/sre-contracts.yml`: focused pure-Python regression on **Linux and Windows** plus PostgreSQL 17 scenario. The overall repository Linux/Windows/PSI/Knowledge CI remains required to close M01–M05.

**M05 status: CODE COMMITTED / CI PENDING.** The code and test additions are not themselves proof of passing PostgreSQL 17 or operating-system matrix. Do not tick M05 in `SRE_IMPLEMENTATION_CHECKLIST.md` until exact-code SHA GitHub Actions runs finish green. M01–M04 were also still CI-pending when M05 started due to GitHub Actions runner backlog.

## Scope boundary

M05 does **not** create a new SRE SQL migration, change the common migrator, install on a real user DB, activate SRE, execute paid Opus calls, solve future Collector/QDP adapters, or certify a real production upgrade. LIVE functionality remains M06+ and later.
