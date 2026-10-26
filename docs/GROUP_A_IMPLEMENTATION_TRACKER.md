# Group A — Implementation Tracker (canonical)

**Источник:** [полный план](GROUP_A_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md). Отмечать этап только после проверенного коммита и доступных тестов.

**Current branch:** `feature/group-a-full-module` (created from main `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`)  
**Baseline main SHA:** re-check at GA-MOD-00; last inspected `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`  
**Last implementation completed:** GA-MOD-11 (QA tracked separately)  
**Next:** GA-MOD-12 (OCR engine: multi-language frame text and geometry)  
**GA-MOD-00 implementation commits:** `6966ce3`, `00342f5`, `e07ddd0`  
**Latest committed GA-MOD-01 implementation:** `06b748201c17efe9d7f48ca9cbc8924639082c92` (this status note follows as a separate commit)  
**QA status:** GA-MOD-00..11 implementation complete (12/25). GA11 static source/branch audit PASS; independent decimal numerical gold PASS. Python+PG17 GitHub Actions QUEUED/NOT EXECUTED; real upstream ASR/VAD accuracy, A003 writer E2E and Windows host NOT VERIFIED. Queue/cancel status is separate from stage implementation.

## Checklist (one stage per request)

- [x] ~~GA-MOD-00 — Чистая ветка, перенос только собственного A1 и постоянный трекер~~ (IMPLEMENTED; QA: QUEUED/NOT RUN)
- [x] ~~GA-MOD-01 — Контрактная матрица и truth semantics (freeze)~~ (IMPLEMENTED; QA: QUEUED/NOT RUN)
- [x] ~~GA-MOD-02 — Самостоятельная архитектура портов и адаптеров~~ (IMPLEMENTED; QA: QUEUED/NOT RUN)
- [x] ~~GA-MOD-03 — Только GA SQL, upgrade path и целостность PostgreSQL~~ (IMPLEMENTED; QA: STATIC PASS / PG17 QUEUED)
- [x] ~~GA-MOD-04 — QDP handoff consumer / inbox без копирования QDP~~ (IMPLEMENTED; QA: STATIC PASS / PG17 QUEUED)
- [x] ~~GA-MOD-05 — Media Manifest и верифицированный Input Resolver~~ (IMPLEMENTED; QA: STATIC PASS / PYTHON+PG17 QUEUED)
- [x] ~~GA-MOD-06 — Восстановление и укрепление A1 (FFprobe/FFmpeg)~~ (IMPLEMENTED; QA: STATIC/FFmpeg CLI PASS, PYTHON CI QUEUED)
- [x] ~~GA-MOD-07 — Frame Sampling / Thumbnail & Evidence Manifest~~ (IMPLEMENTED; QA: STATIC/FFmpeg CLI PASS, PYTHON CI PENDING)
- [x] ~~GA-MOD-08 — Shared Media Understanding / PSI bridge & cache~~ (IMPLEMENTED; QA: STATIC PASS / PYTHON+PG17 QUEUED)
- [x] ~~GA-MOD-09 — Audio Decode + Voice Activity Detection~~ (IMPLEMENTED; QA: STATIC/FFmpeg CLI PASS, PYTHON+PG17 QUEUED; Silero real-model QA OPEN)
- [x] ~~GA-MOD-10 — ASR Pipeline + language, source selection, word alignment~~ (IMPLEMENTED; QA: STATIC/TTS+FFmpeg INPUT PASS, PYTHON+PG17 QUEUED; real ASR model accuracy NOT RUN)
- [x] ~~GA-MOD-11 — Speech metrics: word count, active-time WPM, first speech, coverage~~ (IMPLEMENTED; QA: STATIC/NUMERIC PASS, PYTHON+PG17 QUEUED)
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
| GA-MOD-11 (implementation complete; QA open) | `9aa6338` | ASR-only Unicode lexical tokens, union ASR/VAD with correlation guard, Decimal active WPM, FFprobe/PSI clock offsets, zero/unknown policy, true A003 stored fields and video-local speech rows, schema and adversarial gold tests | STATIC PASS, Python QUEUED | PG17 QUEUED | Direct numerical gold PASS; model accuracy NOT RUN | [Group A workflow](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml) QUEUED | Actual A003 atomic writer GA18 and live ASR/VAD model QA pending |
| GA-MOD-10 (implementation complete; QA open) | `5d51249` | pure source precedence PSI/SRT/TXT/faster-whisper, video vs audio timelines, verified hashes, local inventory-pinned bounded worker, word timing, schema and RU/EN tests | STATIC PASS, Python QUEUED | PG17 QUEUED | Direct RU/EN TTS+FFmpeg input PASS; real ASR MODEL NOT RUN | [GA actions](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml) QUEUED | Model weights absent; cannot verify real WER/inference accuracy |
| GA-MOD-09 (implementation complete; QA open) | `1bbc323` | audio_decode.py, vad.py local pinned Silero JIT, 2 schemas, offline + real synthetic audio/VAD tests, service exports, audio CI requirements | STATIC PASS; Python QUEUED | PG17 QUEUED (not required for offline audio) | Direct FFmpeg 3 scenarios and FD smoke PASS; committed tests QUEUED | [Group A workflow](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml) QUEUED | Genuine Silero JIT weights, GPU, multilingual speech accuracy and host execution NOT VERIFIED |
| GA-MOD-08 (implementation complete; QA open) | `e818583` | exact PSI UUID↔D1 lineage, read-only PSI acquisition/source policy/media verification, transcript/vision normalization, ms precision, current-rights-fenced cache, strict wire schemas, adversarial unit tests | STATIC PASS / Python QUEUED | PostgreSQL integration QUEUED | No video tool needed | [Group A workflow](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml) QUEUED | Live PSI fixture and transaction-scoped host auth remain unverified |
| GA-MOD-07 (implementation complete; QA open) | `58cd886` | deterministic timestamp planner, FFmpeg PTS-confirmed PNG evidence, color-aware dHash dedupe, atomic local FrameStore, strict v1 JSON Schema, fake/real-media tests, Pillow CI | STATIC PASS; Python NOT VERIFIED | PG17 PENDING | Direct FFmpeg FD/PNG CLI PASS; real unit QUEUED | [Group A workflow](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml) PENDING | No claim full OCR/visual coverage; host-bound QDP lineage remains future integration |
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

