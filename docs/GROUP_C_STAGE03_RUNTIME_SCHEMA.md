# Group C — GC-MOD-03: runtime PG17 / upgrade / release notes

**Branch:** `feature/group-c-full-module`
**Status:** code IMPLEMENTED; isolated PG17 CI awaits execution, not certified production-ready.
**Owner:** Group C performance C0–C7 (not Knowledge Extraction `knowledge_c_*`).
**Migration:** `intelligence/migrations/056_group_c_runtime.sql`
**SHA-256 of new SQL:** `ef40af7dc6c1f56ceae88ffc5b6a9801109137c06b117b9eea8215dc21843dac`

## Ordering and neighbouring branches

Audited 2026-10-26:
- main at `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`: ends at `053_ke_c_qualified_execution.sql`.
- Group A at `852b0946e868fd9410b7da2e5903e9645d38772e`: `054_group_a_module_core.sql`, `055_group_a_handoff_inbox.sql`.
- Group B at `391d18faa677bb27482b157813545ae25bd44be5`: `055_group_b_runtime.sql`.
- QDP at `e50f4eaa1e9d5e0a13f0c5f60f52f064266e925e`: `016_qualified_data_pipeline.sql`, `040/041_qdp_*` (QDP branch is based on a different migration history).
- Analytics PR23 at `12921968ef3d5c6f5e706fd8e625c54733405f3b`: `016/017/018_analytics_*`.

The GitHub branch `feature/group-c-full-module` has no migration name `056_*` prior to this change. The independent standalone diff contains **only** `056_group_c_runtime.sql`; no Group A/B/QDP/Analytics migrations are copied. After eventual sibling merges, `055_group_a_*` and `055_group_b_*` may coexist because the migration framework keys each **entire filename**, not its numeric prefix. Integration owners MUST re-audit ordering, dependencies and checksums against the actual main HEAD at merge time. A branch-created migration number is not reserved globally.

`intelligence/migrate.py` sorts `[0-9]*.sql`, locks with PostgreSQL advisory transaction lock, records `schema_migrations(version,sha256)`, rejects checksum drift, and applies a migration atomically. Therefore **never** edit already-deployed `003_performance_c.sql` / `004_performance_c_integrity.sql`; this stage leaves them intact.

## New Group C-owned relations

| Relation | Identity | Mutable state | Constraints |
|---|---|---|---|
| `group_c_inbox` | QDP `event_id` UUID, project/video/QDP run, exact input/payload SHA | received → accepted or quarantined | canonical D1 video FK, project FK, `analysis_group=C`, `qdp-handoff/v1`, immutable wire identity, no re-open from terminal receipt |
| `group_c_inbox_conflicts` | event ID + observed payload/input SHA | append-only | durable conflict evidence, no mutation of original receipt |
| `group_c_runs` | Group C run UUID, project/video/source, exact inbox + input SHA and idempotency SHA | queued/running/blocked/partial/completed/failed/cancelled/invalidated | cannot create until linked inbox accepted; references **same video** for snapshot and publication metadata; complete run needs matching persisted complete calculated metric and non-null config/required cohort lineage |
| `group_c_jobs` | PK bigint, project/run/video, immutable per-job hash | queued/running/completed/failed/cancelled | lease token, worker ID and expiry jointly required when running; attempts/max, lease_generation fence and claim indexes |
| `group_c_revisions` | UUID per event, revision integer per run | append-only | revision type, SHA, JSON evidence and optional prior persisted result |
| `group_c_outbox` | UUID, event key, pinned result ID, payload SHA | pending/leased/delivered/failed | must reference an actually completed Group C run + matching complete `calculated_metrics`; exact pinned payload immutable |

No `videos`, `metric_snapshots`, `source_video_metadata`, `calculated_metrics`, `metric_calculation_configs`, `comparison_cohorts` shadow copies. No external APIs, LLM or auto-approval. The QDP wire is **not** silently transformed into the C internal pointer. Real source policies, snapshot provenance, human approvals, cohort compatibility and score correctness remain separate gates in GC-MOD-04..25.

### Important boundary

Inserting an accepted inbox row is **not** QDP approval or proof of source rights by itself. The future GC-MOD-04 real adapter must verify QDP and project membership in the authoritative DB **inside the transaction** before setting accepted. Creating a job does not mean C7 scoring occurred. Outbox is reserved for *persisted complete* C results only.

## Executable QA

Pure schema and migration-order tests:

```bash
python -m unittest discover -s tests -p 'test_group_c_runtime_schema.py' -v
```

The PostgreSQL test group intentionally skips unless **both** `CM_TEST_PG_DSN` and `GC_TEST_DISPOSABLE_DB=YES` are set. Never point these tests at production, staging with user data or a shared database.

GitHub Actions `group-c-module.yml` runs two isolated PostgreSQL 17 databases:
1. **fresh**: all repository migrations including 056 on a clean DB, then schema SHA and lifecycle tests;
2. **upgrade**: first apply the same migrations excluding 056 in a disposable DB, then apply exactly 056, re-run migrator and confirm no-op checksum idempotency.

Both scenarios include event replay/identity mismatch, accepted-run boundary, cross-FK integrity, lease fencing, immutable revisions and rejection of fake completed results. This is still a **fixture** database, not provider/source proof.

Local verification this stage: static `GroupCRuntimeStatic` 2/2 **PASS** on a disconnected minimal mirror; DB cases **6 SKIP** (no PostgreSQL service installed). A mirror is not equivalent to full project CI. GitHub Actions checks and PostgreSQL results must be recorded only when observed, not anticipated.

## Deployment, rollback, and hazard controls

- Upgrade: use `python -m intelligence.migrate` only after backing up DB and auditing `schema_migrations` and full migration filenames on the target HEAD. Do not apply standalone Group C SQL to a schema missing 001–053.
- New tables are additive; for a failed transaction PostgreSQL rolls the entire migration back. For an already-applied 056, **do not edit it**; rollback the application by disabling Group C scheduling/worker before considering DB changes.
- For a completed migration, prefer keeping inert Group C tables and shipping a **new additive forward-fix migration**. Never delete the entry in `schema_migrations` or manually drop production tables as an automatic rollback. Any data removal follows explicit retention/consent policy.
- GC-MOD-04 will implement inbox receive/replay ACK; GC-MOD-21 implements actual SKIP LOCKED claiming, heartbeat, fencing and racing worker tests; GC-MOD-22 actual revision events; GC-MOD-24 source retention invalidation; GC-MOD-25 real Analytics delivery.
- Until those stages and PostgreSQL 17 CI pass, Group C is **not** production-active and the schema should not be interpreted as a working worker pipeline.
