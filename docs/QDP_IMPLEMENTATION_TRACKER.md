# QDP independent integration — implementation tracker

**FULL 20-stage CHECKLIST (CODE vs QA separated):** [QDP_FULL_IMPLEMENTATION_PLAN.md](QDP_FULL_IMPLEMENTATION_PLAN.md). The markdown checklist is the canonical cross-chat handoff, tracker retains evidence.

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
| 00 | Baseline, typed port declarations, dependency matrix, event/ID contracts, migration inventory, regression evidence | CODE_IMPLEMENTED; QA_QUEUED/NOT_RUN |
| 01 | Strong LIVE SRE intake + eligible run reader | CODE_IMPLEMENTED; QA_QUEUED/NOT_RUN |
| 02 | Coverage, evidence, complete manifest, source lineage | CODE_IMPLEMENTED; QA_QUEUED/NOT_RUN |
| 03 | Forward-only QDP schema, shared migration reconciliation | CODE_IMPLEMENTED; QA_QUEUED/NOT_RUN |
| 04 | Deterministic multi-label human-reviewed resolver | CODE_IMPLEMENTED; QA_QUEUED/NOT_RUN |
| 05 | SRE event inbox / durable replay ACK | CODE_IMPLEMENTED; QA_QUEUED/NOT_RUN |
| 06 | Immutable runs/manifest, exact jobs/idempotency | CODE_IMPLEMENTED; QA_QUEUED/NOT_RUN |
| 07 | Exact-source metadata hydration | CODE_IMPLEMENTED; QA_PASS 116/116 PG17 on b677a8dd; LIVE peer BLOCKED_EXTERNAL |
| 08 | Compatible metric history | CODE_IMPLEMENTED; QA_PASS 135/135 PG17 at 8135b762; Group C LIVE BLOCKED_EXTERNAL |
| 09 | Valid media readiness | CODE_IMPLEMENTED; QA_PASS PG17 154/154 at 0ed73d76; LIVE peers BLOCKED_EXTERNAL |
| 10 | Group-specific A/B/C preparation/current views | CODE_IMPLEMENTED; QA_PASS PG17 169/169 at 4931989f; peers BLOCKED_EXTERNAL |
| 11 | Worker leases/crashes/reaper/cancellation | CODE_IMPLEMENTED; QA_PASS PG17 180/180 at 3b623e1b; delivery/ACK FIX-12 |
| 12 | QDP outbox and consumer ACK | CODE_IMPLEMENTED; QA_PASS PG17 197/197 @52838844; LIVE receivers BLOCKED_EXTERNAL |
| 13 | Group A A1 handoff adapter | CODE_IMPLEMENTED; QA_PASS 206/206 PG17 + actual GA inbox PG17 @a39459dc; GA extraction NOT_RUN |
| 14 | Group B/C consumer and Analytics contracts | TODO |
| 15 | Human/source invalidation and immutable refresh | TODO |
| 16 | API and QDP Studio | TODO |
| 17 | Performance/monitoring 10k | TODO |
| 18 | 120 QA scenarios, integrated acceptance and chaos | TODO |
| 19 | Release readiness / owner signoff; no automatic merge | TODO |

**Status meanings (updated at user request):** CODE_IMPLEMENTED = QDP-owned code committed and checklist title struck through. CODE_PARTIAL/TODO = not yet implemented. QA PASS/FAIL/QUEUED/NOT_RUN = separate test result, never inferred from code or old SHA. BLOCKED_EXTERNAL = missing peer integration; not proof of failure in QDP implementation.

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


## FIX-05 trusted SRE outbox → QDP inbox — 2026-10-26

- QDP stage-entry `f7a853efdafa1e671e707e134a410ddcf07b6941`; SRE PR #22 read at `695942aacf88ea7e2b56b4cd6252cdfc0f3df4a5`. Main stayed `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`.
- SRE real contracts observed: `intelligence/relevance/v1_contracts.py` defines `sre-analytics-event/v1` run-completed summary, and `intelligence/relevance/ports/outbound.py` separately defines per-item `sre.classifications_ready/v1`. **QDP consumes only the run-completed summary**; does not trust per-item payloads as completed run.
- Added `intelligence/qualified_data/sre_inbox.py`: internal-only receive, validate exact event and canonical digest, project and same SRE run DB read, LIVE+approved taxonomy, exact manifest pair/evidence coverage, publication pin. No auto create-run, no media, no provider, no jobs.
- Atomic inbox accept/quarantine; same ID+hash safe replay, different hash sanitized conflict audit, ACK only after transaction scope exits successfully. Backed by additive `041_qdp_sre_inbox_quarantine.sql`; 040 and legacy 016 unchanged.
- `schema.py` and schema tests know about 041; new `tests/test_qdp_sre_inbox.py` plus 7 PG tests in `tests/test_qualified_data_db.py` cover invalid event, cross-project, missing/wrong evidence, hash mismatch, crash before commit, ACK loss after commit, concurrent replay and no jobs.
- `docs/QDP_SRE_INBOX_HANDOFF.md` is the integration implementation contract for a subsequent SRE chat.
- **QDP-owned QA still OPEN:** GitHub Actions new code runs remained QUEUED when last checked. No exact-SHA PostgreSQL 17 green evidence.
- **External integration still BLOCKED_EXTERNAL:** real SRE S10/M36 durable publisher/delivery not finished in this branch. QDP module independent; do not import SRE implementation or pretend LIVE outbound tested. 


## Code vs QA status policy — user update, 2026-10-26

- FIX-00..FIX-06: **CODE_IMPLEMENTED** (titles struck through in QDP_FULL_IMPLEMENTATION_PLAN.md). This is a code-implementation marker, **NOT** a test pass or production readiness endorsement.
- Exact-SHA tests on commits after baseline `f40949c8`: **QA_QUEUED/NOT_RUN**. GitHub Actions jobs have `status=queued`, `runner_name=""`. External runner/billing settings are not accessible by the current GitHub connector; underlying cause cannot be proven. Old green CI at `f40949c8` does not validate the latest code.
- Every future FIX should be marked as CODE_IMPLEMENTED when authored and committed; failed tests remain separately documented as QA_FAIL with logs and corrective tasks.
- FIX-06 code commits: `144d585ca5b046da1f217945d02d603a900ae887`, `73907bffe9cf2cd6491438a17da769375cb16802`, `8ca25a57f82b1f915723e3905474df20c9b4c038`. New `run_manifest.py`, atomic inserts to `qdp_run_manifests` + all lineage + exactly one job per qualified video, read-only auditable helper, zero-work manifest, worker revalidation per video/leaf SHA.
- Next: FIX-07 exact-source metadata hydration after rechecking current HEAD and queued CI.

- Fix-06 QA fallback added: `scripts/qdp_verify.py` with guard `QDP_DISPOSABLE_TEST_DB=YES`, actual PostgreSQL 17 version check, separate unit/PG statuses, JSON evidence report, Node/Python syntax. CI now uses it and uploads evidence if runner starts. See [QDP_CI_TRIAGE.md](QDP_CI_TRIAGE.md).
- CI workflow moved to explicit `ubuntu-24.04` and superseded queue cancellation; latest code SHA at this note: `cf85b17e5801f3cae1da47a33544127c7154d8fd`; still queued when observed.

## Confirmed recovery of PostgreSQL 17 CI — 2026-10-26

