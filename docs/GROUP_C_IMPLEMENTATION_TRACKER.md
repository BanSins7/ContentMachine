# Group C — Implementation Tracker
Canonical branch: feature/group-c-full-module (created 2026-10-26 from main 218f5f3163ccc7179a4809ba4386a1d1b6ff80f5).
Plan: docs/GROUP_C_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md.
Existing source schema: 003_performance_c.sql and 004_performance_c_integrity.sql — unchanged main authority.
Last code-implemented stage: GC-MOD-10 (serializable, versioned frozen exact peer membership, reproducible median/empirical priors, durable immutable evidence; HUMAN APPROVAL and scoring NOT enabled).
Next stage: GC-MOD-11 (explicit human review and append-only approval for exact frozen cohorts and calculation parameters).
Status: GC-MOD-00..10 CODE IMPLEMENTED. Stage10 additive PG17 057 and immutable freeze writer committed. Static GitHub source/migration audit PASS: 2 new tables, 3 triggers, SERIALIZABLE in-transaction GC09 selection and unchanged 003/004. Stage10 13 pure + 8 PG17 tests authored, NOT RUN locally (no psycopg/Postgres or reachable repo); full GitHub Actions CI QUEUED/NOT VERIFIED. Human-approved cohorts, config, C7 worker and cross-branch QDP/SRE/VC E2E remain BLOCKED_EXTERNAL; no live C7 score.

## Mandatory rules for subsequent chat requests
One request = one stage. Read this tracker, full MD plan and GitHub branch HEAD before changes. Implement only Group C in this feature branch; main and sibling branches read-only; commit implementation and tracker updates.
Mark - [x] ~~GC-MOD-NN~~ as IMPLEMENTED even if only last tests fail, but record QA FAIL/BLOCKED/NOT RUN with exact evidence; do not mark QA PASS unless actually observed. Fix failures where feasible before proceeding. Partial code is NOT IMPLEMENTED.
Every stage logs SHA, touched files, tests with status and blockers. Never use production DB for fixture tests or invent alpha/k/priors.

## Stage checklist
- [x] ~~GC-MOD-00 — Bootstrap ветки~~ (IMPLEMENTED; QA: NOT VERIFIED / CI PENDING)
- [x] ~~GC-MOD-01 — Аудит контрактов C0–C7~~ (IMPLEMENTED; QA: NOT VERIFIED / CI QUEUED)
- [x] ~~GC-MOD-02 — Ports и независимый модуль~~ (IMPLEMENTED; 9 isolated pure tests PASS, full project QA NOT RUN / CI PENDING)
- [x] ~~GC-MOD-03 — PG17 runtime schema и upgrade~~ (CODE IMPLEMENTED; QA 2 static PASS / PG17 6 NOT RUN-SKIP / CI QUEUED)
- [x] ~~GC-MOD-04 — QDP C-intake~~ (CODE IMPLEMENTED; unit/PG17 QA NOT VERIFIED, CI QUEUED, LIVE QDP producer BLOCKED_EXTERNAL)
- [x] ~~GC-MOD-05 — Источник фактов D1~~ (CODE IMPLEMENTED; static schema check PASS; pure+PG17 QA NOT VERIFIED / CI QUEUED)
- [x] ~~GC-MOD-06 — C0 Snapshot Read/Validation~~ (CODE IMPLEMENTED; exact-source local smoke 12/12 PASS; authored suite/PG17 NOT VERIFIED, CI QUEUED)
- [x] ~~GC-MOD-07 — Изоляция pure Decimal math~~ (CODE IMPLEMENTED; local isolated 14/14 PASS and legacy 27-field gold parity PASS; GitHub full checkout CI QUEUED / NOT VERIFIED)
- [x] ~~GC-MOD-08 — Publication time evidence~~ (CODE IMPLEMENTED; exact-file local 14/14 smoke PASS; authored 13 pure + 6 PG17 cases NOT VERIFIED; CI QUEUED)
- [x] ~~GC-MOD-09 — Cohort rules~~ (CODE IMPLEMENTED; 16 pure + 6 optional disposable PG17 tests authored, NOT RUN/CI QUEUED; LIVE E2E BLOCKED_EXTERNAL)
- [x] ~~GC-MOD-10 — Freeze cohort membership~~ (CODE IMPLEMENTED; static migration/writer checks PASS; 13 pure + 8 PG17 tests authored, NOT RUN locally / CI QUEUED)
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

