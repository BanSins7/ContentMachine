# M01 · MIGRATION RECONCILIATION PLAN (no DDL executed)

**Baseline:** `integration/full-pipeline-to-pie-audit-20261029@00c2f73343b5e932d0f4038e4f8f0490c3961406`.
**Audit artifacts:** `architecture/M01_SQL_FILE_INVENTORY_20261029.json` pins **140 source SQL files** and Git blob IDs; `scripts/m01_contract_audit.py` recomputes their **sha256** from the local checked-out bytes.

## 1. Existing migrator contract

`intelligence/migrate.py` iterates `sorted(MIGRATIONS.glob("[0-9]*.sql"))` and stores `schema_migrations(version=path.name, sha256=SHA256(file utf-8 bytes))` under one PostgreSQL transaction and advisory lock. Therefore:
- Numeric prefixes repeated across modules (`003/004/054/055/056/...`) are **not** ledger collisions; the full filename is the key. They may, however, hide an **ordering/dependency** problem; actual PG17 DDL must verify it.
- Never edit an **applied** migration, even to fix a typo: the migrator rejects mismatched SHA. Use an additive next file after ledger reconciliation.
- The entire migration run is one transaction; previous partial state from manual/imported DDL must be audited separately, not assumed absent.
- Git blob SHA (source identity) is **not** the database ledger's SHA-256 digest.

## 2. SRE 062: confirmed source divergence

| Source | Git blob SHA | State |
|---|---|---|
| `feature/sre-production-v1-20261023` | `64d029f9772021c0b85c44c62f8e653d457dd819` | malformed CHECK plus duplicated receipt trigger, 590 lines |
| `main` and integrated stage | `ec9eca80ebba07b1afc9b133c4c7852558d12a3a` | reconstructed SQL, 306 lines, one table/trigger |
| Actual operator PostgreSQL | **UNKNOWN** | no ledger, applied SHA, PG version or DDL execution available to this audit |

**Stop condition:** If `schema_migrations` already records 062 with SHA different from the exact checkout, stop before applying new files; preserve DB backup and ledger, reconcile historically applied objects and migrations with a database owner. Never edit the ledger to `pass`, never replace an applied migration with a new body. An old 062 might never have applied (malformed); that is a hypothesis to verify from ledger, not an assumption.

## 3. Dependency-sensitive migration groups

| Set | What it adds / modifies | What must be verified |
|---|---|---|
| `001..009` + `015..017` + `030..` | canonical videos, project, legacy SRE, rights, QDP legacy runs, Search Planner | initial PG17 prerequisites, referenced FK unique keys and composite project IDs |
| `054..064` | concurrent SRE LIVE, Collector, Group A/B/C, Search Planner, budgets, receipts | **lexical filename order**, constraints across different module prefixes, all required source tables exist at execution |
| `065..071` | Group B+C joint, M30 verified, M32 Human Review, M36 outbox | SRE prior receipt integrity and Group C identity, current view access |
| `072` | `qdp_sre_live_events`, `qdp_sre_live_tombstones` | QDP durable idempotency, revocation-before-ready, composite FKs |
| `073` | QDP dual-source modern UUID + legacy BIGINT | exactly one SRE source, current original UUID+content evidence; preserve old SHA |
| `074` | replace QDP current-rights views | existing view columns/order and dependent objects remain compatible; revoked source disappears immediately |
| `080..091` | UAE snapshots, evidence, positive denial guard 085, Group C receipts | no unauthorized qualified inserts or “checked=true” evidence; actual joins match modern 073 |
| `200..207` | PIE manifests, pattern/insight lifecycle, model attempts, orchestration | qualified UAE data not released without independent PIE provenance verifier |

`074` rewrites current-rights **views** rather than historical applied migration files. PostgreSQL `CREATE OR REPLACE VIEW` must retain existing exposed column types/order; a fresh/upgrade migration test is mandatory.

## 4. Windows/local read-only audit before migrations

From the **checked out integration branch** in PowerShell:
```powershell
python -m scripts.m01_contract_audit --output m01-source-audit.json
$env:CM_M01_DISPOSABLE_DB = 'YES'
$env:CM_M01_DB_DSN = 'postgresql://USER:PASSWORD@localhost:54329/contentmachine_disposable'
python -m scripts.m01_contract_audit --db-ledger --output m01-schema-ledger-audit.json
```
This connects with `default_transaction_read_only=on`, reads `schema_migrations`, compares every **applied filename** and **SHA256**, lists missing files, pending migrations and detects non-PG17. Nothing is applied, modified or deleted; output does not print DSN. Store result locally (do not commit connection credentials).

After backing up the database, create a **fresh disposable PostgreSQL 17 DB** and **separately** run `python -m intelligence.migrate` using that DB's `CM_DB_DSN`. On a second disposable clone of an existing database, verify upgrade paths and historical SHA. Re-run M01 ledger audit; then execute actual unit/integration tests. Never use these commands on a production DB until the drift report has been reviewed.

## 5. Release-independent criteria

- Static M01: all 140 files accounted for, every referenced handoff path/test exists, IDs and blockers documented.
- Local DB check: no `modified_applied_versions`, no `deployed_files_missing_in_checkout`; pending files explained; version >= 17.
- DDL runtime check: **fresh install and upgrade** complete on disposable PG17, no missing composite-FK/view dependencies.
- Integration checks: source lost/retention/review update/lease replay do not produce wrongful downstream outputs.

No previous `pytest`, CI or PG17 success is inherited from another SHA. The M01 report cannot be used as a license to skip M05.

## 6. Migration 065 / main PR #42

The stage now contains the latest main `065_sre_live_diagnostic_checkpoints.sql` Git blob `ac6527ce3463d65c8f04a39819ef12f25380cbb7` (previous stage blob `246caaa1810f3239ab0616275858ed2ab7a215fc`), latest `intelligence/migrate.py`, and `tests/test_sre_m31_migration_syntax.py`. **If 065 was previously applied under another checksum, do not change the ledger or continue DDL**. The M01 read-only ledger audit tests *all* applied SHA mismatches, not only 062.
