# Group A — Implementation Tracker (canonical)

**Источник:** [полный план](GROUP_A_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md). Отмечать этап только после проверенного коммита и доступных тестов.

**Current branch:** `feature/group-a-full-module` (created from main `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`)  
**Baseline main SHA:** re-check at GA-MOD-00; last inspected `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`  
**Last implementation completed:** GA-MOD-06 (QA tracked separately)  
**Next:** GA-MOD-07 (Frame Sampling / Thumbnail & Evidence Manifest)  
**GA-MOD-00 implementation commits:** `6966ce3`, `00342f5`, `e07ddd0`  
**Latest committed GA-MOD-01 implementation:** `06b748201c17efe9d7f48ca9cbc8924639082c92` (this status note follows as a separate commit)  
**QA status:** GA-MOD-00..06 implementation committed. GA-MOD-06 source audit PASS, direct synthetic FFmpeg CLI smoke PASS; committed Python/PG17 tests remain QUEUED/NOT RUN. Manual FFmpeg commands are not Python unittest PASS. Windows and host E2E NOT RUN.

## Checklist (one stage per request)

- [x] ~~GA-MOD-00 — Чистая ветка, перенос только собственного A1 и постоянный трекер~~ (IMPLEMENTED; QA: QUEUED/NOT RUN)
- [x] ~~GA-MOD-01 — Контрактная матрица и truth semantics (freeze)~~ (IMPLEMENTED; QA: QUEUED/NOT RUN)
- [x] ~~GA-MOD-02 — Самостоятельная архитектура портов и адаптеров~~ (IMPLEMENTED; QA: QUEUED/NOT RUN)
- [x] ~~GA-MOD-03 — Только GA SQL, upgrade path и целостность PostgreSQL~~ (IMPLEMENTED; QA: STATIC PASS / PG17 QUEUED)
- [x] ~~GA-MOD-04 — QDP handoff consumer / inbox без копирования QDP~~ (IMPLEMENTED; QA: STATIC PASS / PG17 QUEUED)
- [x] ~~GA-MOD-05 — Media Manifest и верифицированный Input Resolver~~ (IMPLEMENTED; QA: STATIC PASS / PYTHON+PG17 QUEUED)
- [x] ~~GA-MOD-06 — Восстановление и укрепление A1 (FFprobe/FFmpeg)~~ (IMPLEMENTED; QA: STATIC/FFmpeg CLI PASS, PYTHON CI QUEUED)
- [ ] GA-MOD-07 — Frame Sampling / Thumbnail & Evidence Manifest
- [ ] GA-MOD-08 — Shared Media Understanding / PSI bridge & cache
- [ ] GA-MOD-09 — Audio Decode + Voice Activity Detection
- [ ] GA-MOD-10 — ASR Pipeline + language, source selection, word alignment
- [ ] GA-MOD-11 — Speech metrics: word count, active-time WPM, first speech, coverage
- [ ] GA-MOD-12 — OCR engine: multi-language frame text and geometry
- [ ] GA-MOD-13 — Screen Text Tracking / temporal OCR intervals
- [ ] GA-MOD-14 — Subtitle detection: true/false/unknown
- [ ] GA-MOD-15 — Audio Event Detection: music and SFX
- [ ] GA-MOD-16 — Photo Carousel & Image Post Extraction
- [ ] GA-MOD-17 — Mixed Carousel & Video Slides
- [ ] GA-MOD-18 — Atomic Assembly, quality/coverage gate & immutable snapshots
- [ ] GA-MOD-19 — Group B consumer contract; CTA circularity resolution
- [ ] GA-MOD-20 — Runtime Orchestrator, retries, resource budgets, invalidation
- [ ] GA-MOD-21 — HTTP API, UI, diagnostics & observability
- [ ] GA-MOD-22 — Performance and accuracy benchmarking
- [ ] GA-MOD-23 — CI, compatibility matrix, Windows/Linux/PG17 E2E
- [ ] GA-MOD-24 — Merge-readiness, dependency handshake, release documentation

## Execution log