| GC-MOD-03 | `42371959bd3bc3c04e552ce1ddd5dd2252dadd4b` (last schema test code SHA; later docs commits) | `intelligence/migrations/056_group_c_runtime.sql`; `tests/test_group_c_runtime_schema.py`; `.github/workflows/group-c-module.yml`; `docs/GROUP_C_STAGE03_RUNTIME_SCHEMA.md`; integration contract and tracker | 2/2 PASS: isolated static local mirror, 6 DB SKIP; full project local NOT RUN | NOT RUN: PostgreSQL 17 unavailable locally; fresh+upgrade jobs configured | NOT RUN: runtime execution and peer handshakes not implemented | QUEUED: GitHub Actions fresh+upgrade at time of writing, no PASS claimed | Waiting PG17 CI; no source rights/cohort approvals/real QDP consumer; no operational C7 scorer |

| GC-MOD-04 | `c2abf0e8f80317b687f19b9f9f474082040632ff` (last Stage04 code/test SHA, later docs and tracker changes) | `intelligence/group_c/qdp_intake.py`; `intelligence/group_c/__init__.py`; `tests/test_group_c_qdp_intake.py`; `.github/workflows/group-c-module.yml`; `docs/GROUP_C_STAGE04_QDP_INTAKE.md`; integration contract & tracker | NOT VERIFIED: pure tests authored, full checkout unavailable | NOT VERIFIED: disposable PG17 synthetic QDP fixture tests CI QUEUED | BLOCKED_EXTERNAL: QDP has no verified live preparation_ready producer / merged source lineage | QUEUED: current HEAD not yet green | Do not ACK absent/revoked event; no C7 computation, no actual upstream delivery receipt yet |

| GC-MOD-05 | `0371e6c21d36521372ab2da31df232b408b57812` (final D1 implementation SHA; bundle/tests/docs committed separately) | `intelligence/group_c/d1.py`; `intelligence/group_c/__init__.py`; `tests/test_group_c_d1.py`; `docs/GROUP_C_STAGE05_D1_PROVENANCE.md`; `.github/workflows/group-c-module.yml` | NOT VERIFIED: 7 pure tests authored; 9 physical table checks and source/method static inspection PASS | NOT RUN: 5 disposable PG17 cases authored, CI queued fresh+upgrade | BLOCKED_EXTERNAL: no real QDP producer / verified social platform observations | QUEUED/NOT VERIFIED at current HEAD | D1 reads source eligibility only; C0 quality and human-approved cohorts/config/scorer still unimplemented |

| GC-MOD-06 | `0eb2d3ddd38d82005dc3b678de49a7c47b1310d0` (Stage06 test authored SHA; D1/ports/exports/CI/docs follow) | `intelligence/group_c/c0.py`, `intelligence/group_c/d1.py`, `intelligence/group_c/ports.py`, `intelligence/group_c/__init__.py`, `tests/test_group_c_c0.py`, `docs/GROUP_C_STAGE06_C0_SNAPSHOT_VALIDATION.md`, CI | 12/12 PASS on local isolated exact Git-blob C0 code, Python3.13; full 17 authored pure tests not run | 5 PG17 cases authored, NOT RUN locally; fresh+upgrade CI queued | BLOCKED_EXTERNAL: no merged real QDP producer/D1 source observation E2E | QUEUED/NOT VERIFIED current HEAD | C0 validates raw metrics and eligibility only; cannot emit C7, no human config/cohort approval |

| GC-MOD-07 | `98033953ae1ae72f40be76e880c5f6ad396d831d` (last Stage07 test commit before docs/tracker) | `intelligence/group_c/pure_math.py`, `tests/test_group_c_pure_math.py`, `docs/GROUP_C_STAGE07_PURE_DECIMAL.md`, `intelligence/group_c/__init__.py`, integration docs | 14/14 PASS: local independent Python3.13 tests on equivalent isolated core; 27/27 exact parity with independently reproduced legacy Analytics reference; 12 GitHub file tests authored NOT RUN on exact checkout | NOT RUN: pure-only Stage07; earlier disposable PG17 jobs remain CI pending | BLOCKED_EXTERNAL: no live source/approved cohorts/worker or merged Analytics delegate | QUEUED/NOT VERIFIED latest HEAD | Source scorer is pure only, not root production API; no second production writer; math complete != approved persisted complete |

| GC-MOD-08 | `9fff2f3e428ef42f93acffbd6f6e4b621ba88177` (final hardened code SHA; unit/CI/docs committed separately) | `intelligence/group_c/publication_time.py`; `intelligence/group_c/__init__.py`; `tests/test_group_c_publication_time.py`; `docs/GROUP_C_STAGE08_PUBLICATION_TIME.md`; `.github/workflows/group-c-module.yml`; integration contract & tracker | 14/14 PASS local isolated independent smoke on SHA-identical source; GitHub 13 pure tests authored NOT RUN | 6 PG17 cases authored, NOT RUN locally; fresh + upgrade CI pending | BLOCKED_EXTERNAL: no live source/QDP/approved cohorts | QUEUED/NOT VERIFIED exact HEAD | Publication evidence does not calculate C6 or publish C7; current DB gate required |

| GC-MOD-09 | `0885b7f1ac66b55991e2f85cf676e51758414bc6` (final Stage09 test revision; docs/tracker subsequent) | `intelligence/group_c/cohort_rules.py`, `intelligence/group_c/__init__.py`, `tests/test_group_c_cohort_rules.py`, `docs/GROUP_C_STAGE09_COHORT_RULES.md`, `.github/workflows/group-c-module.yml`, integration contract & tracker | 16 pure tests authored, NOT RUN locally; static source/read-only checks PASS | 6 opt-in disposable PG17 cases authored; fresh/upgrade CI QUEUED/NOT VERIFIED | No live QDP producer/source E2E or reviewed frozen cohort population | QUEUED/NOT VERIFIED exact current HEAD | Returns candidates_only, approved=False, no medians/priors, no SQL write, no C7 score |

| GC-MOD-10 | `9f55a035f3194b2d09a93bdfb30b9c0fe1f56d1a` (latest Stage10 test fix; CI/docs/tracker commits subsequent) | `intelligence/migrations/057_group_c_cohort_freezes.sql`, `intelligence/group_c/cohort_freeze.py`, GC09 `select_locked`, `tests/test_group_c_cohort_freeze.py`, updated `tests/test_group_c_runtime_schema.py`, Group C package, CI/docs | 13 new pure methods authored; NOT RUN locally, static script/SQL inspection PASS | 8 opt-in disposable PG17 tests authored; fresh and main→056+057 upgrade CI QUEUED, NOT VERIFIED | Live QDP/provider and human approval must remain blocked | QUEUED/NOT VERIFIED | Frozen result is frozen_unreviewed/approved=False, not approved C7; migration 057 additive only |

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


