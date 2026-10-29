# UA-MOD-04 — Standalone UAE PostgreSQL 17 storage foundation

**Implementation date:** 2026-10-26. **Owner:** `feature/unified-analytics-full-module` exclusively. **Schema migration:** `intelligence/migrations/080_unified_analytics_core.sql`. This migration creates storage only; it does NOT activate qualifying services, connected peer readers, HTTP API, workers, or visibility views.

## 1. Verified migration namespace and source-of-truth audit

At the start of the stage, actual migration inventories were queried from:
- `main` HEAD `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`: ends at `053_ke_c_qualified_execution.sql`, and already includes `016_query_validation.sql`.
- QDP `fe75487cb8227e73013ed54f25af6366458b199a`: uses `016_qualified_data_pipeline.sql`, `040–043_qdp_*.sql`.
- SRE `5c25ff8cf2da41a5e3e3e0406abfc137d7756b71`: uses `016_sre_d1_intake_rights.sql`, `017_sre_offline_run_engine.sql`, `054_sre_live_core.sql`.
- Group A `55117dede165bfc0181c47683f2dfb6885370368`: `054_group_a_module_core.sql`, `055_group_a_handoff_inbox.sql`.
- Group B `a4671a9f6b48a785dc564649e94d9fdfd5df73fc`: `055_group_b_runtime.sql`, `056_group_b_qdp_receipts.sql`.
- Group C `2c99ed3dae14f118b75c80c69b4c291904af08ea`: `056_group_c_runtime.sql`.
- Patterns `e7ef8965672d583041c16c5052f71ace4153a54e`: no separately numbered migration observed.

**Decision:** standalone `080_unified_analytics_core.sql`, safely beyond *observed* 054–056; collision-free at this HEAD, not an unconditional guarantee about future peer commits. Before merging, audit all migration SHA and names again. Do not copy or rename PR23 legacy `016_analytics_unified.sql`, `017_analytics_jobs_and_read_gates.sql`, `018_analytics_audit_hardening.sql`.

`intelligence/migrate.py` sorts all migration filenames, obtains transaction-scoped advisory lock `82931674013`, verifies previously applied content SHA256 and records each in `schema_migrations`. A second execution skips unchanged SQL; altering installed SQL raises a checksum error. This migration must **never be edited after its first real installation**; any subsequent change requires new forward-only numbered SQL.

## 2. UAE-owned relations (no peer shadow storage)

| Relation | Keys and requirements | Invariants |
|---|---|---|
| `uae_qualification_requests` | UUID request id, canonical project/video/source/policy FK, exact QDP UUID + input SHA, immutable bounded request key and request digest | UNIQUE(project_id, request_key), project/id scope, canonical source-policy match, immutable identity and legal status transitions; initial state `registered`, **no jobs auto-start** |
| `uae_unified_snapshots` | BIGINT identity for Patterns output; project/video/source/policy/QDP/run/scope + input/decision digests; explicit version, UTC as-of, status, nullable SRE/A/B/C IDs | UNIQUE(project, video, source, version), UNIQUE(project, request, input SHA), exact request scope trigger, status/rules constraints, append-only snapshots; qualified requires all seven ready flags, zero blockers and nonnull SRE/A/B/C pins |
| `uae_snapshot_evidence` | (project_id, snapshot_id, port) PK, typed state, producer version/key, payload/lineage SHA | Exactly one record per source_policy/D1/QDP/SRE/A/B/C; missing/unavailable/revoked must have typed reason and no forged reference/verified flags; positive requires record and digest |
| `uae_snapshot_invalidations` | UUID event with project-qualified snapshot FK, reason code/hash and timestamp | Append-only updates, idempotent composite unique, later source-revocation/read invalidation |
| `uae_audit_events` | BIGINT event, project/video, actor type, action, event hash and bounded JSON object | Strict project scoping, unique project/event digest, no raw media copying, UPDATE forbidden; future explicit retention purge allowed |

Existing canonical tables are referenced via FK where schema-compatible: `project_registry`, `videos`, `sources`, `source_policy_versions`. External peer-specific analysis IDs deliberately remain **version-pinned soft references** because A/B/C feature branches independently evolve; UA-MOD-05/08/09/10 enforce their exact published row semantics. Recreating `videos`, `metric_snapshots`, `calculated_metrics`, cohorts or policy/configuration is prohibited.

