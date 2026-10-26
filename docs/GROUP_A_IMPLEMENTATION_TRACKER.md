# Group A — Implementation Tracker (canonical)

**Источник:** [полный план](GROUP_A_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md). Отмечать этап только после проверенного коммита и доступных тестов.

**Current branch:** `feature/group-a-full-module` (created from main `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`)  
**Baseline main SHA:** re-check at GA-MOD-00; last inspected `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`  
**Last implementation completed:** GA-MOD-00 (QA tracked separately)  
**Next:** GA-MOD-01 (contracts and truth semantics)  
**GA-MOD-00 implementation commits:** `6966ce3`, `00342f5`, `e07ddd0`  
**Latest GA HEAD SHA:** inspect the branch ref (the current tracker cannot embed its own commit SHA)  
**QA status:** GA-MOD-00 GitHub Actions QUEUED (not passed); PG17/ QDP NOT RUN, scheduled separately. Marking code complete does **not** imply passing tests.

## Checklist (one stage per request)

- [x] ~~GA-MOD-00 — Чистая ветка, перенос только собственного A1 и постоянный трекер~~ (IMPLEMENTED; QA: QUEUED/NOT RUN)
- [ ] GA-MOD-01 — Контрактная матрица и truth semantics (freeze)
- [ ] GA-MOD-02 — Самостоятельная архитектура портов и адаптеров
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