## GC-MOD-03 evidence and ordering
- Migration `056_group_c_runtime.sql` SHA-256: `ef40af7dc6c1f56ceae88ffc5b6a9801109137c06b117b9eea8215dc21843dac`; GitHub blob `8603cd3b913a1ca47055671645bb2b669a7a60f4`. No edits to historic `003/004`, no upstream Group A/B/QDP migrations copied.
- Inspected latest main at `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`, Group A at `852b0946e868fd9410b7da2e5903e9645d38772e` (`054/055`), Group B at `391d18faa677bb27482b157813545ae25bd44be5` (`055`), QDP at `e50f4eaa1e9d5e0a13f0c5f60f52f064266e925e`, Analytics at `12921968ef3d5c6f5e706fd8e625c54733405f3b`.
- Local `python -m unittest discover -s tests -p 'test_group_c_runtime_schema.py' -v` on an isolated local mirror: 2 static PASS, 6 PostgreSQL tests SKIP because `CM_TEST_PG_DSN` / disposable flag were absent. This is NOT fresh/upgrade PG17 proof.
- `group-c-module.yml` now includes isolated PG17 **fresh** and **upgrade** test databases (intended runnable CI; queued/pending is not PASS). CI job must run to verify real DDL execution, checksum replay, foreign keys, revision immutability, idempotency and worker lifecycle.
- Stage 03 is CODE IMPLEMENTED, not production-certified. Stage 04 consumer must re-read current verified QDP state before changing an inbox row to accepted. Never treat the schema as an implicit provider/data readiness grant.


## GC-MOD-04 evidence and next owner handshake
- Latest QDP inspected: `6743f6ee9d63009579b728a155a6082f6d1e99d5`; has `042_qdp_group_current_preparations.sql` current C view and `qdp_outbox` table in 040, but no confirmed live QDP preparation_ready outbox writer in examined `qualified_data/repository.py` / `worker.py`. A C receiver cannot invent an upstream event. Producer must emit project-scoped `qdp-event/v1` with exact payload and hash before cross-branch live tests.
- No edits to 003/004 or 056 migration. Gated intake uses QDP outbox row lock, current C preparation v2, canonical D1 snapshot pair and policy, one atomic Group C accepted inbox + run + queued score job. Conflict is durable without ACK, replays revalidate currentness; result returned only after the outer transaction commits.
- New pure tests / isolated PG17 fixture tests include same-ID replay, changing SHA, cross-project, canceled/removed event, source rights revocation, D1 snapshot retraction, concurrent same-event deliveries, late D1 observation invalidation and rollback. Fixture is not QDP actual producer; QA status **NOT VERIFIED**, no synthetic fixture passed off as production.
- Review `docs/GROUP_C_STAGE04_QDP_INTAKE.md` for exact producer event assumptions; status remains `BLOCKED_EXTERNAL` until QDP producer, live SRE/Collector and provider/source policies are jointly verified. Next implement GC-MOD-05 only in this branch, preserving stage-04 identity/ACK tests.

- GC-MOD-04 last hardening: `b40dea48` serializes one QDP outbox event before local receipt check, `5cf9b8b7` rechecks current permissions on replay, `dc1584ea` rejects newly arriving D1 observations since the pinned prior snapshot, `c2abf0e8` adds its PG17 regression. QA on these revisions is **CI QUEUED / NOT VERIFIED**, not PASS.


## GC-MOD-05 QA and integration handoff
- New canonical read-only D1/registry/source adapters at `intelligence/group_c/d1.py`, with exact project link `sre_project_videos` and separate source `source_id`/policy version; no new SQL and no copied QDP/VC module code. `read_scoped_d1_bundle` **does not score** and returns `score_approved=False`.
- GitHub source blob: `8cb8918f2b761949b3859e89019a53629590c3ac`; tests blob: `11b14ab9b10cf904e2702f5b34e38c758e6c4a93`. Static comparison across main's 001/002/003_taxonomy/003_performance/006_discovery migrations confirmed all 9 referenced relation names. This is a static inspection, **not Python unit or PG17 PASS**.
- Authored tests: 12 `test_*` methods (7 pure, 5 PG17) exercising project scope, nullable metrics, creator/platform identity, metric definition/version, exact metadata, active policy, soft-delete/retraction and unknown/invalid IDs. GitHub PG17 fresh+upgrade workflows configured; until run completion report QA `NOT VERIFIED`.
- Canonical video classifier is `videos.c_content_type`, not automatically inferred from `videos.content_kind`. Content kind mismatch or unavailable classification blocks bundle read; missing creator/topic stays None. Stage05 deliberately does not choose older “good” metric snapshots when newer raw observation is retracted; GC-MOD-06 handles strict C0 invalidation semantics.
- Stage05 source policy proves only rights and observed raw provenance. Cohort IDs cannot be treated as verified until 09/10; configuration/human review (11/12), C7 scoring (13..19), persistence (20), worker (21) and Analytics (25) remain pending. QDP current producer/cross-branch E2E remains BLOCKED_EXTERNAL.


