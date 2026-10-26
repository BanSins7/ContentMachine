# QDP integration migration audit — FIX-00 (read-only inventory)

**Captured:** 2026-10-26. No migration rewritten or applied during this FIX. Database `schema_migrations(version,sha256)` could not be inspected because no deployment/disposable DSN is available in this execution environment.

## Existing migration runner

`intelligence/migrate.py` uses `sorted(MIGRATIONS.glob("[0-9]*.sql"))`, and stores the **full filename** as `schema_migrations.version` with SHA-256 of bytes. It uses `pg_advisory_xact_lock` + one transaction. Same numeric prefix alone does not collide, but lexicographic execution order may violate cross-module FK dependencies. Applied filenames and checksums are immutable.

## Observed branch heads / migration tails

- `main` @ `218f5f31`: `016_query_validation.sql`; `030_project_source_intelligence_foundation.sql` ... `039_project_evidence_grounding.sql` (Project Source Intelligence PR #29 merged; PR #30 merged too).
- QDP PR #24 @ `f40949c8`: `016_qualified_data_pipeline.sql`, without the current main 030+ migrations.
- Collector PR #20 @ `a4f658de`: `016_collector_planning.sql` and `017_collector_worker.sql`.
- SRE PR #22 @ `2576c867`: `016_sre_d1_intake_rights.sql` and `017_sre_offline_run_engine.sql`.
- Analytics PR #23 @ `12921968`: `016_analytics_unified.sql`, `017_analytics_jobs_and_read_gates.sql`, `018_analytics_audit_hardening.sql`.
- Group A PR #26 @ `ae9fa80`: QDP `016_qualified_data_pipeline.sql` and `017_group_a_technical_extraction.sql`; PR base = QDP branch.

## Verified ordering concerns

- QDP `016_qualified_data_pipeline.sql` FK to `sre_classification_runs`, `sre_taxonomy_versions`, `sre_project_videos`, plus D1 tables; it requires the legacy baseline already present.
- SRE `017_sre_offline_run_engine.sql` adds run `execution_mode` with legacy default and creates `sre_live_classification_results` view; QDP current branch lacks that new migration.
- Group A A1 `group_a.repository._eligible` reads QDP `qdp_readable_enrichments` and `qdp_preparations` and so needs QDP schema first.
- Analytics `016_analytics_unified.sql` may sort before QDP's 016 and must not be assumed independently safe in one combined migrations directory; its actual dependency graph must be validated at FIX-03.
- Current main migrations 030–039 are not in QDP branch; do not blindly overwrite them during merge.

## Required reconciliation procedure (FIX-03)

1. READ ONLY production/dev `SELECT version,sha256 FROM schema_migrations ORDER BY version`; never print DSN/passwords.
2. Record all filenames and cross-table/column/view dependencies on an agreed integration SHA.
3. Already applied filename+SHA must be preserved exactly, including duplicate numeric prefixes.
4. For not-yet-applied branch SQL, establish dependency-safe ordering with integration owner; do not rewrite peer-owned SQL in this QDP branch.
5. Add forward-only QDP integration migration *after its actual dependencies*. If sorted names cannot express dependencies, a reviewed dependency manifest/runner validation is required.
6. Test fresh PostgreSQL 17, seeded upgrade, second migration no-op, changed-file checksum rejection, FKs and group read-current views. Verify complete installation before activating live QDP API.
7. Recheck lease_generation, metric definition columns, availability views, project scoping and source FK invariants on agreed merged schema.

**BLOCKER:** No actual `schema_migrations` database rows were inspected at FIX-00. Previous GitHub CI PG17 successes refer only to original QDP SHA and its isolated migration graph. Cross-branch live schema merge is not yet verified.


## FIX-03 QDP-owned implementation: forward migration 040

At stage 2026-10-26 the only created QDP migration is:
- `intelligence/migrations/040_qdp_integration_foundation.sql`, after main's already observed 030–039 and existing QDP 016 under lexicographic full-filename sorting.
- Tables: `qdp_sre_inbox` (inbound event/hash/quarantine), `qdp_run_manifests` (pinned snapshot/review), `qdp_item_lineage` (every SRE verdict, not only qualified), `qdp_outbox`, `qdp_outbox_deliveries` (per consumer durable attempts), `qdp_delivery_receipts` (ACK is NOT completed analysis), `qdp_invalidations`; helper view `qdp_unrevoked_preparations`, **not** a complete source-rights gate.
- All facts in manifest/lineage/outbox/receipts/invalidation are update-immutable with explicit triggers; SRE lineage INSERT checks exact project/run/taxonomy/video/result/content and human correction scope in SQL.
- A missing LIVE SRE producer is not replaced with mock production rows. This DDL does not require SRE execution_mode, allowing independent QDP install.
- New `intelligence/qualified_data/schema.py` implements QDP schema preflight and protects original `016` by expected git blob SHA-1; applied checksums compare bytes against migration file SHA-256 in `schema_migrations`.
- Installation/topology smoke: `python -m unittest tests.test_qdp_schema_migrations -v`, with `CM_TEST_PG_DSN` set to disposable PostgreSQL 17 for DB constraints, re-apply no-op and transaction checksum rollback.

**Important deployment qualification:** Migration 040 had its lineage FK corrected before any known deployment/testing in this execution. Once applied anywhere, its final checksum must be immutable; if an installation applied the earlier draft SHA, STOP and reconcile via a new forward migration rather than replacing the applied file. Never edit any already-applied SQL.

**Not yet verified:** Actual source cluster `schema_migrations` contents, fresh+seeded-upgrade PG17 after 040, cross-branch combined SQL graph, SRE S4/S5 live producer and ACK consumer schemas. These are external QA gates, not claims of completion.


## FIX-05 additive 041 quarantine migration

`041_qdp_sre_inbox_quarantine.sql` follows `040_qdp_integration_foundation.sql`, uses the latter's `qdp_guard_integration_fact` immutable trigger. It records sanitized `(event_id,observed_sha256,claimed_sha256,previous_sha256,reason_code)` with per-event/hash/reason uniqueness, **never raw SRE payload**. The original inbox accepted row remains unchanged when an event ID collides with a different payload. Update the QDP `schema_migrations` and checksum preflight after installing 041. Never edit 040 after a deployment has applied it. `intelligence/qualified_data/schema.py` now requires both 040 and 041 for SRE completion receive.

Test QDP 041 with disposable PG17 and actual `schema_migrations` checksums; a simulated offline fixture is not a deployed SRE producer.


## FIX-10 additive 042 / 043 (schema checksum)

`042_qdp_group_current_preparations.sql` creates `qdp_current_base_enrichments` and `qdp_group_current_preparations`: independent A/B media rights and C scoring/metric rights, not the old cross-domain global `qdp_readable_enrichments`. This 042 was applied to a disposable CI database and must not be edited once applied. `043_qdp_metadata_readability_fix.sql` uses `CREATE OR REPLACE VIEW` on the new base metadata view to remove an overstrict qdp_items status condition; legal metadata can remain readable for historically blocked items, but per-group `state='ready'` gate stays intact. Both hashes now required in QDP schema preflight and new PG17 tests. Never edit 016/040/041/042 after application.


## FIX-12 forward-only journal migrations

`044_qdp_delivery_attempt_journal.sql` adds QDP-owned append-only `qdp_delivery_attempt_log` and immutable trigger. On an actual PostgreSQL 17 test run, direct immutable trigger blocked the preexisting FK `ON DELETE CASCADE` when deleting an entire QDP run. **Do not rewrite 044**, as it may be applied and checksummed. `045_qdp_delivery_journal_cascade_fix.sql` uses `CREATE OR REPLACE FUNCTION` to permit DELETE only when the parent `qdp_outbox_deliveries` row is already absent, while direct updates/deletes with a living parent still fail. All earlier QDP-owned SQL checksums untouched; full PG17 tests [40319126522](https://github.com/BanSins7/ContentMachine/actions/runs/40319126522) PASS 197/197 with 044+045.


## FIX-14 B/C peer migration prefix coexistence

Group B independent branch owns `055_group_b_runtime.sql` and `056_group_b_qdp_receipts.sql`, Group C owns a distinct file `056_group_c_runtime.sql`. Prefix `056` appears twice, but the migrator's `schema_migrations.version` keys by **complete filename**. This is not a byte-for-byte filename conflict and must not be described as one; however QDP has not tested both peer 056 migrations in one combined fresh/upgrade DB. Do not rename already applied migrations. Check the entire ordering/foreign-key/view lifecycle before declaring a fully integrated main build, especially with Group A 054/055 and UAE 080/081. Only C and A separate peer CI PASS so far.


## FIX-15 additive forward migrations

`046_qdp_invalidation_generation.sql` adds run and delivery invalidation generations and PG17 `NULLS NOT DISTINCT` replay-uniqueness; `047_qdp_current_human_review.sql` redefines only QDP current group view, comparing effective SRE human label identity against original lineage, with the exact column shape preserved. Earlier applied QDP SQL unchanged. Cross-branch peer revocation processing is not yet co-installed.
