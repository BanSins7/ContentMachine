# Content Machine — Project Source Intelligence · Stage 07: Unified Transcription

## Status

Implements **07.1–07.7** from the amended Phase I plan. Uses the Stage 06 verified acquisition manifest; it does not download videos or call OpenRouter. This is a preparation layer for Stage 09 Timeline Fusion, **not** Evidence approval or video-vision analysis.

## Data contract: `transcript/v1`

- `segments: [{start_ms, end_ms, text, language, source, confidence?}]` — strictly ordered non-overlapping integer millisecond ranges; source is `srt` or `asr`; confidence omitted unless a provider actually supplied one.
- `video_sha256`, `platform`, `native_id`, `normalizer_version`, `engine`, `model`, `model_version`, `speech_status`, `processing_status`, `detected_language`, `languages`.
- `source_text.srt`, `source_text.txt` preserve the *read text* (bounded), even when TXT lacks timing. Exact immutable underlying bytes and per-asset SHA remain in Stage 06 output.
- `timing_coverage: {duration_ms, first_start_ms, last_end_ms, speech_timed_ms, fraction_of_video}`; fraction is null if no trusted duration. Fraction reflects voiced timed intervals, **not** guaranteed ASR accuracy or coverage of all speech.
- `warnings` distinguishes malformed cues, overlapping intervals, out-of-bounds, source mismatches, truncation, and untimed text. Malformed cues are excluded, not assigned arbitrary timestamps.

### Status truthfulness

- `speech_detected`: one or more *valid timed* cues.
- `no_speech`: explicit downloader metadata says no speech with empty sidecars, or an independently completed local ASR returns no segments.
- `asr_error`: the downloader recorded ASR failure; **not** reclassified as silence.
- `unknown`: absent/unusable SRT or TXT-only transcription; no timestamps are guessed.
- Processing: `completed`, `partial`, or `unavailable`. In `unavailable` cases a traceable transcript output can still preserve the untimed original and the warnings; consumers **must not** fuse it to a timeline.

Languages are taken from metadata or per-ASR segment. `auto` yields `unknown` unless a decoder returns its actual detected language; language is never guessed from the text.

## Pipeline

1. User registers one TikTok/Instagram/YouTube *video* URL and executes the Stage 06 downloader.
2. Stage 06 stores a verified `video-acquisition/v1` manifest pinned to an append-only `project_source_versions` row, including MP4 SHA and sidecar SHA/path.
3. User manually clicks `Собрать таймкоды из SRT` (reuse, free). HTTP inserts a project-scoped `stage='asr'` job; it does not run anything inline. A repeat request for the same pinned version and configuration returns the existing job.
4. Optional, explicit `Распознать заново локально` sets mode `local_asr`. The worker calls the **existing** local faster-whisper model in a separate, timeout-bounded process, on the verified MP4. This creates its **own** versioned processing output; the SRT, original MP4 and previous transcript remain untouched. No paid model is involved.
5. Worker verifies source ID, native ID, original Stage 06 `source_version`, MP4 hash, metadata, and sidecar SHA/path/size; failure marks job unavailable/failed instead of fabricating an output. A mutable legacy archive changed since Stage 06 requires reacquisition/new manifest.
6. Normalizer reads the SRT timestamp lines or ASR segment timestamps. TXT without timing goes to `source_text.txt` only. Saves `source_processing_outputs(output_type='transcript', schema_version='transcript/v1')` and SHA256 of canonical JSON, pinned to the exact video version and extraction job.
7. GET API serves job state and paged segments, returning the total count and `has_more` without changing the stored output.

PostgreSQL migration `036_project_unified_transcripts.sql`: immutable transcript request parameters, `project_id/source_id/video_source_version_id` FK to original acquisition, ASR job/source version FK, output FK, unique idempotent mode/config per input video version and one in-flight ASR per project source. Migrations `030`–`035` remain untouched.

## API (same local-origin protections as existing Content Machine)

Base: `/api/projects/{project_id}/sources/{source_id}/transcription`

- `POST /start` JSON `{ "mode": "reuse_sidecar", "language": "auto" }`; or `{ "mode": "local_asr", "language": "ru" }`. Optional `acquisition_job_id` pins a historical Stage 06 acquisition. Modes `reuse_sidecar` (default) and `local_asr`; languages `auto`, `ru`, `en`.
- `GET /jobs`, `GET /jobs/{job_id}?offset=0&limit=50` (max limit 100), `POST /jobs/{job_id}/retry` with `{}` only, for failed/unavailable runs while attempts remain.
- Errors are project-scoped and sanitized. No source can be retrieved across projects by guessing a UUID. Existing API key and local origin restrictions apply to mutations.

### Run the local worker

```bash
python -m intelligence.migrate
python -m intelligence.project_sources.transcript_jobs --once
# or continuously, explicitly started:
python -m intelligence.project_sources.transcript_jobs --poll 2
```

This worker is **not automatically running** just because the frontend enqueued work. The local-ASR mode requires the existing `faster-whisper` dependencies/model and may need more RAM/time than reuse mode. When unavailable it is a failed run; there is no automatic switch to paid OpenRouter. `--once` processes at most one job. Retry is explicit and bounded. Existing downloaders page and nine-step project questionnaire are unchanged.

## Safety / QA

Tests use synthetic UTF-8/CP1251 SRT, malicious timestamp/overlap and duration edge cases, multilingual ASR, explicit no-speech, ASR failure, missing and changed sidecars, TXT-only sources, stable version FK/PG constraints, idempotent queue, mock local ASR, cross-project denial, and HTTP. No internet, real video downloads, OpenRouter charge, or copyright permission checks are triggered by CI. Live Whisper quality and the behavior of real platform SRTs require a separate opt-in E2E run.
