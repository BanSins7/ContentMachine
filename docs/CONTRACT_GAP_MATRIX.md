# M01 · CONTRACT GAP MATRIX — ContentMachine

**Audit date:** 2026-10-29. **Repository:** `BanSins7/ContentMachine`.
**Source base:** `integration/full-pipeline-to-pie-audit-20261029@00c2f73343b5e932d0f4038e4f8f0490c3961406`, Git tree `b8801407a0db9ebd5fb4c632b37b5168d1978bf7`.
**Main baseline:** `dd28cd2b4075672286790e825fd6c95a53cb659d`.
**Scope:** M01 **inventory only**, not PostgreSQL migration execution, model/provider E2E, signed UAE release or PIE verification.

This document follows the four uploaded design documents, but evaluates the **actual branch files** rather than assuming those proposals are already implemented. Full pinned branch SHAs, field identities, 12 handoff schemas, owner and intended test oracles:
[`architecture/M01_BRANCH_CONTRACT_SNAPSHOT_20261029.json`](architecture/M01_BRANCH_CONTRACT_SNAPSHOT_20261029.json).
140 source migration filenames, their original Git blob IDs and byte sizes:
[`architecture/M01_SQL_FILE_INVENTORY_20261029.json`](architecture/M01_SQL_FILE_INVENTORY_20261029.json).

## 1. Canonical identities — authoritative PostgreSQL DDL

| Entity / relation | Canonical type | Nullable / important distinction | Evidence |
|---|---|---|---|
| `project_registry.id` | TEXT | project-scoped across all handoffs | `030_project_source_intelligence_foundation.sql` |
| `videos.id`, `discoveries.id` | BIGINT | global video identity; occurrences separate | `001_foundation.sql` |
| `sources.id`, `source_policy_versions.id` | TEXT, BIGINT | `can_analyze_media`, `can_derive_scores` default-deny | `001_foundation.sql`, `002_discovery.sql` |
| `sre_live_runs.run_id` | UUID | genuine M11 LIVE; distinct from legacy `sre_classification_runs.id` UUID | `054_sre_live_core.sql` |
| `sre_live_items.id` | BIGINT | belongs to LIVE run UUID + video BIGINT | `054_sre_live_core.sql` |
| `sre_live_verified_pair_results.result_id` | UUID | original M30 model decision + authenticated receipt/evidence | `064_sre_live_verified_coverage.sql` |
| `sre_live_human_reviews.review_id` | UUID | immutable revision; **latest** M32 review required | `066_sre_live_human_reviews.sql` |
| `sre_live_result_outbox.event_id` | UUID | M36 ready/invalidation; event SHA; lease state | `070_sre_live_result_outbox.sql` |
| `qdp_runs.id`, `qdp_runs.sre_live_run_id` | UUID, UUID NULL | modern source only; legacy `sre_run_id UUID NULL`; exactly one source | `016_qualified_data_pipeline.sql`, `073_qdp_modern_sre_qualification.sql` |
| `qdp_item_lineage.sre_live_result_id` | UUID NULL | exclusive with legacy positive `sre_result_id BIGINT NULL` | `073_qdp_modern_sre_qualification.sql` |
| `ga_runs.id`, `ga_results.id` | UUID, BIGINT | inbox ACK is **not** completed analysis | `054_group_a_module_core.sql` |
| `group_b_runs.id`, `group_c_runs.id` | UUID, UUID | source/QDP/project/policy must agree | `055_group_b_runtime.sql`, `056_group_c_runtime.sql` |
| `group_c_outbox.id`, `uae_group_c_receptions.id` | UUID, BIGINT | UAE received metric is **not** qualified UAE snapshot | `056_group_c_runtime.sql`, `091_uae_group_c_receptions.sql` |
| `uae_unified_snapshots.id` | BIGINT | status `qualified/partial/blocked`; positive blocked by 085 | `080_unified_analytics_core.sql`, `085_unified_analytics_transaction_guards.sql` |
| `pie_research_runs.id`, `pie_dataset_manifests.id` | UUID, UUID | dataset must independently prove current eligible source | `200_pie_standalone_foundation.sql` |

**Scope invariant:** canonical `videos.id` remains one identity; one video can have many discovery occurrences. SQL `project_id TEXT`, UUID run/event and BIGINT video/result IDs must not be swapped or inferred from JSON.

**Temporal/source invariant:** preserve `created_at`, `verified_at`, `reviewed_at`, `observed_at`, `as_of`, content/evidence SHA; forbid future evidence and expired/denied source rights. Every peer ACK merely records the handoff state; it is not an authorization to read later or a completed downstream computation.

## 2. Exact producer → consumer handoffs

All source paths below were checked for **existence** in the pinned tree. Schema meaning and acceptance oracle are specified in the accompanying machine-readable manifest; passing runtime tests is **not** implied.

