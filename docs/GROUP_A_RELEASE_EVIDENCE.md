# Group A — release evidence ledger

## Current verdict

**NOT RELEASE READY** — GA-MOD-00 through GA-MOD-18 **implementation complete** (19 of 25 stages); GA18 runtime QA NOT VERIFIED. Verified QDP handoff/media, A1/FFprobe/FFmpeg, PSI reuse, GA09–15 speech/OCR/subtitles/audio facts, photo/mixed carousels, and GA18 source-hash/certification-gated atomic A003/A004 snapshots are implemented inside Group A's standalone branch. GA18 static source/SQL/schema/fixture checks **70/70 PASS (structure only)**; actual Python/PG17/mixed transaction execution, genuine independently authorized full-video coverage, real OCR/ASR/audio/subtitle accuracy, current-result invalidation, Windows/Linux matrix and external QDP↔B handshake remain **NOT VERIFIED**. GA19 Group B consumer, GA20 worker/invalidation, GA21 API, GA22 benchmarks, GA23 CI/E2E and GA24 release gates are open. Do not merge or claim QA PASS based on static evidence.

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

## GA-MOD-11 — Speech metrics / A003 compatibility (2026-10-26)

- Frozen `ga-speech-metrics/v1` computes lexical spoken words only from source-bound audible ASR, active-time duration from union of observed ASR or ASR-correlated VAD intervals, WPM via Decimal with explicit rate quality gate, and video-local first speech/coverage only with FFprobe or exact PSI acquisition timeline proof. True no track/exact silent PCM yields zero; unknown SRT/TXT-only/VAD-only metrics remain null.
- Verified actual `003_video_features_a.sql`: A003 stores `spoken_words`/`speech_duration_sec`/`speech_status`, while `speech_wpm`, `speech_coverage_pct` and `first_speech_sec` are VIEW-derived using speech segment rows. GA11 returns true stored-field projection and optional video-local rows; writer remains future GA18.
- Source/branch static audit **PASS (15 checks)** and independent arithmetic **PASS**: 5 words / 2.000s = 150.00 WPM; / 2.100s = 142.86 WPM; 10s video coverage = 20.00% / 21.00%. New Python tests including JSON Schema committed, but GitHub Actions **QUEUED/NOT EXECUTED**. Real ASR/VAD/model accuracy and PG17 E2E **NOT VERIFIED**.

## GA-MOD-12 RU/EN frame OCR QA — 2026-10-26

- `extractors/ocr.py`, `tesseract_ocr.py`, strict `ga-frame-ocr/v1` and `tests/test_group_a_ocr.py` added: actual GA07 PNG/source SHA, real PTS, Tesseract5 executable + eng/rus/osd traineddata model pin, per-word original-pixel boxes, raw engine confidence, rotation/preprocessing and bounded offline execution. Failed/absent model/text remain unknown, never full video absence; rights-fenced project/run cache included. Paddle/GPU not claimed.
- Independent local Tesseract 5.5 CLI on DejaVuSans PNGs recognized EN `HELLO WORLD`, RU `ПРИВЕТ МИР`, 90°/270° `ROTATED TEXT` with real TSV boxes/confidence; **DIRECT TOOL SMOKE PASS**, not committed Python tests. Static scope audit **18/18 PASS**. Production accuracy on noisy video and PG17 E2E **NOT VERIFIED**.
- First OCR workflow edit temporarily produced invalid YAML (GitHub file-level `failure`, zero jobs). Repaired by `fd5b033`, confirmed GitHub now recognizes `Group A standalone (frame OCR and PG17)` and latest run is **QUEUED**. Do not describe malformed YAML as failed Python unit tests; Python and PG17 test verdict remains NOT RUN.

## GA-MOD-13 — Sparse screen text tracking evidence (2026-10-27)

- Added pure `text_tracking.py`, strict `ga-screen-text-tracking/v1`, public `GroupAService.track_local_screen_text` and `tests/test_group_a_text_tracking.py`. Exact GA12 observation/GA07 manifest canonical digests, source/frame/project/run/actual sampled PTS checked and host `verify_current` required. Unicode normalization, Tesseract line grouping, word-bbox union, text+spatial similarity, one-to-one global matching and per-frame dedup implemented. Unknown failed frames/large sparse gaps split tracks; reappearance after a documented blank is a new occurrence. No phantom scene-wide text absence.
- Tracks contain timestamped observations, observed first/last seconds, unknown intervening continuity, recurrence-based sampled word count, failure/absence gap markers. `first_text_true_onset_sec` and `video_wide_screen_word_count` remain null. Verified actual A003 `video_feature_a_screen_text` requires `end_sec>start_sec`; GA13 `a003_screen_text_projection=[]` and `blocked_unverified_continuity` until independent dense temporal proof / GA18 writer. It is NOT an A003 publication success.
- Static GitHub scope/source audit **19/19 PASS**, new Python tests cover flash/two captions/moving overlay, OCR edit, gap reappearance, tampering/auth and strict schema plus GA12→GA13 integration. Actions Python/PG17 **QUEUED/NOT EXECUTED**; real-video human-reviewed temporal precision/recall **NOT VERIFIED**. `main` and peer module branches untouched.

## GA-MOD-14 — subtitle evidence and production QA (2026-10-27)

- GA14 `ga-subtitle-presence/v1` maps strictly to real A003 `has_subtitles` `true|false|unknown`. Verified FFprobe subtitle stream **packets** and GA10 current-authorized timed SRT/PSI-SRT may prove `true`. Untimed TXT, ASR alone, uncalibrated sparse GA13 OCR and text-free frames cannot prove `true` or `false`.
- Visual OCR↔ASR lower-region temporal matching uses verified GA11 video clock and original GA13 frame evidence. Positive without externally authorized heldout English/Russian human gold calibration remains UNKNOWN. `subtitle_calibration.py` evaluates preselected threshold with stratified Wilson 95% precision lower-bound ≥95%, but no authentic production gold corpus is supplied or benchmarked. A source-attested full-duration dense negative review is required for `false`; no dense scanner bundled. Group B B6 style and A003 atomic writer stay disabled.
- Added local packet-level subtitle-probe, fail-closed current rights and SHA lineage validation, strict JSON Schema, adversarial tests and real generated MKV/SRT FFmpeg fixture. Direct independently executed FFmpeg/FFprobe **CLI PASS**: empty subtitle streams on plain MKV, `subrip` 1 packet in subtitle MKV. Direct Wilson formula numerical check **PASS** (100 correct/0 false ⇒ 96.30% lower bound; 40 correct/0 false ⇒ 91.24%). These are NOT completed Python/PG17 tests or real visual accuracy.
- Source/branch static audit **PASS 19/19**, changes scoped to GA feature branch; GitHub workflow `Group A standalone (subtitle truth and PG17)` **PENDING**, Python+PG17 NOT RUN. Production calibrated visual recall/precision and dense negative reviewer NOT VERIFIED. Review the [GA14 guide](GROUP_A_SUBTITLE_DETECTION_GUIDE.md) before merge.

## GA-MOD-15 — Music/SFX local event implementation QA (2026-10-27)

- Opt-in `audio_events.py` + `onnx_audio_events.py` detect bounded, source-SHA-pinned window candidates for simultaneous music/sound_effect/other without suppressing speech overlap. GA09 PCM provenance, current media rights, exact SHA-pinned local model and class-map/host license verifier are mandatory. No purchased API, model downloads, fallback GPU, persisted audio or SQL mutation.
- `ga-audio-events/v1` schema records observed/failure windows, per-class raw scores, event timeline, true/false/unknown presence: false only with authoritative no-track or zero PCM; unavailable/no detections are unknown. `full_video_acoustic_coverage=false`, uncalibrated scores never copied to A003 confidence. A003 projections only with FFprobe audio/video clock mapping; actual writer stays GA18.
- Static GitHub source/branch audit **21/21 PASS**; Python adversarial/JSON Schema tests committed. GitHub Group A standalone workflow **QUEUED/NOT EXECUTED**, no real ONNX weights, license-approved music/SFX accuracy corpus, YAMNet/PANNs latency or hostile model stress tests completed. In-process ONNX inference requires GA20 hard process watchdog. See [GA15 guide](GROUP_A_AUDIO_EVENTS_GUIDE.md).

## GA-MOD-16 — image-post/photo-carousel extraction/A004 readiness (2026-10-27)

- Implementation committed on `feature/group-a-full-module`: `extractors/carousel.py` verifies GA05 media manifest SHA and 1-based host-authoritative slide order, reads ONLY descriptor-verified local image assets with current project/run/policy/asset authorization, checks SHA/size, decodes bounded PNG/JPEG/GIF/WebP via Pillow, applies EXIF orientation and measures reduced pixel aspect ratios. Animated/mixed/video slides fail closed; no inferred CTA, audio, image playback duration, or swipe interval.
- Reuses GA12 typed OCR model protocol for per-image words/confidence and documented RU/EN text; missing/partial language/backend yields null whole-carousel text totals and first text, never false zero. `ga-photo-carousel/v1` immutable digest wire, strict JSON Schema, `GroupAService` API and exact A004 table projection implemented.
- `extractors/carousel_writer.py` uses explicit psycopg3 transaction, locks enabled source/active policy/each exact media asset and checks live QDP/project callback. It creates parent pending+items then transitions parent to partial/completed; completed A004 rows immutable by existing DB trigger. Stable observation-key replay reads and compares saved items. Does not merge QDP or claim GA18 multistream commit. PG fixtures test A004 summary first_text_slide, total count, idempotent replay and revocation.
- Source-level **structural audit 25/25 PASS**, including valid parsed `ga-photo-carousel-v1.json`, expected SQL guards, exports and test registration. **This is not Python syntax/unit/PG17 run evidence**. Real PNG/JPEG EXIF, fake OCR, rights revocation and conditional disposable PG17 tests are committed but successful runtime execution is **NOT VERIFIED**; current GitHub Actions Group A run was [40559869169](https://github.com/BanSins7/ContentMachine/actions/runs/40559869169) `pending` at guide commit `1d8d817`. Older runs canceled under `cancel-in-progress` are not test failures. Recheck the latest head/status after this ledger commit.
- No real Tesseract carousel benchmark or accuracy gold, HEIF/AVIF Pillow decoder plugin, platform-adapter rights attestation, validated Windows symlink/race handling, true GA17 video slides or GA18 atomic Group A snapshot E2E yet. Do NOT merge as release-ready on static QA alone. See [GA16 guide](GROUP_A_PHOTO_CAROUSEL_GUIDE.md).

## GA-MOD-17 — mixed carousel / video slide handoff QA (2026-10-27)

- Implemented in standalone `feature/group-a-full-module`: exact order from GA05 `ga-media-manifest/v1` with image and video asset refs; GA16 Pillow/EXIF and GA12 OCR for images; exact source SHA-bound video descriptor for video slots. Only A003 video analysis ID explicitly chosen by media asset ID is accepted, with current enabled source/policy, matching D1 video, nonexpired media and `measurement_methods.ga_asset_sha256` pin. No inferred URLs/ID-order or downloaded video.
- `PinnedA003SlideReader` reads exact existing A003 IDs and all four bounded per-video local interval types (scene, speech, screen text, audio) with duration and ratio matching the individual asset, not carousel swipe time. No video A003 auto-write or fake `video_a_analysis_id`; a missing explicitly selected A003 causes partial result.
- `ga-mixed-carousel/v1` stores immutable SHA over ordered mixed facts, source/policy/A003 binding and clock provenance, images with null durations, videos with measured positive durations, and no fabricated CTA. Missing video-wide OCR completeness leaves mixed summary `first_text_slide` / `total_screen_word_count` unknown even if image OCR is complete. `has_audio` can be true/false only with source-SHA matched GA09 `DecodedAudio` evidence, and absence requires all video tracks confirmed absent.
- A004 opt-in writer under one transaction: GA05/source/policy/media row lock+reauthorization, read exact A003 again under parent `FOR UPDATE` + existing child interval `FOR SHARE`, reject changed A003 binding before insert, pending→items→completed/partial, safe repeat key+full row comparison. Existing 004 immutable analysis trigger remains in effect. **GA18 atomic Group A snapshot and GA20 post-commit revocation/invalidation are NOT implemented** and historical A003 can be mutable by convention; do not assume prior snapshots automatically revoke themselves.
- Tests committed in `tests/test_group_a_mixed_carousel.py`: image/video/image, multiple video assets, optional real FFmpeg generated MP4, GA09 present/absent/unknown, invalid A003/media SHA, out-of-duration segments, missing/partial A003, changed video bytes, strict JSON Schema, conditional disposable PostgreSQL 17 A003/A004 view/replay/source revocation/immutability. No passing Python/FFmpeg/PG17 logs have been observed in this work. Static GitHub source/schema/writer/service/source-scope audit **34/34 PASS** is NOT equivalent to runtime test success.
- See [GA17 guide](GROUP_A_MIXED_CAROUSEL_GUIDE.md) and [execution tracker](GROUP_A_IMPLEMENTATION_TRACKER.md). Next GA18 must author A003 per video asset with actual SHA-pinned `measurement_methods.ga_asset_sha256`, commit all streams transactionally and maintain authentic quality/coverage semantics.

## GA-MOD-18 — atomic assembly and immutable A003/A004 snapshots QA (2026-10-27)

- **Implemented:** GA18 `validation.py` canonical GA05 source-media SHA binding, GA06 full scene geometry/duration, GA11 ASR-only word-count and speaker timeline, GA13 sparse OCR-only truth, GA14 subtitle true/false/unknown (negative requires dense review), GA15 local event projections. All intervals video-asset-local, ordered, nonduplicate and bounded; missing streams remain NULL/partial/unavailable. `cta_start_sec` is NULL and no swipe duration is synthesized.
- **Implemented:** `assembly.py` `ga-video-snapshot/v1` with `analysis_state`, exact stream status/coverage and reason metadata, signed-by-checksum per-extractor provenance and original QDP source/run/policy/asset SHA and measured UTC timestamp. Distinguishes a canonical SHA integrity check from actual evidence authenticity: external `verify_origin` and independent `verify_coverage` callbacks are required at persistence; a fake callback must not be used in production. GA13 positive sparse text is never certified complete without a separate dense interval producer.
- **Implemented:** `snapshot_repository.py` one psycopg transaction A003 pending+rows+partial/completed, optional same-unit-of-work GA16 photo A004 or GA17 pre-extracted mixed A004 with *inside-transaction* rebind to just-created A003 video IDs. No OCR/media decode or external provider calls under DB locks; rights, source SHA, origin and coverage verified again. Idempotence compares whole parent/child and exact source/version/clock/cert digests.
- **Implemented:** additive forward-only Group A migration `056_group_a_immutable_video_snapshots.sql` version-scope completed A003 immutability triggers over parent, scenes, speech, screen text and audio rows, plus `ga18_video_snapshot_receipts` view. Legacy A003/004 original SQL preserved, existing `video_feature_a_summary` and `carousel_feature_a_summary` not rewritten. PostgreSQL 17 execution and retained FK deletion/cleanup behavior still **NOT VERIFIED**.
- **Tests committed, NOT VERIFIED passing:** `tests/test_group_a_atomic_snapshot.py` unit and conditional PG17 scenarios: full video with deliberately fake independent review callback; technical-only partial with NULL WPM, positive sparse OCR block, invalid source versions/SHA, duplicate intervals, illegal duration, forged completed, expired rights, A003 SQL view WPM/coverage/first speech/NULL first text, GA18 completed immutability trigger, idempotent replay, insert-then-rollback, mixed image+video+image atomic A003+A004. Also strict JSON Schema and service/API/wiring checks.
- **Static GitHub source/schema/SQL structural audit: 70/70 PASS**. This is not Python compilation, database migration/application, QA of a real 2026 media file, independent human gold evaluation, production permissions attestation, or Windows/Linux E2E. Latest CI status needs to be verified for the HEAD commit; queued or cancelled CI does not become PASS. GA20 revocation/current-result selection and GA23/24 merge readiness remain blockers. [GA18 guide](GROUP_A_ATOMIC_SNAPSHOT_GUIDE.md) · [GA tracker](GROUP_A_IMPLEMENTATION_TRACKER.md).
