# Group A — release evidence ledger

## Current verdict

**NOT RELEASE READY** — GA-MOD-00 through GA-MOD-10 **implementation complete** (11 of 25 stages). GitHub Python/PG17 CI QUEUED/NOT VERIFIED. Standalone QDP intake, verified media, A1 FFmpeg scenes, sparse frames, PSI reuse, offline audio/VAD and opt-in local ASR source/worker implemented. Real Silero/faster-whisper weights and multilingual accuracy NOT VERIFIED. Speech metrics, OCR, A003/A004 atomic assembly, QDP host worker, Group B/Analytics and cross-platform E2E remain future stages.

## Provenance (snapshot at bootstrap)

- Repository: `BanSins7/ContentMachine`
- Canonical branch: `feature/group-a-full-module`
- Branch bootstrap commit: `42889c094d52b4051e5a545aef4cada1171d2339`
- Main baseline/merge base: `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` (2026-10-26)
- Recovery A1 commit: `ae9fa80f6b53eb4ba2fe16eb3ecf65102e13bc7d`
- Old PR #26: closed as superseded by draft PR #35; no old history force-rebased
- Current QDP branch checked: `4054a3ecc739b467c8ffcc10fc99dfa5e1868101`
- Canonical GA draft PR: https://github.com/BanSins7/ContentMachine/pull/35

## CI and QA matrix — do not invent PASS

