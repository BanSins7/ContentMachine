# Group A standalone module — integration guide (through GA-MOD-12)

**Branch:** `feature/group-a-full-module` • **PR:** [#35](https://github.com/BanSins7/ContentMachine/pull/35), draft into `main`.
**Status:** GA-MOD-00..12 implemented, **not yet a deployable QDP→Group A processing pipeline**. Intake is available only through explicit dependency injection; active workers remain disabled.
**Canonical plan:** `docs/GROUP_A_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md`.

## Historical GA-MOD-00 bootstrap snapshot

Initially, only Group A-owned A1 extraction code, offline tests, its historical SQL reference
and this module's docs/CI have been transferred from the old PR #26.
`intelligence/group_a/extractor.py` measures verified local video duration,
display aspect ratio and *technical* FFmpeg scene boundaries. It does not
perform ASR, VAD, OCR, subtitles, audio events, carousel extraction or CTA
interpretation. Its A003 feature payload keeps all unmeasured observations
`NULL/unknown/not_run` and analysis state partial.

The original `017_group_a_technical_extraction.sql` is **archived under**
`intelligence/group_a/legacy_sql/`. The common migrations runner loads only
`intelligence/migrations/[0-9]*.sql`, so this legacy SQL **must not be run
manually in a clean main database**. GA-MOD-03 has now defined additive,
versioned schemas 054/055 with a legacy A1 upgrade path. The original A1 repository code is
preserved for later adaptation, **not for live invocation**.

## Wiring and capability negotiation

```text
QDP (independent branch; producer/delivery dispatch not yet wired to GA)
     → opt-in QDPReader / current QDP outbox+preparation validation
Group A GA054/055 inbox + immutable run, media and initial queue receipt
     → no automatic worker until later runtime stages
     → future byte-verified GA-MOD-05 processing and A003/A004 output
     → future Group B / Analytics consumers
```

See [GA-MOD-04 QDP handoff integration](GROUP_A_QDP_HANDOFF_INTEGRATION.md).
A receiver must be explicitly instantiated with enabled settings and real
QDP/MediaCatalog adapters. Default HTTP tasks and background worker remain
disabled; `qdp_intake.state=opt_in_unwired` in capabilities is intentional.

A host may query the opt-in `intelligence.group_a.capabilities.capability_snapshot()`
or, if the `http_api` router is explicitly mounted later,
`GET /api/projects/{project_id}/group-a/capabilities`. It reports:

- `qdp_handoff.state=unavailable` and `runtime.state=disabled`, code
  `qdp_handoff_not_integrated` until automatic host/worker integration in later stages.
- `technical_a1.state=tools_detected/tools_unavailable` depending on local
  `ffmpeg` and `ffprobe`; this is **not** a pipeline-ready declaration.
- Legacy HTTP task creation/read operations return 503 during bootstrap, and
  the Group A automatic worker is disabled.

**No host app hooks are installed through GA-MOD-04.** Mounting the existing
`http_api` is deliberately postponed until version-pinned schema/intake
and capability checks exist. Neither this branch nor the PR changes
`main`, QDP, SRE, PSI, Group B, Analytics or the common migrator.

## Safe developer commands

```bash
python -m compileall -q intelligence/group_a tests/test_group_a_*.py
python -m unittest discover -s tests -p 'test_group_a_*.py' -v
python -c "from intelligence.group_a.capabilities import capability_snapshot; print(capability_snapshot())"
```

No PostgreSQL credentials, API tokens or internet access are required for
the bootstrap core tests. `tests/test_group_a_db.py` is an **archival A1
integration fixture**: its true QDP/PG17 integration tests are skipped
unless a disposable `CM_TEST_PG_DSN`, `psycopg` and the legacy QDP module
are present. Even if present, this does not prove that the *new* handoff
contract is operational.

**Do not** run a worker against production data from this bootstrap branch.
Do not manually merge the legacy QDP migration. Use a separate disposable
PostgreSQL 17 instance once GA-MOD-03 has supplied proper GA migrations.

## Module boundaries / planned compatibility

| Boundary | GA-MOD-00 behavior | Future owner/stage |
|---|---|---|
| QDP `qdp-handoff/v1` | `unavailable`, no silent best-effort SQL fallback | GA-MOD-04 inbox |
| Media asset ID/hash/slide manifest | A1 local path only, no verified manifest yet | GA-MOD-05 |
| A003 technical video schema | A1 output contract available, no new GA schema installed | GA-MOD-03/18 |
| A004 photo/mixed carousel | Not implemented | GA-MOD-16/17 |
| PSI hash bridge | Not implemented; never equate PSI and D1 IDs | GA-MOD-08 |
| Group B facts/CTA | Not implemented; Group A never infers CTA | GA-MOD-19 |
| Analytics reader | Not wired | GA-MOD-19/24 |
| User-facing UI | Not mounted | GA-MOD-21 |

## Backout and review

Revert GA-only commits on the feature branch or keep the PR as draft. No
production migration or `main` write is performed by GA-MOD-00. The
A1 source and its legacy schema are retained for recovery from
`ae9fa80f6b53eb4ba2fe16eb3ecf65102e13bc7d` (old PR #26).
Fresh schema installation, upgrade roll-forward/rollback planning, host wiring
and worker deployment are explicitly out of scope of this bootstrap stage.

## GA-MOD-01 — Frozen public contract interface

See [Group A v1 contract matrix](GROUP_A_CONTRACT_MATRIX_V1.md) and the four machine-readable schemas under `intelligence/group_a/schemas/`. Pure validators are exported by `intelligence.group_a.contracts` and `intelligence.group_a`; external consumers must additionally verify cross-project and media digest lineage. This freeze does **not** activate the QDP adapter or the legacy repository.

## GA-MOD-02 — Isolated port architecture

The standalone port declarations, flags and non-activating planning service are documented in [GROUP_A_PORTS_AND_ADAPTERS.md](GROUP_A_PORTS_AND_ADAPTERS.md). All peer adapters remain unavailable until their contractual integration stages; no auto-wired modules are imported, and A1 legacy DB execution is still disabled.

## GA-MOD-03 — PostgreSQL storage schema

See [Group A PostgreSQL migration](GROUP_A_POSTGRES_MIGRATION.md). New additive `054_group_a_module_core.sql` runs against main without QDP; legacy A1 017 is still archive-only. Public writer/queue adapters remain unavailable, although GA-local schema metadata can be read. CI PG17 migrations are queued; do not activate Group A until GA-MOD-04 handoff and later release gates pass.

## GA-MOD-04 — Durable QDP inbox

The standalone opt-in QDP reader/receiver, exact event envelope and committed ACK are documented at [GA QDP handoff integration](GROUP_A_QDP_HANDOFF_INTEGRATION.md). The new `055_group_a_handoff_inbox.sql` is an additive GA-only migration. The QDP branch remains separate. No automatic host/API/worker is installed and no live cross-branch test has run; default intake stays disabled until explicitly configured with QDP and a byte-verifying MediaCatalog.

## GA-MOD-05 — Verified local MediaCatalog

The byte-checking resolver and exact D1 media catalog adapter are described in [Group A Media Manifest](GROUP_A_MEDIA_MANIFEST_GUIDE.md). They use explicit trusted roots, complete SHA and file magic, no network or automatic startup. Carousel order requires an authoritative host callback. Runtime remains off by default; PG17 and Python unit tests remain queued until GitHub runner capacity is available.

## GA-MOD-06 — A1 technical FFprobe/FFmpeg extraction

The new canonical `extractors/technical.py` is strict and offline-only. `extractor.py` preserves its legacy A1 API and DTO. See [technical guide](GROUP_A_TECHNICAL_EXTRACTION_GUIDE.md). New strict calls require explicit authorized roots, reset video start-time offsets, limit duration to 600s and scenes to 300, fail on decoder errors and expose separate version/method evidence. A1 is still a **partial** measurement, not complete Group A. Manual FFmpeg CLI smoke PASS; committed Python tests remain queued; no automated worker activation.

## GA-MOD-07 — Sparse frame evidence / immutable local PNG

See [frame sampling guide](GROUP_A_FRAME_SAMPLING_GUIDE.md). Explicitly call `GroupAService.sample_local_frames` with a GA-MOD-05 `VerifiedFile`, GA-MOD-06 `ProbeInfo`, authorized local roots, optionally technical scenes, and `FrameStore` with project/run IDs. The module produces a strict `group-a-frame-manifest/v1`, real observed source PTS ticks, image SHA and conservative unknown OCR/visual temporal coverage. On Linux, FFmpeg receives the verified source FD. No automatic job, SQL image storage, Group B semantic inference or provider call is installed. CLI synthetic PNG smoke PASS; committed Python/PG17 CI PENDING.

## GA-MOD-08 — PSI read-only shared media reuse

See [PSI reuse guide](GROUP_A_PSI_REUSE_GUIDE.md). Separate exact PSI UUID source/version/acquisition IDs from D1 Discovery BIGINT video ID, and verify the original media SHA/size plus current D1 and PSI rights before reuse. PSI transcript `transcript/v1` and local visual preparation `vision-prepare/v1` have explicit version translation (Group A VisionReader port `visual-preparation/v1`). `GASettings(allow_psi_reuse=True)` is mandatory; it is default-off, without network/paid Sonnet calls or automatic local fallback execution. Timed words and single-frame OCR observations stay partial/unknown-coverage; no duplicate ASR/OCR catalog. New unit tests and three schemas committed; GitHub Python/PG17 tests queued, not yet passed.

## GA-MOD-09 — Audio decode and VAD

See [audio/VAD guide](GROUP_A_AUDIO_VAD_GUIDE.md). Call `GroupAService.inspect_local_audio(verified_media, roots=[...], model=local_sha_pinned_Silero_or_None)` explicitly. FFmpeg uses the authorized GA05 media descriptor, decodes 16k mono PCM into **memory only**. Missing audio track is not a decoder failure; exact PCM silence is not 'missing track'; absent VAD or no candidates never falsely produces a completed speech transcript. Actual Silero weights must be preinstalled from a trusted source and pinned SHA; no automatic download/paid provider inference is enabled. Direct FFmpeg audio/FD smoke PASS; GitHub Python+PG17 CI QUEUED. Full ASR/words/WPM, model accuracy and host worker activation remain GA-MOD-10+ concerns.

## GA-MOD-10 — ASR pipeline and verified words/captions

See [ASR pipeline guide](GROUP_A_ASR_PIPELINE_GUIDE.md). `GroupAService.transcribe_local_audio(decoded_audio, config=ASRConfig(...), psi=..., psi_verify_current=..., sidecar=..., backend=LocalFasterWhisper(...), video_duration_sec=...)` explicitly chooses sources; it does not auto-dispatch, download model weights or invoke paid providers. Captions from SHA-bound SRT and untimed TXT remain distinct from audible ASR, PSI needs a current-rights verifier and video duration, and word timestamps remain null unless truly observed. Local faster-whisper must use a verified model-file inventory and host resource lease; run via bounded subprocess. Model accuracy and real Silero interoperability NOT VERIFIED; Python/PG17 CI QUEUED. Next GA11 measured speech metrics.

## GA-MOD-11 — Speech metrics and real A003 projection

See [speech metrics guide](GROUP_A_SPEECH_METRICS_GUIDE.md). `GroupAService.measure_local_speech(decoded_audio, transcript, video_probe=..., vad=..., config=SpeechMetricsConfig(...), psi_timeline=...)` produces immutable GA11 evidence. Words count only audible ASR, not SRT/TXT; VAD alone cannot count words, and unrelated music-like candidate windows cannot become WPM active duration. Missing video clock => first speech and coverage unknown, never guessed. `a003_speech_projection` matches real stored `video_feature_a_analyses` columns, and `a003_speech_segments_projection` supplies video-local rows for later view-derived first_speech_sec; no mutable SQL write or complete Group A assertion. Source audits/formula gold PASS; Python/PG17 tests queued, and GA18 atomic writer still required.

## GA-MOD-12 — Verified frame OCR and geometry

See [OCR guide](GROUP_A_OCR_GUIDE.md). `GroupAService.inspect_local_frame_text(frame_manifest,png_blobs,project_id=...,run_id=...,backend=TesseractAdapter(binary=...,tessdata_dir=...,expected_sha256=...),config=OCRConfig(...),cache=OCRCache(),verify_current=host_rights_callback)` is offline-only and validates every GA07 PNG byte against its manifest. Tesseract5 CPU-only `eng`/`rus` traineddata and executable must be SHA-pinned, optional 0/90/180/270 rotations return original PNG pixel boxes, real PTS and raw OCR confidence. Missing model/partial language/budget failure stay unknown; never infer text-free whole video from sparse frames. No automatic writer or GPU/Paddle runtime. Real independent Tesseract CLI RU/EN/rotation smoke PASS; committed Python/PG17 CI QUEUED/NOT VERIFIED. Next GA13 tracks text through time; GA18 owns A003 SQL.
