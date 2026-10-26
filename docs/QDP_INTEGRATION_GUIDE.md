# Content Machine QDP — independent module integration guide

**Branch:** `feature/qualified-data-pipeline-v1-20261023`, PR #24. QDP is independently developed, not a container for other modules. This guide is the entry point for future integration owners. See `QDP_IMPLEMENTATION_TRACKER.md`, `QDP_DEPENDENCY_MATRIX.md`, `QDP_EVENT_AND_ID_CONTRACTS.md`, and `QDP_MIGRATION_RECONCILIATION.md`.

## Current implementation boundary

Baseline QDP provides read-only preview, explicit run creation, deterministic preliminary qualified-set selection, D1 enrichment, A/B/C preparation rows, namespaced PostgreSQL worker, local API/UI. It is NOT presently production live-safe because strict LIVE SRE ingestion needs peer migration integration and some source/read gates require hardening. Never run it against synthetic/offline SRE data as if production.

FIX-00 adds typed QDP-owned ports in `intelligence/qualified_data/integrations/contracts.py`. Ports do not connect to peer implementations yet. Real adapters and actual publication/manifest guards are delivered in later FIX stages.

## Plugging in a completed SRE

SRE owner supplies: project-scoped completed run with `execution_mode='live'`, immutable taxonomy publication/category keys, content×hypothesis expected manifest, evidence and effective human label revisions, validated event producer. QDP adapter reads DB-authoritative refs and rejects missing/corrupt coverage. Importing a standalone fake fixture does not make SRE integration complete.

## Plugging in a completed Collector/D1

Collector commits canonical `videos.id`, discovery/source request, exact metadata observation and policy version. QDP never writes external source rows. It resolves exact SRE content observation metadata and compatible metrics/media, no cross-source guessed fallback. Any missing source relation gets an explicit blocker.

## Plugging in Group A/B/C

QDP commits independently gated `qdp-preparation/v1` group rows. Outbox dispatcher sends versioned pointer; consumer atomically records inbox receipt+job (and rechecks current eligibility). Group A can adapt to PR #26 `create_task` after exact API/schema compatibility is verified. B/C do not become successful simply because they ACK. External provider budget and execution remain owned by consumers.

## Plugging in Analytics

Analytics must consume independently completed, verified A/B/C artifacts with matching source/project/version, not QDP readiness flags or delivery ACK alone.

## Checklist for a future integration chat

1. Read this guide, tracker and dependency matrix; read real peer head SHA, not old claims.
2. Compare peer schema and producer/consumer payload with QDP proposed ports; record differences.
3. Implement only QDP-owned adapter shims in this branch or controlled integration commit; avoid importing peer source until actual integration ref is stable.
4. Reconcile migrations in integration branch/owner; do not rename applied SQL.
5. Run unit, isolated contract tests and disposable PG17 cross-module integration.
6. Run an explicit real LIVE smoke only with approved provider budget; never run it from GET or default CI.
7. Review API/UI, currentness/invalidation, outbox ACK and analytics actual completed gates.
8. Record SHA, migration inventory, CI links, blockers and owner approval before main merge.

## Local verification

`python -m unittest tests.test_qualified_data_pipeline tests.test_qdp_integration_contracts -v`

`CM_TEST_PG_DSN=<disposable PG17 URL> python -m unittest tests.test_qualified_data_db -v`

`python -m compileall -q intelligence/qualified_data`

`node --check site/qualified-data.js`

Use the dedicated `.github/workflows/qdp-tests.yml` for isolated PostgreSQL 17. Do not run migrations or destructive DB tests against production data. For current baseline CI evidence see tracker.


## FIX-01 SRE compatibility (added)

SRE run intake now **requires** an actual `execution_mode` column from the SRE owner. Without that column, preview/create return 503 `sre_live_schema_unavailable`, never attempt legacy fallback. With that column, only `completed` + `live` + approved project taxonomy and non-fixture resolved model metadata may proceed; tests simulate the column in disposable PG until real SRE branch integration. The QDP port is `intelligence/qualified_data/intake.py`. Real semantic model attestation and full video/leaf coverage still require FIX-02 and a completed real SRE integration.


## FIX-03 QDP schema integration

Add `040_qdp_integration_foundation.sql` after existing original `016_qualified_data_pipeline.sql` without changing its hash. The 040 schema adds trusted event inbox, full-result lineage, immutable manifests, delivery/outbox/receipts, and invalidation records. See `QDP_MIGRATION_RECONCILIATION.md`. To validate: `python -m unittest tests.test_qdp_schema_migrations -v`; with disposable `CM_TEST_PG_DSN` the test suite also checks schema checksums and constraints.

Importing a future SRE module must first install its original versioned migrations (not a QDP-crafted copy); before processing live data call `intelligence.qualified_data.schema.require_qdp_schema(conn)` plus `intake.require_sre_live_schema(conn)`. Both fail closed. No QDP table implies a real external SRE/Group A/Analytics executor is available.