**Rights and retention:** direct insertion of a `qualified` snapshot rechecks source and policy `enabled/active` and all four explicit source+policy rights. Qualifying snapshots remain immutable historical facts, but source expiry/revocation can happen *after* insertion. The read side must always revalidate *current* rights and invalidation events before publishing any historical status; no `analytics_visible_snapshots` read view is defined at UA-MOD-04. References use ON DELETE CASCADE for project/video/source/policy deletion to permit later retention purge, while UPDATE to snapshots, evidence, invalidations or audit history is denied.

**Caveat:** a privileged SQL writer could manufacture matching source permissions and seven fake evidence references. The database constraint only validates **shape, source scope and cardinality**, NOT SRE LIVE authenticity, QDP membership, media validity, Group C approved score, producer signature or active rights at future read time. UA-MOD-05–13/19 must enforce these through authoritative adapters. Never treat `qualified` in raw storage as safely readable.

## 3. Deferred transactional integrity

A `qualified` row cannot be committed alone: the `uae_snapshot_qualified_check` and `uae_snapshot_evidence_qualified_check` triggers are **DEFERRABLE INITIALLY DEFERRED**. At commit they require exactly seven distinct `complete`, `checked=TRUE`, `is_current=TRUE` evidence records with exact provenance metadata; the evidence table's port CHECK/PK prevents extra fake ports and duplicates. This supports inserting parent and evidence in one transaction, while refusing to delete required evidence from an existing qualified snapshot. Deleted parent with cascading child deletions is allowed for future privacy purge.

No `ON CONFLICT DO UPDATE` may mutate a snapshot, nor may a downstream consumer infer that a missing group is complete. A missing/failed/cancelled request is a diagnostic, not a calculated metric.

## 4. QA coverage and exact commands

`tests/test_unified_analytics_schema.py` has six always-available static migration/ownership checks and three **conditional disposable PostgreSQL 17** checks:
- Reentrancy, migration checksum registration and table existence.
- Two concurrent calls to `migrate.apply_migrations()` verify the migration advisory lock and no duplicate apply.
- An isolated rollback-only fixture inserts canonical project, provider, policy, video and QDP-scoped request, then tests duplicate idempotency key, cross-source policy mismatch, allowed blocked diagnostic snapshot, immutable UPDATE refusal, deferred *qualified without seven child facts* refusal, successful seven-child structural insert and refusal of subsequent child removal. It explicitly proves **SQL shape only, never LIVE peer integration**.

Run:
```bash
python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
python -m compileall -q intelligence/unified_analytics
```

To run genuine PG17 locally set **only** `CM_TEST_PG_DSN` to a fresh, disposable PostgreSQL 17 DB and repeat the unittest command. If `CM_TEST_PG_DSN` matches `CM_DB_DSN`, the DB fixture explicitly refuses to run. Never point integration tests at a production database. The workflow `.github/workflows/unified-analytics-module.yml` now includes a separate `postgres:17` service job using a fresh ephemeral database. CI results remain **pending until actually observed**; adding a workflow does not constitute PG17 PASS.

## 5. Release constraints and stage ownership

- **UA-MOD-05:** canonical lineage and project/source cross-reference readers. Existing SQL soft refs alone are not proof.
- **UA-MOD-06–13:** SRE LIVE, QDP qualified, exact A/B/C, current policy adapters and single authenticated gate.
- **UA-MOD-14–15:** assembly/persistence must use one transaction: request + immutable snapshot + seven child evidence records, pinned exact SHA, source review, duplicate-key resolution.
- **UA-MOD-16–18:** append event inbox/outbox, leases and worker tables in separate forward migrations; not within this one.
- **UA-MOD-19:** qualified-only current-rights-aware visibility; SQL snapshots without a verified source read are not exposed.
- **UA-MOD-29:** safe TTL/right-to-delete purging and audit sanitization without leaving orphan leaks.
- **UA-MOD-30–32:** full PG17 E2E/scale/release readiness after peers actually integrate.

This schema is standalone **on baseline main**; the resulting UAE pipeline is not yet a merge-ready production service.
