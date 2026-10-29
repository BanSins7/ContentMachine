# GA-MOD-03 — Independent PostgreSQL 17 schema and upgrade contract

**Implementation:** `intelligence/migrations/054_group_a_module_core.sql` (additive).
**Runtime:** intentionally disabled until verified QDP integration (GA-MOD-04).
**QA:** SQL/static check PASS; executable PG17 checks scheduled in Group A CI;
not yet proven against a live PostgreSQL service as of 2026-10-26.

## 1. Why 054 instead of the archived 017

The pre-existing `intelligence/group_a/legacy_sql/017_group_a_technical_extraction.sql`
references `qdp_items`, `qdp_preparations` and `qdp_readable_enrichments`.
Those do not exist in main. Running that SQL directly against main would
fail or silently require a QDP branch merge. Never copy it into
`intelligence/migrations/` or edit an already-applied migration checksum.

054 is sorted *after* the 053 baseline and depends on **existing public D1**
`project_registry`, `sre_project_videos`, `videos`, `sources`,
`source_policy_versions`, `media_assets`, and A003/A004 tables.
It does **not** depend on QDP tables, create a second D1 video catalog,
rewrite existing A1 output, or create a new public GA HTTP route.

## 2. Owned tables

| Table | Responsibility | Integrity |
|---|---|---|
| `ga_runs` | one immutable input/config/version run; source project/media pins | project-video composite FK; immutable identity, attempt fence, deadlines, explicit invalidation |
| `ga_media_refs` | ordered SHA+size asset pins for 1..100 media items | composite run/project/video/source FK, D1 asset FK, asset/video/source/hash checks, immutable writes |
| `ga_inbox` | durable deduplicated QDP event receipt | unique `event_id`, immutable pointer digest/identity, matched GA run |
| `ga_steps` | individual bounded extractor step jobs | unique step/run, monotonic attempts, leases, terminal-output immutability |
| `ga_results` | immutable A003/A004-indexed snapshots | matched GA run/project/video/source and matching concrete A003/A004 row; append-only |
| `ga_outbox` | durable Group B/Analytics-ready event payload | project/result FK, immutable payload SHA, retry and delivery fencing |
| `ga_cache_refs` | exact asset-specific cache lineage | only verified GA-run asset may be pinned, media-asset retention cascade |

The `ga_local_readable_results` view is **local-only**, not a QDP-ready
authorization signal. It checks source and active policy, ALL ordered media
pins, asset hashes and current expiry, and retained A003/A004 pointers.
It never joins fabricated QDP rows. Public result reads will require a
fresh authoritative QDP check at GA-MOD-04/18.

The `ga_legacy_a1_inventory` view is present even on clean main (empty typed
result); when an old `group_a_tasks` table is already present, it reads that
table without modifying it. Existing 017 task identities and A003 rows
remain untouched. **No automatic legacy reprocessing or copying** occurs.

## 3. Versioning and safety boundaries

- A run is identified by project, QDP UUID, canonical D1 video ID, QDP SHA,
  manifest SHA and config SHA, with exact content kind and frozen stage version.
  Distinct input revisions or config versions create separate rows.
- `expected_media_items` and sequential 1-based slide indices prevent
  incomplete media manifests from appearing in the local read projection.
  Run completion additionally requires a completed immutable A003/A004
  output; read projection hides missing/retained media pointers.
- Upstream `media_assets.sha256` must be present and match the pinned
  lowercase digest; source/video must match. This is **not** proof that the
  local bytes were hashed: GA-MOD-05 implements byte verification.
- Disabling a source or revoking an active policy hides completed facts
  immediately from the local view. Marking a run blocked/invalidated remains
  possible after rights change; processing cannot continue under revoked policy.
- Deletion of D1 media removes GA cache/media pins; existing results are not
  returned as complete/valid without their original manifest. Video/project
  cascade retention removes GA-owned rows; an A003/A004 retention operation
  can clear only the foreign pointer without changing historical content.
