# SRE-R01 — Integration Compatibility Matrix / GitHub Baseline

**Audit date:** 2026-10-26 (Europe/Amsterdam).  
**Status:** R01 COMPLETE AS READ-ONLY SOURCE AUDIT; not an integration/merge or deployment-DB certification.  
**Single working branch:** `feature/sre-production-v1-20261023` / draft PR #22.  
**Sources:** live GitHub REST/connector reads of branch heads, PRs, recursive trees, source SQL/Python, compare_commits and Actions logs; the owner's `SRE_ATOMIC_EXECUTION_ROADMAP_RU.txt` R01 DoD and `ContentMachine_SRE_COMPLETE_INTEGRATION_IMPLEMENTATION_PLAN_RU(1).txt` §§01,14,19,20.  
**No cross-branch cherry-pick, rebase, file replacement, schema change, provider POST or main merge performed in R01.**

## 1. Frozen audit baseline — DO NOT reuse SHA without re-reading

| Reference | HEAD verified | Observed state / authority |
|---|---|---|
| main | `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` | Source of deployed-ready upstream code, with QV and now A/B 030–053 migrations; actual deployment state NOT accessible |
| SRE, PR #22 (draft) | `e3d54a3789a01052fb78cd754fe6cf3263bd0db7` | S0–S4 code; isolated branch; unmerged; **use this as R01 pre-change test baseline** |
| Collector, PR #20 (draft) | `a4f658de77fb82de2437474e60c2b917ae2a9811` | Stage 2–9; unmerged, GitHub `mergeable=false` at audit |
| QDP, PR #24 (draft) | `f40949c833f735bc57e939d7db98c2b265f366cd` | Qualified Data Pipeline; unmerged, `mergeable=false` |
| Analytics, PR #23 (draft) | `12921968ef3d5c6f5e706fd8e625c54733405f3b` | Analytics separated; unmerged; no inferred compatibility |
| Group A, PR #26 (draft) | `ae9fa80f6b53eb4ba2fe16eb3ecf65102e13bc7d` | BASE IS QDP PR #24, not main; unmerged |
| Project Source Intelligence A, PR #29 | `1970cd0620e826542c4cae70db4d4cf8f824a607` | **MERGED into main** on 2026-10-26; no longer an open dependency branch |
| Knowledge Extraction B, PR #30 | `f0c9e76a8eb270a66c3654cde28a1dc0b5df21a7` | **MERGED into main** on 2026-10-26; upstream base was PR #29 |
| DTK TikTok Tool, PR #13 | `ebad8e29b8a837df13e4b927cf9ecef47c8c07cc` | OPEN; connector workflow distinct from missing Collector→D1 ingest |
| Old SRE PR #6 / Collector PR #10 | `f012d7203...` / `ff2b961d...` | **CLOSED, NOT MERGED**. Never treat them as active merge candidates or prerequisites |

Main advanced during the audit from previously observed `8d9bee17402019a26bb4eaa27df040beb06153b9` to last confirmed `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`; the baseline row above uses the latest, and the GitHub compare output was computed against the same latest SHA. The branch comparison `main...feature/sre-production-v1-20261023` returned **diverged: 67 commits ahead / 143 behind**, merge base `4e51de5fc59f52d83e4fa71492d2d767689cd019`. These are snapshot counts, not a permanent property of either branch.

**Important update to 2026-10-25 plan:** the plan described A/B as open PRs; at this audit both are merged. C Corrector **v2 approved publication and SRE v2 reader were NOT verified**; do not infer C v2 completion from merged A/B.

## 2. Current module/source-of-truth map