## GC-MOD-06 QA and next-stage handoff
- `intelligence/group_c/c0.py` verified against GitHub blob **`50b50317ae63c6f6e3ebfc2a0a7e36e9c11e5f1c`** with local `git hash-object`; `python -m py_compile` PASS. A temporary disconnected mirror `/mnt/data/group_c_stage06` ran 12 locally authored independent smoke tests against that byte-identical C0 code: **12 PASS, 0 FAIL**, Python 3.13. The local mirror uses minimal stubs only for the DB-dependent `d1.py`/contract inventory imports; *it does not prove PostgreSQL access, or the complete GitHub-authored test file*. No user-owned repository checkout was created or modified.
- GitHub `tests/test_group_c_c0.py`: 17 pure and 5 PG17 tests written; the exact test file has NOT been run locally, and PG17 fresh+upgrade CI status remains QUEUED. Do NOT report those as passed.
- C0 raw `views/likes/comments/shares/saves/followers` are unchanged nullable BIGINT; 0 != NULL; C4 denominator must be views>0. Exact `observed_at` never inferred from `collected_at`. View metric definition v1 allowlist is `platform_post_views`, explicit version mandatory; `reach`/impressions prohibited. Incompatible/expired/retracted/newer raw D1 source observations block current-pair eligibility, never choose an older good pair implicitly.
- Stage06 only adds code, pure tests and CI; **no SQL migrations**, no provider interaction, no C1..C7 computation, no result persistence. `GroupCPorts` D1 method list now includes `read_recent_raw_observations`; adapters must implement it to remain compatible.
- Existing `status_for_blocker` maps a bounded legacy v1 subset of known errors to SQL statuses. New diagnostics `invalid_raw_counter` and `metric_history_changed` must be explicitly mapped in a versioned persistence/readiness design; they must NOT be inserted verbatim into `calculated_metrics.score_status`.
- Next `GC-MOD-07`: pure Decimal arithmetic helper with exact 12-place `NUMERIC(30,12)` rounding/overflow behavior, no invented parameters, no normalized score/0..100. Continue only Group C branch, preserve C0 pinning/retraction tests.

- Stage06 hardening on `1fbe6cf2` blocks malformed non-boolean `require_score_fields` options in pair validation; `9fc924ff` added its negative test. Retested exact hardened `c0.py` against 12 independent local smoke tests: all PASS; Git blob matches `50b50317ae63c6f6e3ebfc2a0a7e36e9c11e5f1c`. These are not the full published suite, and PG17 remains unverified until CI completes.