- Unknown media kind/foreign video/project/source/wrong hashes, negative
  attempts, mutable completed snapshots, wrong version and duplicate event
  identity are rejected by DB constraints/triggers. The DB cannot establish
  QDP readiness or AI truth on its own.

## 4. Installation and upgrade procedure

On an *authorized disposable* PostgreSQL 17 instance, run:

```bash
# CM_DB_DSN must point at the chosen test database, not production.
python -m intelligence.migrate
```

`intelligence.migrate.apply_migrations` records filename and SHA-256 in
`schema_migrations`; every existing migration is compared against its
committed original bytes. 054 runs once and re-running the migrator is a
no-op. A changed 054 or a changed historical migration triggers the
existing checksum rejection and requires an explicit later forward migration
(not an in-place edit).

To test the upgrade from old 017, `tests/test_group_a_migration_db.py`
installs existing main migrations in a fresh schema, creates **test-only
minimal QDP stubs**, runs the archived 017 unchanged, and then installs
054 with a migration directory containing only 054. It checks the legacy
trigger remains intact, legacy inventory view works, checksum replays
are no-ops, and post-deployment edits fail.

**Do not** install QDP stubs in an actual deployment. They exist solely
in the disposable test schema to exercise a historic 017 upgrade.

## 5. Python interfaces and integration boundary

`intelligence/group_a/storage_pg.py` introduces
`migration_snapshot(conn)` (read-only relation detection),
`PostgreSQLGroupAStore` and `PostgreSQLGroupATaskQueue`.
The latter are opt-in, require an explicitly injected connection factory,
report `unavailable` during bootstrap and deny writes before a real QDP
handoff; they never auto-connect at import. A1
`repository.schema_diagnostics(conn)` delegates only to metadata inspection.

These adapters are deliberately **not** claimed as a completed
production ResultWriter/TaskQueue. Wiring durable event acceptance,
locking/replay and verified adapter responses is GA-MOD-04; final
atomic A003/A004 writer and result assembly are GA-MOD-18/20.
The SQL is ready for those later stages without exposing unsafe operations.

## 6. Test coverage & unresolved evidence

- `tests/test_group_a_migration_db.py`: structural assertions for 7 GA
  tables, correct PL/pgSQL dollar delimiters, and no QDP FK. With PG17,
  it tests fresh install, repeat/no-op, legacy 017 migration upgrade,
  checksum mismatch rejection, source project isolation, D1 asset/source
  mismatch, media hash/retention, immutable step/result/inbox/outbox, and
  read-time rights revocation.
- `tests/test_group_a_storage_pg.py`: no-network fake connection for
  read-only schema introspection; public writes stay 503/disabled without
  opening a connection.
- `.github/workflows/group-a-module.yml`: disposable Postgres 17 service,
  `CM_TEST_PG_DSN` pointing only at CI's `ga_ci` database; installs test-only
  `psycopg[binary]==3.2.6` and `jsonschema==4.23.0`. CI compiles all GA
  files, discovers all GA tests and does not assume the real QDP peer exists.
- A read-only GitHub structural audit on the committed files found all
  7 tables, all 7 function bodies with balanced `$$`, legacy `DO`
  delimiters, PG17 CI provisioning and GA-only diff **PASS**.
- Actual PG17 fresh/legacy upgrade, Python unit and FFmpeg media smoke:
  **QUEUED / NOT EXECUTED**, because the GitHub Actions runner backlog
  prevents jobs from starting. This is neither PASS nor an observed FAIL.
  Latest status: [Group A workflow](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml).

## Post-review fixes

- The completion gate requires an exact full media manifest AND a completed GA result pinned to a completed A003/A004 source row.
- The project rights-revocation test first confirms local read suppression. It then restores the source before deleting media so the pre-existing A003 `ON DELETE SET NULL` rights trigger can complete; this does not bypass source rights to create or publish measurements.
- The experimental 054 migration has not yet been applied anywhere by this chat. Changing its bytes is allowed **only while unapplied**; post-deployment checksum protection requires a new filename instead.
