# UA-MOD-01 — Legacy PR #23 audit and source-of-truth ownership map

**Audit date:** 2026-10-26. **Status:** stage-01 source audit; **not** a live integration or validated SQL upgrade.
**Write branch:** `feature/unified-analytics-full-module`. **Legacy branch (READ ONLY):** `feature/analytics-engine-qualified-pipeline-20261023`.
**Pinned legacy commit:** [`12921968ef3d5c6f5e706fd8e625c54733405f3b`](https://github.com/BanSins7/ContentMachine/commit/12921968ef3d5c6f5e706fd8e625c54733405f3b). **Baseline main:** `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`. **UAE head read before stage:** `92d126ffb7cebceb5bbc0269bce41de8712fe8db`.

This document is the mandatory decision register for later UA-MOD stages. It inventories the **actual old implementation**, distinguishes reusable *ideas* from safe code, and assigns every object to exactly one module owner. No files were copied from old PR #23 in this stage. Historical tests, pending CI and synthetic fixtures **do not establish** an operating cross-module engine.

## 1. Audit scope and pinning

The legacy branch differs from current main by 18 ahead / 143 behind commits and 26 changed files (comparison observed at audit). Legacy runtime was implemented under `intelligence/analytics/`; new implementation lives solely in `intelligence/unified_analytics/`. [Old design note](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/docs/ANALYTICS_ENGINE_UNIFIED_V1.md) is a historical architecture reference, not a current deployment claim.

Pinned peer heads read at audit (heads may change independently):
| Dependency | Branch | Observed head | UAE's source of truth |
|---|---|---|---|
| SRE | `feature/sre-production-v1-20261023` | `9249e8cdbe7a52441d88b249d0f5d52bf92a4348` | Approved current **live** match or current human override; immutable taxonomy and content version |
| QDP | `feature/qualified-data-pipeline-v1-20261023` | `3f528afd8ea74ff305bb92177487bda0fab0113e` | Qualified item, preparation fingerprint and run lineage; not merely B/C-ready |
| Group A | `feature/group-a-full-module` | `d682093ca7cce1819e2a54be10b432ea25219f45` | Completed exact A analysis; `ga_runs/ga_results`, media refs and coverage |
| Group B | `feature/group-b-full-module` | `573873e937813ef81cfd71e26a1633d2dddeeaf6` | B1–B8 complete, no review, same A PK and taxonomy version |
| Group C | `feature/group-c-full-module` | `242b4a814cc7a7ec70f972cfb55144d8db176229` | **Persisted** completed `calculated_metrics` and approved configuration/cohorts; never recalculate C in UAE |
| D1/core | `main` | `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` | `videos.id`, `sources`, `source_policy_versions`, canonical project ownership |

**Important:** the old branch's own `016`, `017`, `018` SQL filenames must **not** be cherry-picked or reapplied under their old names. QDP and SRE also contain historical `016_*.sql` migrations. Current `main` has `016_query_validation.sql` and `030–053`. Order, SHA and table availability matter more than a numeric prefix alone; a safe new UAE migration number must be chosen at UA-MOD-04 after a fresh merged-dependency inventory.

## 2. Source file port/ownership matrix

**Disposition vocabulary:** **1:1 reuse** = no semantic change needed (small pure helper only, after verification); **adapter** = preserve interface or algorithm idea, replace dependencies; **rewrite** = implement from revised trusted port contract; **drop** = excluded from UAE entirely. Some rows mix decisions at function granularity.

| Legacy source (all links fixed to old commit) | Functions / responsibility | Owner; disposition | Required change / migration target |
|---|---|---|---|
| [`contracts.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/contracts.py) | `_json_value`, `canonical_hash`, `validate_request` | UAE; **adapter**, limited 1:1 canonical serializer principles | Keep Decimal/UTC-safe deterministic JSON and project/video/request key checks; use new `contracts.py` existing canonical pointer; prohibit unknown/NaN and silent ID coercion. Old `canonical_hash` expects date/Decimal; new bootstrap hashes JSON-only: reconcile deliberately, do not assume binary-equivalent digests. |
| [`contracts.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/contracts.py) | `evaluate_qualification` | UAE; **rewrite** at UA-MOD-13 | Preserve three states and typed blockers, but gate QDP membership, live SRE mode/human override, exact A/B/C records/versions, current policy, evidence TTL. Old gate checks no QDP, no explicit SRE run mode; `image_post` appears in legacy enum despite no B2 contract. |
| [`metrics.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/metrics.py) | `compute_c_metrics`, `MetricError`, weights and Decimal math C1–C7 | **Group C**; **drop from UAE** | Group C is the sole calculator. UAE must read a persisted C row, checking `score_status`, input metric version, config approvals and cohort lineage. No second C scoring function or `[0,100]` normalization in UAE. |
| [`service.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/service.py) | `_connect`, `_one`, `_all`, `_json` | UAE repository; **adapter** | Generic psycopg infrastructure only, injected connections and serializable DTOs; no import from old analytics package. |
| [`service.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/service.py) | `_assert_rights`, `_source` | D1 policy authoritative; UAE **adapter** | Read current `source_policy_versions` + `sources` + current retention on **writes and reads**. Require exact source-policy-version match, media evidence. Do not treat a stored rights flag as perpetual authorization. |
| [`service.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/service.py) | `_project_video` | D1/SRE/QDP project provenance; UAE **rewrite** | `sre_project_videos` link alone is insufficient: verify canonical `videos.id`, project, QDP qualified membership and preparation/input SHA. |
| [`service.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/service.py) | `_a_and_b`, `_b_payload` | Group A/B fact owners; UAE **rewrite** readers | Retain idea of typed A/B fields, join via **pinned** A and B IDs from adapter/handoff, not latest-completed-by-time. Verify Group A actual completion/coverage, Group B exact A relationship, applicable B1–B8, media SHA and active evidence. Carousel uses distinct A PK, B2 `image_post` unsupported. |
| [`service.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/service.py) | `_relevance` | SRE fact owner; UAE **rewrite** adapter | Old query chooses a match in an active taxonomy but cannot prove *live* execution and may allow outdated positive/human-vs-classifier decisions. Require signed/current SRE decision semantics and supersession on read. |
| [`service.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/service.py) | `_metrics` and `_store_c` | Group C; **drop** | Old implementation selects prior metric/cohorts, computes `compute_c_metrics`, and inserts `calculated_metrics` inside UAE. Do NOT port. Implement persisted-C read adapter only (UA-MOD-10); cohort approval belongs Group C. |
| [`service.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/service.py) | `run_qualification`, `list_unified`, `_public` | UAE; **rewrite** + thin compatibility **adapter** | Keep bounded transaction/advisory-lock inspiration and versioned immutable snapshots; orchestrate trusted SRE+QDP+A+B+C port reads. Preserve request-key conflicts, but reject stale fingerprint cache when currently unreadable. Qualified-only read default, separately authorized partial diagnostics; no evidence leakage on expired media. |
| [`aggregate.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/aggregate.py) | `_dimension_values`, `_percentile`, `summarize_qualified` | UAE; **adapter** (pure algorithm may be 1:1 conceptually) | Retain 8 typed dimensions, Decimal median/linear p25/p75/mean and per-stratum dedup. Require current qualified read filter, validated non-null version keys, metric comparability, sample/missingness counts. Avoid duplicate inclusion of one video within a segment. |
| [`aggregate.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/aggregate.py) | `aggregate_project` | UAE; **rewrite** SQL reader | Old `DISTINCT ON(video_id)` may silently pick a different version; freeze eligibility and explicit compatible C config/metric/source stratum first, then dedup. No mixing providers or inferred statistical significance. |
| [`queue.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/queue.py) | `enqueue_batch`, `process_one`, `list_jobs`, `purge_unreadable` | UAE; **rewrite**, reuse SKIP LOCKED **pattern** | Lease fencing + bounded retries are useful; new work must pin input fingerprint, prove current QDP/source on every claim, handle cancellation/recovery, emit atomic outbox, protect job diagnostics. Old queue doesn't implement event inbox/outbox or full peer invalidation. |
| [`worker.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/worker.py) | CLI `--once/--limit/--purge/--watch` | UAE; **adapter** | Manual/explicit opt-in execution, graceful bounded runs and no LLM/provider dispatch. Integrate new queue, health and audit logs; do not launch background polling automatically. |
| [`http_api.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/http_api.py) | Project `/analytics`, `/qualify`, `/batch`, `/jobs`, `/jobs/work`, `/aggregate` | UAE; **adapter** | Preserve versioned route intent only; rewire through new module and project-scoped authorization, strict input limits, local Host/Origin/mutation-header protections. Nothing wired during stage 01. |
| [`__init__.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/__init__.py) | Exports `compute_c_metrics` | UAE **rewrite** exports; old metrics export **drop** | Public API from `intelligence.unified_analytics`; no Group C scorer exposed. |
| [`site/analytics.js`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/site/analytics.js) | Project selector, qualify/batch, jobs, aggregate, debug JSON | UAE Studio; **adapter** | No live actions during audit. Later build `site/unified-analytics.js` with stale/partial/blocked UX, exact versions, safe text rendering, no misleading 0–100 C7 bar, no automatic provider requests. |
| [`app.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/app.py), [`site/index.html`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/site/index.html) | Routes and Analytics panel registration | Shell (minimal opt-in UAE hook); **adapter** | Rebase only a guarded route/panel hook during UA-MOD-26/27, never copy old versions wholesale: current main has unrelated changes since PR23. |

**No unqualified "1:1 code copy":** only pure serializing/percentile and basic arithmetic formatting *ideas* are close to 1:1, subject to tests and revised schemas. All SQL/service/worker integrations require verified adapters or rewrites.

## 3. Legacy test disposition matrix

| Legacy regression (old SHA links) | New owner / target | Preserve / repair |
|---|---|---|
| [`test_analytics_engine.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/tests/test_analytics_engine.py) | UAE gate tests; C math belongs Group C | **Split.** Port fail-closed A/B/SRE/source blockers and canonical hashing; do **not** copy C1–C7 scorer tests into UAE. Add missing QDP and live SRE negatives. |
| [`test_analytics_aggregate.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/tests/test_analytics_aggregate.py) | UAE-MOD-20–23 | **Adapter.** Retain gold 1,2,3,4 => median 2.5, p25 1.75, p75 3.25, multi-label, empty set, incompatible version and min-N negatives. Add source/project policy eligibility. |
| [`test_analytics_pipeline_postgres.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/tests/test_analytics_pipeline_postgres.py) | UAE-MOD-15–19 | **Rewrite.** Idempotency/race, retry, revocation on disposable PG17; old SQL dependencies are incompatible. |
| [`test_analytics_complete_pipeline_pg.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/tests/test_analytics_complete_pipeline_pg.py) | UAE-MOD-30 | **Rewrite.** Old synthetic pipeline calculates C in service; future exact QDP→A/B/C→UAE fixtures including complete video/carousel and negative mismatch. No claim of real live platforms. |
| [`test_analytics_http_local.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/tests/test_analytics_http_local.py) | UAE-MOD-26 | **Adapter.** 6 routes, 403 bad Host/Origin/mutation and 422 invalid body; also cover cross-project 404, unknown SHA/version. |
| [`test_analytics_worker_watch.py`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/tests/test_analytics_worker_watch.py) | UAE-MOD-18/29 | **Adapter.** Explicit foreground watch only; independent queue, no silent daemon. |
| [`test_analytics_ui.cjs`](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/tests/test_analytics_ui.cjs) | UAE-MOD-27 | **Adapter.** Input/event smoke and safe rendering; add unknown/partial/expired/error screens. |
| [legacy workflow](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/.github/workflows/analytics-qualified-e2e.yml) | UAE CI | **Drop** old workflow as runtime dependency; use new `unified-analytics-module.yml` and verified **fresh + upgrade** PG17 migrations, no paid provider. |

## 4. Statement-level migration decision register

### Old `016_analytics_unified.sql` (92 lines; historical-only)

[Legacy file](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/migrations/016_analytics_unified.sql)

| Lines / DDL | Owner; disposition | Reason |
|---|---|---|
| 3–39 `CREATE TABLE analytics_unified_snapshots` | UAE; **rewrite** | Core immutable snapshot idea reusable, but lacks QDP run/preparation fingerprints, exact peer versions and stable evidence capabilities; `derived_expires_at` only arrives later. Must represent all qualified prerequisites structurally. Avoid unconditional `ON DELETE CASCADE` as a substitute for explicit lifecycle design. |
| 40–46 three indices | UAE; **adapter** | Index project/status, video, C pointer; add exact lineage/idempotency/read selectivity after schema design, no unused C scoring indexes. |
| 48–55 no-UPDATE function + trigger | UAE; **adapter** | Preserve immutability of conclusions; separately define append-only validity and retention/purge mechanism. |
| 58–92 `analytics_visible_snapshots` | UAE; **rewrite** | Source and evidence read-time approach useful but missing QDP, current SRE mode, media checksum/retention and downstream version checks. View must not expose cached evidence after revocation. |

### Old `017_analytics_jobs_and_read_gates.sql` (165 lines; historical-only)

[Legacy file](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/migrations/017_analytics_jobs_and_read_gates.sql)

| Lines / DDL | Owner; disposition | Reason |
|---|---|---|
| 3–26 `analytics_qualification_jobs` + 2 indexes | UAE; **rewrite** | Durable queue model useful; add current input pin, lease token/generation, cancellation/idempotent result, recovery, retry/backoff and outbox. |
| 28–91 `cm_analytics_insert_guard` + trigger | UAE; **rewrite** | Verify exact A/B/C references, but old guards check SRE project link not QDP and do not prove all current live/mode/config versions. Future guards must match new schema. |
| 94–165 `CREATE OR REPLACE VIEW analytics_visible_snapshots` | UAE; **rewrite** | Adds previous metric/publication and SRE current review checks, but still no QDP and no current approved C config/cohort health. Must fail closed for missing proof. |

### Old `018_analytics_audit_hardening.sql` (229 lines; historical-only)

[Legacy file](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/migrations/018_analytics_audit_hardening.sql)

| Lines / DDL | Owner; disposition | Reason |
|---|---|---|
| 3–5 `analytics_unified_snapshots.derived_expires_at` + index | UAE; **adapter** | Include retention field in **new initial table**, not via risky ALTER to absent legacy table. |
| 8–13 `calculated_metrics` `uq_c_calculation_lineage` mutation | **Group C**; **drop from UAE** | Identity must include publication metadata; Group C is owner of C persistence and uniqueness. No duplicate constraint replacement. |
| 15–21 prior metric FK cascade mutation | **Group C**; **drop from UAE** | Score retention and input evidence deletion semantics belong Group C; coordinate read-side validity only. |
| 24–37 `cm_c_cohort_member_guard` replacement | **Group C**; **drop from UAE** | Changes C cohort immutability under deletion; could conflict with current Group C migrations. |
| 39–68 `analytics_config_approvals`, `analytics_cohort_approvals` + index | **Group C**; **drop as UAE-owned DDL** | Despite historical `analytics_` naming these are C config/cohort approvals. UAE may read independently verified C approvals through a port; never create shadow approval tables. |
| 70–108 `analytics_cohort_healthy` SQL function | **Group C**; **drop from UAE** | Cohort member/source/definition/version validity is Group C's calculation contract; UAE consumes proof rather than repeating C validity logic. |
| 111–229 `analytics_visible_snapshots` replacement | UAE; **rewrite** | Retain current retention + media presence + CTA review + C/SRE evidence gating **concepts**. Rebuild against verified QDP and peer ports. No copying of C-cohort joins as an alternate authority. |

**DDL collision proof:** Main has `016_query_validation.sql`; old PR23 adds `016_analytics_unified.sql` and 017/018 on a much older migration graph. Another peer QDP has `016_qualified_data_pipeline.sql`; SRE has `016_sre_d1_intake_rights.sql`. Name changes cannot make old SQL safe. Every new UAE migration must be *new*, forward-only and deliberately ordered after the effective upstream schema.

## 5. Migration dependency graph and separation

```text
main/001_foundation                    -> sources, videos(id BIGINT), metric_snapshots, media_assets
main/002_discovery                     -> source_policy_versions, source_video_metadata, video_topics
main/003_performance_c + 004_integrity -> calculated_metrics, metric_calculation_configs,
                                         comparison_cohorts/members (GROUP C-owned)
main/003_video_features_a              -> video_feature_a_analyses, *_summary (GROUP A-owned)
main/004_carousel_feature_a            -> carousel_feature_a_analyses/items/summary (GROUP A-owned)
main/005_content_features_b            -> content_b_analyses, B1-B8 tables/views (GROUP B-owned)
main/006_discovery_sre_workspace       -> sre_project_videos, runs/results/human_labels (SRE-owned)
main/016_query_validation             -> existing, different 016; DO NOT replace
main/030..053                         -> PSI/KE dependencies; append-only migrations already installed
peer SRE 016/017...                    -> live classification/version handoff [NOT MAIN]
peer QDP 016 + 040/041...             -> qualified runs/items, prep fingerprint/outbox [NOT MAIN]
peer Group A 054/055...               -> ga_runs, ga_results, media/inbox/outbox [NOT MAIN]
peer Group B feature migrations        -> verify exact version refs [NOT MAIN]
peer Group C feature migrations        -> persisted C score / approvals [NOT MAIN]
              \/
   [ALL required upstream schemas/ports verified & integrated]
              \/
UA-MOD-04: **new** collision-free additive UAE migration(s) (UAE-only)
    -> UAE snapshots/read guards, queue, inbox/outbox after prerequisites
    -> UA-MOD-15/16 transactionality and UA-MOD-19 read policy
```

Edges in legacy 016: snapshot FK `project_registry`, `videos`, `sources`, `source_policy_versions`, video/carousel A, B, `metric_snapshots`, `calculated_metrics`, SRE label/result. Legacy 017 adds job FK to snapshots and trigger dependencies on `sre_project_videos`, all A/B/C/metric tables. Legacy 018 depends on `016`, `017`, Group C `calculated_metrics`, `comparison_cohort_members`, C approvals and publisher/source policy data. **Revised graph adds QDP qualified preparation and peer handoff contracts before any UAE qualified write.**

`intelligence/migrate.py` sorts migration **filenames**, hashes entire SQL by filename and refuses editing an applied file. Therefore replacing/renaming already installed SQL is not an upgrade path. New forward-only migrations must handle **fresh DB** and **existing main DB** (and distinguish an optional legacy PR23 deployment explicitly, without assuming it was installed). Deferred until UA-MOD-04. No SQL was installed, modified or reserved by this stage.

## 6. Discovered correctness and compatibility risks

| Risk | Legacy evidence | Mandatory future remediation |
|---|---|---|
| False qualified without QDP | `evaluate_qualification` and `_project_video` require an SRE link but no QDP qualified item | UA-MOD-07 + 13, exact QDP run/preparation fingerprint |
| Legacy SRE classifier can be offline | `_relevance` filters status/decision only; lacks live execution provenance | UA-MOD-06 current live-mode gate plus active human override |
| UAE recalculates C and approves via own SQL | `_metrics` calls `compute_c_metrics`, `_store_c` writes `calculated_metrics` | UA-MOD-10 persisted Group C read-only adapter, no C approvals written by UAE |
| Temporal latest A/B selection mixes revisions | `_a_and_b` chooses newest compatible pair | UA-MOD-08/09/12 pinned IDs/versions and media fingerprints |
| Cached fingerprint may hide changed prerequisites | `ON CONFLICT(project_id,fingerprint)` and request-key reuse | UA-MOD-14–16 versioned input signature, idempotency guard and invalidation; current read checks |
| Partial can carry derivative text after media expiry | Feature A/B payloads persisted in snapshots | UA-MOD-11,19,29 fail-closed read & purge on current policy/media, no blocked evidence leak |
| Old schema lacks QDP and event delivery | 016–018 have no QDP pointer or transactional outbox | UA-MOD-04 + 16–18 new snapshot, inbox/outbox, worker |
| Source/cohort-unsafe comparisons | Older aggregate can choose latest `DISTINCT ON(video_id)` and accepts optional version keys | UA-MOD-20–23 explicit compatible strata, version/hash checks, sample/missingness |
| Misleading readiness in partial status | Legacy `readiness` is local to A/B/C/relevance and doesn't include QDP | UA-MOD-13 typed readiness including QDP and policy, fail closed |
| Invalid image-post inference | Legacy schema allows `image_post` but B2 lacks that mapping | UA-MOD-02/08/09 block or mark unsupported, never coerce to carousel |
| Contention and worker eventual completion | Queue has SKIP LOCKED and attempt/lease fields, no outbox/peer invalidations | UA-MOD-16–18 fencing/idempotence and stale-run handling |

## 7. Required port handshakes before implementing joins

1. **Identity & projects:** `project_id` = 32 lower-hex, `videos.id` positive BIGINT; `source_id` and exact `source_policy_version_id`. Reject foreign project memberships.
2. **SRE:** live/current approved `match` or latest explicitly effective human-approved match, exact taxonomy/content and supersession; absence or offline fixtures block qualification.
3. **QDP:** an actual **qualified** item, QDP run ID, approved preparation fingerprint, source/media/metric refs and no invalidation; group-ready flags are *not* an A/B/C completion receipt.
4. **A:** concrete completed video/carousel A record, corresponding `ga_results`/run receipt where required, media-assets linked to same video/source/policy and still authorized; unknown coverage is not observed zero.
5. **B:** concrete completed B analysis with `needs_review=false`, explicit exact parent A PK, valid taxonomy/version and all applicable B1–B8; `image_post` unsupported until B2 contract is defined.
6. **C:** already **persisted**, complete, non-normalized C7, exact metric snapshot and source metric version; explicit independently approved config/cohorts, current retention; no recompute and no invented cohort.
7. **Output:** UAE-issued immutable joined snapshot and diagnostic blocker codes; read-side current authorization and evidence validity; future Patterns module consumes only verified descriptive evidence.

Keep UAE `UnifiedAnalyticsInputPointer` from bootstrap as a validated *untrusted reference*, not a certificate of qualification. It currently takes a canonical QDP UUID and `sre_decision_sha256`; reconcile those with **actual** peer schemas before locking the DTO at UA-MOD-02. The pointer alone never justifies qualified status.

## 8. Stage-01 verification and handoff

**Observed:** old source files, seven old test modules + UI test, three complete migrations, current main schema/migrator, and named peer branches were read from GitHub. Audit classification is **documented**, not proven by a live fixture. Old PR23 remains unchanged. No new migrations, runtime endpoints, paid calls or background jobs.

**QA at audit:** PostgreSQL 17 **NOT RUN**; new/old full Python tests **NOT RUN locally**; previous bootstrapping CI run [#40309471694](https://github.com/BanSins7/ContentMachine/actions/runs/40309471694) still **queued** when checked. No CI PASS claim. Document-only stage can be independently reviewed via the fixed-SHA links above; later regression tests must exercise real adapters.

**Next stage UA-MOD-02:** define UAE-owned ports/DTOs/capabilities using peer schemas and source fingerprints, with explicit unavailable/missing outcomes; keep no fake qualifying adapters and no C scorer. Pin any peer advancement in tracker. Do not start stage 02 in the same user request.
