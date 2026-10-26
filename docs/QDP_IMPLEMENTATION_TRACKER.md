# QDP independent integration — implementation tracker

**Owner:** QDP only. **Repository:** BanSins7/ContentMachine. **Only working branch:** `feature/qualified-data-pipeline-v1-20261023`. No branch rename or duplication. **PR:** #24 (draft → main). This file is a baseline inventory, not a claim of live E2E.

## FIX-00 baseline (26 October 2026)

| Ref | Recorded Git SHA | PR state at audit |
|---|---|---|
| main | `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` | default |
| QDP PR #24 | `f40949c833f735bc57e939d7db98c2b265f366cd` | open draft |
| Collector PR #20 | `a4f658de77fb82de2437474e60c2b917ae2a9811` | open draft |
| SRE PR #22 | `2576c867842ad1445475e75d98c7d7315f6fc962` | open draft |
| Analytics PR #23 | `12921968ef3d5c6f5e706fd8e625c54733405f3b` | open draft |
| Group A PR #26 | `ae9fa80f6b53eb4ba2fe16eb3ecf65102e13bc7d` | open draft; base QDP PR #24 |
| PSI PR #29 | `1970cd0620e826542c4cae70db4d4cf8f824a607` | merged |
| Knowledge Extraction B PR #30 | `f0c9e76a8eb270a66c3654cde28a1dc0b5df21a7` | merged |

These observations must be refreshed before every next stage; a historical PR HEAD is not a lock on any peer. Stage FIX-00 only writes QDP-owned paths and its dedicated CI workflow. Its work must not be merged into main without explicit review.

## Existing QDP baseline, verified by file inspection

- `intelligence/qualified_data/contracts.py`: pure qualification, configuration, A/B/C readiness.
- `repository.py` (559 lines at baseline): read-only preview, create_run, D1 hydration, background processing, runs/cancel; `_sre_snapshot` does **not** test `execution_mode='live'`.
- `worker.py`: namespaced `qdp.prepare_video` background worker, lease handling and revoke purge.
- `http_api.py` and `site/qualified-data.js`: local preview/runs/detail/cancel endpoints.
- `intelligence/migrations/016_qualified_data_pipeline.sql`: QDP runs/items/enrichments/preparations/events and a broad read-gated view.
- `tests/test_qualified_data_pipeline.py`, `tests/test_qualified_data_db.py` and `.github/workflows/qdp-tests.yml`: original offline and disposable-PG17 regression.

The current `qdp_readable_enrichments` view has cross-domain media/metrics gates. FIX-10 must split group-current eligibility. The existing `qdp_events` table is an audit journal, not a transactional per-consumer outbox. Existing `_authorized_metadata` uses latest permitted metadata rather than pinned SRE observation. Existing SRE tests construct completed runs without live execution mode; FIX-01 must update fixtures instead of treating them as production live.

## FIX stage ledger

Each stage records its own Git SHA, changes, tests and external blockers; stages are independent of peer code readiness but require honesty about live integration.

| Stage | QDP-owned deliverable | Status |
|---|---|---|
| 00 | Baseline, typed port declarations, dependency matrix, event/ID contracts, migration inventory, regression evidence | IN_PROGRESS (commit/CI verification required) |
| 01 | Strong LIVE SRE intake + eligible run reader | IN_PROGRESS (code committed; new SHA CI not yet verified) |
| 02 | Coverage, evidence, complete manifest, source lineage | TODO |
| 03 | Forward-only QDP schema, shared migration reconciliation | TODO |
| 04 | Deterministic multi-label human-reviewed resolver | TODO |
| 05 | SRE event inbox / durable replay ACK | TODO |
| 06 | Immutable runs/manifest, exact jobs/idempotency | TODO |
| 07 | Exact-source metadata hydration | TODO |
| 08 | Compatible metric history | TODO |
| 09 | Valid media readiness | TODO |
| 10 | Group-specific A/B/C preparation/current views | TODO |
| 11 | Worker leases/crashes/reaper/cancellation | TODO |
| 12 | QDP outbox and consumer ACK | TODO |
| 13 | Group A A1 handoff adapter | TODO |
| 14 | Group B/C consumer and Analytics contracts | TODO |
| 15 | Human/source invalidation and immutable refresh | TODO |
| 16 | API and QDP Studio | TODO |
| 17 | Performance/monitoring 10k | TODO |
| 18 | 120 QA scenarios, integrated acceptance and chaos | TODO |
| 19 | Release readiness / owner signoff; no automatic merge | TODO |

**Status meanings:** TODO not implemented; IN_PROGRESS partial or not all stage gates verified; BLOCKED only external/unavailable integration or failed gate; DONE code + tests + evidence for this SHA. A QDP port may be DONE while external live E2E stays BLOCKED.

## Baseline CI evidence (before FIX-00 changes)

At QDP `f40949c8`, GitHub Actions reports **success**:
- [QDP isolated integration #39979057642](https://github.com/BanSins7/ContentMachine/actions/runs/39979057642) — PostgreSQL 17 container, migration, QDP offline + PG tests, Python/JS syntax.
- [ContentMachine checks #39979057605](https://github.com/BanSins7/ContentMachine/actions/runs/39979057605) — migrations, Python regression, JS checks.
- [Windows D1 smoke #39979057726](https://github.com/BanSins7/ContentMachine/actions/runs/39979057726) — hosted PG17, unit/PG tests, Windows localhost D1 smoke.

They prove the old baseline only, not FIX-00 changes. Dedicated CI must include new QDP contract tests at the new commit SHA. No direct disposable PG17 instance was provided to this assistant, so no new local PG test was executed.

## Next stage gate

FIX-01 must enforce authoritative `status='completed' AND execution_mode='live'` and approved same-project taxonomy. The SRE branch's `sre_live_classification_results` view currently includes partial runs; filtering the run itself is mandatory. Missing live column/view in current QDP schema is an unavailable dependency, not permission to fall back to legacy results.


## FIX-01 (2026-10-26) — code and contract verification

- Implementation: `intelligence/qualified_data/intake.py` with explicit SRE `execution_mode` capability probe (503 when absent), completed-LIVE + same-project approved taxonomy gating, resolved-model fake guard, DB-authoritative result query with explicit LIVE SQL join, no silent dropping unapproved categories or taxonomy mismatches; read-only eligible LIVE run list with bounded keyset pagination.
- `repository._sre_snapshot` delegates to intake; the existing preview/create/worker route uses these gates.
- Tests: `tests/test_qdp_live_sre_intake.py` (isolated negative and positive cases) and updated `tests/test_qualified_data_db.py` (PG modes and completed/foreign checks).
- Disposable PG fixture creates SRE-like `execution_mode` only on `CM_TEST_PG_DSN`; this is a test stand-in, NOT a production migration nor claim that LIVE SRE provider works. FIX-03 must reconcile actual SRE migrations.
- Full video × category coverage and true content/evidence hashes are explicitly FIX-02; SRE outbox is FIX-05; actual provider/Opus execution not yet integrated.
- New commit SHA / new CI outcome must be recorded before marking this FIX DONE. No external provider calls made.
