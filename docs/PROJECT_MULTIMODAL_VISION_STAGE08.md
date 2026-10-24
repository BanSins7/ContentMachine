# Content Machine — PSI Stage 08 / Multimodal Vision Engine

Status: implemented in `feature/project-source-intelligence-v1`, **not merged**. Scope: document plan §08. Independent of Unified Transcription stage 07 and future timeline fusion stage 09.

## What it does

1. User explicitly selects a project video with a completed, verified Stage 06 MP4 acquisition.
2. `POST /api/projects/{pid}/sources/{sid}/vision/prepare` creates a **free, local** `source_extraction_jobs(stage='vision')` task. Worker verifies `source_version_id`, native platform video ID, canonical URL, SHA-256 and byte size using the Stage 06 adapter; it does not use the Stage 07 ASR output.
3. In an isolated child process the worker uses ffprobe, or FFmpeg metadata fallback on Windows, to obtain duration, codec, frame count where available, width/height, and presence of audio. It runs a bounded first-120s low-fps scene-cut detector and takes up to eight FFmpeg JPEG samples (early seconds + evenly-spaced timeline + cut-aware choices). Long videos are explicitly flagged as only partially scanned. Invalid frames are recorded as failures, not substituted.
4. Frame JPEG bytes (≤180KB each) are saved atomically in the project's `vision_assets/{prep_job_uuid}/fNNN_{sha256}.jpg` directory; the resulting `visual-preparation/v1` JSON records exact frame timestamps, frame-id/hash/path, scene boundaries, codec/duration/audio presence, OCR text/status and bounded OCR **single-frame-only** interval. Optional local Tesseract defaults to English; if Tesseract isn't installed, the status is `unavailable` rather than fabricated. No ASR, no model requests.
5. Only after local prepare reports `completed` or `partial`, `POST .../vision/quote` produces a **$0.15 planning estimate**, expires after 20 minutes and is pinned to the exact source version and prepare job. The estimate is **not** a guaranteed charge cap.
6. `POST .../vision/analyze` requires that exact quote ID and matching `approved_estimate_usd`, then queues a paid `mode=analyze` task. The server does **not** send any data from the HTTP handler. The worker re-checks the video and **every sampled frame** against stored SHA-256 before building a single OpenRouter `POST /api/v1/chat/completions` request with `anthropic/claude-sonnet-4.6`, bounded `image_url` base64 JPEG blocks and JSON-only result request. It reuses the API key from Content Machine's existing OpenRouter Settings (`worker/openrouter_key.txt`). No web browsing tool is used for this vision call.
7. Strict response validator requires real scene IDs, exact intervals and actual sampled frame IDs; each scene includes `visual_description`, `observed_visible`, actions, objects, people observations **without identity claims**, product/logo visibility, camera/editing observations, optional separate `inferred_interpretation`, and uncertainty. Results are `visual-observations/v1`, `trust_level=model_reported_unverified`, and are **not** promoted to verified facts automatically. Source output JSON is pinned to the video source version and immutable in its linkage.
8. Provider usage and cost (if returned) are recorded. Missing cost is `charge_state=unknown`; an ambiguous paid failure is `needs_review/unknown`. Paid requests have `max_attempts=1`; expired processing leases are changed to `needs_review` rather than automatically re-sent. Retry endpoint is **local preparation only**.

## Durability and boundary of evidence

Video remains in existing verified platform archives. `project_vision_runs` and `project_vision_quotes` live in migration `037_project_multimodal_vision.sql`; local and model outputs are separately checkpointed as `source_processing_outputs(output_type='visual_scenes')` referencing exact source versions. Old migrations `030`–`036` and old outputs are untouched. The JSON frame sampling metadata intentionally says `all_frames_analyzed=false`. OCR does **not** mean that screen text was visible outside the exact sampled frame instant. Scene-cut boundaries are heuristic and only scanned for the first 120 seconds; coverage is not continuous even for short videos.

Important limits: at most 8 JPEG frames; 180 KiB per image; 2 MB JSON request; 2,200 model output tokens; 1 provider request per confirmed quote; child preparation wall-clock ≤240 seconds. `OCR` and FFmpeg locally are free aside from computer resources. Tesseract is optional. The FFprobe binary should be installed for best Windows/FFmpeg support; fallback works from imageio-ffmpeg's FFmpeg. No platform downloads, no auto paid calls and no copyright/licensing backend gate. Infrastructure scoping and provider cost consent remain.

## API

- `POST /api/projects/{project_id}/sources/{source_id}/vision/prepare` — `{"config":{"max_frames":8,"early_focus":true,"ocr":true}}`; optionally `acquisition_job_id` to pin a historical acquisition.
- `GET .../vision/jobs` — list for a source.
- `GET .../vision/jobs/{job_id}?offset=0&limit=20` — paginated scene/frames and status. Actual image bytes are not exposed via this API.
- `POST .../vision/quote` — `{"prepare_job_id":"uuid"}`.
- `POST .../vision/analyze` — `{"quote_id":"uuid","approved_estimate_usd":"0.15"}`. Clear consent; cost may exceed estimate and provider returns final spend when available.
- `POST .../vision/jobs/{job_id}/retry` — `{}`, only for failed local prep.

All routes are handled after the application's existing same-origin checks. No inline FFmpeg execution or paid HTTP calls. The project registry UI adds Prepare, Quote/Confirm, History, Results, and Retry buttons only on individual video sources.

## Worker

```powershell
python -m intelligence.migrate
python -m intelligence.project_sources.vision_jobs --poll 3
# or one job:
python -m intelligence.project_sources.vision_jobs --once
```

Run separately from the stage 06 downloader worker and stage 07 transcript worker. For CI all provider calls are mocked; a real paid image call requires the actual OpenRouter API key, a verified MP4 and explicit confirmation. No live paid model or platform-download operation is part of automated acceptance.

## QA and next steps

- Local FFmpeg-generated scene-changing short MP4 without audio: ffprobe, 2-second cut detection, JPEG and sparse frame times.
- Unit: invalid config, long video bounded coverage, broken frames, optional OCR unavailable, strict JSON fabricated scene/frame/time rejection, mock single OpenRouter request.
- HTTP: route scoping, invalid query/body and no inline paid work.
- PostgreSQL17 integration: Stage 06 pin, local checkpoint, prepare idempotency, quote consent, one paid call, unknown provider outcome, missing API key and unchanged local OCR after paid failure, project isolation.
- Windows regression: full test suite and D1 smoke.

Stage 09 will fuse separate `transcript/v1`, `visual-preparation/v1` and `visual-observations/v1` by verified time/locator. It is not part of stage 08.
