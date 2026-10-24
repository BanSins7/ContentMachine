# PSI Phase II, Stage 09 — Timeline Fusion & Video Intelligence JSON

## Purpose and limits

Join **already captured, exact-version** Stage 06 MP4 provenance, Stage 07 normalized transcript and Stage 08 sampled-frame OCR and optional model-reported descriptions into an immutable per-project **video-intelligence/v1** snapshot. The fusion worker is **local, deterministic, has no LLM/FFmpeg/ASR/network calls**, and costs **$0** itself. Do not mutate or automatically classify any Group A/B records. Content Features B remain candidates for human review. Stage 10 Evidence Normalization and RTB integration are not implemented here.

## Inputs, pins and consistency

1. An explicit `POST /api/projects/{project_id}/sources/{source_id}/fusion/start` locks the source row and selects a **completed/partial Stage06 acquisition output**. The exact project, source, video_source_version_id, acquisition job/output and immutable JSON checksum are pinned.
2. Select latest completed/partial/unavailable Stage07 transcript **with stored output**, latest Stage08 `prepare` with a stored output, and latest Stage08 `analyze` tied to **that exact preparation** (never combine two different frame samplings). Optional job IDs select exact revisions. Omit an input using `include_transcript:false`, `include_vision:false`, or `include_vision_ai:false`; at least transcript or visual preparation is required.
3. Jobs are idempotent on the content signature: fusion engine version + pinned input job IDs, processing_output IDs and SHA-256 + exact video source version. Changed upstream inputs produce **new** fusion jobs; prior snapshots are immutable. Inputs must share exact `project_id`, `source_id`, `video_source_version_id` and `video_sha256`.
4. PostgreSQL migration `038_project_timeline_fusion.sql` adds strict project-scoped composite FKs, immutable input-identity triggers, one active fusion job per source, job leasing, and append-only `source_processing_outputs.output_type='video_analysis'`, `schema_version='video-intelligence/v1'`.
5. `timeline_jobs.run_one()` verifies each immutable JSON blob's SHA-256, constructs the deterministic result, rechecks the lease/attempt fence before writing, and stores SHA-256 of canonical JSON. Failed local jobs may be explicitly retried within the cap. **No duplicate paid provider calls.**

## Timeline and evidence fidelity

- Intervals are merged from *measured* start/end timestamps of ASR/SRT, short OCR frame spans (max 40 ms), model descriptions pinned to a sampled frame (max 40 ms), and scene boundaries. The result includes `start_ms`, `end_ms`, `spoken_text`, `on_screen_text`, `visual_description`, `scene_id`, and typed evidence IDs. ASR spans never overlap, OCR observations are never stretched across a scene, and model descriptions do not imply full-video coverage.
- Stable evidence IDs: `speech:{transcript_job}:seg00001`, `ocr:{prepare_job}:f001`, `scene:{prepare_job}:sc001`, `frame:{prepare_job}:f001`, `model_vision:{vision_job}:sc001:f001`. The embedded `evidence` ledger preserves exact strings/locators, source job ID, and trust. Model descriptions remain `model_reported_unverified` and `needs_review`.
- Unknown duration, wrong hashes, cross-video source versions, incorrect/unordered intervals, fabricated scene/frame IDs, inflated OCR time spans, or missing evidence references **fail closed**. Missing speech is not equal to no-speech, missing OCR is not an empty on-screen text across the whole video.
- Coverage records total video duration, frame sample count, model described frame count, OCR samples with text, speech segment count, actual interval count, truncation warnings and `all_frames_analyzed:false`. Extremely long event streams cap at 18,000 timeline intervals with explicit `partial`/`timeline_truncated`, while outputs beyond 12MiB fail explicitly rather than silently dropping evidence.

## Video Intelligence JSON v1

Top-level fields: `schema_version`, `fusion_engine_version`, `processing_status`, `platform`, `platform_url`, `native_video_id`, `video_sha256`, project/source/version IDs, `transcript`, `visual_scenes`, `frame_samples`, `ocr_segments`, `visual_observations`, `timeline`, `evidence`, `coverage`, `creative_analysis`, `group_a_compatible_unpublished`, `group_b_candidates_unpublished`, `provenance`, `cost_summary`, `warnings`, `review`.

`creative_analysis` includes: Hook literal opening text (candidate); format (unclassified); narrative (unclassified); sampled key shots; editing observations; subtitle style (unclassified); CTA literal-regex candidates (unreviewed); brand/product appearances (model-reported, review required); and audience signals (unclassified). **No genre/category, audience or product attributes are invented.**

For the agreed [Video Features A](VIDEO_FEATURES_A_SCHEMA_V1.md), measured speech word count, WPM, first speech and coverage come from timed speech; the Stage08 sampled scene count and first sampled OCR text are labeled **sampled**, *not* promoted to true scene_count or first_text_sec. Presence of an SRT sidecar does not prove rendered subtitles. Full-scene counts, visible-subtitle style and music/SFX are null until measured. No Group A tables are written.

For the agreed [Content Features B](CONTENT_FEATURES_B_SCHEMA_V1.md), B1 Hook, B2 Format, B3 Narrative/Story, B4 Visual, B5 Editing, B6 Subtitle Style, B7 CTA and B8 Categories remain **candidates or null**. In particular, no AI-origin guess, no automatic B taxonomy assignment, and no inferred CTA destination. This subsystem does not write B tables.

## API and UI

- `POST /api/projects/{id}/sources/{source_id}/fusion/start` accepts `{}` or explicit input job IDs and the boolean include flags; returns `{job_id,state,created,video_source_version_id,included}`. Same-origin mutation requirements are inherited from `app.Handler`.
- `GET /api/projects/{id}/sources/{source_id}/fusion/jobs` lists runs and versions.
- `GET /api/projects/{id}/sources/{source_id}/fusion/jobs/{job_id}?offset=0&limit=50` returns a paginated timeline with matching evidence IDs and the rest of the intelligence fields.
- `GET /api/projects/{id}/sources/{source_id}/fusion/jobs/{job_id}/export` returns the complete pinned JSON snapshot.
- `POST /api/projects/{id}/sources/{source_id}/fusion/jobs/{job_id}/retry` with `{}` retries failed/unavailable local tasks, never paid upstream tasks.
- UI mounts under each concrete video link in the existing Project Reference Registry. Video Downloaders and intake wizard remain unchanged.

Run migrations via the established `python -m intelligence.migrate`, then in a separate terminal `python -m intelligence.project_sources.timeline_jobs --poll 2` (or `--once`). HTTP **only enqueues**, never performs heavy processing inline.

## QA and known limitations

All unit/HTTP/integration test fixture inputs are synthetic; never call TikTok, YouTube, Instagram, Whisper or Sonnet. PostgreSQL17 CI is authoritative for migrations/FKs. Test both Linux and Windows. Stage08 OCR uses a sparse fixed-frame sample; creative analysis remains candidate-only and full B3 story-role sampling (2fps first 5 seconds + 1fps later) is not provided by Stage08's 8-frame preparer. No invented video comprehension between frames.
