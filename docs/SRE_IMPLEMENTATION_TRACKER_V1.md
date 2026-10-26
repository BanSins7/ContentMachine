# SRE Production V1 — implementation tracker

**Branch:** feature/sre-production-v1-20261023  
**Parent branch:** main  
**Base commit:** 4e51de5fc59f52d83e4fa71492d2d767689cd019 (2026-10-23)  
**Master specification:** docs/SRE_PRODUCTION_IMPLEMENTATION_PLAN_V1.md  
**Overall status:** S0–S4 IMPLEMENTED + R01 AUDITED; NEW standalone modular roadmap M01–M47 TODO; M01 NEXT; paid execution unavailable  
**Production paid calls performed under this branch:** 0  
**Merge into main:** NOT REQUESTED / NOT PERFORMED


## CANONICAL STANDALONE CHECKLIST — START HERE

**Primary stage-by-stage progress and source of truth:** [`docs/SRE_IMPLEMENTATION_CHECKLIST.md`](SRE_IMPLEMENTATION_CHECKLIST.md). This is a complete Markdown plan M01–M47 with the original scope and DoD of every step. For each **actually completed and fully verified** stage, change its checkbox to `[x]`, wrap its title with `~~strikethrough~~`, and add full commit SHA plus GitHub Actions evidence to the journal. New chats MUST read this before implementing anything. The old R-series and this long tracker are historical/technical supporting materials, not the primary active checkbox list.