| Producer / consumer | Canonical authority and verified live code | Compatibility for SRE | Current action |
|---|---|---|---|
| A8 project sources | main: `intelligence/migrations/030_*..041_*`; merged PR #29 | Project snapshots upstream of C; not classified-video evidence | Do not copy source research observations into S3 video citations |
| B9 approved knowledge | main: `intelligence/knowledge_extraction/*`, migrations `042_*..053_*`; merged PR #30 | New B→C handoff present in main, but no verified approved C v2 SRE adapter | At R02 freeze v1/v2 boundaries; R38 conditioned on actual C v2 |
| C9 RTB | main: `taxonomy_builder/deep_repository.py`, `tb_deep_publications`, two `tb_deep_reviews`, `tb_sre_taxonomy_links` | SRE S2 `taxonomy_adapter.py` reads only approved publication, 11-field deep hierarchy, UUID5 keys; current SRE branch **does not yet include merged A/B main changes** | R05 verify integrated publication shape on reconciled branch, preserve v1 hashes |
| Query Validation / Search Planner | main: `search_planner/query_validation/{service,contracts,embedding}.py`; `get_validated_bundle`, `pending_validated_outbox`, `ack_validated_outbox`; migration `016_query_validation.sql` | `validated_queries/v1` / QV candidate UUIDs must be converted by trusted bridge to Collector's `validated_query_set/v1` | R02 must version concrete schema and negative ID tests; no guessed conversion |
| Video Collector | PR #20 `collector/{contracts,repository,worker,provider_inspector}.py` | **NO Stage 10 D1 persistence/outbox in inspected worker**; `process_one` on provider done records QA counts and returns `ingestion_pending=True` | R29–R31; do not claim real Collector→SRE handoff |
| Discovery D1 | main: `videos`, `discoveries`, `source_requests`, `source_video_metadata`, source policies | One global D1 `videos.id BIGINT`; no SRE-owned duplicate video table | Keep canonical D1 and per-project association only |
| SRE S2 | PR #22 `taxonomy_adapter.py`, `taxonomy_mapping.py`, `hypothesis_context.py` | Source-approved v1 category UUID5 leaf contexts, two human reviews | Preserve unchanged until R05 |
| SRE S3 | PR #22 `intake.py`, `source_policy.py`, `content_builder.py` + migration 016_sre | Explicit selected D1 discovery→project, source-specific metadata observations, reviewed semantic rights | Revalidate against real Collector provenance at R06/R29 |
| SRE S4 | PR #22 `offline_runs.py`, `offline_worker.py`, `offline_fake.py` + migration 017_sre | DB-backed **offline_fixture**, zero paid LLM; fake evidence **not real semantic accuracy** | Preserve for regression; R03–R10 implement separate live DTOs/run engine |
| Qualified Data | PR #24 `qualified_data/repository.py::_sre_snapshot` | **P0**: requires completed run+approved taxonomy but **does not require `execution_mode='live'`**. It directly queries `sre_classification_results`, so SRE's own live-only view does not protect QDP | R32 mandatory fail-closed gate + tests, before any QDP result creation |
| Group A / Analytics | PR #26 based on PR #24; Analytics PR #23 | Separate downstream; requires truly qualified D1 video, permitted media/metrics and non-synthetic outputs | R33–R34 E2E only after QDP live gate |

### Identity and provenance collision (P0 until bridged)

- D1 `source_requests.query_id` and `discoveries.search_query_id` are nullable **BIGINT FK** referencing `search_queries.id`.
- QV/SQG candidates and Collector `collector_task_queries.query_id` are **textual candidate UUIDs**. Collector `collector_task_queries` has many-to-many query-to-source_request links. **Never cast UUID into BIGINT**, invent a legacy `search_queries.id`, or collapse multiple query associations to one arbitrary ID.
- PR #20 `collector/repository.py` explicitly inserts source requests and many `collector_task_queries`, but inspected `collector/worker.py` stores only inspection aggregates on `done`. A real D1 insert, `collector_video_hypotheses` relation and `collector.discovery_persisted/v1` transactional event are missing.
- SRE S3 `intake._authority` currently compares D1 source_request run/source/query references, but does not establish ownership from the missing Collector event/provenance mapping. **Never automatically attach a discovery merely by trusting a caller-supplied project_id**.
- A validated search for hypothesis H1 **is not** a semantic match of H1. Classify the D1 video against all approved leaf hypotheses unless a separately calibrated versioned selection contract exists.

### Explicit P0 downstream gate

QDP PR #24 `_sre_snapshot` currently uses `SELECT r.id,r.taxonomy_version_id,r.status,t.status AS taxonomy_status`, checks `r.status='completed'` and taxonomy approved, then queries raw SRE results with human override. It does **not** check `r.execution_mode` or read `sre_live_classification_results`. The S4 fake worker can set `completed` with `offline_fixture` matches. **Do NOT run QDP against the combined schema before R32 adds/validates live-only gating in QDP itself.** No assumption that existing SRE migration 017 view solves another branch's consumer query.

