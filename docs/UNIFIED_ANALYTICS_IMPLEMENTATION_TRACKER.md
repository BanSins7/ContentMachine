# Unified Analytics — Implementation Tracker
**Canonical branch:** `feature/unified-analytics-full-module` (new branch based on main `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` on 2026-10-26).
**Legacy source branch to PRESERVE:** `feature/analytics-engine-qualified-pipeline-20261023` (PR #23, observed `12921968ef3d5c6f5e706fd8e625c54733405f3b`). **Do not close, delete, rename, force-push or merge without user request.**
**Plan:** `docs/UNIFIED_ANALYTICS_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md` (and TXT copy).
**Last implemented:** UA-MOD-02 — standalone injected UAE ports, DTOs, capability negotiation, inert config and mock regression. **Next:** UA-MOD-03 — Legacy pure qualification gate migration.
**QA:** UA-MOD-02 static source/interface inventory PASS (8 ports, version pins, 4 new source/test files fetched and checked); Python unittest and compileall NOT RUN locally; PostgreSQL17 NOT RUN; peer LIVE handshakes BLOCKED by independent branches; GitHub Actions Unified Analytics run #40313449488 observed QUEUED (not PASS). Earlier UA-MOD-00/01 QA remains unverified.
**Main changed:** NO.

## Permanent instructions for each next chat
1. Read current branch HEAD, full plan and this tracker; inspect latest peer heads and QA results. One user message = one UA-MOD stage, not multiple bundled.
2. Commit only into `feature/unified-analytics-full-module`; main, old PR23, QDP, Group A/B/C and SRE are strictly read-only.
3. New UAE must not copy C's `compute_c_metrics` nor install old conflicting migrations `016_analytics_unified.sql`, `017_analytics_jobs_and_read_gates.sql`, `018_analytics_audit_hardening.sql` under their old names.
4. If code for a stage is done, mark `- [x] ~~UA-MOD-NN~~ (IMPLEMENTED; QA PASS/FAIL/BLOCKED/NOT RUN)`, even if final tests are pending/failed. Record exact commands, errors, SHA, and repair pending QA before claiming release-ready. If implementation is incomplete keep `[ ]`.
5. Never invent live SRE/A/B/C/QDP data, approved configuration, evidence, cohorts, scores or current rights. Missing dependency is unavailable, not a mock production pass.

## Stage checklist
- [x] ~~UA-MOD-00 — Чистая ветка, план, tracker, CI bootstrap~~ (IMPLEMENTED; QA NOT VERIFIED / CI PENDING; PG17 NOT RUN)
- [x] ~~UA-MOD-01 — Точное наследие PR23 и source-of-truth map~~ (IMPLEMENTED; STATIC AUDIT PASS; UNIT NOT RUN; PG17 NOT RUN; CI NOT VERIFIED)
- [x] ~~UA-MOD-02 — Standalone Ports, DTO & capabilities~~ (IMPLEMENTED; STATIC CHECK PASS; UNIT NOT RUN; PG17 NOT RUN; CI QUEUED #40313449488)
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
| UA-MOD-01 | `26cb2aa1c95adff1f633c26618ac870f5c319979` | `docs/UA_LEGACY_PORT_MATRIX.md` (169 lines); PR23 runtime/UI/tests/016–018 statement-level ownership audit; `tests/test_unified_analytics_legacy_audit.py`; CI scope widened to UAE test pattern | NOT RUN; static inventory 18/18 PASS | NOT RUN | NOT RUN | NOT VERIFIED after stage push | PR23 unchanged; SRE/QDP/A/B/C handshake is future; no migration/runtime changed |
| UA-MOD-02 | `d3ac81c841f6900a7f8da915de2019087b56ccf0` | `dto.py`, `ports.py`, `configuration.py`, exports, 13 mock unit tests + contract docs | NOT RUN; static PASS | NOT RUN | NOT RUN | QUEUED #40313449488 | B/C output wires unconfirmed; no peer writes/SQL/qualification |

## UA-MOD-01 verified observations / future work
- Pinned old PR23 HEAD `12921968ef3d5c6f5e706fd8e625c54733405f3b`, 18 ahead / 143 behind `main`; old branch remains READ ONLY.
- Baseline main HEAD `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`; before stage UAE HEAD `92d126ffb7cebceb5bbc0269bce41de8712fe8db`.
- Peer HEADs were captured in [audit map](UA_LEGACY_PORT_MATRIX.md), not assumed integrated.
- No SQL installed/copied; main `016_query_validation.sql`, old `016/017/018_analytics`, QDP/SRE `016_*` demonstrate migration conflict risk.
- Audit checks: actual GitHub fetch of new document, test, CI workflow; source references 18/18; new Python test execution and Windows/Linux CI outcome **not observed**. New test command: `python -m unittest discover -s tests -p 'test_unified_analytics_*.py' -v`. SQL/PG tests remain unrun.
- Fix pending QA during UA-MOD-02 if CI signals a failure. Stage code/documentation stays marked implemented even if CI is unavailable.

## UA-MOD-02 handoff record
- **Source implementation:** `intelligence/unified_analytics/dto.py`, `ports.py`, `configuration.py`, updated `__init__.py`. New regression: `tests/test_unified_analytics_ports.py` (13 designed mock/negative tests). Version-and-risk reference: [UNIFIED_ANALYTICS_PORTS_DTO_V1.md](UNIFIED_ANALYTICS_PORTS_DTO_V1.md).
- **Observed peer HEADs at audit:** SRE `25f4465ff492bf74f1a64e3ad980b52c83c835b4`, QDP `0ed73d76df65cffcc7b16ea965da417f3baed5fc`, A `381fc721814241989d97a20bd0f8bb1502371719`, B `3415c87242bb568ebd732a388bdeed0220c2c848`, C `39512bf236ddb527172eecdb5c5896a3643f25f0`; revisit before future integration.
- **Capability truth:** eight ports optional/default unavailable, structural `declared` never `qualified`. SRE/QDP/A inbound wires pinned to observed version; B/C peer output wires intentionally `None` pending real handshakes. `EvidenceRead.located` is untrusted/unsigned metadata, not eligibility, and cannot bypass later current source-policy, QDP or SRE LIVE checks.
- **Static QA:** GitHub re-fetch of `dto.py`, `ports.py`, `configuration.py`, `test_unified_analytics_ports.py`; 8 port names, strict version/source/policy bindings, no C scorer, disabled implicit execution confirmed. Static checks are **not** Python unit execution.
- **Required commands, NOT RUN:** `python -m unittest discover -s tests -p 'test_unified_analytics_*.py' -v`; `python -m compileall -q intelligence/unified_analytics`. CI observed `queued` at [#40313449488](https://github.com/BanSins7/ContentMachine/actions/runs/40313449488); Windows/Linux outcome NOT VERIFIED. Disposable PostgreSQL 17 E2E NOT RUN (no schema yet).
- **UA-MOD-03:** implement a pure, fail-closed qualification decision over trustworthy future adapter **observations**, not input pointers alone. Do not implement backend SQL, runtime workers or peer adapters as part of stage 03.

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