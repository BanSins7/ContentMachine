# Group A — Implementation Tracker (canonical)

**Источник:** [полный план](GROUP_A_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md). Отмечать этап только после проверенного коммита и доступных тестов.

**Current branch:** `feature/group-a-full-module` (created from main `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`)  
**Baseline main SHA:** re-check at GA-MOD-00; last inspected `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`  
**Last implementation completed:** GA-MOD-02 (QA tracked separately)  
**Next:** GA-MOD-03 (GA-only PostgreSQL schema, migration, upgrade path)  
**GA-MOD-00 implementation commits:** `6966ce3`, `00342f5`, `e07ddd0`  
**Latest committed GA-MOD-01 implementation:** `06b748201c17efe9d7f48ca9cbc8924639082c92` (this status note follows as a separate commit)  
**QA status:** GA-MOD-00..02 implemented. STATIC JSON schema parsing PASS; GA Python unit and FFmpeg CI QUEUED (GitHub runners not started), not PASS. PG17/QDP live NOT RUN. No observed test failures; status reflects nonexecution.

## Checklist (one stage per request)

- [x] ~~GA-MOD-00 — Чистая ветка, перенос только собственного A1 и постоянный трекер~~ (IMPLEMENTED; QA: QUEUED/NOT RUN)
- [x] ~~GA-MOD-01 — Контрактная матрица и truth semantics (freeze)~~ (IMPLEMENTED; QA: QUEUED/NOT RUN)
- [x] ~~GA-MOD-02 — Самостоятельная архитектура портов и адаптеров~~ (IMPLEMENTED; QA: QUEUED/NOT RUN)
- [ ] GA-MOD-03 — Только GA SQL, upgrade path и целостность PostgreSQL
- [ ] GA-MOD-04 — QDP handoff consumer / inbox без копирования QDP
- [ ] GA-MOD-05 — Media Manifest и верифицированный Input Resolver
- [ ] GA-MOD-06 — Восстановление и укрепление A1 (FFprobe/FFmpeg)
- [ ] GA-MOD-07 — Frame Sampling / Thumbnail & Evidence Manifest
- [ ] GA-MOD-08 — Shared Media Understanding / PSI bridge & cache
- [ ] GA-MOD-09 — Audio Decode + Voice Activity Detection
- [ ] GA-MOD-10 — ASR Pipeline + language, source selection, word alignment
- [ ] GA-MOD-11 — Speech metrics: word count, active-time WPM, first speech, coverage
- [ ] GA-MOD-12 — OCR engine: multi-language frame text and geometry
- [ ] GA-MOD-13 — Screen Text Tracking / temporal OCR intervals
- [ ] GA-MOD-14 — Subtitle detection: true/false/unknown
- [ ] GA-MOD-15 — Audio Event Detection: music and SFX
- [ ] GA-MOD-16 — Photo Carousel & Image Post Extraction
- [ ] GA-MOD-17 — Mixed Carousel & Video Slides
- [ ] GA-MOD-18 — Atomic Assembly, quality/coverage gate & immutable snapshots
- [ ] GA-MOD-19 — Group B consumer contract; CTA circularity resolution
- [ ] GA-MOD-20 — Runtime Orchestrator, retries, resource budgets, invalidation
- [ ] GA-MOD-21 — HTTP API, UI, diagnostics & observability
- [ ] GA-MOD-22 — Performance and accuracy benchmarking
- [ ] GA-MOD-23 — CI, compatibility matrix, Windows/Linux/PG17 E2E
- [ ] GA-MOD-24 — Merge-readiness, dependency handshake, release documentation

## Execution log