- M01: branch sync complete at merge SHA `09a8c7542`, [SRE focus PASS](https://github.com/BanSins7/ContentMachine/actions/runs/40289331842), but [Linux](https://github.com/BanSins7/ContentMachine/actions/runs/40289331759) and [Windows](https://github.com/BanSins7/ContentMachine/actions/runs/40289331770) **FAILED at legacy PSI release gate** after 1733 Python tests passed. Root cause was treating newly ADDED SRE migrations as changed historical SQL. Fixed in SRE branch `scripts/psi_release_gate.py` with status-filter and rename detection, regression `tests/test_sre_psi_gate_compat.py`; new complete CI pending; **do not strike yet**.
- M02: independent ports/manifest implemented, full CI still unfinished; **do not strike yet**.
- M03: independent namespace config and health implemented in `intelligence/relevance/config.py`/`health.py`; 17 focused offline unit methods and PostgreSQL17 preflight tests in `tests/test_sre_runtime_config.py`/`test_sre_health_pg.py`; [M03 documentation](SRE_M03_RUNTIME_CONFIG.md). CI is queued; **do not strike yet**.
- M04: minimal SRE GET-only localhost host mount, manifest/health, separate static Studio entry, route deny defaults and integration/PSI regression tests implemented. [M04 documentation](SRE_M04_HOST_MOUNT.md); [targeted SRE CI](https://github.com/BanSins7/ContentMachine/actions/runs/40297067246), [full Linux](https://github.com/BanSins7/ContentMachine/actions/runs/40297067242), [full Windows](https://github.com/BanSins7/ContentMachine/actions/runs/40297067293) on code SHA `5496a29022134dbec46b2e744aa1eabdf374779c` pending; **do not strike yet**.

## Active modular strategy — independent SRE in one branch

**Owner decision 2026-10-26:** replace the direct FIX/R-series release sequence with the new standalone module roadmap `docs/SRE_STANDALONE_MODULE_IMPLEMENTATION_PLAN_RU.txt` (**M01–M47**). R01 audit remains **DONE**; existing S0–S4 code and tests remain **DONE**. **M01 merged but old full CI failed PSI compatibility (fix committed, retest pending); M02–M04 code implemented / CI pending; M05–M47 TODO** — M02's contracts, M03 safe runtime config and M04 host mount exist but are not yet classified DONE. Main merged into SRE at code SHA 09a8c7542abafa7c856e3555581a70ea52b87796; M01 legacy CI failure recorded; new full CI pending on corrected code. M02 proceeded as a side-effect-free isolated change while waiting on GitHub runners; both milestones need exact-code QA before DONE.

**New release boundary:** SRE independently merges into latest `main` through PR #22 once complete. It requires ONLY the currently supported `main` RTB C9 + Discovery D1 and its own additively versioned SRE DB, module APIs, UI, workers, rights, ledger and test harness; no unmerged Collector, QDP, Analytics, Group A or C v2 branch may be a hard prerequisite. Stable inbound and outbound event contracts/fixture adapters are implemented inside SRE; consumer-side changes remain the owner module's responsibility. External end-to-end is separately recorded as `ECOSYSTEM_CONNECTED`, not falsely counted for `MODULAR_READY`.

**Three independent gates:** `MODULAR_READY` (merge-ready standalone, disabled by default), `LIVE_VERIFIED` (separately authorized real paid Opus smoke), `ECOSYSTEM_CONNECTED` (Collector and QDP consumers actually integrated/tested). No paid live invocation or main merge authorized by the roadmap.

**M01 report:** `docs/SRE_M01_MAIN_SYNC_REPORT.md`: two-parent merge `09a8c7542abafa7c856e3555581a70ea52b87796` assembled from current main tree + 32 SRE paths, confirmed compare behind=0 and no unexpected diffs. Linux/Windows/PG and Knowledge Stage14 workflow results pending on that SHA. Do not declare DONE before observed full green CI.

**M01 verified partial QA:** [S1–S4 targeted Linux/Windows/PostgreSQL17](https://github.com/BanSins7/ContentMachine/actions/runs/40289331842) SUCCESS on actual merge code SHA `09a8c7542`. Full Linux [40289331759](https://github.com/BanSins7/ContentMachine/actions/runs/40289331759), Windows [40289331770](https://github.com/BanSins7/ContentMachine/actions/runs/40289331770), and B Stage14 [40289331751](https://github.com/BanSins7/ContentMachine/actions/runs/40289331751) not yet fully completed at this tracker write.

**M03 code SHA:** `9802b9628c2477a55e5bf96ea35c1f87c80c9d6e`; [SRE M03 targeted CI](https://github.com/BanSins7/ContentMachine/actions/runs/40294735685) + [full Linux](https://github.com/BanSins7/ContentMachine/actions/runs/40294735714) / [Windows](https://github.com/BanSins7/ContentMachine/actions/runs/40294735799) on this SHA queued at current audit. 17 offline unit methods + 2 PG17 tests, immutable feature flags, exception-safe dependency probe. DO NOT mark complete until PASS.

**M02 progress:** `intelligence/relevance/module.py` (default-off capability manifest), `ports/taxonomy.py` (verified C9 UUID5), `ports/discovery.py` (verified source-scoped D1 BIGINT), `ports/inbound.py` (optional Collector-only typed immutable envelope), `ports/outbound.py` (strict LIVE-only full-leaf output), `tests/test_sre_module_ports.py` (15 unit methods with many negative cases), real PG port tests in S2/S3 fixture, `docs/SRE_M02_MODULE_CONTRACTS.md`, focused CI. **No Collector/QDP imports/DB inbox/outbox/model calls; feature flags for unimplemented capabilities reject activation.** New exact-code CI triggered at https://github.com/BanSins7/ContentMachine/actions/runs/40291763231. The test outcome MUST be inspected before marking M02 DONE.

**M01 scope:** bring already merged upstream `main` changes into the SRE branch safely, preserving S0–S4 and a SRE-only PR diff, then run regression. **Do not** merge SRE into main or cherry-pick other module feature branches. GitHub current main at the previous R01 audit: `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` (recheck immediately before implementation).

**Previous R01–R41 plan:** retained as historical design traceability; no longer the active implementation queue. Its R01 audit and compatibility blockers remain valid at the observed SHA. M02 now explicitly owns module interfaces, M35 inbound D1/optional Collector, M36 SRE live-only outbox/optional QDP, M43 merge rehearsal, M47 final gate.

## Historical SRE-R01–R41 integration roadmap (supersedes direct S5 implementation order)

Owner file: `SRE_ATOMIC_EXECUTION_ROADMAP_RU.txt` (2026-10-26), based on `ContentMachine_SRE_COMPLETE_INTEGRATION_IMPLEMENTATION_PLAN_RU(1).txt`. S0–S4 remain already implemented; the R-series completes production LIVE behavior and cross-module integration without rebuilding or merging them by implication.

| R-stage | Status | Verified evidence / next contract |
|---|---|---|
| **R01** | **DONE (AUDIT)** | `docs/SRE_INTEGRATION_COMPATIBILITY_MATRIX.md`; source trees, exact PR/HEAD SHAs, conflicts, all migration filenames, known QA-071..074 limits, exact SRE baseline CI checks |
| **R02** | **NEXT / TODO** | Field-level source-of-truth and versioned A/B/C/QV/Collector/D1/SRE/QDP interfaces, typed ID/version negative tests |
| R03–R41 | TODO / dependency-gated | See atomic roadmap; no implicit paid tests, external PR integration or main merge |

**R01 baseline at audit on 2026-10-26:** main `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`; SRE pre-change code `e3d54a3789a01052fb78cd754fe6cf3263bd0db7`; PR #22 draft/unmerged. **PR #29 and #30 MERGED into main**; PR #20 Collector and #24 QDP remain unmerged and had `mergeable=false`; PR #6 and #10 are CLOSED/NOT MERGED. The SRE branch diverged (67 ahead, 143 behind against current main at audit). Main was observed advancing from 8d9bee1 to 218f5f3 during R01; this is the final observed SHA and a per-audit snapshot, not permanent repo state.

**Critical P0s:** Collector Stage9 provider-done only saves QA counts, not D1 video/discovery/outbox; textual Collector query candidate UUID != nullable D1 `search_queries.id` BIGINT; QDP `_sre_snapshot` accepts `completed` `offline_fixture` runs without `execution_mode='live'` filter. Must be closed at owning integration stages before real cross-module E2E. Only SRE branch receives R-series commits.

**Verified baseline CI on pre-audit branch SHA:** full Linux 426 tests (1 skipped) https://github.com/BanSins7/ContentMachine/actions/runs/40071341275 ; full Windows D1 426 tests (1 skipped) https://github.com/BanSins7/ContentMachine/actions/runs/40071341187 ; focused SRE S1–S4 Linux 39/39, Windows 39/39, PG17 integration 16/16 https://github.com/BanSins7/ContentMachine/actions/runs/40071341191 . No combined main+SRE+VC+QDP/Analytics migration run or deployment DB schema_migrations access; QA-071..074 for merged topology remain unverified and belong to R04/R39.

**R01 changes:** only this tracker and `docs/SRE_INTEGRATION_COMPATIBILITY_MATRIX.md`; no runtime code, test fixtures, migrations, CI definitions, branch merge or paid calls changed. R02 begins only on user instruction. Deployment database, C v2 publication and provider catalog remain UNKNOWN until separately verified.


## Status legend

TODO = work not started; IN_PROGRESS = active; BLOCKED = dependency; DONE = implementation, tests, and review notes complete. Planned feature steps are not treated as already working.

## Execution table

| Milestone | Name | Status | Dependencies | Acceptance evidence |
|---|---|---|---|---|
| S0 | Branch and durable plan | DONE | main SHA inspected | Isolated branch, spec and tracker files |
| S1 | Provider-agnostic contracts | DONE | S0 | 22/22 Linux and 22/22 Windows (targeted offline CI on fb5c6399), strict negative cases, documented API |
| S2 | RTB deep taxonomy adapter | DONE | S1 | Read-only approved RTB/SRE mirror, all 11 fields, H1/H2 reviewer provenance, category/fact/examples integrity; 23+7 tests Linux and Windows and real PostgreSQL E2E passed |
| S3 | Discovery D1 intake and policy | DONE | S2 | Explicit project-discovery intake, rights/review gates, versioned metadata, immutability and revocation; targeted Linux/Windows + PG17 CI passed |
| S4 | Offline run engine & fake E2E | DONE | S3 | Durable pinned PostgreSQL run/items, lease fencing, fake grounded labels, idempotent replay, rights/cancel/rollback; S1–S4 Linux/Windows + PG17 CI passed |
| S5 | Financial quote/consent ledger | TODO | S4 | Decimal reserve/dispatch/settle and chaos tests |
| S6 | OpenRouter LLM adapter | TODO | S5 | Strict model pinned transport, controlled opt-in smoke |
| S7 | Embeddings and multi-model cascades | TODO | S6 | Reranking, cache, verified calibration, accuracy comparison |
| S8 | Human review and experiments | TODO | S4/S7 | Durable corrections, gold dataset and benchmark metrics |
| S9 | HTTP API and SRE Studio | TODO | S5/S8 | Authenticated localhost mutation and complete UI behavior tests |
| S10 | Collector + Analytics handoff | TODO | S4/S9; Collector contract | D1-backed intake, outbox, consumer idempotency |
| S11 | CI, rights, resilience hardening | TODO | S1–S10 | Fresh/upgraded PG17, Python 3.12, Node22, Windows, crash tests |
| S12 | Release and guarded merge | TODO | S11 | Reviewed draft PR, passing checks, approved merge SHA |

## Required evidence for DONE

1. Exact branch head SHA and date.
2. List of modified files and contract/migration versions.
3. Actual test commands and observed pass/fail counts.
4. Tests of project isolation, rights, idempotency, budget, and recovery whenever relevant.
5. Known limitations and links to follow-up work.
6. No claim of paid provider E2E unless user separately authorized the provider cost and execution.

## Conflict watch

- Open draft PR #20 — Collector offline query intake and registry; dependent integration is S10.
- PR #10 — **CLOSED / NOT MERGED**; do not resurrect or merge blindly.
- Open PR #13 — DTK manual TikTok Tool; SRE must not depend on its live runtime.
- PR #6 — **CLOSED / NOT MERGED**; much of its schema is already in main.
- PR #29 and PR #30 — **MERGED into main on 2026-10-26**. Main now contains PSI/Knowledge migrations 030–053; SRE branch does not.
- Shared files: app.py, site/index.html, .github/workflows/tests.yml, .github/workflows/windows-d1-smoke.yml, intelligence/migrations/.
- Main may advance rapidly; before every step re-read current SHA, open PRs, migrations and overlapping files.

## Next coding checkpoint

**Active next stage is M07** (only upon next user request, after checking queued CI). Canonical M01–M47 tracker: `docs/SRE_IMPLEMENTATION_CHECKLIST.md`. Legacy R02/S5 notes below are superseded by that plan. SRE-R01 migration/PR baseline audit is DONE. R03–R41 follow atomic roadmap; original S5 budget work is still TODO and will be tackled in relevant R stages. S0–S4 implemented; DO NOT reuse the S4 fake worker transaction for paid calls or assume Collector automation/media processing. Maintain existing behaviors in:
- intelligence/relevance/contracts.py
- intelligence/relevance/pipeline.py
- intelligence/relevance/presets.py
- intelligence/relevance/storage.py
- intelligence/taxonomy_builder/sre_bridge.py

Use fake providers and fixture D1 rows before any live model work. Do not begin paid execution or scrape external platforms by implication.

## Progress log

| Date | Status | Details |
|---|---|---|
| 2026-10-23 | S0 completed | Branch created from 4e51de5; implementation plan committed; tracker established. No engine code modifications. |
| 2026-10-23 | S1 implemented | V1 contracts in relevance/v1_contracts.py, v1_taxonomy.py, v1_runtime.py; docs/SRE_DATA_CONTRACTS_V1.md; original 22 offline unit tests; Linux + Windows S1 passed. Extended to 23 tests under S2 for real RTB reviewed facts. Draft PR #22. No paid calls or main changes. |
| 2026-10-23 | S2 completed | Read-only deep RTB loader and exact SRE projection/fact/example verification, canonical per-hypothesis contexts, 7 unit adversarial tests, real PostgreSQL human-reviewed deep-publication E2E. Targeted CI passed Linux/Windows/PG17 on 72b79f6. Full Linux CI 392 tests passed on earlier S2 SHA ff655971. Draft PR #22 remains open; main unaffected. |
| 2026-10-24 | S3 completed | Explicit D1 discovery->project association, reviewed semantic rights, metadata-only revisions, read-time revocation, append-only SQL guard, source/policy/observation/association lineage, QA with fake and disposable PG17. Tested S3 code commit 95c55cb. Full Linux CI 410 tests (one skipped) and Windows D1 smoke SUCCESS on same code SHA 95c55cb; migration and rights checks green. No paid API calls or main edits. |
| 2026-10-24 | S4 implemented | Offline-only pinned RTB+D1 run planning, lease/fencing worker with bounded retries, evidence-grounded fake labels, result and stage receipts, cancellation and partial rights, simulator-isolation analytics view. Targeted CI success on SHA 2998945feb1e23858c647e906a1468306d1e2e82: 39/39 offline per OS + 16 PG17 integration tests. Full Linux regression 426 tests (1 skipped) SUCCESS; Windows full D1 also SUCCESS: 426 tests (1 skipped) and complete local smoke at the same code SHA. No paid provider I/O. |
| 2026-10-26 | **R01 DONE — audit** | Verified main/SRE/Collector/QDP/A-B PR head and merge statuses, comparison ancestry, lexical migration inventory and checksum runner, P0 source/QDP gaps, shared-file blob collisions, SRE exact-code CI. New compatibility matrix and tracker only; 0 merges/paid calls. R02 next; combined PG release tests remain future. |
| 2026-10-26 | **M05 CODE COMMITTED / CI PENDING** | Source-anchored historical 016/017 Git SHA1 pin + full main 016 QV/030–053 manifest, SHA-256 ledger read-only dry-run, blocked unknown/drift, future migration prefix 054. Unit regression on Linux/Windows and real isolated PG17 fresh/main-upgrade/reapply tests added to SRE workflow. Code commits 8468c61, 71ea857, b988f45, 11dd6b7; see docs/SRE_M05_MIGRATION_COMPATIBILITY.md. M01–M05 remain unchecked pending exact-code GitHub CI; no SQL rewrite, no main changes, no paid calls. |
| 2026-10-26 | **M06 CODE COMMITTED / CI PENDING** | PostgreSQL-authoritative C9 v1 bundle single read, human H1/H2 DB gate from S2, exact pinned 11-field source tree/facts, complete UUID5 ancestor & leaf keys, deterministic full leaf contexts. New ports/approved_c9.py, stable taxonomy port alias, pure tamper tests and real PG17 E2E assertions; GitHub workflow Linux/Windows amended. Details docs/SRE_M06_C9_APPROVED_PORT.md. Code done marker yes; QA not yet PASS, no paid calls/main merge. |

## S4 evidence and boundaries

- Verified implementation code SHA: `2998945feb1e23858c647e906a1468306d1e2e82` (2026-10-24; later documentation-only HEAD may differ).
- Targeted CI: https://github.com/BanSins7/ContentMachine/actions/runs/40070994551 — SUCCESS on Linux/Windows pure S1–S4 (23 + 7 + 6 + 3 = 39 each) and PG17 (11 S2/S3 + 5 S4 = 16).
- Full repository Linux: https://github.com/BanSins7/ContentMachine/actions/runs/40070994545 — SUCCESS: 426 tests, 1 skipped, same verified S4 code SHA. Full Windows D1: https://github.com/BanSins7/ContentMachine/actions/runs/40070994527 — SUCCESS: 426 tests (1 skipped), versioned migrations, Python integration, JS/Python syntax and actual local D1 smoke, same verified S4 code SHA.
- Additive migration: intelligence/migrations/017_sre_offline_run_engine.sql, with pinned run+item tables, lease token/generation, immutable context SHA, explicit execution_mode and safe live-only Analytics view.
- Code: intelligence/relevance/offline_fake.py, offline_runs.py, offline_worker.py; existing storage.py checks partial terminal run.
- Tests: tests/test_sre_offline_unit.py and tests/test_sre_offline_pg.py. Detailed documentation: docs/SRE_OFFLINE_RUN_ENGINE_V1.md.
- Trust gate: S2 actual approved RTB verified at planning, S3 readable content verified at planning AND settlement, only a single-stage stored pipeline, completely fake zero-cost stage. No external calls even if source permits external transmission.
- Results are marked offline_fixture and must never be treated as live relevance Analytics. Terminal partial/cancelled/failed states close legacy storage writes. Tenant scope and rights-filtered result reader included.
- Verified cases: two-review actual RTB -> SRE hypotheses -> D1 videos -> six classification results; zero USD and fake stage journal, replay key conflict, cross-project rejection, cancelled claimed worker, expired/fenced worker, bounded retry exhaustion, dual-worker claims, atomic crash rollback, source revocation between claim and commit, immutable snapshots.
- Remaining: S5/S6 paid system, S7 retrieval, S8 review, S9 authenticated API, S10 Collector/Analytics outbox, S11 physical data purge. S4 has no background service or real quality measurement. Draft PR #22 stays unmerged; main unchanged.

## S3 evidence and boundaries

- Verified S3 code SHA: 95c55cb2a515fd9b7b0c5a5047bab7b540f2591f; documentation and tracker HEAD may differ.
- Targeted CI: https://github.com/BanSins7/ContentMachine/actions/runs/40067535297 — **SUCCESS**; Linux: S1 23/23 + S2 7/7 + S3 pure 6/6; Windows: same; PostgreSQL 17: S2 integration + S3 rights, revision and immutable snapshot suites succeeded.
- Full Linux CI at tested code SHA: https://github.com/BanSins7/ContentMachine/actions/runs/40067535269 — SUCCESS, 410 tests (one skipped). Full Windows D1 smoke: https://github.com/BanSins7/ContentMachine/actions/runs/40067535267 — SUCCESS on exact same code SHA 95c55cb2, including PostgreSQL migrations, Python integration tests, JS/Python compile and local D1 smoke.
- Full Linux CI at earlier S3 SHA ea329b481c1250611128c3fc1849869289778f0e — success after updating existing SRE storage fake policy fixtures, no production permission downgrade.
- Main code: intelligence/relevance/intake.py, source_policy.py, content_builder.py.
- Additive migration: intelligence/migrations/016_sre_d1_intake_rights.sql. Old checksummed migrations 001–015 not modified.
- Unit/PG tests: tests/test_sre_intake_unit.py, tests/test_sre_intake_pg.py; legacy test fixture changed in tests/test_relevance_storage.py only.
- Documentation: docs/SRE_D1_INTAKE_RIGHTS_V1.md.
- Rights: no implicit semantics for historical D1 manual sources, no external LLM grant by metadata consent, reviewed active policy required; SQL trigger/view reject stale source, old observation, non-boolean grants, deleted project association; S3 revisions append-only.
- S3 stores metadata-only, no media download, no fake hashtags/transcript/scenes; no classification, no spending. S4 runtime and complete cross-system physical retention purge remain deferred.
- Draft PR #22 remains unmerged; S3 branch is independent of Collector #20, Analytics #23 and Qualified #24.

## S2 evidence and boundaries

- Verified code SHA: 72b79f6edf9fca4945222c2e68c59b5f0cfeee57 (2026-10-23); follow-up documentation-only HEAD may differ.
- Targeted workflow: https://github.com/BanSins7/ContentMachine/actions/runs/39985744975 — SUCCESS.
- Observed per OS: S1 contracts 23/23 and S2 adversarial unit tests 7/7 on Linux AND Windows. Separate PostgreSQL17 real approved RTB->SRE test 1/1 on Linux (no network or paid calls).
- Full repository Linux CI (code SHA ff6559715da4cd298420dc7b0ce2b6a0e583dc4c): https://github.com/BanSins7/ContentMachine/actions/runs/39985219647 — SUCCESS, 392 tests, one skipped. A newer run including one additional nullable-provenance S1 test was pending when this entry was written.
- Windows full D1 smoke for verified code SHA: https://github.com/BanSins7/ContentMachine/actions/runs/39985744952 — check live conclusion; still in progress at this entry.
- New code: intelligence/relevance/taxonomy_adapter.py; taxonomy_mapping.py; hypothesis_context.py.
- Adjusted real RTB fact schema: intelligence/relevance/v1_taxonomy.py; tests/test_sre_contracts_v1.py.
- Tests: tests/test_sre_taxonomy_adapter.py and tests/test_sre_taxonomy_adapter_pg.py; dedicated Linux/Windows and PG17 workflow .github/workflows/sre-contracts.yml.
- Detailed architecture: docs/SRE_TAXONOMY_ADAPTER_V1.md.
- All S2 reads are transactional REPEATABLE READ READ ONLY, bound to project/run/publication; root RTB + SRE flat projection + mirrored categories, facts, fact edges and examples must match; no flat fallback, no new DB migrations, no paid API calls.
- S2 validates approved Taxonomy only. S3 D1 rights and metadata are implemented now; S4 offline fake worker is implemented; S5 financial enforcement is still NOT implemented.
- Draft PR: https://github.com/BanSins7/ContentMachine/pull/22. No merge requested; main stays separate.

## S1 evidence and boundaries

- Verified code SHA: `fb5c6399a21ae9855f8bce3410d187c2eeb291bf`
- Targeted workflow: https://github.com/BanSins7/ContentMachine/actions/runs/39977578466
- Observed: 22/22 S1 tests Linux; 22/22 S1 tests Windows; compileall pass in the same jobs.
- PR: https://github.com/BanSins7/ContentMachine/pull/22 (DRAFT, not merged).
- Full repository tests: https://github.com/BanSins7/ContentMachine/actions/runs/39977578108 (pending at this entry).
- Windows full D1 smoke: https://github.com/BanSins7/ContentMachine/actions/runs/39977578223 (pending at this entry).
- Existing relevance.contracts/pipeline/storage remain unchanged. No DB migrations, automatic classification, HTTP API or paid requests delivered in S1.
- Critical distinction: shape validation never authorizes a RTB publication, D1 rights or paid budget; S2/S3/S5 must implement DB-verified enforcement.

## Future update format

### S[n] — [name]
- Status:
- Implementation commit:
- Contracts/migrations:
- Changes:
- Tests run:
- Test results:
- Risks/blocks:
- Ready for next milestone:
