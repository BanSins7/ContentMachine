# Group C — Implementation Tracker
Canonical branch: feature/group-c-full-module (created 2026-10-26 from main 218f5f3163ccc7179a4809ba4386a1d1b6ff80f5).
Plan: docs/GROUP_C_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md.
Existing source schema: 003_performance_c.sql and 004_performance_c_integrity.sql — unchanged main authority.
Last code-implemented stage: GC-MOD-01 (audited/frozen C0–C7 contracts and static QA; scoring NOT implemented).
Next stage: GC-MOD-02 (ports + standalone dependency unavailable semantics).
Status: GC-MOD-00 and 01 code IMPLEMENTED; QA unit NOT VERIFIED (no local clone), static remote-content assertions PASS (not Python unit), PG17 NOT RUN; peer integration BLOCKED; CI QUEUED at last check (run #40309249961 at 074416f, current SHA pending runner).

## Mandatory rules for subsequent chat requests
One request = one stage. Read this tracker, full MD plan and GitHub branch HEAD before changes. Implement only Group C in this feature branch; main and sibling branches read-only; commit implementation and tracker updates.
Mark - [x] ~~GC-MOD-NN~~ as IMPLEMENTED even if only last tests fail, but record QA FAIL/BLOCKED/NOT RUN with exact evidence; do not mark QA PASS unless actually observed. Fix failures where feasible before proceeding. Partial code is NOT IMPLEMENTED.
Every stage logs SHA, touched files, tests with status and blockers. Never use production DB for fixture tests or invent alpha/k/priors.

## Stage checklist
- [x] ~~GC-MOD-00 — Bootstrap ветки~~ (IMPLEMENTED; QA: NOT VERIFIED / CI PENDING)
- [x] ~~GC-MOD-01 — Аудит контрактов C0–C7~~ (IMPLEMENTED; QA: NOT VERIFIED / CI QUEUED)
- [ ] GC-MOD-02 — Ports и независимый модуль
- [ ] GC-MOD-03 — PG17 runtime schema и upgrade
- [ ] GC-MOD-04 — QDP C-intake
- [ ] GC-MOD-05 — Источник фактов D1
- [ ] GC-MOD-06 — C0 Snapshot Read/Validation
- [ ] GC-MOD-07 — Изоляция pure Decimal math
- [ ] GC-MOD-08 — Publication time evidence
- [ ] GC-MOD-09 — Cohort rules
- [ ] GC-MOD-10 — Freeze cohort membership
- [ ] GC-MOD-11 — Review config/cohort
- [ ] GC-MOD-12 — Config activation
- [ ] GC-MOD-13 — C1 Creator Ratio
- [ ] GC-MOD-14 — C2 Niche Outlier
- [ ] GC-MOD-15 — C3 Growth
- [ ] GC-MOD-16 — C4 Raw Engagement
- [ ] GC-MOD-17 — C5 Bayesian Engagement
- [ ] GC-MOD-18 — C6 Freshness
- [ ] GC-MOD-19 — C7 Breakout Score
- [ ] GC-MOD-20 — Calculated metrics persistence
- [ ] GC-MOD-21 — Durable Worker
- [ ] GC-MOD-22 — Incremental recompute
- [ ] GC-MOD-23 — Metric Quality Gate
- [ ] GC-MOD-24 — Retention/revocation
- [ ] GC-MOD-25 — Group C -> Unified Analytics
- [ ] GC-MOD-26 — HTTP API
- [ ] GC-MOD-27 — Group C Studio
- [ ] GC-MOD-28 — Monitoring/recovery
- [ ] GC-MOD-29 — Load/performance QA
- [ ] GC-MOD-30 — Math gold-set QA
- [ ] GC-MOD-31 — PG17 cross-platform E2E
- [ ] GC-MOD-32 — Final release gate

## Stage execution log
| Stage | SHA | Code | Unit | PG17 | E2E | CI | Blocker |
|---|---|---|---|---|---|---|---|
| GC-MOD-00 | `0fea0ecfe3bc2949db94f1e3e72bc5adf78d88f4` | bootstrap branch/plan/contracts/tests/workflow | NOT VERIFIED: local clone DNS failure | NOT RUN | NOT RUN | QUEUED run #40307715486 | QDP/Analytics separate branches; no live adapters |
| GC-MOD-01 | `7f264b63ea078897720aa2f5f7202692a8d3597f` (last code-fix SHA, tracker is a subsequent commit) | `intelligence/group_c/contract_audit.py`; `tests/test_group_c_contract_audit.py`; `docs/GROUP_C_C0_C7_CONTRACT_AUDIT_V1.md`; `.github/workflows/group-c-module.yml`; tracker | NOT VERIFIED: no local GitHub clone/DNS; remote static source assertions PASS (not Python test) | NOT RUN: contract-only stage | NOT RUN: cross-module unimplemented | QUEUED: 40309249961 (at 074416f, before last test fix) | 12 documented SQL/scorer/QDP gaps; GC-MOD-02+ must implement guards, no production C scorer yet |

## Peer handshakes
| Module | Ref | Contract/status |
|---|---|---|
| QDP | feature/qualified-data-pipeline-v1-20261023 (PR24) | C readiness/handoff; requires actual version capability |
| D1/Collector | main / feature/video-collector-v1 | canonical videos and metric_snapshots; no assumption of live metrics |
| SRE | feature/sre-production-v1-20261023 (PR22) | relevance indirectly via QDP |
| Group A | feature/group-a-full-module (PR35) | parallel branch, no direct C dependency |
| Group B | feature/group-b-full-module (PR36) | parallel branch, no direct C dependency |
| Unified Analytics | feature/analytics-engine-qualified-pipeline-20261023 (PR23) | old pure C arithmetic must be replaced by Group C source of truth at integration |

## QA truth
GC-MOD-01 contract matrix: `docs/GROUP_C_C0_C7_CONTRACT_AUDIT_V1.md`; details and 12 audited gaps pinned to precise upstream SHAs. Commands: `python -m unittest discover -s tests -p 'test_group_c_*.py' -v`. The CI workflow now runs both foundation and stage-01 audit on Linux/Windows. A remote file-content assertion is not equivalent to executing the Python test. No local repo available due DNS restriction. CI queued is not PASS. Unit not executed is NOT VERIFIED. PG17 not present is SKIP/NOT RUN. A completed fixture test does not prove provider observation or production scores. No actual C0–C7 execution is claimed at bootstrap.