| Edge | Producer → consumer | Concrete contract / pin | Current code status | Acceptance oracle |
|---|---|---|---|---|
| E01 | RTB → Search Planner | approved taxonomy publication/project, budgeted verified queries | present; LIVE unverified | unapproved taxonomy cannot be dispatched |
| E02 | Search Planner → Collector/D1 | durable task inbox, request hash, provider capability | present; LIVE unverified | duplicate task gives one accepted dispatch, no unauthorized spend |
| E03 | Collector/D1 → SRE M11 | canonical BIGINT video + true content origin, M11 UUID run | present; operator-gated | forged or expired D1/collector receipt blocked |
| E04 | SRE M30/M32/M36 → QDP | ready + invalidated events, UUID M30/review, immutable ACK/tombstone | code present; E2E unverified | original ready accepted once; invalidated replay rejected |
| E05 | QDP → Group A | `preparation_ready` / `preparation_invalidated`, scoped media SHA | code present; operator-gated | no current preparation or rights ⇒ no GA work |
| E06 | QDP → Group B | durable B inbox/run, exact QDP preparation hash | code present; operator-gated | stale source/QDP changes cannot complete B |
| E07 | QDP → Group C | C inbox/run, actual snapshots & config | code present; operator-gated | no measured rights-compliant metric ⇒ no C success |
| E08 | Group A → Group B | `ga_results.id BIGINT` pinned to B UUID run | code present; operator-gated | B never reads incomplete GA media analysis |
| E09 | Group B + C | `group-bc-qualified-join/v1`, same project/video/source/policy/QDP | code present; E2E unverified | mismatched pair cannot be joined |
| E10 | Group C → UAE | C outbox UUID → receipt BIGINT | code present; E2E unverified | UAE receipt not equivalent to qualified status |
| E11 | modern SRE/QDP/A/B/C → UAE | 7-port joined source-backed approval; UUID M30, original M32 | **positive qualification blocked** | true approved source should pass only via independent verifier; current 085 forbids |
| E12 | UAE → PIE | signed one-time dataset+row provenance+rights | **verified-live admission blocked** | signed but unapproved data never becomes `ready_for_analysis` |

**Existing tests** (not executed): `tests/test_sre_qdp_integrated_main.py`, `test_group_ab_pipeline.py`, `test_group_bc_pipeline.py`, `test_group_c_uae_pipeline.py`, `test_uae_modern_sre_qdp_bridges.py`, `test_uae_pie_pipeline.py`. The manifest maps every edge to a test path and **intended** oracle, not a measured result.

## 3. Integration gaps — assessed against actual code

| ID | Finding | Evidence | Ownership / action | Priority |
|---|---|---|---|---|
| CR-01 | SRE feature branch carries **corrupted migration 062**; main/stage has a *different reconstructed immutable file* | Original Git blob `64d029f9...`: broken `evidence_sha256` CHECK, duplicated receipt triggers. Stage `ec9eca80...`: one table and one trigger. No live DB ledger inspected | SRE/migration owner; read-only applied-checksum reconciliation BEFORE deploying | P0 |
| CR-02 | Modern SRE/QDP → UAE read bridge **authored**, but no PG17 cross-FK/live evidence certified | `intelligence/unified_analytics/{lineage,sre_adapter,qdp_adapter}.py`; 072–074, modern M30 UUID | UAE and QDP owner; disposable DB and rights/revocation E2E | P0 |
| CR-03 | 7-port UAE positive qualification **explicitly disallowed** | `qualification_orchestrator.py` returns `qualified=False`; `worker.py` rejects; 085 trigger refuses positive `qualified` and checked/current | UAE release owner; independently approved, fenced positive transaction and *new additive SQL* | P0 |
| CR-04 | PIE `verified_live` / `ready_for_analysis` **explicitly default-deny** | `patterns_insights/intake/{uae,provenance}.py` returns False; source signer is not enough | PIE owner; row-provenance authority + independent source rights, release approval | P0 |
| CR-05 | Local HTTP host guard is **not** human/multiuser identity | `app.py Handler.authorized`: Host/Origin and `X-ContentMachine:1` only | HTTP owner; verified project/actor session + CSRF + authorization before privileged SRE/UAE/PIE writes | P0 |
| CR-06 | Several peer delivery stages require explicit operator/feature flag and actual data; no all-stage auto coordinator demonstrated | `qualified_data/group_{a,bc}_runtime.py`, `group_ab/runtime.py`, `relevance/qdp_delivery_cli.py` | integration owner; bounded supervised dispatch and terminal-state trace; no auto paid calls | P0 |
| CR-07 | Source coverage and timing needed by topic trending and point-in-time ranking **not fully established** | 4 research specs propose coverage snapshots and distinct-author trend store, not in stage tree | Collector/Discovery + evaluation owner; additive evidence contracts and null/coverage semantics | next M06+M12 |
| CR-08 | Ranking, trends, evaluation packages **not present** | no `intelligence/evaluation/`, `intelligence/unified_analytics/ranking/`, `intelligence/trend_detection/` in audited stage | evaluation, UAE ranking, Trend Math owners; avoid duplicate existing scoring or review tables | M07–M14 |
| CR-09 | Actual PG17 first-install/upgrade, media/ONNX, provider LIVE, cost receipt and PIE full E2E **not observed** | no local DB, provider key or videos in this audit | operator on Windows; replay+permissions+cost ledger; file-based PASS at exact SHA only | P0 |

A **blocked** edge is not automatically a bug to solve by removing a guard. CR-03 and CR-04 are explicit design release barriers. Do not convert to positive values without independent trusted authorities.

## 4. M01 output and next step

- **Already implemented:** source files for all 12 handoffs, legacy+modern UUID bridge, 140 SQL files, source-policy gates and bounded dispatch hooks.
- **Compatible at static level:** canonical IDs, existence of referenced producer/consumer paths and their test oracles; modern columns and guard tokens appear in source. **No SQL was executed.**
- **Conflict:** SRE 062 applied-ledger uncertainty; blocked UAE qualified/PIE admission; privileged HTTP auth needs stronger actor identity; local runtime check needed.
- **New components for later milestones:** shared Evaluation, goal Ranking and Trend Detection. Not part of implementing M01.
- **Approval authority:** only scope-limited operator can release paid/media actions or set positive qualification. No network identity inferred from localhost headers.

Read [M01 migration plan](M01_MIGRATION_PLAN.md) and [M01 change requests](M01_CHANGE_REQUESTS.md) before M02/M04. Local read-only audit:
`python -m scripts.m01_contract_audit --output m01-audit.json`.
**Exit 0 means M01 static inventory complete, NOT operational pipeline PASS.**
