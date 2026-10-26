# Unified Analytics — Implementation Tracker
**Canonical branch:** `feature/unified-analytics-full-module` (new branch based on main `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` on 2026-10-26).
**Legacy source branch to PRESERVE:** `feature/analytics-engine-qualified-pipeline-20261023` (PR #23, observed `12921968ef3d5c6f5e706fd8e625c54733405f3b`). **Do not close, delete, rename, force-push or merge without user request.**
**Plan:** `docs/UNIFIED_ANALYTICS_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md` (and TXT copy).
**Last code-implemented:** UA-MOD-00 — bootstrap. **Next:** UA-MOD-01 — PR23 source audit and ownership map.
**QA:** Unit NOT VERIFIED (CI queued); PG17 NOT RUN; integration BLOCKED until peer feature contracts are ready; CI **QUEUED** run #40309471694 (not a PASS).
**Main changed:** NO.

## Permanent instructions for each next chat
1. Read current branch HEAD, full plan and this tracker; inspect latest peer heads and QA results. One user message = one UA-MOD stage, not multiple bundled.
2. Commit only into `feature/unified-analytics-full-module`; main, old PR23, QDP, Group A/B/C and SRE are strictly read-only.
3. New UAE must not copy C's `compute_c_metrics` nor install old conflicting migrations `016_analytics_unified.sql`, `017_analytics_jobs_and_read_gates.sql`, `018_analytics_audit_hardening.sql` under their old names.
4. If code for a stage is done, mark `- [x] ~~UA-MOD-NN~~ (IMPLEMENTED; QA PASS/FAIL/BLOCKED/NOT RUN)`, even if final tests are pending/failed. Record exact commands, errors, SHA, and repair pending QA before claiming release-ready. If implementation is incomplete keep `[ ]`.
5. Never invent live SRE/A/B/C/QDP data, approved configuration, evidence, cohorts, scores or current rights. Missing dependency is unavailable, not a mock production pass.

## Stage checklist
- [x] ~~UA-MOD-00 — Чистая ветка, план, tracker, CI bootstrap~~ (IMPLEMENTED; QA NOT VERIFIED / CI PENDING; PG17 NOT RUN)
- [ ] UA-MOD-01 — Точное наследие PR23 и source-of-truth map
- [ ] UA-MOD-02 — Standalone Ports, DTO & capabilities
- [ ] UA-MOD-03 — Legacy pure qualification gate migration
- [ ] UA-MOD-04 — UAE-owned SQL schema и миграции
- [ ] UA-MOD-05 — Canonical ID/project/source lineage
- [ ] UA-MOD-06 — SRE current approved relevance adapter
- [ ] UA-MOD-07 — QDP handoff & qualified set verification
- [ ] UA-MOD-08 — Group A exact-version verified facts
- [ ] UA-MOD-09 — Group B B1–B8 exact-version reader
- [ ] UA-MOD-10 — Group C persisted score adapter
- [ ] UA-MOD-11 — Current source policy, expiry, revocation
- [ ] UA-MOD-12 — Joined input context and exact version selection
- [ ] UA-MOD-13 — Unified Qualification / Status & blockers
- [ ] UA-MOD-14 — Immutable unified snapshot assembly
- [ ] UA-MOD-15 — Transactional persistence & versioned snapshots
- [ ] UA-MOD-16 — Event inbox/outbox and invalidation lifecycle
- [ ] UA-MOD-17 — Durable qualification Job Queue
- [ ] UA-MOD-18 — Dedicated Python Worker and scheduler control
- [ ] UA-MOD-19 — Read-side visibility views
- [ ] UA-MOD-20 — Descriptive aggregation baseline
- [ ] UA-MOD-21 — Eight typed segmentation dimensions
- [ ] UA-MOD-22 — Compatible strata & cohort comparisons
- [ ] UA-MOD-23 — Sample size and statistical uncertainty
- [ ] UA-MOD-24 — Project-level summaries & rankable views
- [ ] UA-MOD-25 — Patterns Engine handoff contract
- [ ] UA-MOD-26 — HTTP API integration
- [ ] UA-MOD-27 — Unified Analytics Studio
- [ ] UA-MOD-28 — Observability & operational CLI
- [ ] UA-MOD-29 — Source lifecycle & retention purge
- [ ] UA-MOD-30 — Full fixture E2E QDP→A/B/C→UAE
- [ ] UA-MOD-31 — Scale & adversarial QA
- [ ] UA-MOD-32 — Independent release & merge-readiness

## Execution log
| Stage | SHA | Implementation | Unit | PG17 | E2E | CI | Blockers |
|---|---|---|---|---|---|---|---|
| UA-MOD-00 | `838e8cfc73799cc8e1e1e099d7d7ff9e815228f1` | branch, full plan MD/TXT, tracker, contracts, bootstrap tests, workflow | NOT VERIFIED | NOT RUN | NOT RUN | QUEUED #40309471694 | legacy PR23 preserved; QDP/A/B/C/SRE independent |

## Dependency handshakes
| Module | Source branch / PR | UAE integration responsibility |
|---|---|---|
| SRE | `feature/sre-production-v1-20261023`, PR #22 | current LIVE classification + approved hypotheses, human revisions |
| QDP | `feature/qualified-data-pipeline-v1-20261023`, PR #24 | Qualified Set/Preparation, project-video-input SHA |
| Group A | `feature/group-a-full-module`, PR #35 | pinned measured stream completeness and media evidence |
| Group B | `feature/group-b-full-module`, PR #36 | verified B1–B8 linked to exact A analysis |
| Group C | `feature/group-c-full-module`, PR #37 | persisted calculated_metrics and frozen configuration; UAE does not score |
| Unified Analytics old | `feature/analytics-engine-qualified-pipeline-20261023`, PR #23 | READ ONLY reusable code/tests; preserve original |
| Patterns/Insights | Future module | receives evidence-rich descriptive results only, UAE does not infer patterns itself |

## Truth of CI / status
Queued CI is not PASS; a skipped DB suite is not green PG17 validation. A pure fake fixture success does not establish end-to-end integration or live analytics.