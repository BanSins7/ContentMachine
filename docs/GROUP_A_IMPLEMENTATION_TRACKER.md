# Group A — Implementation Tracker (template)

**Источник:** `ContentMachine_Group_A_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md`; в GA-MOD-00 перенести этот файл в `docs/GROUP_A_IMPLEMENTATION_TRACKER.md` канонической GA-ветки и заполнять после каждого этапа.

**Current branch:** `feature/group-a-full-module` (created from main `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`)  
**Baseline main SHA:** re-check at GA-MOD-00; last inspected `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`  
**Last completed:** none  
**Next:** GA-MOD-00 (bootstrap verification and contract isolation pending)  
**Latest GA HEAD SHA:** N/A  
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
| — | — | Planning only | NOT RUN | NOT RUN | NOT RUN | NOT RUN | GitHub branch bootstrap |

## Dependency handshake / status

- QDP: `qdp-handoff/v1`, availability and version to be confirmed on each stage.
- SRE: no import dependency; SRE model selection Embeddings + Opus 5.5 does not affect GA extraction algorithms.
- PSI: `transcript/v1` / `visual-preparation/v1` reuse only with exact verified media hash mapping.
- Group B: independent consumer of A003/A004 and `group-a-to-b/v1`; CTA semantics owned by B.
- Analytics: reads version-pinned A snapshots, no GA inference claims.

## Update rule

After real commit/tests, replace completed checkbox with `- [x] ~~GA-MOD-NN — Title~~` and add commit SHA and test evidence. Never close unfinished stages.