# Group C — Implementation Tracker
Canonical branch: feature/group-c-full-module (created 2026-10-26 from main 218f5f3163ccc7179a4809ba4386a1d1b6ff80f5).
Plan: docs/GROUP_C_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md.
Existing source schema: 003_performance_c.sql and 004_performance_c_integrity.sql — unchanged main authority.
Last code-implemented stage: GC-MOD-02 (versioned standalone ports, strict QDP wire, fail-closed dependency gates; scoring NOT implemented).
Next stage: GC-MOD-03 (additive PG17 runtime schema and fresh/upgrade verification).
Status: GC-MOD-00..02 code IMPLEMENTED. GC-MOD-02 new port tests local-isolated 9/9 PASS (Python 3.13; SHA-identical GitHub files); full-repository suite NOT RUN (no network clone), PG17 NOT RUN, live peer handshake BLOCKED; GitHub Actions pending, do not mark PASS.

## Mandatory rules for subsequent chat requests
One request = one stage. Read this tracker, full MD plan and GitHub branch HEAD before changes. Implement only Group C in this feature branch; main and sibling branches read-only; commit implementation and tracker updates.
Mark - [x] ~~GC-MOD-NN~~ as IMPLEMENTED even if only last tests fail, but record QA FAIL/BLOCKED/NOT RUN with exact evidence; do not mark QA PASS unless actually observed. Fix failures where feasible before proceeding. Partial code is NOT IMPLEMENTED.
Every stage logs SHA, touched files, tests with status and blockers. Never use production DB for fixture tests or invent alpha/k/priors.

## Stage checklist
- [x] ~~GC-MOD-00 — Bootstrap ветки~~ (IMPLEMENTED; QA: NOT VERIFIED / CI PENDING)
- [x] ~~GC-MOD-01 — Аудит контрактов C0–C7~~ (IMPLEMENTED; QA: NOT VERIFIED / CI QUEUED)
- [x] ~~GC-MOD-02 — Ports и независимый модуль~~ (IMPLEMENTED; 9 isolated pure tests PASS, full project QA NOT RUN / CI PENDING)
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

| GC-MOD-02 | `f83d084d0457b1d9b16a680a31695534cf824c7e` (code facade SHA; docs/tracker follow) | `intelligence/group_c/ports.py`, `intelligence/group_c/__init__.py`, `tests/test_group_c_ports.py`, `docs/GROUP_C_STAGE02_PORTS.md`, integration contract | 9/9 PASS: isolated local Python 3.13 tests with SHA-identical ports.py/test file; full repo NOT RUN | NOT RUN (interface-only) | BLOCKED: production QDP/D1/Analytics adapters not implemented | QUEUED/UNKNOWN until current HEAD check | Pure dependency gates only; no scorer, durable inbox or live consumer |

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

## GC-MOD-02 evidence
- Source and test file SHA-1 Git blobs verified byte-for-byte against local tested files: `ports.py` = `81232cfd3d704a12c9ec5594384eddf7247600f2`; `test_group_c_ports.py` = `1373224b380aec60e23c6db2cba3f15a7c2fffe7`.
- `python -m unittest discover -s tests -p 'test_group_c_ports.py' -v`: 9 tests PASS in a local isolated mirror. The mirror substitutes only a minimal compatible ContractError and GroupCInputPointer because full repo checkout is unavailable; therefore this is not full project QA.
- `python -m unittest discover -s tests -p 'test_group_c_*.py' -v` must still run in GitHub CI; pending is not green.
- GC-MOD-03 should first reconcile migration numbers and applied checksum history before adding new DB objects.
