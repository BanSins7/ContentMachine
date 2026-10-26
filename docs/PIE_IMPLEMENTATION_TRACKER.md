# Patterns & Insights Engine (PIE) — Implementation Tracker
**Canonical branch:** `feature/patterns-insights-full-module` (from main `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` on 2026-10-26).
**Plan:** `docs/PIE_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md`; TXT identical.
**Last implementation stage:** PIE-MOD-04 — isolated additive PIE-owned PostgreSQL schema, DB tests and PostgreSQL 17 CI job. **Next:** PIE-MOD-05 UAE integration reader & handshake.
**QA:** Python 3.13 local PIE module tests: 52 total, 44 PASS, 8 PostgreSQL-dependent SKIPPED (PG server/psycopg unavailable locally); Python compileall PASS; GitHub PIE standalone CI #40318903587 observed QUEUED at PIE-MOD-04 code commit, no verified PASS. Dedicated PG17 fresh/upgrade job added but actual PostgreSQL execution NOT VERIFIED. UAE live handoff BLOCKED; LLM calls NOT RUN.
**DO NOT edit:** main, `feature/unified-analytics-full-module` / PR #38, legacy UAE PR #23, QDP, A/B/C/SRE branches.

## Rules for next chat
One user command 'продолжай работу' = one PIE-MOD stage. Before touching code inspect HEAD, this tracker, full plan, latest peer branch status and prior CI results. Every stage needs implemented code, negative tests, own GitHub commit and tracker update.
Mark `[x] ~~PIE-MOD-NN~~ (IMPLEMENTED; QA: PASS/FAIL/BLOCKED/NOT RUN)` when the code portion is actually finished even if final tests fail or are blocked. Include commands/results and blocker; do not claim QA PASS without observed green runs. If code unfinished keep `[ ]`. Fix failing tests if possible.
No main/peer branch merge without explicit user approval. Do not copy UAE aggregator or Group C scorer. Statistics must use real comparable qualified input, or synthetic **test** fixtures clearly identified.

## Full stage checklist
- [x] ~~PIE-MOD-00 — Bootstrap: clean PIE branch + execution contract~~ (IMPLEMENTED bootstrap; QA: NOT VERIFIED / CI QUEUED; PG17 NOT RUN)
- [x] ~~PIE-MOD-01 — Inventory UAE & statistical ownership freeze~~ (IMPLEMENTED; QA: 5/5 isolated local audit tests PASS; repository CI NOT VERIFIED; live UAE BLOCKED)
- [x] ~~PIE-MOD-02 — Architecture, ports, dependency capability checks~~ (IMPLEMENTED; QA: local 20/20 PASS, compileall PASS; CI QUEUED; live UAE BLOCKED)
- [x] ~~PIE-MOD-03 — Versioned PIE dataset input contract~~ (IMPLEMENTED; QA: local 41/41 PASS + compileall PASS; CI QUEUED, live UAE BLOCKED)
- [x] ~~PIE-MOD-04 — PIE-owned PostgreSQL and migrations~~ (IMPLEMENTED schema/tests/CI; QA: 44 local tests PASS, 8 PG tests SKIPPED; PG17 and GitHub CI NOT VERIFIED)
- [ ] PIE-MOD-05 — UAE integration reader & handshake
- [ ] PIE-MOD-06 — Provenance & source-eligibility gate
- [ ] PIE-MOD-07 — Reproducible analysis dataset builder
- [ ] PIE-MOD-08 — Comparable strata and source definitions
- [ ] PIE-MOD-09 — Outcome and comparison policy
- [ ] PIE-MOD-10 — Controls and comparison group assembly
- [ ] PIE-MOD-11 — Feature matrix A/B/C selection
- [ ] PIE-MOD-12 — Missingness, sample and coverage QA
- [ ] PIE-MOD-13 — Candidate discovery: marginal patterns
- [ ] PIE-MOD-14 — Effect size estimation
- [ ] PIE-MOD-15 — Uncertainty estimation & confidence intervals
- [ ] PIE-MOD-16 — Multiple testing and discovery correction
- [ ] PIE-MOD-17 — Holdout / cross-validation split
- [ ] PIE-MOD-18 — Confounder and adjustment strategy
- [ ] PIE-MOD-19 — Temporal and cohort stability tests
- [ ] PIE-MOD-20 — Multifeature interactions
- [ ] PIE-MOD-21 — Story and sequence pattern discovery
- [ ] PIE-MOD-22 — Continuous technical factor patterns
- [ ] PIE-MOD-23 — Robustness, sensitivity and negative controls
- [ ] PIE-MOD-24 — Pattern quality scoring and release gates
- [ ] PIE-MOD-25 — Immutable Pattern Registry
- [ ] PIE-MOD-26 — Pattern retrieval, explanations and dashboard queries
- [ ] PIE-MOD-27 — Pattern invalidation and drift monitor
- [ ] PIE-MOD-28 — Insight semantic specification and prompt templates
- [ ] PIE-MOD-29 — LLM model adapter & spend governance
- [ ] PIE-MOD-30 — Insight Generation Engine
- [ ] PIE-MOD-31 — Insight verification, contradictions & hallucination guard
- [ ] PIE-MOD-32 — Human Review and feedback loop
- [ ] PIE-MOD-33 — Orchestrator, Workers, queue and retries
- [ ] PIE-MOD-34 — HTTP API, Studio and Pattern Explorer
- [ ] PIE-MOD-35 — E2E research quality, performance and standalone release