## GA-MOD-07 implementation report (2026-10-26)

- **IMPLEMENTED; QA separately pending.** [Sparse frame sampler guide](GROUP_A_FRAME_SAMPLING_GUIDE.md).
- Code: `extractors/frames.py` deterministic early+scene+periodic sampling capped 48, FFmpeg local-only PNG output from verified FD on Linux, observed PTS/ticks cross-checked against FFprobe timebase, Pillow color-aware perceptual dedup and conservative unknown OCR/visual coverage; optional `not_observed` for EOF misses.
- Storage: `extractors/frame_store.py` atomic same-directory staging, file/dir fsync, immutable digest-checked replay, no images in PostgreSQL. Strict `schemas/group-a-frame-manifest-v1.json`. `GroupAService.sample_local_frames` exports offline-only interface, automatic worker remains disabled.
- Tests: `tests/test_group_a_frames.py` includes deterministic planner, actual/fake PNG output, duplicate and source mutation, strict PTS, malformed decoder results, filesystem write rollback, tamper/replay, path escape, generated red/blue and black video. CI pins Pillow and requires FFmpeg.
- **Read-only GitHub source audit PASS** (14 checks, isolated main diff). **Direct FFmpeg CLI smoke PASS** for 5 timestamps on generated red→blue clip, with observed frame PTS 0.0,0.3,0.8,1.2,1.7 and 128x72 PNGs. **Committed Python/PG17 CI PENDING/NOT VERIFIED**; do not claim unit tests passed.
- Implementation commits: `6f10d83` (sampler), `10ba602` (store), `45deb2d` (schema), `0265f42` (EOF sparse truth), `3faa503` (tests), `1d2007f` (public exports/CI), `fbddfca` (small budget/symlink root), `58cd886` (coverage/phash integrity).
- Next GA-MOD-08 reads PSI transcript/visual outputs only when exact source hash/contract/coverage match. No 8-frame sparse contract conflation with full OCR.

## GA-MOD-08 implementation report — 2026-10-26

