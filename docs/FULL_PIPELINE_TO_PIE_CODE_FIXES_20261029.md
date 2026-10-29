# 2026-10-29 — Source-code fixes staged for local pipeline-to-PIE verification

This is an addendum to the earlier [snapshot audit](FULL_PIPELINE_TO_PIE_AUDIT_20261029.md). The previous audit describes the baseline **before** the fixes in this document. The commit to this integration branch does NOT assert a PostgreSQL17 test run or a production merge.

## Changes actually implemented without GitHub Actions

**UAE Canonical Lineage (D1/SRE):** A previous hard join to legacy `sre_classification_runs` made genuine M11 LIVE run UUIDs unreachable. The updated project-scoped reader admits a strictly completed, not-cancelled `sre_live_runs` row as an **alternative identity source**, without creating/altering legacy classifications. D1 metadata/source/video, A/B/C identities and current-policy checks remain separate and mandatory.

**UAE SRE relevance reader:** Retains legacy checks but adds a source-authenticated M30/M32/M36 path, including reviewed hypothesis coverage, M30 verified receipt/chunk SHA, latest M32 human revision, original M36 ready outbox, QDP durable ACK and revocation tombstone/intent checks. A direct UUID/HMAC claim is not interpreted as approval. Exact per-leaf model-versus-human decisions and the pinned effective-decision SHA must match, or the read fails closed.

**UAE QDP membership reader:** Uses `COALESCE(r.sre_run_id,r.sre_live_run_id)` but accepts only **exactly one** valid producer. Added a modern UUID source read over original `sre_live_verified_pair_results`, latest `sre_live_human_reviews`, live M36 export and acknowledged `qdp_sre_live_events`. Changed `QDPQualifiedPins.sre_result_ids` to accept a homogeneous tuple of canonical UUIDs **or** positive legacy BIGINTs; mixed types/invalid UUIDs are rejected. Existing QDP manifest SHA, QDP A/B/C input hashes, D1 versions, retention, score config and source policy rechecks are unchanged.

**Local verification:** Extended `scripts/pipeline_to_pie_preflight.py` to detect missing *modern* readers rather than obsolete legacy joins; added `tests/test_uae_modern_sre_qdp_bridges.py` and updated `tests/test_pipeline_to_pie_preflight.py`. Added a local PowerShell runner `scripts/run_pipeline_to_pie_local.ps1`. All such tests are **authored, not executed** over a local clone in this GitHub-connector-only environment.

## Not silently bypassed

The existing UAE `QualificationOrchestrator` and fenced worker intentionally withhold *positive qualified* snapshots until real source-backed approval is designed/installed. The PIE `UAEReadResult` and `EligibilityReport` intentionally withhold `verified_live` and `ready_for_analysis` absent independent authority. These are **release gates, not syntactic errors**. No safe change can certify them merely by setting hard-coded booleans to True. Signed receipts authenticate a payload, not the source rights, entire historic evidence chain or a reviewed authorization to release findings.

Before enabling these stages, implement and verify an independently trusted **operator approval/release workflow**, fresh SRE LIVE/QDP/Group A/B/C/read-rights receipts, PIE row-provenance authority and signing-key custody/rotation. A disposable DB and real video E2E are required.

## Run locally on your Windows PC

1. Checkout the integration branch, install the project dependencies in a Python venv and provision a **separate disposable PostgreSQL 17 database**; keep production credentials isolated.
2. In PowerShell: `.\scripts\run_pipeline_to_pie_local.ps1 -WithTests`. The status preflight is intentionally nonzero until release gates are fulfilled. The script compiles source and runs independent contract suites **without GitHub Actions**.
3. For schema checks, set `$env:CM_TEST_DISPOSABLE_DB = 'YES'` and `$env:CM_TEST_PG_DSN = '<disposable local DSN>'`, then run `.\scripts\run_pipeline_to_pie_local.ps1 -WithTests -WithDisposablePostgres`. Only read-only DB probes run, not automatic migrations.
4. After inspecting the SRE SQL-062 historical checksum, test migrations on an EMPTY disposable DB, inspect `schema_migrations`, run the same command. Complete real source video ingest → SRE verified M30/M32/M36 → QDP current run → Group A/B/C verified outputs → UAE qualified release → PIE intake and independently authorized research. This is a separate live E2E exercise, not performed by the preflight.

**Current verdict:** Cross-module modern SRE/QDP/UAE read contract **implemented in staged source**. Positive UAE qualified release, live PIE authorization, and actual PG17/source-media E2E **remain unavailable/unverified**; do not merge to `main` or call this a production PASS based only on an offline audit.
