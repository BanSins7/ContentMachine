# QDP independent integration — implementation tracker

**FULL 20-stage CHECKLIST WITH STRIKETHROUGH STATUS:** [QDP_FULL_IMPLEMENTATION_PLAN.md](QDP_FULL_IMPLEMENTATION_PLAN.md). The markdown checklist is the canonical cross-chat handoff, tracker retains evidence.

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
| 02 | Coverage, evidence, complete manifest, source lineage | IN_PROGRESS (implementation and test cases committed; new exact-SHA QA pending) |
| 03 | Forward-only QDP schema, shared migration reconciliation | IN_PROGRESS (SQL + schema guard + tests committed; PG17/merged-schema QA pending) |
| 04 | Deterministic multi-label human-reviewed resolver | IN_PROGRESS (pure resolver + DB wiring + regression tests committed; CI/PG17 unverified) |
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


## FIX-02 implementation submission — 2026-10-26

- SRE HEAD read at stage entry: `75e17f2e67c6deb9bd6e3500c7c5cf49ad7f29de`. Source contract: `intelligence/relevance/v1_runtime.py::validate_run_plan`; exact `items[]{video_id,content_version_id,content_sha256,hypothesis_keys[]}`.
- Code: `coverage.py`, `intake.py::verify_live_coverage`, `repository._sre_snapshot`; pinned manifest×approved taxonomy leaves×DB results, raw content SHA, stage/evidence checks, human effective decision retains model evidence, target-video revalidation.
- Unit: `tests/test_qdp_coverage.py` (2000 pairs, missing/duplicate, mismatched category/content/hash, quote grounding, fake provenance, manifest identity, targeted refresh and hash stability), updated `test_qdp_live_sre_intake.py`.
- Integration: `tests/test_qualified_data_db.py` augmented with disposable SRE-like `content_sha256` and `dataset_snapshot`, plus negative manifest/hash tests.
- This does NOT prove LIVE Opus execution, SRE S5/S6 completion, SRE event outbox, completed cross-branch migrations or coverage CI on current SHA. Remain IN_PROGRESS until verified.
- Next: FIX-03 additive QDP migration reconciliation; revisit FIX-00/01/02 test gates first.


## FIX-03 forward-only integration schema — 2026-10-26

- QDP HEAD at stage start: `303dd5e119d64b3e902a3a4756b4f3f4cbc5836d`. Main unchanged: `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`. SRE PR #22 head at stage entry `c9fc55bf0acfe4cda8b6e4f5fe9283e5f905eb78`.
- `040_qdp_integration_foundation.sql` = single additive QDP-owned forward migration following main `030..039`; original `016_qualified_data_pipeline.sql` untouched. No modifications to peers.
- `schema.py`: pure topology, original baseline Git SHA-1 blob verification, installed version+SHA256 read-only preflight; distinguishes missing SRE migrated columns from QDP schema.
- New `tests/test_qdp_schema_migrations.py`: base 016 checksum-lock, topology, tables/FKs/static DDL, conditional PG17 fresh/upgrade repeat no-op, checksum tamper rollback, inbox constraints.
- Extended `tests/test_qualified_data_db.py`: SRE no_match lineage saved without qualified item, immutable fact, cross-category FK guard.
- `.github/workflows/qdp-tests.yml`: QDP branch push + PR triggers and new integration schema suite.
- **QA GATE OPEN:** standalone GitHub workflow runs on new HEAD absent when checked, no direct disposable PostgreSQL 17 DSN. **Actual multi-branch merged schema compatibility also not demonstrated.** Do not mark DONE or strike-through.
- Next stage once available: run exact-SHA PG17 actions; if no runner exists, retain documented environment blocker and continue QDP-owned code with explicit IN_PROGRESS status. FIX-04 is deterministic Qualified Set Resolver.


## FIX-04 deterministic resolver — 2026-10-26

- Stage entry QDP `a44b6942a4aa15688554cc4f3f0b61b0df35abca`; `main` was `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`.
- New `intelligence/qualified_data/qualification.py`: pure deterministic resolver, one video = one status, all positive leaf/category refs preserved, current per-leaf human labels override raw SRE model decision, inaccessible match unresolved. Strict LIVE mode enforces canonical UUID, pinned content hash/version and human provenance; compact old API preserved.
- `intake.get_live_result_rows` now reads human_label_id, human_label_decision, review_created_at from authoritative lateral same-project/taxonomy/category query. This is not browser-controlled.
- `repository.create_run` fingerprints exact SRE taxonomy manifest + verified selection + human review watermark. Existing historical v1-only runs with matching idempotency key will not silently replay as trusted v2; require new key/run if its fingerprint differs. Existing method/routes retained.
- `repository._process_locked` compares still-eligible category keys/result IDs against immutable `qdp_items` before writing preparations. A changed matched leaf requires explicit refresh; future FIX-15 will propagate invalidation downstream and protect previously completed readiness.
- Tests: `tests/test_qdp_qualification.py` pure cases (1-of-20, 5 statuses, human veto/promote, missing content, malformed IDs, 3-state partition, order-independent hashes, legacy wrapper) plus PG17 tests for veto/replay/Worker guard.
- Test workflow runs for `0870bb2e`, `31f5e199`, `4054a3ec` were QUEUED at latest inspection; no new green exact-SHA result. FIX-00..04 remain IN_PROGRESS, no strikethrough.
- **Current external blocker:** GitHub Actions runners not yet executing jobs. No production PG17 DSN available; integration QA cannot be inferred from prior green `f40949c8`.