## Execution log
| Stage | Implemented SHA | Code | Unit | PG17 | Model E2E | CI | Blockers |
|---|---|---|---|---|---|---|---|
| PIE-MOD-00 | `ece899d59b5595ed433cb91e44f020abd1dbe4d4` | branch, 36-stage plan MD/TXT, tracker, versioned input pointer, tests, CI, integration contract | NOT VERIFIED | NOT RUN | NOT RUN | QUEUED run #40311890159 | UAE PR38 progressed through UA-MOD-01 and is evolving in parallel; real row-level research manifest not yet verified |
| PIE-MOD-01 | `412cc3960b11747954134e2478c03451c10c3e96` | pinned old PR23 / new UAE source inventory; ownership + data dictionary + handoff blockers; 2 signed synthetic fixtures; fail-closed negative tests; CI test discovery widened | 5/5 isolated harness PASS; full repo NOT VERIFIED | NOT RUN | BLOCKED (no UAE reader) | NOT VERIFIED | UAE [d2ac64c](https://github.com/BanSins7/ContentMachine/commit/d2ac64cd4af04773988c6c6060345c69a61cf99d) UA-MOD-01 only, no production qualified manifest |
| PIE-MOD-02 | `d04389de1a2d3da97f982f2028c5d102096af18a` + docs commit | PIE-owned six Protocols, frozen capability declaration/state, strict adapter + proposed peer wire checks, sanitized errors, opt-in settings, 11 new port unit tests, ports architecture docs | local isolated PIE subset 20/20 PASS, compileall PASS | NOT RUN | BLOCKED (no UAE row-level reader) | QUEUED #40314820541 (code commit) | UAE stage UA-MOD-02 only; no proof of live qualified input, current source rights or human-approved patterns |
| PIE-MOD-03 | `2ccf65db12e1959d649f757a665ef076e0993508` | Proposed `uae-to-pie/v1` envelope; exact pointer+row/feature SHA binding; canonical IDs, current-version equality, frozen included/excluded sets, typed feature claims, order and time checks; synthetic fixtures and 21 adversarial/replay tests | 41/41 local PIE subset PASS; compileall PASS; GitHub CI queued | NOT RUN | BLOCKED (no authenticated UAE row-level provider) | QUEUED #40316717444 | UAE handoff UA-MOD-25 not verified; structural SHA != authenticated data or current source rights |
| PIE-MOD-04 | `3c1b737d4e8ce68745529a5f76823a7d13da66e6` | `200_pie_standalone_foundation.sql`: 14 isolated PIE tables, source-policy matching trigger, scoped FK references, append-only evidence, spend-guard, queued jobs/outbox, PG17 dedicated CI and docs/tests | local 44 PASS, 8 PG SKIPPED; compileall PASS | NOT VERIFIED; PG17 CI QUEUED | BLOCKED (no UAE reader) | QUEUED #40318903587 | PostgreSQL engine not present locally; no verified fresh/upgrade/SQL runtime results; ensure peer merge compatibility |

## Peer handshake references
| System | Branch or PR | Dependency and evidence |
|---|---|---|
| Unified Analytics (UAE) | `feature/unified-analytics-full-module` / PR #38 (observed SHA `d3ac81c841f6900a7f8da915de2019087b56ccf0`) | UAE progressed through UA-MOD-02; ports are metadata only, UAE row-level handoff pending UA-MOD-25 |
| UAE historical | `feature/analytics-engine-qualified-pipeline-20261023` / PR #23 | Descriptive aggregator / 8 dimensions reference, do not duplicate or modify |
| Group A | `feature/group-a-full-module` / PR #35 | PIE indirectly uses A features via UAE, never bypasses UAE quality gate |
| Group B | `feature/group-b-full-module` / PR #36 | PIE indirectly uses B1–B8 semantics via UAE |
| Group C | `feature/group-c-full-module` / PR #37 | C score comes from UAE, not recalculated by PIE; C7 not 0..100 |
| SRE / QDP | PR #22 / PR #24 | PIE uses only UAE-approved scope, project membership and current relevance |
| Strategy & Ideation | Future module | consumes verified reviewed insights only; not implemented here |

## PIE-MOD-01 evidence and next-stage decisions
- [Audit + pinned source-to-field matrix](PIE_UAE_OWNERSHIP_AUDIT.md) records old aggregate and read-time gate vs new UAE bootstrap. Test fixtures are synthetic test-only HMAC and never attest current rights or qualified production rows.
- Run on branch: `python -m unittest discover -s tests -p 'test_pie_*.py' -v`; `python -m compileall -q intelligence/patterns_insights`. CI workflow now discovers all `test_pie_*.py`. Full repository CI requires observed completed run; don't claim PASS until then.
- No peer files, schema migrations, PRs or `main` changed. PIE-MOD-02 must build typed capability ports without importing UAE.

## PIE-MOD-02 evidence and next-stage decisions
- [Ports architecture and exact capability matrix](PIE_PORTS_ARCHITECTURE.md). Local Python 3.13 isolated PIE subset: `python -m unittest discover -s tests -p 'test_pie_*.py' -q` -> 20/20 PASS; `python -m compileall -q intelligence/patterns_insights` -> PASS. Full branch and GitHub Windows/Linux CI **NOT VERIFIED**, queued run is not green.
- `intelligence/patterns_insights/ports.py`, `settings.py`, exports, `tests/test_pie_ports.py` committed. No paid calls, workers or production provider dispatch.
- UAE `uae-to-pie/v1` remains a **proposed** peer wire; next PIE-MOD-03 must validate exact row-level fixtures, and PIE-MOD-05/06 will perform actual source and permission handshake. Do not fabricate live integration.

## PIE-MOD-03 evidence and next-stage decisions
- [Exact proposed dataset contract](PIE_DATASET_CONTRACT_V1.md). `intelligence/patterns_insights/dataset_contract.py` and `test_pie_dataset_contract.py` with 2 synthetic fixtures; no production samples, no duplicated UAE scorer/aggregator or runtime adapter.
- **Locally executed on `/mnt/data/pie_stage03`**: `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_pie_*.py' -v` -> **41 tests OK** (Python 3.13); `python -m compileall -q intelligence/patterns_insights` -> **PASS**. GitHub content SHA of all six changed code/test/fixture/docs files matched locally computed git blob IDs byte-for-byte; GitHub Actions code commit PIE workflow #40316717444 **QUEUED**, not PASS. PG17 **NOT RUN**.
- UAE peer head observed during contract design: `12d937d2fbbae22fd4c2bfd910798078c1dfef63` (not a completed dataset handoff). PIE-MOD-04 must audit migration numbering/ownership without installing old 016–018 analytics migration under conflicting names. `PIEPorts().require('uae')` remains unavailable unless explicitly injected; no bypass of current qualified-only source gate.

## PIE-MOD-04 implementation and QA record
- [Database schema, ownership, FK/immutability/budget design](PIE_MOD04_POSTGRESQL.md) and [PG17 verification commands](PIE_MOD04_QA_COMMANDS.md).
- New migration: `intelligence/migrations/200_pie_standalone_foundation.sql`, exclusively additive PIE objects; same shared `intelligence.migrate` applies migration with SHA lock and per-filename tracking. Current main is `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` (max 053); SRE/A/B/C currently occupy 054–056 in peer branches, QDP separate 040–043. Recheck upon integration; main/peers remain untouched.
- Executed **locally** from `/mnt/data/pie_stage04`: `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_pie_*.py' -q`: **52 tests, 44 PASS, 8 SKIPPED** due to absent `CM_TEST_PG_DSN`; `python -m compileall -q intelligence/patterns_insights tests/test_pie_db_schema.py`: PASS. Local SHA of 5 changed files matched live GitHub file SHA. PostgreSQL 17 execution **NOT RUN**, not a green result.
- Dedicated Linux `postgres17-schema` job in `.github/workflows/pie-module.yml` is configured for separate disposable fresh and upgrade databases, `psycopg[binary]`, and `test_pie_db_schema.py`; Windows/Linux static tests remain in job matrix. Workflow #40318903587 observed **queued** after code push. Never infer actual SQL success until completed CI reports PASS.
- UAE HEAD observed `37486fe5b45d04fa48de6015b48c220ed07da32e` while closing stage. PIE's database permits storing **unverified** pointer-derived metadata only; authorizing source/qualified UAE evidence, visibility and publication requires later PIE-MOD-05/06/27/32/33/34. Schema records alone never attest real data or human approval.
- NEXT PIE-MOD-05 — build a fail-closed real UAE adapter when upstream interface actually exists; otherwise typed `capability_unavailable` and do not fabricate qualified samples.

## QA truth
CI QUEUED != PASS. Offline pure-pointer tests do not prove real qualified UAE data. No live statistical conclusions, paid model results or production readiness asserted at bootstrap.