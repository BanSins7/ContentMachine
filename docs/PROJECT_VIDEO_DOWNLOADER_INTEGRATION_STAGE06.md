# Stage 06 — Video Downloader Integration

## Scope
Connect **concrete** user-registered TikTok/YouTube/Instagram video sources to existing manual verified downloaders. No new yt-dlp, cookie or Whisper implementation, no automatic downloads on onboarding, no D1-video IDs mixed with project source IDs, no rights/copyright gate. Stage 07 transcription normalization will follow separately.

## Pipeline

`Project Reference Registry → POST /video/start → PostgreSQL source_extraction_jobs(video_download) + project_video_acquisition_runs → explicit local video_jobs worker → platform adapter → existing verified worker in isolated process → inspect metadata/ID/SHA/MP4 → versioned project_source_versions + project_media_assets + source_processing_outputs(other) → UI`.

**Adapters:** `VideoAcquisitionAdapter.validate_url / resolve_identity / request_download / get_job_status / collect_outputs / verify_asset / cancel`. TikTok uses `worker/tiktok_worker.download_job`, YouTube uses `worker/youtube_worker.download_job`, Instagram uses `worker/local_worker.exact_download_job`. They do platform-specific verification themselves. No browser-cookie reuse; engine=`local` avoids paid remote ASR. Existing workers **also perform local speech-to-text** if needed; Stage 06 only *registers* outputs and does not normalize transcripts yet.

**Cache:** Existing platform archive is checked before downloading. A cache hit requires matching native video ID, source link, actual MP4 size/magic and SHA-256 matching the existing downloader JSON metadata. A given project source can reference existing verified bytes without changing D1 provenance; every project obtains its own `project_source_versions`/`project_media_assets` identities. Historical snapshots are append-only; same SHA for the same source reuses the prior output/version rather than creating a duplicate. Sidecars are optional (e.g. silent or ASR failure) and visibly recorded as absent, never invented.

**Safety:** Only exact video URLs are accepted; unknown/unresolved shortlinks, accounts, unsupported platforms, archived sources and mismatched native IDs are not scheduled. Existing downloader authentication/access failures become `unavailable`, not `completed`. MP4 and sidecars are read from trusted platform archives; paths are verified not to escape root. No browser credentials in manifest. Abort/timeout cannot create a project-level "completed" without file and metadata checks.

**Durability:** Global `source_extraction_jobs` with unique active source guard, 3 bounded attempts, leases refreshed through heartbeats, PostgreSQL session advisory lock per platform native ID (avoid two project tasks writing same legacy filename), and attempt fencing before results commit. Worker is *not* started by HTTP. An interrupted processing lease can be reclaimed; repeated requests are idempotent by default, explicit `refresh:true` can add a new job after a completed one, and failed jobs require manual `/retry`. Cancel may terminate the isolated process; unfinished legacy `.cm_tiktok` etc temporary files can remain after forced termination and require a later cleanup pass. External archive content may be overwritten by legacy manual downloaders; the manifest preserves the SHA and must be reverified before downstream use.

**API** (localhost CSRF/origin guards enforced by `app.Handler`):
- `POST /api/projects/{pid}/sources/{sid}/video/start` `{}` or `{ "refresh": true }`.
- `GET /api/projects/{pid}/sources/{sid}/video/jobs`.
- `GET /api/projects/{pid}/sources/{sid}/video/jobs/{jid}`.
- `POST /api/projects/{pid}/sources/{sid}/video/jobs/{jid}/cancel` `{}`.
- `POST /api/projects/{pid}/sources/{sid}/video/jobs/{jid}/retry` `{}`.

Worker: `python -m intelligence.project_sources.video_jobs --poll 3` or `--once`. Existing Downloaders page and 9-step project intake continue unchanged. **No real platform downloads in automated tests.**

## Known constraints
- Existing platform downloaders depend on yt-dlp/FFmpeg, local Whisper models and public platform access; fail honestly for challenges, removed/private posts or unverified IDs.
- TikTok shortlinks without exact platform video ID cannot be enqueued. Browser-only Instagram capture is not attempted silently; exact-download path may fail on restrictions.
- Worker can be cancelled during a live download, but stage-06 subprocess termination cannot guarantee cleanup of all FFmpeg grandchildren; project state will not falsely claim a completed asset.
- Same asset path can become stale if an external tool deletes/mutates archives; versioned SHA anchors provenance, subsequent stages must verify it before use.