## 3. Migration inventory and ordering — actual source evidence

**Migration runner:** `intelligence/migrate.py` unchanged across investigated branches (blob `e97caee5734d82f1bc0db2a37007e999135aea01`). It:
1. acquires PostgreSQL transaction advisory lock `82931674013`;
2. iterates sorted `[0-9]*.sql` by FULL FILENAME, **not numeric prefix**;
3. records `schema_migrations(version TEXT PRIMARY KEY,sha256 TEXT,applied_at)`;
4. rejects modified content for any previously applied filename;
5. applies each missing SQL inside one transaction.

| Branch | Branch-specific SQL after 015 | Risk / dependency |
|---|---|---|
| main | `016_query_validation.sql`; merged PSI/KE `030_..041_...`, `042_..053_...` | Main has substantial SQL beyond SRE branch; rebase/merge must not omit those |
| SRE PR #22 | `016_sre_d1_intake_rights.sql`, `017_sre_offline_run_engine.sql` | S3/S4 migrations depend on earlier D1/SRE base tables; filenames and hashes already pinned by SRE CI |
| Collector PR #20 | `016_collector_planning.sql`, `017_collector_worker.sql` | Source request + task tables; no Stage10 source-persist/event schema |
| QDP PR #24 | `016_qualified_data_pipeline.sql` | Must update live-only enforcement after SRE 017 execution_mode before consumer use |
| Group A PR #26 | QDP `016_qualified_data_pipeline.sql` + `017_group_a_technical_extraction.sql` | Child of QDP; no independent merge to main |
| Analytics PR #23 | `016_analytics_unified.sql`, `017_analytics_jobs_and_read_gates.sql`, `018_analytics_audit_hardening.sql` | Depends on qualified context; cannot use Analytics without QDP integration |

**Union sort if all these filenames were present:** `016_analytics_unified.sql` → `016_collector_planning.sql` → `016_qualified_data_pipeline.sql` → `016_query_validation.sql` → `016_sre_d1_intake_rights.sql` → `017_analytics_jobs_and_read_gates.sql` → `017_collector_worker.sql` → `017_group_a_technical_extraction.sql` → `017_sre_offline_run_engine.sql` → `018_analytics_audit_hardening.sql` → `030_...` → `053_...`. This is **lexical enumeration ONLY**, NOT evidence that migrations can be applied together. The qv source exists only in main; the other 016/017 variants are unique full filenames, not duplicate `schema_migrations.version` values. Foreign-key/function/view dependencies and install order must be independently checked on a disposable combined PG17 DB at R04 and before any merge.

**Checksum safety rule:** never rename or edit any filename/content that may have been recorded in any deployment's `schema_migrations`. R01 does not have access to a real deployment DB; therefore **which files were already applied in production is UNKNOWN**, regardless of Git history or Actions migrations. A forward-only compatibility migration after current highest shared installed filename may be required. R04 owns definitive schema design and clean+upgrade validation.

## 4. Shared source-file conflict inventory

Blob SHA comparison against eight Git trees (main/SRE/Collector/QDP/Analytics/A/B/Group A):

| Path | main | SRE | Integration risk |
|---|---|---|---|
| `app.py` | `045b70ea8` | `22317f4c7` | Different backend route adapters; Collector/QDP/Analytics each have a distinct version — integration must be additive per endpoint |
| `site/index.html` | `c07bae9a7` | `c5c55c3b1` | Main A/B UI already merged; different VC/QDP/Analytics UIs; no whole-file overwrite |
| `.github/workflows/tests.yml` | `366e3ff84` | `b370d55a2` | CI scripts evolved with A/B; preserve mandatory jobs and SRE suites |
| `.github/workflows/windows-d1-smoke.yml` | `0cc9a1115` | `4a01439ce` | Main Windows smoke includes A/B adjustments not present in SRE branch |
| `intelligence/migrate.py` | `e97caee57` | `e97caee57` | Same runner, **but differing migration sets**, not proof of combinability |
| `intelligence/migrations/016_query_validation.sql` | `71bff4e1e` | absent | SRE branch must integrate QV prior to real Collector lineage |
| `intelligence/migrations/016_sre_d1_intake_rights.sql` | absent | `d77350188` | SRE-only D1 rights changes must remain immutable |