- GitHub Actions run [40307133052](https://github.com/BanSins7/ContentMachine/actions/runs/40307133052) completed **SUCCESS** for QDP commit `e2844c8ee6efbd7c0e581a40b313915dbfe6624e`.
- Earlier run [40305617307](https://github.com/BanSins7/ContentMachine/actions/runs/40305617307) completed **FAIL** with 105 cases, 2 test failures, 3 test errors. Root causes: dict_row mismatch in tests calling tuple-based shared migration function, incorrect human label category order assertion, incorrect negative+positive lineage expected count. These 5 were fixed in `e2844c8e`; actual PG17 run success verified.
- This success proves QDP FIX-00..06 tests on their preceding code snapshot, NOT FIX-07 changes. No real external LIVE SRE provider or cross-branch merged schema was exercised.
- FIX-07 implementation commits: `7cf8496f`, `50266593`, `44c5e88d`, `8c904a57`, `1ac40dae`. Tests authored; exact current SHA CI still pending at last observation.
- Next stage: FIX-08 metric history with exact source/policy/revisions. Maintain independent CODE and QA statuses.

## FIX-07 verified QA and continuation handoff — 2026-10-26

- [40308355403](https://github.com/BanSins7/ContentMachine/actions/runs/40308355403) for code SHA `b677a8dd7f150a3b1dfabc8d897b995c4f13c9c0`: **SUCCESS** PostgreSQL 17 (reported PG17 17.x), 116/116 tests, 0 failures, 0 errors, 0 skipped, Python syntax PASS, JS syntax PASS; QA report `overall=PASS`.
- FIX-07 exact-source provenance resolved. Earlier [40307884044](https://github.com/BanSins7/ContentMachine/actions/runs/40307884044) failed 2 test errors; both corrected in `b677a8dd`.
- The CI simulated SRE S3-owned fields/links on a disposable database. **Actual SRE branch merge and LIVE SRE provider run not performed**. Integration gate remains `BLOCKED_EXTERNAL` independently of QDP-owned QA PASS.
- **Next independent module stage: FIX-08 Metric Snapshot Enrichment**. Continue in same branch; keep completed code strike-through while recording tests as separate outcomes.


## FIX-08 metric history / Group C — 2026-10-26

- Code: `86d30374c4f54648b843b5c9c67b1367adb45f2f` (read-only scoped 64-row history and pure compatible-pair validator); `437c12323580eac652ac069dadfcebcf44812a4f` (14 unit/5 DB tests); `8135b7620dcd73b4c6e91e18e8c854f7f13ba9c5` (single active config isolation in disposable PG fixture).
- **QA_PASS:** [40310914445](https://github.com/BanSins7/ContentMachine/actions/runs/40310914445) exact code SHA `8135b762`: 135/135, zero failed/errored/skipped, PG17 migration, Python and JS syntax PASS.
- Preserves one-video/source/policy raw D1 snapshots and NULL fields; exact observation times, source metric definition+version, latest non-decreasing view pair, and corrected/retracted safe blocking.
- Group A/B readiness independent of missing/incompatible C metric history; no score, provider or external calls.
- Handoff: [QDP_METRIC_HISTORY_HANDOFF.md](QDP_METRIC_HISTORY_HANDOFF.md). **Real Group C producer/consumer LIVE E2E is BLOCKED_EXTERNAL**, not covered by disposable DB.
- **Next QDP-owned FIX-09:** media asset lookup and eligibility for independent A/B.


## FIX-09 Group A/B real D1 media readiness — 2026-10-26

- Code `7a48245dcc45176116ee94beb7edbb159e29d2f3`: read-only media verification, local path containment, container header/streaming SHA-256, A/B vs C isolation. Code/tests `0ed73d76df65cffcc7b16ea965da417f3baed5fc`: 15 unit + 4 PG17 and legacy assertion updated.
- [40312069484](https://github.com/BanSins7/ContentMachine/actions/runs/40312069484) **FAIL** 1 legacy expected reason; repaired.
- [40312315651](https://github.com/BanSins7/ContentMachine/actions/runs/40312315651) **QA_PASS**: 154/154 tests, 0 failures, 0 errors, 0 skipped; PG17 migrations/Python/JS syntax PASS on exact code SHA `0ed73d76`.
- Peer Group A `feature/group-a-full-module` and Group B `feature/group-b-full-module` require separate current execution checks; carousel media manifest remains BLOCKED_EXTERNAL, not invented.
- **Next: QDP-FIX-10 — Three Independent Preparation Contracts.**


## FIX-10 independent A/B/C immutable contracts — 2026-10-26

- `6743f6ee9d63009579b728a155a6082f6d1e99d5`: independently versioned `preparation.py` A/B/C inputs, exact D1/SRE SHA, separate A/B media and C metrics, no paid work. `042_qdp_group_current_preparations.sql` creates shared source-metadata permission gate + per-group current rights view without global metric/media coupling.
- `18a1166fba804d42f5652936e32fa85ba618d349`: group-specific `get_run` presentation and installed SQL checksum preflight. CI [40314193155](https://github.com/BanSins7/ContentMachine/actions/runs/40314193155) found historic blocked item metadata regression.
- `735adc593600d24faa3a33d93938f2c71375b6fb`: 11 pure tests + 4 isolated PG17 tests (all/any/none readiness, C rights not blocking A/B and vice versa, revocation without changing historical immutable p, project isolation). [40314590625](https://github.com/BanSins7/ContentMachine/actions/runs/40314590625) **FAIL** 1 old test (historical blocked metadata) out of 169.
- `4931989ff51f4e89d12618acfddc5bae8cfd5357`: **new additive forward-only `043_qdp_metadata_readability_fix.sql`** replaces only new FIX-10 base view SQL (no edit of already checksum-locked 042) to permit readable metadata for historically blocked items while keeping `qdp_group_current_preparations` locked to each ready group.
- **Code complete; QA pending exact 043 SHA CI.** No new Group A/B/C task is executed by QDP. Next independent stage FIX-11 Worker Hardening.


## FIX-10 exact-SHA QA closure — 2026-10-26

- [40314853464](https://github.com/BanSins7/ContentMachine/actions/runs/40314853464) on code SHA `4931989ff51f4e89d12618acfddc5bae8cfd5357`: **SUCCESS**, all 169/169 tests passed; zero failures, errors and skips, PostgreSQL 17 migrations and independent group view tests PASS, Python/JS syntax PASS. Test evidence artifact persisted.
- Earlier [40314590625](https://github.com/BanSins7/ContentMachine/actions/runs/40314590625) FAIL 1 existing historical metadata test; forward-only 043 fixes regression. Both run statuses retained honestly.
- QDP-FIX-10 = CODE_IMPLEMENTED and QDP-owned QA_PASS, **not** a claim that Group A/B/C consumer execution or outbound events were tested.
- **Next FIX-11:** Worker Hardening, retries, lease fencing, crash/reaper, exact downstream outbox transaction.


## FIX-11 worker hardening — 2026-10-26

- Code `fc12a458c81f5b267c2115b6ad501ab3d9a88741`: QDP-only SKIP LOCKED claim + generation fenced completion, 30–1800s exponential retries, exhausted lease reaper to item blocked / run failed-or-partial, atomic immutable preparations + outbox and initial ready-group delivery records, truthful completed/partial events. No peer job execution or paid calls.
- Tests `50b7d9ab` plus injection correction `91769fa0` and parameterized PostgreSQL LIKE correction `3b623e1b`. 4 pure and 7 PG17 tests including 10 workers, crash before/after commit, lease reclamation/exhaustion, no duplicate outbox, peer job isolation.
- [40316427951](https://github.com/BanSins7/ContentMachine/actions/runs/40316427951) FAIL: 2 test harness errors/179. [40316619295](https://github.com/BanSins7/ContentMachine/actions/runs/40316619295) FAIL: 3 test SQL parameterization errors/180. All fixed.
- **QA_PASS** [40316720077](https://github.com/BanSins7/ContentMachine/actions/runs/40316720077), exact code SHA `3b623e1b690fc5fc9a5b989ffa467b57fd9e2afb`: 180/180, failed=0, errors=0, skipped=0; PostgreSQL 17 migration + Python/JS syntax PASS; report artifact saved.
- Next: **FIX-12** durable per-consumer delivery, retry/ACK and consumer inbox (not yet implemented). See [QDP_WORKER_HARDENING_HANDOFF.md](QDP_WORKER_HARDENING_HANDOFF.md).


## FIX-12 — per-consumer delivery, replay and ACK (2026-10-26)

- **CODE_IMPLEMENTED** in `outbox_delivery.py`: per-consumer SKIP LOCKED lease claim, generation CAS, backoff/dead-letter, strict event SHA/project/run/video/group validation, current-read gate and durable receipt/ack atomic commit. `receiver=None` fails with 503, never silently acknowledges.
- Standalone consumer acceptance is **an adapter contract**. FIX-12 PG17 tests create a disposable-only peer simulation `qdp_test_consumer_jobs`; it independently commits inbox+job and returns a durable receipt. 50 duplicates→one receiver job and 10 concurrent dispatcher attempts→one receipt were actually tested, **not** a production Group task.
- Append-only transition audit `qdp_delivery_attempt_log` via **forward-only `044_qdp_delivery_attempt_journal.sql`** and FK-cascade correction `045_qdp_delivery_journal_cascade_fix.sql` (no changes to applied 016/040/041/042/043/044). Journal transitions: leased/retry_scheduled/acknowledged/dead_letter; direct UPDATE/DELETE forbidden, parent run deletion legally cascades.
- Code commits `8535ef5a`, `b196ca2e`, `695fa3c3`, `8c6e78ba`, `8111425b`, `52838844`.
- [40318140710](https://github.com/BanSins7/ContentMachine/actions/runs/40318140710) PASS 195/195 before strengthened replay and journal coverage; [40318478990](https://github.com/BanSins7/ContentMachine/actions/runs/40318478990) PASS strengthened 196 tests before 044 migration; [40318748792](https://github.com/BanSins7/ContentMachine/actions/runs/40318748792) FAIL due to FK-cascade with too-strict initial immutable journal trigger; 045 repaired it.
- **QA_PASS** [40319126522](https://github.com/BanSins7/ContentMachine/actions/runs/40319126522) on code SHA `5283884425a0326b40fd182607f74b453f11621e`: 197 tests, 0 failures/errors/skips, PG17 migrations, Python/JS syntax pass.
- **Next stage FIX-13:** concrete Group A integration/actual receiver, without QDP calling paid analysis or fabricating media/codec evidence. Group B/C peer adapters are FIX-14. Cross-branch LIVE integration remains BLOCKED_EXTERNAL.


## FIX-13 exact peer Group A intake QA — 2026-10-26

- `bbc6bd5b0f64de7a62ad6257c758e219837e41d1`: QDP opt-in `integrations/group_a.py` first GA-MOD-04 adapter/receipt translation. `91bc250840a5f7ac7e3e9ade91d0dd098cc37a8a`: correct QDP current reader with PostgreSQL dict/tuple rows, explicit peer negotiation without implicit import. `86eccfdb9de44e49da1991f14bcf1dad5f0147e5`: 8 pure GA contract tests and one QDP PG17 provenance/revocation read test.
- `a39459dc031ef63377321dbb6af2099c8265e7c2`: CI job checks out **exact** independent Group A head `cc1d43497aa70b6f1831b104d835c8674861e9ab` (read-only), temporarily composes its own `intelligence/group_a` and **GA-owned 054/055 migrations** into a disposable PG17 checkout, tests actual `GroupAInboxReceiver + VerifiedMediaCatalog` with QDP dispatcher and real local bytes. No GA branch or main merge.
- [Actions 40321549312](https://github.com/BanSins7/ContentMachine/actions/runs/40321549312) **SUCCESS**: main QDP job 206/206 Python+PG17 tests, 0 errors/failures/skips, Python/JS syntax PASS. Cross-branch job **SUCCESS**: Group A `ga_runs=1`, `ga_steps=1`, `ga_inbox=1`, QDP receipt=1, direct repeated deliveries 50 times produce exactly one GA step/receipt. Group A ACK means durable intake, not video analysis complete.
- Group A's legacy automatic `repository.create_task` and `group_a.work_once` are **offline-gated by peer bootstrap**; FIX-13 deliberately uses the newer isolated opt-in GA-MOD-04 receiver, does not bypass that gate or claim FFprobe extraction. No paid tasks initiated.
- Group B/C and Analytics connectors remain FIX-14. Next QDP stage **FIX-14**.