- **IMPLEMENTED (QA separately open).** [PSI reuse contract / read-only bridge](GROUP_A_PSI_REUSE_GUIDE.md).
- Added `adapters/psi_reuse.py`: `PSIAssetBinding` with separate Discovery BIGINT and PSI UUID source/version/acquisition IDs; read-only Postgres PSI/D1 current rights, acquisition video SHA/size and source provenance; actual transcript/v1 & vision-prepare/v1→visual-preparation/v1 negotiation, output SHA/config/model version checks and exact integer ms↔decimal seconds.
- PSI transcript timestamps and source type survive normalization; no guessed ASR timing/no-speech. Vision OCR remains one sampled frame only, with uncalibrated confidence null and explicit unknown temporal/scene coverage. No paid Sonnet invocations or PSI schema copies.
- `PSIReuseCache` uses source+output+config+model+version digest and requires live reauthorization on every lookup. `GroupAService.reuse_psi` is explicit opt-in, default flag off, returns unavailable when outputs/peer missing rather than running local/paid extraction.
- Frozen three JSON Schemas and adversarial `tests/test_group_a_psi_reuse.py` include SHA/ID mismatches, rights, config, timebase, sparse OCR, schema and default-off service.
- Commits: `eda6c13` adapter, `3ac4c42` rights/config, `141e850` PG locks, `974b1e7` regression, `dbc3b68` D1 verification, `fadff67` D1 test, `9a39f01` public facade, `f8f9ae5` cache integrity, `5657ee4` default-off tests, `8a54974` schemas, `a43be7b` PSI OCR limit, `8f5f5e5` schema tests, `e818583` SQL fake QA fix.
- Static GitHub audit **PASS (12 checks)**; GitHub-hosted Python/PG17 QA **QUEUED / NOT RUN**, real PSI host project integration pending. No false PASS.

## GA-MOD-09 implementation report — 2026-10-26

- **IMPLEMENTED; QA separately unresolved.** See [audio/VAD implementation guide](GROUP_A_AUDIO_VAD_GUIDE.md).
- Added `extractors/audio_decode.py`: positive FFprobe audio track existence/codec, explicit multitrack choice, memory-only s16le PCM 16k mono, stream start offset provenance, bounded timeout/PCM output, corrupt/empty decoder rejection. Distinguishes proven no-audio, exact PCM digital silence, decoded nonzero signal; never infers speech from amplitude.
- Added `extractors/vad.py`: explicit local SHA-pinned Silero TorchScript CPU/CUDA, model byte hash + load through same file handle, 512-sample probability windows, hysteresis, padded/gap-merged nonoverlapping ms intervals, no model/no candidates truth states and deterministic SHA. No model weights/downloading/ASR or music claims.
- Added frozen `schemas/ga-audio-decode-v1.json` and `ga-vad-observation-v1.json`, `tests/test_group_a_audio_vad.py` adversarial and real generated audio fixtures, offline `GroupAService.inspect_local_audio` and public exports. CI requires PCM codec/aresample; model weights remain opt-in.
- Independent direct FFprobe/FFmpeg CLI **PASS**: absent audio stream, 44100 Hz stereo digital silence (16k mono 1s, peak 0), 48000 Hz tone (16k mono 1s, peak 4095). Linux verified FD /proc path FFprobe + FFmpeg **PASS**. Source/branch audit PASS; committed Python/PG17 GitHub CI **QUEUED/NOT RUN**. Real Silero model accuracy/CPU/GPU and multilingual corpus tests not performed.
- Implementation commits `d69a26f` audio decoder, `1eb04bc` VAD, `daa1907` track selection, `826b744` offline+real tests, `fe46b18` JSON Schema, `15080bd` public exports, `753aecf` negative PTS/model FD pin, `b4aa75d` schema tests, `140c405` CI PCM tooling, `1bbc323` bounded malformed-input error handling.
- Next: GA-MOD-10 ASR source selection/word alignment; do not promote raw VAD candidate time or silent intervals to completed transcript or WPM.

## GA-MOD-10 implementation report — 2026-10-26