The exact blob SHA values are GitHub tree entries, not package or deployment versions. Merging source files should be by imports/routes/contracts, never by picking whichever branch is newest. PR #20 and #24 were reported `mergeable=false` at R01; PR #22 `mergeable=true` does **not** prove domain-level E2E compatibility.

## 5. Verified CI at exact SRE baseline SHA

All three workflows completed **SUCCESS** at `e3d54a3789a01052fb78cd754fe6cf3263bd0db7`:

| Workflow | Result (verified from GitHub job logs) | Link |
|---|---|---|
| Full Linux `ContentMachine checks` | 426 tests; 1 skipped; pass | https://github.com/BanSins7/ContentMachine/actions/runs/40071341275 |
| Windows D1 smoke | 426 tests; 1 skipped; Python/PG tests, migrations, syntax and local D1 smoke pass | https://github.com/BanSins7/ContentMachine/actions/runs/40071341187 |
| Focused SRE S1–S4 | Linux 23+7+6+3=39 tests; Windows 39; PostgreSQL17 S2–S4 11+5=16 tests; all pass | https://github.com/BanSins7/ContentMachine/actions/runs/40071341191 |

Earlier upstream main `8d9bee1740...` has a **previous main success**, https://github.com/BanSins7/ContentMachine/actions/runs/40068101126. Two more recent merged-A/B upstream CI runs were observed: `5cd931058229...` **success** https://github.com/BanSins7/ContentMachine/actions/runs/40284069493, and `218f5f3163...` **in progress** at audit https://github.com/BanSins7/ContentMachine/actions/runs/40284202868. Thus **the exact newest upstream main code has no observed completed CI outcome in this audit**. Cross-branch combined executable CI **has not been run**.

### QA-071 through QA-074: truthful audit classification

| QA | Baseline evidence | Outstanding cross-branch verification |
|---|---|---|
| QA-071 fresh PG17 install | Existing SRE GitHub PostgreSQL job success on SRE migration tree | Fresh merged main+Collector+SRE+QDP+Analytics migration install **NOT TESTED** |
| QA-072 upgrade populated DB | Individual branch regression successful; no combined production DB access | Copy/restored populated pre-016 deployment DB upgrade **NOT TESTED** |
| QA-073 rerun no-op | Migration runner code computes immutable checksum and skips applied rows; previously tested within isolated suites | Reapply full combined migrations to upgraded DB **NOT TESTED** |
| QA-074 tampered prior SQL checksum | Runner explicitly raises RuntimeError when recorded checksum differs | No destructive check on production database; only disposable DB negative test allowed at R04 |

**R01 acceptance is the auditable baseline and migration plan, not declaring QA-071..074 PASS for the future combined schema.** Those checks are scheduled as executable gates in R04/R39.

## 6. Priority blockers, by actual owning module and next task

| Priority | Defect / blocker | Owner and actionable stage | Gate |
|---|---|---|---|
| P0 | Synthetic `completed offline_fixture` might qualify in QDP via raw result query | QDP owner + SRE R32 | Add DB-backed `execution_mode='live'`, QDP source read gate, negative fixture QA-075 |
| P0 | Collector `done` stores counts but not D1 discovery/outbox | Collector Stage10 + SRE R29–R31 | Durable D1 commit + hash-stable event before SRE inbox ACK |
| P0 | BIGINT legacy D1 query FK vs UUID Collector candidate IDs | Collector/Query Validation + SRE R02/R29 | Explicit typed provenance relation, many-to-many preserved |
| P1 | Existing SRE branch missing merged upstream QV/PSI/KE migrations | SRE R01/R04 and release R41 | Reconciling migration manifest, clean+upgrade PG17, preserve SQL checksums |
| P1 | API/UI/CI files diverged on parallel PRs and merged A/B | SRE R35–R37 and release R41 | Additive routes/DOM/CI review; no full-file overwrite |
| P1 | Collector's active source policy might only permit metadata storage | D1/Collector owner + SRE R06/R29 | Explicit reviewed semantic rights; do not grant by default |
| P1 | C approved v2 publication not evidenced by A/B merge alone | C Corrector owner + SRE conditional R38 | Read actual human-approved v2, preserve S2 v1 compatibility |
| P1 | S4 worker uses in-transaction local fixture, unsafe for remote Opus POST | SRE R03/R08–R25 | Separate committed paid dispatch intent + budget ledger and unknown-outcome reconciliation |
| P1 | QDP PR #24 and Collector PR #20 currently not mergeable against main | Owners + release R41 | Resolve actual Git conflicts only after dependency contracts are reviewed |
| P2 | Older PR #6/#10 were closed without merge | None | Exclude from release plan; do not resurrect blindly |