| Check | Status during GA-MOD-00 | Evidence |
|---|---|---|
| main→GA diff scope | PASS at bootstrap | only GA code/tests/docs; no QDP/PSI/Analytics diffs |
| Python GA unit contracts | QUEUED | [bootstrap run #40298383330](https://github.com/BanSins7/ContentMachine/actions/runs/40298383330) |
| Python syntax | QUEUED | same workflow |
| FFmpeg real synthetic media | QUEUED | `test_group_a_core.py` conditionally runs if tools present |
| PostgreSQL 17 QDP→GA | NOT RUN/NOT INTEGRATED | legacy QDP not present in clean main |
| Windows E2E | NOT RUN | scheduled GA-MOD-23 |
| QDP v1 inbox delivery/ACK | UNAVAILABLE | scheduled GA-MOD-04 |
| Full five-kind media extraction | NOT IMPLEMENTED | scheduled GA-MOD-06..18 |
| Group B/Analytics handoff | NOT IMPLEMENTED | scheduled GA-MOD-19..24 |

Bootstrap implementation commit: `6966ce38b270a2d1f6099825b52984192a98d530`. Checks are **queued**, not passed, at time of this update. Latest checks and commit SHAs should be added after each real CI result.
Migrations are forward-only and checksum-protected by
`intelligence/migrate.py`; the original 017 GA SQL is archival, outside
the runner migration directory. No applied SQL file was changed.

## External dependency matrix

- QDP v1 contract is developed on another branch: do not copy its migration
  or infer readiness from shared SQL table names.
- SRE result selection (Embeddings + Opus 5.5) is outside GA ownership.
- PSI `project_source_id` is not D1 `video_id` / `media_asset_id`;
  re-use requires a verified hash bridge in GA-MOD-08.
- Group B supplies semantic interpretation and CTA, while GA owns
  measured temporal/visual/audio facts.
- Analytics consumes immutable, version-pinned A snapshots.

Revalidate the branch heads, statuses and matrix at the next stage.

## Bootstrap hardening pending CI

Direct A1 repository APIs (`create_task`, `list_tasks`, `task_details`, `work_once`) now use the same unavailable handoff guard as the worker and HTTP entrypoints. Prior legacy DB integration tests remain archival and skipped until a real version-pinned handoff exists. This is a bootstrap fail-closed safeguard, not proof of production QDP interoperability.

## GA-MOD-01 contract freeze evidence

- Strict Python v1 QDP/manifest/stream/result parsing and SHA lineage; wire format pinned by four JSON Schemas in `intelligence/group_a/schemas/`.
- Adversarial tests committed but GitHub-hosted runner has not executed them. CI state: **QUEUED**, **NOT PASSED**, not an observed FAIL.
- [GA contract matrix](GROUP_A_CONTRACT_MATRIX_V1.md) records real QDP, A003, A004, B, PSI and Analytics schema limitations.
- Repeated GA workflow runs now use `concurrency.cancel-in-progress` to reduce redundant queued jobs; not a guarantee of immediate runner capacity.

## GA-MOD-01 current QA evidence

- GitHub connector fetched four committed JSON Schema 2020-12 files; each passed JSON parsing and required/extra-field schema metadata checks (**STATIC PASS**).
- No local direct Git checkout possible (container DNS to github.com unavailable). Python unit/FFmpeg/PG17 validation NOT RUN by this session. GitHub Actions [current scheduled run](https://github.com/BanSins7/ContentMachine/actions/runs/40302252202) is PENDING; no CI PASS or FAIL conclusion yet.
- Whole repository has many unrelated queued workflow runs. New GA workflow concurrency cancels older duplicate GA runs; observed cancellations are due to deduplication, not test failures.

## GA-MOD-02 — Port isolation evidence (2026-10-26)

- Modules: `intelligence/group_a/{ports.py,config.py,service.py,adapters/unavailable.py}`.
- Pure injected `GAPorts` defines QDPReader, MediaCatalog, TranscriptReader, VisionReader, ResultWriter, HandoffConsumer, Clock, TaskQueue. All missing peers explicitly report unavailable, `GroupAService.start()` refuses execution.
- Offline fake tests are committed in `tests/test_group_a_ports.py`, including full positive **planning only**, peer version mismatch, status revocation, cross-project/asset mismatch, and read exceptions; no live queue/writer possible.
- `capability_snapshot()` exposes `adapter_readiness` and preserves the existing public fail-closed bootstrap contract.
- `compare_commits(main, feature/group-a-full-module)` verified GA-only diff: PASS. Python unittest, full `jsonschema` validation, FFmpeg smoke, PG17: **NOT VERIFIED** at this time, workflows still QUEUED. A cancelled outdated CI run is not a test failure. [Stable workflow URL](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml).

### Post-implementation review — GA-MOD-02

- Public exports and environment-aware diagnostics added in `c41e459` and `b92f3da`.
- Structural read-only audit of port declarations and forbidden peer imports: STATIC PASS.
- GitHub compare main→Group A shows only GA files, own workflow/docs/tests; main unchanged.
- CI continued queued across repository workflows. No executed Python test report is available; PG17 E2E still NOT RUN. Do not merge into main until later release gate.

## GA-MOD-03 implementation / validation evidence

- SQL: `intelligence/migrations/054_group_a_module_core.sql` independent from QDP, optional dynamic historical 017 view, seven tables and seven integrity trigger functions.
- Static structural verification on GitHub HEAD: **PASS** (balanced 7 SQL function delimiters, no QDP FK, seven GA tables, PG17 service CI, GA-only diff).
- Tests: `tests/test_group_a_migration_db.py`, `tests/test_group_a_storage_pg.py`; disposable PG17 is configured in the Group A workflow. **Actual PG17/SQL apply and Python tests not yet run**, CI queued. Do not assert `python -m intelligence.migrate` was executed successfully on an actual PG17 instance; it remains an open release check.
- Historical 017 remains archived and untouched; 054 upgrade path never creates fake QDP tables outside disposable fixture.

### GA-MOD-03 post-check refinement

- Follow-up fixes `3631b73`, `0571c3c`, `ca88f0a` protect complete run gates, reject completion with pending A003/A004, and align retention test with main A003 trigger.
- No code outside standalone Group A or its single new `054_group_a_module_core.sql` migration edited.
- The migration checksum is not yet deployed to a live PostgreSQL17 DB; as a new unapplied migration, its final content is allowed to evolve on this isolated branch. Once deployed, use a new forward migration for any further changes.

## GA-MOD-04 independent module / QA evidence

- New `055_group_a_handoff_inbox.sql`: conflict quarantine + read-only historical receipt projection (no QDP FKs or cloned QDP tables).
- `GroupAInboxReceiver` and `PostgresQDPReader` opt-in with capability/version negotiation, outbox/delivery/invalidation checks and source/policy/media exact pins; public Group A facade exports safe integration entrypoints. Two JSON Schemas document event envelope/committed receipt.
- Static GitHub audit: GA-only branch changes **PASS**, strict required checks visible **PASS**. Test code for PG17 idempotent replay, concurrent delivery, failing queue rollback, outbox absence and post-accept invalidation committed. **Execution: QUEUED/NOT RUN**, no false PostgreSQL PASS.
- Need peer QDP outbox producer to emit the agreed nested `qdp.preparation_ready/v1` payload, host dispatch to deliver it, MediaCatalog byte verification (GA-MOD-05), and eventual activated media workers before claiming live release readiness.

## GA-MOD-05 implementation evidence (2026-10-26)

- Added `intelligence/group_a/media_manifest.py` and `adapters/media_assets.py`: fail-closed 2GiB local SHA+size/magic validation, explicit roots, symlink/traversal rejection, opened file descriptor revalidation, retained A1 `authorized_file` video gate, full-SHA opaque refs.
- `tests/test_group_a_media_manifest.py` exercises five post types, image/video formats, tampering, byte-change/rename after verification, symlinks, FIFOs, and missing/foreign assets.
- `tests/test_group_a_media_catalog.py` exercises selected D1 asset, matching QDP source/policy, rights revocation, expiry and required trusted carousel ordering.
- `tests/test_group_a_handoff.py` adds disposable PG17 integration through the real verified catalog, proving byte checks precede durable inbox ACK at test design level.
- Read-only GitHub source audit **STATIC PASS**; executable Python/PG17 remains **QUEUED/NOT RUN**, no claim of runner success. Real host producer/dispatcher, codec validation, media workers and Windows race tests remain future stages.

## GA-MOD-06 — technical A1 evidence (2026-10-26)

- Strict `extractors/technical.py` plus old `extractor.py` API shim, A1 technical facts only, v2 tool/method metadata (public A003 still partial; no FPS/resolution or CTA classification).
- Static audit PASS: FFprobe timebase/offset, first-frame selection, max 600s/300 scenes, no network, `-xerror`, bound output/progress, authorized roots and post-decode mutation check.
- Manual local FFmpeg command smoke PASS: generated 2s red/blue video with 3s timestamp offset => stream duration 2s, normalized PTS 0 and 1, 2 selected frames; static 2s video => one selected frame at PTS 0.
- Python `test_group_a_technical.py` committed, **GitHub Actions QUEUED/NOT EXECUTED**, including existing PG17 tests. Manual CLI does NOT certify the committed Python module. Windows media race and live QDP worker release validation remain open.

## GA-MOD-07 sparse frame evidence (2026-10-26)

- `extractors/frames.py` deterministic early/technical-scene/periodic planner; exact FFmpeg observed PTS vs source timebase, Linux verified descriptor, safe PNG output, color+dHash duplicate suppression, bounded budget and truthful sparse coverage (OCR/visual temporal unknown).
- `extractors/frame_store.py` local-only append/atomic directory commit, immutable digest-checked replays, SHA/size/PNG/perceptual evidence checks, rollback cleanup; no frame bytes in DB. Strict v1 JSON Schema committed; public service wiring stays offline-only.
- 14-point GitHub source and branch audit **STATIC PASS**. Real FFmpeg command on disposable synthetic red→blue clip yielded five valid 128×72 PNGs with observed PTS `0,0.3,0.8,1.2,1.7` (requested `0,0.25,0.75,1.2,1.7`): **DIRECT CLI PASS**, not a Python test.
- Unit and real FFmpeg end-to-end tests in `tests/test_group_a_frames.py` committed, CI requires test-only Pillow. **Python/PG17 GitHub Actions PENDING/NOT VERIFIED**, no false PASS. Host mapping to authoritative QDP media manifest, future ASR/OCR/PSI bridge, Windows/macOS race handling and performance remain open.

## GA-MOD-08 — PSI reuse implementation / QA evidence

- Inspected PSI main contracts `transcript/v1` and actual `vision-prepare/v1`; did not mistake PSI UUID source_version_id for Discovery video BIGINT or auto-call Sonnet vision. Read-only `PSIAssetBinding` + `PostgresPSIReader` check project D1 video/SHA/source permission, upstream acquisition SHA/asset size, PSI version, rights, job state, output SHA and config identity.
- Timed SRT/ASR observations translated exactly from integer milliseconds; sparse OCR from PSI visual preparation never claims full coverage, absent speech or calibrated OCR confidence. Three frozen schemas, explicit default-off `GroupAService.reuse_psi`, and current-rights-fenced `PSIReuseCache` added.
- Static GitHub source audit **PASS (12 checks)**; new unit tests include mismatched IDs/hashes/rights/config, model version, OCR sparse coverage, cache revocation and schema validation. Actual Python+PG17 jobs still **QUEUED/NOT EXECUTED**; no published runtime PASS. Need live PSI+Discovery project fixtures and host transaction authority before merging.

## GA-MOD-09 — Audio decode / VAD evidence (2026-10-26)

- Added GA09 opt-in local audio decode with exact D1 media SHA, FFprobe track enumeration, explicit multitrack selection, 16k mono PCM and truth distinction (no track vs digital silence vs nonzero audio). No PCM bytes persisted or paid calls.
- Added local-only full-SHA-pinned Silero TorchScript adapter, CPU/CUDA config, hysteresis/interval union and conservative no-speech semantics; 2 frozen JSON Schemas and adversarial Python/real FFmpeg fixture tests. Actual pinned Silero weights NOT installed or benchmarked yet.
- Direct independent real FFprobe/FFmpeg smoke **PASS** (no audio vs stereo silent vs tone; 16k mono; silence peak=0, tone peak=4095), and Linux pass_fds descriptor probe/decode **PASS**. Read-only GitHub source/scope audit **PASS**. GitHub-hosted Python/PG17 suite still **QUEUED / NOT RUN**, genuine Silero inference, multilingual ground-truth accuracy and host E2E **NOT VERIFIED**.

## GA-MOD-10 — ASR implementation evidence (2026-10-26)

- Pure local ASR with strict source ordering: current-reauthorized PSI ASR/SRT, hashed sidecar SRT video-local captions, TXT untimed, or explicit resource-leased local faster-whisper worker with preinstalled inventory-SHA model. No external network or automated paid fallback.
- `ga-asr-transcript/v1` preserves timed segments and optionally model-observed word timestamps only. Silent/no-audio input bypasses local ASR but may still retain video captions; distinct audio/video time origins and PSI rights rechecks mandatory. End-to-end word count/WPM is NOT inferred.
- Actual RU/EN eSpeak→FFmpeg generated local input PCM at 16k mono, 33,983/33,137 samples: **DIRECT INPUT TOOL PASS**. Model package/weights missing: no true ASR accuracy claim. Adversarial and schema tests committed, and opt-in real model WER gate; **Python/PG17 GitHub Actions QUEUED/NOT EXECUTED**. Static source audit (16 checks) and GA scope PASS.