## GC-MOD-07 ownership and QA handoff
- Reference peer `feature/analytics-engine-qualified-pipeline-20261023`, SHA `12921968ef3d5c6f5e706fd8e625c54733405f3b`, file `intelligence/analytics/metrics.py`, gold fixture `tests/test_analytics_engine.py`. **Read-only** peer; no code changed there.
- Pure standalone calculator `intelligence/group_c/pure_math.py`, GitHub blob **`f79e63364f7f570dd418a857accee17da65ff89b`**; new frozen 12-test suite `tests/test_group_c_pure_math.py`, blob **`01b8c21d56ca33404409cf31b3597cce633e773a`**. Neither imports Analytics or SQL/provider code.
- Local disconnected mirror verified 14/14 separate unit tests in Python 3.13 and compared 27/27 output fields from independently reconstructed reference Analytics `compute_c_metrics`, exact parity including C7 `16.811535581108`. This is a strong local arithmetic result but the GitHub files were manually committed, not byte-for-byte run as repository checkout, so **full exact HEAD unit QA NOT VERIFIED**. The GitHub suite is wired into existing `python -m unittest discover -s tests -p 'test_group_c_*.py' -v` on Linux/Windows; CI queued.
- Strict Decimal 12-place HALF_UP in local context, exact timedelta microsecond conversion, fixed weights/half-life, no zero/default alpha/k/priors, JSON float weights only if exactly canonical, NUMERIC(30,12) output overflow guard; C2 excluded from raw unbounded C7.
- `compute_c_metrics` intentionally not exported as a ready root Group C service; mathematical `score_status=complete` must **never** be directly persisted/used for Analytics until DB current source, publication evidence, cohort freeze and human approvals, data quality, and transaction/worker are implemented and reverified in stages08..25. The existing Analytics pure function remains only in sibling feature branch; on future merge Stage25 must make its adapter delegate to Group C rather than keeping two independent production scorers.
- Stage07 has **no migrations**, no DB writes, no provider access, no `main` or peer branch changes. Next GC-MOD-08 verifies exact `source_video_metadata.published_at`, pinned source/policy, lifecycle/TTL and timezone without guessing dates.


## GC-MOD-08 QA and next-stage handoff
- Exact standalone module `intelligence/group_c/publication_time.py` **GitHub blob `c59063bd25a5c1e2f9e1fc28bcd8c4111db62c17`**. A manually copied module in isolated `/mnt/data/group_c_stage08` had the same `git hash-object` and passed `python -m py_compile`. Its 14 **independent smoke tests all PASS** on Python3.13 with minimal DB-dependent import stubs. This verifies pure source behavior, not the full repository test suite.
- `tests/test_group_c_publication_time.py` **13 authored pure tests** and **6 authored opt-in PG17** tests. Full GitHub-authored file NOT RUN locally; PostgreSQL17 not available locally. New explicit PG17 fresh/upgrade CI jobs plus existing Linux/Windows pure glob scheduled; until observed otherwise mark `NOT VERIFIED/QUEUED`, never PASS.
- Pure `validate_publication_time` requires an exact D1 metadata row ID/video/source/policy + `is_current=True`, `deleted_at=NULL`, verified timezone-aware `published_at <= fetched_at <= checked_at`, pinned `published_at <= prior.observed_at < current.observed_at <= checked_at`, and valid TTL. Missing `published_at` is fatal even if `videos.published_at` exists.
- `read_publication_time` performs one project-scoped, current source/policy/retention D1 read under an independent `REPEATABLE READ, READ ONLY` transaction, not user-supplied historical clock. Immutable `PublicationTimeEvidence` gives exact Decimal age based on observed_at and a stable metadata+snapshot lineage SHA excluding wall-clock checked_at. DB source date remains canonical; no duplicated publication table/SQL migration.
- No C6 calculation, C7 persistence, worker, Analytics publisher or edits to sibling branches or main. Next `GC-MOD-09` defines strictly compatible creator/niche/prior strata and excludes target self, followed by freeze and human approvals.