## 7. Frozen non-negotiable interface decisions

1. **One production semantic classifier only:** OpenRouter exact `anthropic/claude-opus-5.5` (availability must be verified at catalog preflight). No Fast LLM, no automatic Sonnet/GPT/Haiku fallback. Local embeddings only **rank all** approved leaves; `allow_candidate_drop=false`.
2. C9 11-field deep approved publication remains pinned immutable, two actual human reviews, UUID5 keys. C v2 must have a **new** approved/versioned adapter; no rewrite of v1 tree hash.
3. Discovery D1 is the canonical videos table. SRE stores project-specific references and versioned content snapshots. Only approved reviewed rights permit semantics and only separate explicit rights permit external model transmission.
4. One canonical video can have many query provenance links and many hypothesis decisions; Collector query association is never SRE match evidence.
5. LIVE Opus dispatch requires independent SRE quote, owner consent, Decimal budget reservation, durable committed intent, and strict response evidence validator. GET/preview/quote/fake route never sends paid requests.
6. QDP consumes only complete, live, approved, currently readable SRE results; no synthetic/legacy; Group A/B/C analytics are downstream and must not invent missing media or metrics.
7. New migration/schema work is forward-only; never mutate a deployed checksum. Do not alter others' branches or merge automatically.
8. Current SRE S0–S4 remain intact and regression-covered. R02 does a detailed field-level contract freeze; R01 documents observed source/ownership boundaries and blockers.

## 8. Execution plan after R01

- **Immediate next: R02.** Exact versioned fields for A/B/C/QV/Collector/D1/SRE/QDP, strict invalid ID/version contract tests, freeze conversion rules. R02 may proceed on SRE branch without merging Collector or QDP because interfaces are read-only inspected from branch source.
- **Then R03.** Dedicated provider-neutral LIVE run/item/batch/attempt DTO and state transitions, not the S4 fake worker.
- **Then R04.** SAFE core live migrations. Before any migration apply to real user DB, request/check the actual deployment's `schema_migrations(version,sha256)` and clean+upgrade PG17 test. No accidental assumption that a filename absent from Git is absent from production.
- **R29–R32 later.** Collector Stage10 source producer and QDP live gate are external dependency gates and can block E2E; do not represent them as SRE-owned finished just because unit mocks pass.
- **R40.** Any actual paid Opus smoke requires explicit user-confirmed cap separately. **R41.** User-controlled merge gate; no automatic merge by this audit.

### Read-only audit and source links

- Main: https://github.com/BanSins7/ContentMachine/tree/main
- SRE draft: https://github.com/BanSins7/ContentMachine/pull/22
- Collector: https://github.com/BanSins7/ContentMachine/pull/20
- QDP: https://github.com/BanSins7/ContentMachine/pull/24
- Analytics: https://github.com/BanSins7/ContentMachine/pull/23
- Merged A: https://github.com/BanSins7/ContentMachine/pull/29
- Merged B: https://github.com/BanSins7/ContentMachine/pull/30
- Migration runner: https://github.com/BanSins7/ContentMachine/blob/main/intelligence/migrate.py

**Deployment-state unknowns:** real installed migration rows/checksums; actual externally configured OpenRouter model/price and secrets; live Collector provider ingestion; current user-specific source policy approvals; actual approved C v2 publication; combined branch schema install/upgrade; QDP live-mode enforcement; merged E2E C→D1→SRE→QDP. None is inferred from the above test successes.