| Stage | Commit SHA | Files/modules | Unit | PG17 | Media smoke | CI | Blockers |
|---|---|---|---|---|---|---|---|
| GA-MOD-06 (implementation complete; QA open) | `e0ee9cd` | extractors/technical.py, legacy shim, root/mutation protection, timebase and scene limits, metadata, mocked & real-video tests, CI FFmpeg required | STATIC PASS; Python NOT RUN | PG17 QUEUED | Direct FFmpeg CLI PASS; Python QUEUED | [Group A workflow](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml) QUEUED | Future FD-backed worker execution and Windows QA pending |
| GA-MOD-05 (implementation complete; QA open) | `1bb8069` | descriptor-based Media Manifest, D1 SQL catalog, secure roots/magic/SHA, A1 reuse, five media kinds, offline/PG fixtures, exported API | STATIC PASS, Python NOT RUN | PG17 QUEUED | NOT RUN (FFmpeg decode is next stage) | [Group A workflow](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml) QUEUED | No authoritative carousel order without host selector; live dispatcher/Windows E2E not wired |
| GA-MOD-04 (implementation complete; QA open) | `35b0019` | adapters/qdp.py, handoff.py, new 055 quarantine migration, 2 wire schemas, replay/tamper/concurrency/PG tests, public exports, CI | STATIC PASS; Python NOT RUN | PG17 QUEUED | NOT RUN (no media decoder) | [Group A workflow](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml) QUEUED | QDP outbox producer must emit exact envelope; verified MediaCatalog installed at GA-MOD-05 |
| GA-MOD-03 (implementation complete; QA open) | `ca88f0a` | GA-only migration 054 (7 tables, 2 views, 7 PLpgSQL guards); PostgreSQL adapter, isolated fresh/017-upgrade DB tests, disposable PG17 CI | STATIC PASS; Python NOT RUN | PG17 QUEUED, not passed | NOT RUN (SQL-only stage) | [Group A workflow](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml) QUEUED | Runner scheduling; archived 017 requires QDP only in legacy upgrade fixture |
| GA-MOD-02 (implementation complete; QA open) | `b92f3da` | ports.py, config.py, adapters/unavailable.py, service.py, capabilities, test_group_a_ports.py, workflow, docs | NOT RUN (runner queued) | NOT RUN (no GA schema at stage 02) | NOT RUN (test-only fake ports) | [Group A workflow](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml) QUEUED | GitHub scheduling backlog; no adapter-based live QDP until GA-MOD-04 |
| GA-MOD-01 (implementation complete; QA open) | `06b7482` | pure v1 contracts, 4 JSON Schemas, adversarial tests, compatibility matrix | NOT RUN (Python CI pending); STATIC JSON PASS | NOT RUN (stage has no DB writes) | NOT RUN (pure schema tests) | [latest workflow runs](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml) QUEUED/PENDING (inspect current HEAD) | Runner backlog; optional jsonschema validator may be skipped |
| GA-MOD-00 (implementation complete; QA open) | `e07ddd0` | GA bootstrap, direct API guards, fixture gating, CI & docs | QUEUED | NOT RUN (QDP unavailable) | QUEUED | [run #40299294300](https://github.com/BanSins7/ContentMachine/actions/runs/40299294300) QUEUED | Do not equate queueing with failed tests; await actual runner outcome |

## Dependency handshake / status

- QDP: `qdp-handoff/v1`, availability and version to be confirmed on each stage.
- SRE: no import dependency; SRE model selection Embeddings + Opus 5.5 does not affect GA extraction algorithms.
- PSI: `transcript/v1` / `visual-preparation/v1` reuse only with exact verified media hash mapping.
- Group B: independent consumer of A003/A004 and `group-a-to-b/v1`; CTA semantics owned by B.
- Analytics: reads version-pinned A snapshots, no GA inference claims.

## Update rule

An implementation-complete stage must be marked `- [x] ~~GA-MOD-NN~~ (IMPLEMENTED; QA: PASS/FAIL/QUEUED/NOT RUN)` once its code is committed, even when CI cannot run; independently preserve test outcomes, blockers and links. Never assert tests PASS or release readiness without evidence. For genuine unresolved implementation defects, keep the stage open.
## GA-MOD-00 progress

- Branch based on main baseline; preserved A1 and its legacy SQL outside the migrator.
- Added explicit `qdp_handoff_not_integrated` capability, HTTP/automatic worker disabled during bootstrap.
- Conditionalized QDP-only DB fixture import to allow offline test discovery.
- Added GA-only CI, integration guide, release evidence and full plan alias.
- Implementation commit `6966ce38b270a2d1f6099825b52984192a98d530` pushed; [bootstrap CI run](https://github.com/BanSins7/ContentMachine/actions/runs/40298383330) queued at last check. Completion pending test results; do not claim PostgreSQL/QDP E2E.
- Repository public entrypoints now deny direct calls without a negotiated QDP handoff; new regression tests cover API, worker and repository entrypoints.

## Tracking policy (2026-10-26)

Separate **implementation** from **test verification**. CI queued due to unavailable runner is not FAIL; local validation may be performed independently. Later failing CI is recorded as QA=FAIL, with a fix follow-up; other completed stages are not hidden by one testing blocker. Stage status alone is never a release gate.

## GA-MOD-01 execution note

- Implemented immutable wire shapes in `intelligence/group_a/contracts_v1.py`, four JSON Schema 2020-12 files, public exports, and `tests/test_group_a_contracts_v1.py` with adverse cases.
- Source of truth: [GA contract matrix](GROUP_A_CONTRACT_MATRIX_V1.md), including observed QDP `input_sha256` naming, A003/A004/B FKs, PSI distinct IDs, Analytics incomplete-A gate, no CTA semantic ownership.
- Code commits: `c60c164`, `9a3de9b`, `9d02a80`, `02ef38b`, `a6bad49`; final docs/exports commit check HEAD. Full tests remain unverified until GitHub runner starts.
- The repo-wide GitHub Actions backlog includes other module workflows; cancellation of redundant GA PR/push runs enabled via branch-scoped workflow concurrency. Stale workflow status `cancelled` is deliberate deduplication, not a GA test FAIL.

## QA diagnostic (latest update)

- GitHub Actions runs for multiple independent Content Machine modules were still queued during review, indicating runner scheduling/backlog rather than a Group A test failure. Earlier GA runs marked CANCELLED after branch-scoped concurrency deduplication; that is expected.
- Connector-based static check read and JSON-parsed all 4 GA v1 schema files: **PASS**. Full Python unittest, optional JSON Schema semantic validation, FFmpeg media test: **NOT RUN locally**, GitHub job **PENDING**; no false PASS claimed.
- Direct local checkout could not be fetched from github.com because the execution container has no DNS/network access; thus local Python execution was not possible in this session. CI remains the authoritative runtime test path.
- Current workflow (latest run always changes on commit): [Group A Actions](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml). Prior run IDs may become CANCELLED intentionally when a newer commit queues.

## GA-MOD-02 implementation report (2026-10-26)

- **IMPLEMENTED**, QA remains independent (GitHub Actions QUEUED / local Python NOT RUN). See [ports and adapters](GROUP_A_PORTS_AND_ADAPTERS.md).
- Verified GitHub isolation by compare against main: only GA-owned files, GA tests, GA docs and its own workflow changed; **no changes** to QDP, SRE, PSI, Group B, Analytics or main.
- Code commits: `cb154e9` (eight protocols, pure service, missing adapters), `76c93c3` (capability diagnostics), `6e80027` (fake-reader tests), `d190d3b` (CI validation), `a31f33e` (integration notes), `b8dfde1` (read-error fallback), `c41e459` (stable public exports), `b92f3da` (env flags diagnostic).
- `readiness()` always includes `durable_handoff_not_installed`, even if the caller supplies seemingly functional adapters and enables both feature flags; **no simulated QDP ingestion or jobs**. Legacy `repository.py` remains dormant until GA-MOD-03/04 replacement.
- CI workflow [Group A standalone](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml) still queued; redundant earlier GA runs cancelled by branch-scoped concurrency and **must not** be counted as test failures.
- Next: implement real GA SQL and disposable PG17 upgrades in GA-MOD-03. Do not interpret GA-MOD-02 IMPLEMENTED as deployable live integration.

## GA-MOD-02 static verification

- Github connector fetched current committed `ports.py`, `service.py`, `config.py`, `adapters/unavailable.py`; expected 8 Protocol declarations present and no direct imports from QDP, PSI, Analytics or Group B: **STATIC PASS**.
- `main...feature/group-a-full-module` comparison: **PASS** (22 commits ahead and zero behind at check, all changed paths GA-owned). No other branches written.
- Public `from intelligence.group_a import GroupAService, GAPorts, GASettings` now exported and explicitly covered by unit regression.
- Exact newest implementation patch `b92f3da53778f0074d6a43067836a26f38d3fa2b`; test execution **QUEUED** at [Group A Actions](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml); the queue and cancelled obsolete runs are NOT failing unit tests.

## GA-MOD-03 execution report (2026-10-26)

- **IMPLEMENTED (tests tracked separately).** [GA PostgreSQL 17 migration/upgrade guide](GROUP_A_POSTGRES_MIGRATION.md).
- New forward-only `intelligence/migrations/054_group_a_module_core.sql` includes `ga_runs`, `ga_media_refs`, `ga_inbox`, `ga_steps`, `ga_results`, `ga_outbox`, `ga_cache_refs`, two views and seven guarded PL/pgSQL routines. No QDP table FK, no modifications to earlier applied main migrations, no forked video table.
- Debugging: static scan caught and repaired malformed `$$` delimiters; new regression test asserts exactly seven balanced functions. PG fixture `TestCase.run` helper renamed to `add_run` to avoid unittest override; duplicate-run fixture pinned to exact same QDP UUID and hash.
- Commits: `3781695` (GA 054), `f63c3ff` (retention/invalidation guards), `804507b` (retained pointer visibility), `ddd7ff3` (PG upgrade tests), `e697c3b` (PG17 service CI), `cbe2549` (SQL parser-delimiter fix), `7e3a5ad` (fixture fix), `1e84b7e` (disabled PostgreSQL ports), `2a6a67b` (adapter test), `656173e` (schema diagnostic exports), `5313e41` (manifest completeness), `d37dd66` (retention/finished gate tests).
- Read-only structural audit **PASS**: 7 GA tables, 7 paired PL/pgSQL `$$` functions, `DO` block pair, no QDP FK, no main D1 `ALTER TABLE`, PG17 CI enabled, diff limited to GA and `054_group_a_module_core.sql`. PG17 runtime and Python unit tests remain **QUEUED** due GitHub runner scheduling. No false PG17 PASS.
- The presence of 054 in the migrator does not enable QDP-backed live processing. GA HTTP/worker/legacy repository remain fail-closed. GA-MOD-04 will add authoritative QDP handshake/ACK and replays.

### GA-MOD-03 final follow-up fixes

- `3631b73`: completion guard now checks every insert/update without unsafe `OLD`-row access on INSERT.
- `0571c3c`: completed result must point to a completed A003/A004 analysis, not merely a pending upstream analysis; negative PG test added.
- `ca88f0a`: retention fixture restores source status before deleting media because main's pre-existing A003 FK cleanup trigger enforces current source policy. Revocation read-gate is asserted *before* restoring the fixture status.
- SQL structural verification remains **PASS**, but the actual disposable PG17 fixture is **QUEUED/NOT RUN**, not PASS.

## GA-MOD-04 implementation report (2026-10-26)

- **IMPLEMENTED (QA separately unresolved).** [GA QDP handoff integration](GROUP_A_QDP_HANDOFF_INTEGRATION.md). Public QDP source contract and schema in peer branch examined, including `qdp_outbox`/`qdp_outbox_deliveries` and `qdp_invalidations`.
- Added opt-in `GroupAInboxReceiver`: strict canonical envelope + digest, current QDP/public-outbox reader, pinned media source/policy/asset ID, same transaction for GA run/media/initial queue/inbox. ACK only after commit. Replay returns same run/job; conflict persists in separate immutable GA055 quarantine and is rejected without ACK.
- Default flags remain off; HTTP, old A1 worker and paid processing remain disabled. Group A never writes QDP tables or copies QDP implementation.
- Tests include real disposable PG17 QDP-like fixture: missing outbox, invalidation, tampering, duplicate delivery, concurrent workers, rollback injected at queue step. Runtime outcomes **not yet verified** due GitHub runner queue. Exact peer QDP producer/dispatcher live handshake is a later integration gate.
- Read-only GitHub code/SQL audit confirmed GA-only changed files, GA055 absent QDP FKs, outbox/delivery/invalidation query, explicit QDP version, and response constructed after DB transaction context: STATIC PASS. A source-level audit cannot certify SQL executes successfully; PG17 test status remains QUEUED.

### GA-MOD-04 documentation accuracy pass

- The integration guide and release verdict header now distinguish **implemented opt-in QDP inbox** from **unwired public producer/dispatcher and disabled analysis runtime**. QA remains queued; no claim of live QDP→Group A end-to-end.

## GA-MOD-05 execution report (2026-10-26)

- **IMPLEMENTED; QA unresolved separately.** Read [verified Media Manifest design](GROUP_A_MEDIA_MANIFEST_GUIDE.md).
- Actual bytes, SHA256, size, kind/magic, allowed root, symlink/traversal and media inode/time identity checked using local descriptor, with optional re-open verification; five media kinds and explicit original order supported. Original A1 `authorized_file` is retained as an additional gate.
- D1 SQL adapter reads canonical video, QDP preparation/enrichment, active source/policy, selected asset and currently allowed/unexpired D1 media; missing trusted carousel order fails closed. No D1/QDP table copies or paid/media downloads.
- Code commits: `0b3063b` (byte manifest), `d6dbbf6` (D1 adapter), `af2ec5a` (filesystem adversarial tests), `3dfefd0` (nonblocking FIFO/stale refs), `3535c35` (A1 gate), `4b39c44` (catalog SQL tests), `d7c5957`/`18569d5` (PG17 actual file handoff fixture and fix), `d212d3e` (empty/special file guard), `dc3f0bc` (full SHA refs), `1bb8069` (public facade and diagnostics).
- GitHub comparison main→Group A confirmed all modified paths belong to Group A and its own migrations/tests/docs/workflow. 14-point source audit STATIC PASS, **Python and PG17 CI QUEUED/NOT EXECUTED**. Static success is NOT successful media end-to-end testing.
- Follow-up: GA-MOD-06 decodes actual MP4/MOV/WEBM with FFprobe, verifies timebase/duration/scene robustness. GA-MOD-20 rechecks QDP/source rights at worker execution; GA-MOD-23 covers Windows symlink/race behavior.

## GA-MOD-06 implementation report — 2026-10-26

- **IMPLEMENTED**; test verification remains independent. [Technical A1 guide](GROUP_A_TECHNICAL_EXTRACTION_GUIDE.md).
- New `extractors/technical.py`: strict FFprobe stream/codec/SAR/timebase/start offset, stream-first duration, 600s limit, normalized FFmpeg PTS, first-frame retention, deterministic max-300 scene boundaries, `-xerror` decoder failure, progress and separate v2 method/version evidence; unmeasured B/ASR/OCR remains unknown.
- Previous `extractor.py` remains backward-compatible (`measure`, `inspect_probe`, `parse_cuts`, `authorized_file`, old constants and A1 `TechnicalMeasurement` DTO). `GroupAService.inspect_local_video` provides explicit offline-only access with required roots and pre/post file mutation guard.
- New `tests/test_group_a_technical.py` covers audio-only, unknown codecs, weird SAR, malformed/long duration, attached pics, invalid/missing timebase, debounce/300 scenes, injected timeout/errors, root escapes/symlinks, file changes during processing, real generated MP4 abrupt/static/gradual/offset video; workflow explicitly requires FFmpeg and FFprobe.
- **Static GitHub source/branch audit PASS**, **direct synthetic FFmpeg command smoke PASS** (3s origin normalizes observed PTS to 0 and 1; static video emits first-frame PTS 0). **Committed Python/PG17 GitHub Actions QUEUED, not passed**; old cancelled runs are concurrency deduplication, not FAIL.
- Implementation code commits: `b116947`, `8740706`, `55117de`, `fc295d6`, `9ed5dc3`, `6ae67c5`, `5aaf5c2`, `1778e0c`, `296bcb9`, `f0e692a`, `e0ee9cd`.
- Next: GA-MOD-07 Frame Sampling / Thumbnail & Evidence Manifest; sparse frame sampling must not claim full OCR coverage.