| Stage | Commit SHA | Files/modules | Unit | PG17 | Media smoke | CI | Blockers |
|---|---|---|---|---|---|---|---|
| GA-MOD-02 (implementation complete; QA open) | `b92f3da` | ports.py, config.py, adapters/unavailable.py, service.py, capabilities, test_group_a_ports.py, workflow, docs | NOT RUN (runner queued) | NOT RUN (no GA schema at stage 02) | NOT RUN (test-only fake ports) | [Group A workflow](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml) QUEUED | GitHub scheduling backlog; no adapter-based live QDP until GA-MOD-04 |
| GA-MOD-01 (implementation complete; QA open) | `06b7482` | pure v1 contracts, 4 JSON Schemas, adversarial tests, compatibility matrix | NOT RUN (Python CI pending); STATIC JSON PASS | NOT RUN (stage has no DB writes) | NOT RUN (pure schema tests) | [latest workflow runs](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml) QUEUED/PENDING (inspect current HEAD) | Runner backlog; optional jsonschema validator may be skipped |
| GA-MOD-00 (implementation complete; QA open) | `e07ddd0` | GA bootstrap, direct API guards, fixture gating, CI & docs | QUEUED | NOT RUN (QDP unavailable) | QUEUED | [run #40299294300](https://github.com/BanSins7/ContentMachine/actions/runs/40299294300) QUEUED | Do not equate queueing with failed tests; await actual runner outcome |

## Dependency handshake / status

- QDP: `qdp-handoff/v1`, availability and version to be confirmed on each stage.
- SRE: no import dependency; SRE model selection Embeddings + Opus 5.5 does not affect GA extraction algorithms.
- PSI: `transcript/v1` / `visual-preparation/v1` reuse only with exact verified media hash mapping.
- Group B: independent consumer of A003/A004 and `group-a-to-b/v1`; CTA semantics owned by B.
- Analytics: reads version-pinned A snapshots, no GA inference claims.

## Update rule

An implementation-complete stage must be marked `- [x] ~~GA-MOD-NN~~ (IMPLEMENTED; QA: PASS/FAIL/QUEUED/NOT RUN)` once its code is committed, even when CI cannot run; independently preserve test outcomes, blockers and links. Never assert tests PASS or release readiness without evidence. For genuine unresolved implementation defects, keep the stage open.
## GA-MOD-00 progress

- Branch based on main baseline; preserved A1 and its legacy SQL outside the migrator.
- Added explicit `qdp_handoff_not_integrated` capability, HTTP/automatic worker disabled during bootstrap.
- Conditionalized QDP-only DB fixture import to allow offline test discovery.
- Added GA-only CI, integration guide, release evidence and full plan alias.
- Implementation commit `6966ce38b270a2d1f6099825b52984192a98d530` pushed; [bootstrap CI run](https://github.com/BanSins7/ContentMachine/actions/runs/40298383330) queued at last check. Completion pending test results; do not claim PostgreSQL/QDP E2E.
- Repository public entrypoints now deny direct calls without a negotiated QDP handoff; new regression tests cover API, worker and repository entrypoints.

## Tracking policy (2026-10-26)

Separate **implementation** from **test verification**. CI queued due to unavailable runner is not FAIL; local validation may be performed independently. Later failing CI is recorded as QA=FAIL, with a fix follow-up; other completed stages are not hidden by one testing blocker. Stage status alone is never a release gate.

## GA-MOD-01 execution note

- Implemented immutable wire shapes in `intelligence/group_a/contracts_v1.py`, four JSON Schema 2020-12 files, public exports, and `tests/test_group_a_contracts_v1.py` with adverse cases.
- Source of truth: [GA contract matrix](GROUP_A_CONTRACT_MATRIX_V1.md), including observed QDP `input_sha256` naming, A003/A004/B FKs, PSI distinct IDs, Analytics incomplete-A gate, no CTA semantic ownership.
- Code commits: `c60c164`, `9a3de9b`, `9d02a80`, `02ef38b`, `a6bad49`; final docs/exports commit check HEAD. Full tests remain unverified until GitHub runner starts.
- The repo-wide GitHub Actions backlog includes other module workflows; cancellation of redundant GA PR/push runs enabled via branch-scoped workflow concurrency. Stale workflow status `cancelled` is deliberate deduplication, not a GA test FAIL.

## QA diagnostic (latest update)

- GitHub Actions runs for multiple independent Content Machine modules were still queued during review, indicating runner scheduling/backlog rather than a Group A test failure. Earlier GA runs marked CANCELLED after branch-scoped concurrency deduplication; that is expected.
- Connector-based static check read and JSON-parsed all 4 GA v1 schema files: **PASS**. Full Python unittest, optional JSON Schema semantic validation, FFmpeg media test: **NOT RUN locally**, GitHub job **PENDING**; no false PASS claimed.
- Direct local checkout could not be fetched from github.com because the execution container has no DNS/network access; thus local Python execution was not possible in this session. CI remains the authoritative runtime test path.
- Current workflow (latest run always changes on commit): [Group A Actions](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml). Prior run IDs may become CANCELLED intentionally when a newer commit queues.

## GA-MOD-02 implementation report (2026-10-26)

- **IMPLEMENTED**, QA remains independent (GitHub Actions QUEUED / local Python NOT RUN). See [ports and adapters](GROUP_A_PORTS_AND_ADAPTERS.md).
- Verified GitHub isolation by compare against main: only GA-owned files, GA tests, GA docs and its own workflow changed; **no changes** to QDP, SRE, PSI, Group B, Analytics or main.
- Code commits: `cb154e9` (eight protocols, pure service, missing adapters), `76c93c3` (capability diagnostics), `6e80027` (fake-reader tests), `d190d3b` (CI validation), `a31f33e` (integration notes), `b8dfde1` (read-error fallback), `c41e459` (stable public exports), `b92f3da` (env flags diagnostic).
- `readiness()` always includes `durable_handoff_not_installed`, even if the caller supplies seemingly functional adapters and enables both feature flags; **no simulated QDP ingestion or jobs**. Legacy `repository.py` remains dormant until GA-MOD-03/04 replacement.
- CI workflow [Group A standalone](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml) still queued; redundant earlier GA runs cancelled by branch-scoped concurrency and **must not** be counted as test failures.
- Next: implement real GA SQL and disposable PG17 upgrades in GA-MOD-03. Do not interpret GA-MOD-02 IMPLEMENTED as deployable live integration.

## GA-MOD-02 static verification

- Github connector fetched current committed `ports.py`, `service.py`, `config.py`, `adapters/unavailable.py`; expected 8 Protocol declarations present and no direct imports from QDP, PSI, Analytics or Group B: **STATIC PASS**.
- `main...feature/group-a-full-module` comparison: **PASS** (22 commits ahead and zero behind at check, all changed paths GA-owned). No other branches written.
- Public `from intelligence.group_a import GroupAService, GAPorts, GASettings` now exported and explicitly covered by unit regression.
- Exact newest implementation patch `b92f3da53778f0074d6a43067836a26f38d3fa2b`; test execution **QUEUED** at [Group A Actions](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml); the queue and cancelled obsolete runs are NOT failing unit tests.