- **IMPLEMENTED, QA pending separately.** [ASR contract & local worker guide](GROUP_A_ASR_PIPELINE_GUIDE.md).
- `extractors/asr.py`: `ASRConfig`, `ASRBackend`, verified SHA-256 `Sidecar`, source-policy `prefer_verified|psi_only|sidecar_only|local_only`. SRT cue timestamps remain video-local (captions not speech); TXT is untimed; PSI GA08 transcript requires current rights callback and independent video duration; verified no-audio/video captions coexist without false speech.
- `extractors/local_asr.py` and `asr_worker.py`: optional installed local faster-whisper behind CPU/GPU lease, full file inventory SHA pin incl model.bin/config, child SHA recheck, 16k mono PCM temp WAV, private bounded JSON and wallclock timeout, mandatory `local_files_only=True`, offline HF flags; no model acquisition, media networking or ASR auto-run.
- Validated ordered timed segments and **only actually observed** optional word timestamps/confidence; unknown words stay unknown. Language `auto/ru/en`, separate source/model/engine provenance; no fabricated WPM or complete ASR coverage. Strict `schemas/ga-asr-transcript-v1.json` and explicit `GroupAService.transcribe_local_audio` exports added.
- `tests/test_group_a_asr.py` adversarial fixtures for source precedence, transcript language, sidecar and PSI rights, no audio/digital silence, time origin, missing/overlap word times, model inventory mutation, resource lease, subprocess timeout/error, JSON Schema. `tests/test_group_a_asr_real_model.py` opt-in actual RU/EN TTS→faster-whisper WER gate; **SKIPPED unless real weights locally supplied**.
- GitHub static source audit **PASS (16 checks)**, main/feature isolation PASS. Direct RU/EN eSpeak + FFmpeg resample generated 33,983/33,137 16k mono samples: **INPUT SMOKE PASS**. Installed faster-whisper package/weights absent; **actual transcription/WER NOT RUN**. GitHub Python/PG17 CI **QUEUED/NOT EXECUTED** (old cancelled runs are concurrency deduplication).
- Implementation commits `3e5c157` ASR core, `0507872` leased local subprocess, `d8b998f` worker, `504f4bc` separate clocks/current PSI rights, `613015b` PSI confidence, `67d366a` tests, `c5b3411` optional word timing, `ca9a7a9` schema, `246132e` exports, `a8d7707` schema tests, `2bd1cc7` real model opt-in corpus, `5d51249` disabled alignment fix.
- Next GA-MOD-11 must not treat sidecar text as spoken words or VAD candidate intervals as independently confirmed total speech coverage.

## GA-MOD-11 implementation report — 2026-10-26

- **IMPLEMENTED; QA separately open.** [Speech metrics guide](GROUP_A_SPEECH_METRICS_GUIDE.md).
- `extractors/speech_metrics.py`: immutable SHA-bound GA09 audio + GA10 ASR + optional GA09 VAD metrics. Count Unicode NFC Russian/English/mixed speech transcript lexical tokens only; SRT/TXT and VAD-only never become words. Interval union and VAD-ASR temporal correlation avoid double-counting/music-like false denominator.
- Active `speech_duration_sec` 3 decimals, WPM=`words*60/active_sec` 2 decimals; less than 250ms or >3000 WPM is unavailable with explicit reason. Verified audio absence/digital zero = actual zero speech metrics; unknown audio/ASR = null, not zero.
- Verified video-local first speech and coverage require actual FFprobe audio/video offset, or explicit original SHA+PSI-output-pinned acquisition timeline binding. Unproven clock => null coverage and first speech. Source/video/PCM/ASR/VAD digests and bounds checked; output `complete_speech_coverage=false` always.
- Frozen `schemas/ga-speech-metrics-v1.json`; `GroupAService.measure_local_speech` explicit offline facade; A003 projection matches real `003_video_features_a.sql` stored columns and separate video-local speech rows, leaving derived `speech_wpm/coverage/first_speech` for A003 view. SQL writes remain GA18, not GA11.
- `tests/test_group_a_speech_metrics.py` covers numeric gold, RU/EN, repeated words, gap/overlap, uncorrelated VAD, missing/negative video offset, invalid source/PCM hash, silence/no audio/SRT/TXT, strict schema and deterministic results.
- **Static GitHub audit 15/15 PASS**; independent Decimal WPM 150.00/142.86 and coverage 20.00/21.00 NUMERIC PASS. Committed Python/PG17 CI **QUEUED/NOT EXECUTED** because GitHub Actions scheduling remains blocked. No declaration of real test or ASR model quality PASS.
- Implementation commits: `985cb94` core, `645aa11` gold tests, `17def4c` correlated VAD guard, `0b0e3f4` schema, `d509de1` schema tests, `cb86206` service exports, `9aa6338` exact A003 stored/segment projection.
- Next GA-MOD-12 multilingual OCR. ASR text must never become measured screen text without actual frame OCR.
