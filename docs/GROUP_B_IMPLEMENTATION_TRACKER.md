# Group B — Implementation Tracker
**Canonical branch:** `feature/group-b-full-module` (created from main `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` 2026-10-26).
**Plan:** `docs/GROUP_B_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md`.
**Source schema:** `intelligence/migrations/005_content_features_b.sql` is already owned by main; never modify.
**Last implemented stage:** GB-MOD-00 — initial bootstrap (QA: NOT RUN until independently observed).
**Next stage:** GB-MOD-01 — B1–B8 contract matrix and truth semantics.
**CI status:** QUEUED (initial run #40305301323; no results yet). **PG17 status:** NOT RUN (no disposable DSN attached). **Real LLM paid E2E:** NOT RUN (approval required).

## Rules for continuing chats
Read this tracker and plan before every code change. One user request = one stage. Mark implementation as checked/struck-through even if the final tests fail, but record exact failed tests and continue repairing QA in the next request; never claim a PASS without observed evidence. Update current stage, commit SHA, files, test commands, blockers, next stage. Preserve all existing stages and actual evidence. Do not edit main/peer branches.

## Stage checklist
- [x] ~~GB-MOD-00 — Самостоятельная ветка и bootstrap~~ (IMPLEMENTED: bootstrap branch + docs/package/pure unit; QA: NOT RUN/CI PENDING; PG17 NOT RUN)
- [ ] GB-MOD-01 — Аудит реального кода и заморозка B1–B8
- [ ] GB-MOD-02 — Структура независимого модуля и Ports
- [ ] GB-MOD-03 — Собственные SQL migrations и upgrade
- [ ] GB-MOD-04 — QDP Inbox и versioned preparation
- [ ] GB-MOD-05 — Group A exact-version adapter
- [ ] GB-MOD-06 — Media Evidence Manifest
- [ ] GB-MOD-07 — Readiness & dependencies по B1–B8
- [ ] GB-MOD-08 — Context Builder и budgetable manifests
- [ ] GB-MOD-09 — Model Provider и capability probe
- [ ] GB-MOD-10 — Cost Manager и ledger
- [ ] GB-MOD-11 — Job engine и orchestrator
- [ ] GB-MOD-12 — B5 Editing — полностью локальный
- [ ] GB-MOD-13 — B1 Hook semantic classifier
- [ ] GB-MOD-14 — B2 Format classifier
- [ ] GB-MOD-15 — B3 Story sampling engine
- [ ] GB-MOD-16 — B3 Story semantics & patterns
- [ ] GB-MOD-17 — B4 Visual content
- [ ] GB-MOD-18 — B6 Subtitle style
- [ ] GB-MOD-19 — B7 CTA occurrences
- [ ] GB-MOD-20 — B8 Topic registry & resolver
- [ ] GB-MOD-21 — Unified evidence/schema validator
- [ ] GB-MOD-22 — Atomic write B1–B8 в schema 005
- [ ] GB-MOD-23 — Human Review, gold set, quality
- [ ] GB-MOD-24 — Downstream invalidation & refresh
- [ ] GB-MOD-25 — Analytics adapter & contract handoff
- [ ] GB-MOD-26 — API & Studio
- [ ] GB-MOD-27 — Instrumentation, throughput & recovery
- [ ] GB-MOD-28 — Полный QA и cross-platform CI
- [ ] GB-MOD-29 — Final merge-readiness & standalone release

## Execution log
| Stage | Commit SHA | Implemented scope | Unit | PG17 | Model smoke | CI | Blockers |
|---|---|---|---|---|---|---|---|
| GB-MOD-00 | `baa097ffe199b85b071ca89e351144ab59c2c751` | branch, full plan MD/TXT, contract README, initial pure Python boundaries, CI | NOT RUN | NOT RUN | NOT RUN | QUEUED (run #40305301323) | Group A/QDP/Analytics independent branches; no live adapters yet |

## Peer integration and known blockers
| Module | Observed branch | Available integration | Notes |
|---|---|---|---|
| QDP | feature/qualified-data-pipeline-v1-20261023 | proposed qdp-handoff/v1; requires handshake | QDP ready does not mean A complete |
| Group A | feature/group-a-full-module | proposed group-a-to-b/v1 | current branch evolves, verify SHA |
| SRE | feature/sre-production-v1-20261023 | indirect via QDP | embeddings+Opus5.5 in SRE, no B classifier reuse |
| Unified Analytics | feature/analytics-engine-qualified-pipeline-20261023 | reads B1-B8 from main schema | must verify exact A/B relationship |
| Group C | Analytics branch | independent metrics | B must not manufacture C output |

## QA truth
IMPLEMENTED != PASSED. A failed or skipped test is documented as FAIL/BLOCKED/NOT RUN and is never silently treated as success.