## GC-MOD-09 evidence and next owner handoff
- `intelligence/group_c/cohort_rules.py` committed, final GitHub source blob (before tracker) `6dd5daf1e755aedfcd02aab99e2934c6b060df0b`; `tests/test_group_c_cohort_rules.py` blob `1949f8fa409945320dc5001bc4f4b71f97ef82ab`. Standalone pure `CohortRules` + `select_compatible_peers`, SQL read-only `PostgreSQLCohortCandidates.select`, no peer module import or production job.
- Explicit exact project/video, creator/niche/prior strata, source/policy, platform/kind, `platform_post_views` plus metric **version**, publication range and observed age range, non-future cutoff, fixed max candidates 1..500; target self excluded in pure/SQL. Latest raw peer snapshot selected **before** checking metric quality/version; latest raw target at cutoff independently checked. All admitted peers must satisfy current D1 rights and metadata/snapshot retention.
- Results `CohortSelection(status='candidates_only', approved=False)` with deterministic rule/candidate hashes and audit reasons for rejections. Empty peer set remains empty; no fabricated median/prior, no default age windows, no truncation if population cap exceeded.
- Stage09 test file has 16 pure methods and 6 PostgreSQL17 methods, all authored and included by the existing Linux/Windows pure glob and dedicated PG17 fresh/upgrade steps. PostgreSQL17 unavailable locally and GitHub Actions status still queued at last check: **NOT VERIFIED**, do not claim PASS. Static GitHub inspection confirms no database mutation SQL in the Stage09 adapter.
- **Important schema dependency:** historical 003/004 `comparison_cohorts` does not include explicit metric version/project/policy lineage. DO NOT treat historic cohort as verified. GC-MOD-10 must supply additive versioned frozen membership/evidence, with no edits to historical migration checksums. GC-MOD-11/12 then require human review and approval, not automatic score activation.
- Continue only `feature/group-c-full-module`; `main`, A/B/QDP and Analytics branches unchanged. See `docs/GROUP_C_STAGE09_COHORT_RULES.md`.


## GC-MOD-10 immutable evidence, source blobs and next owner handoff
- **Migration:** `intelligence/migrations/057_group_c_cohort_freezes.sql`, exact GitHub blob `0acd440074c213e6fe2a998c04a64ce651b79fae`. Two additive proof tables, distinct exact legacy parent/membership and D1 composite FKs, immutable UPDATE/DELETE guards on new proof rows, deferrable COMMIT trigger verifies old parent frozen and all exact member pairs. Historic `003/004/056` files are **unchanged**; `intelligence.migrate` retains checksum locking.
- **Implementation:** `intelligence/group_c/cohort_freeze.py`, exact GitHub blob `d268552e365d3b55f0b9435a565341599a8b27c5`. Exact stage09 peer election **inside same SERIALIZABLE write transaction** (`PostgreSQLCohortCandidates.select_locked`, stage09 source blob `306ef44ed880ad8d6e01d8b553505d9134b2a23e`); no permission check → write gap. Creates legacy cohort+members plus 057 proof+members then seals parent; concurrent serializable/unique conflicts surfaced, not falsely ACKed.
- **Pure stats:** `build_frozen_evidence` exact 12-place Decimal median for creator/niche (even sample mean of middle pair); pooled empirical L/C/S per total views for engagement prior (not average of per-post rates), no default prior values or zero for missing peers. Evidence hash includes immutable sorted member snapshot/metadata IDs, observed/published and original counters; exact rule and selection SHA. Replay idempotent only when selection and evidence SHA match; same rules+changed evidence fails closed.
- **QA inventory:** `tests/test_group_c_cohort_freeze.py`, exact GitHub blob `f70736eae307ca04f952ce8b0dbcf30e1cb5fb1c`: **13 pure + 8 PG17** test methods. Local PostgreSQL17 and psycopg **absent**; repo unavailable to container over DNS; no committed suite executed locally. Static source inspection confirms exact method inventory, SQL 2 tables/3 triggers and no applied migration alteration: PASS (not executable SQL PASS). CI runs pure Linux/Windows via Group C glob and Stage10 PG17 fresh/upgrade. Results **QUEUED/NOT VERIFIED** at last observation.
- **Upgrade-path fix:** Stage03 `tests/test_group_c_runtime_schema.py` now simulates a true pre-Group-C database by excluding both 056 and 057, then asserts both apply in order while respecting recorded checksums. This avoids spuriously failing the previous stage after adding new independent schema.
- **Next GC-MOD-11:** append-only human decision lifecycle for exact frozen member sample and evidence hash, explicit approved reviewer, configuration alpha/k/prior/version evidence, non-default sample-size criteria. GC-MOD-12 config activation; only GC-MOD-20/21 allowed to produce durable approved C7. Frozen `is_frozen` alone does **not** authorize use.
