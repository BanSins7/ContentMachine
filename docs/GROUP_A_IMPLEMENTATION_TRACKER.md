# Group A — Implementation Tracker (canonical)

**Источник:** [полный план](GROUP_A_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md). Отмечать этап только после проверенного коммита и доступных тестов.

**Current branch:** `feature/group-a-full-module` (created from main `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`)  
**Baseline main SHA:** re-check at GA-MOD-00; last inspected `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`  
**Last completed:** none; GA-MOD-00 in progress  
**Next:** GA-MOD-00 (final CI and stage evidence pending)  
**GA-MOD-00 implementation commit:** `6966ce38b270a2d1f6099825b52984192a98d530`  
**Latest GA HEAD SHA:** inspect the branch ref (the current tracker cannot embed its own commit SHA)  
**QA status:** bootstrap sources copied; runtime PostgreSQL validation pending GA-MOD-03

## Checklist (one stage per request)

- [ ] GA-MOD-00 — Чистая ветка, перенос только собственного A1 и постоянный трекер
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
| GA-MOD-00 (in progress) | `6966ce3` | capabilities.py, http_api.py, worker.py, bootstrap tests, CI, docs | QUEUED in GitHub Actions | NOT RUN (QDP unavailable) | QUEUED in GitHub Actions | QUEUED | Full GA bootstrap check pending live CI result |

## Dependency handshake / status

- QDP: `qdp-handoff/v1`, availability and version to be confirmed on each stage.
- SRE: no import dependency; SRE model selection Embeddings + Opus 5.5 does not affect GA extraction algorithms.
- PSI: `transcript/v1` / `visual-preparation/v1` reuse only with exact verified media hash mapping.
- Group B: independent consumer of A003/A004 and `group-a-to-b/v1`; CTA semantics owned by B.
- Analytics: reads version-pinned A snapshots, no GA inference claims.

## Update rule

After real commit/tests, replace completed checkbox with `- [x] ~~GA-MOD-NN — Title~~` and add commit SHA and test evidence. Never close unfinished stages.
## GA-MOD-00 progress

- Branch based on main baseline; preserved A1 and its legacy SQL outside the migrator.
- Added explicit `qdp_handoff_not_integrated` capability, HTTP/automatic worker disabled during bootstrap.
- Conditionalized QDP-only DB fixture import to allow offline test discovery.
- Added GA-only CI, integration guide, release evidence and full plan alias.
- Implementation commit `6966ce38b270a2d1f6099825b52984192a98d530` pushed; [bootstrap CI run](https://github.com/BanSins7/ContentMachine/actions/runs/40298383330) queued at last check. Completion pending test results; do not claim PostgreSQL/QDP E2E.
- Repository public entrypoints now deny direct calls without a negotiated QDP handoff; new regression tests cover API, worker and repository entrypoints.
