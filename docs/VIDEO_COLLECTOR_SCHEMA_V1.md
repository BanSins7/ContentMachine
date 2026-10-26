# VC-MOD-02 — Video Collector PostgreSQL schema and safe upgrades

Status: schema implemented in feature/video-collector-v1. PostgreSQL 17 CI and final regression outcomes remain separate verification gates. No production database was modified.

## Migration inventory

Existing 001–016 and 030–055 migrations are left unchanged. New:
- 056_collector_qv_manifest.sql: source QV event to a single immutable manifest; query shards, UUID query links and per-hypothesis coverage.
- 057_collector_discovery_outbox.sql: atomic D1 page checkpoints, canonical video/discovery/metadata references, many-to-many candidate links, one durable SRE-ready outbox.

The standard migration runner orders full SQL filenames, stores SHA-256 and rejects changes to installed files. This module does not add a second videos table, Search Planner, SRE results table, or provider transport.

## Persistent relations and contracts

| Relation | Responsibility |
| --- | --- |
| collector_qv_manifests | One immutable QV outbox/validation run; scope, lineage, hashes, selected_count 0..480, no_queries |
| collector_query_shards | Distinct ordinal and trusted query_set_id for each manifest; 1..50 queries |
| collector_manifest_queries | Candidate UUID -> one shard; QV selected decision FK; source payload/hypothesis/direction/rank |
| collector_manifest_coverage | Exact QV coverage FK and no-selected reasons, including zero-query manifests |
| collector_ingest_pages | Immutable provider task/page-index receipt, lease generation, digest, QA counts and project/run/source scope |
| collector_ingest_page_videos | Real D1 video/discovery/metadata identity, composite FKs that prohibit mismatch |
| collector_page_video_queries | Preserves all query UUIDs for each observed video, references existing 054 many-to-many lineage |
| collector_sre_outbox | Only Collector SRE queue, metadata_only/not_evaluated event, stable delivery_key, retry/lease/retention |

D1 numeric video/discovery/source_request/metadata IDs must always remain BIGINT. QV candidate IDs remain UUID (TEXT when stored in legacy Collector task links). No fabricated search_queries rows or coercion to D1 search_query_id BIGINT.

The SQL adds project-scoped composite foreign keys and uniqueness constraints, not independent duplicate models. Real Discovery and source metadata are still authoritative, and the SRE module may be absent without losing durable events.

## Atomic producer invariants for later stages

VC-MOD-04 must verify and insert manifest/shards/coverage/query links, then ACK qv_outbox in the SAME committed transaction. Replays require identical source sha256; conflicting content must fail.

VC-MOD-09/10 must use a valid fenced worker lease generation to insert canonical D1 videos, discoveries, metadata observations, source/query/hypothesis links, immutable page checkpoint and SRE-ready events in ONE transaction before marking the provider task complete. Provider state done alone must never produce a successful ingest state. Replayed task/page ordinals must have the same page_sha256 or be rejected.

The SRE outbox is committed even when SRE delivery is disabled. SRE must determine relevance independently. Search provenance is NOT a relevance classification. Retention fields do not grant permission to mutate immutable forensic receipts.

## Non-destructive migration preflight

For a NEW disposable database:
1. Run python -m scripts.collector_schema_audit with CM_DB_DSN pointing to that database.
2. Run python -m intelligence.migrate using the same DSN.
3. Re-run the migrator; it must return No pending migrations.

For a POPULATED deployment:
1. Back up the actual database and restore it into a disposable clone before attempting any upgrade.
2. Run the read-only audit against the restored clone and inspect schema_migrations(version,sha256).
3. If legacy 016_collector_planning.sql / 017_collector_worker.sql are in the ledger, STOP. Old branch schema may already contain the same tables under different migration filenames. Never rewrite checksums or auto-rename schema_migrations rows. Compare installed SQL/hash and structural equivalence, then create an explicitly reviewed reconciliation plan.
4. If no blockers, upgrade the disposable clone, rerun no-op, verify historical data unchanged and composite FK constraints.
5. An operator must separately approve any rollout to a populated or production database. This branch cannot infer the actual deployment history.

## Evidence and QA

- tests/test_video_collector_schema_v1.py: static invariants and isolated PostgreSQL17 old-main -> full main+VC migrations; rerun no-op; checksum tamper rollback; scoped FK/triggers inspection.
- tests/test_video_collector_schema_audit.py: safe history and legacy branch blockers.
- GitHub Actions Linux PG17 service runs the focused stage QA and the full Python suite under CM_TEST_PG_DSN.

Testing a cross-project FK with an invalid foreign parent is not proof of a full trusted positive QV publisher; positive QV -> manifest -> shard and concurrent publishing will be tested in VC-MOD-03/04 with real approved source data.

## Status and dependencies

Schema-only stage. These tables are NOT evidence that trusted QV publishing, atomic D1 ingest, SRE delivery or live DTK E2E exists. Next VC-MOD-03: trusted production QV reader and converter.
