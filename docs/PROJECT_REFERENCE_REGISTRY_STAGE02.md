# Project Reference Registry — Stage 02 (2026-10-24)

Base: `main@8d9bee17`; development builds upon Stage 01 PR #29.

## Scope

Each link is represented by a project-scoped `project_sources` row, an immutable metadata-only `project_source_versions` v1 registration, and optional immutable `project_source_origins` rows for each legacy field. `original_input` and questionnaire text remain unchanged. `identity_key` deduplicates canonical URLs and native video IDs *within one project only*; a video is never confused with a creator profile. Partial legacy failures return warnings and do not erase other sources.

### Explicit non-goals

No download, site crawl, redirect resolution, DNS fetch, web AI, ASR, copyright/license gate, or paid provider request. Source registration status is `registered` not `completed` or `extracted`. No rights check is performed by the Stage 02 service. Stage 01's legacy policy columns remain present in migration 030 for safe deployment continuity but are not consulted; separately migrate obsolete columns later without editing previously applied migration files.

## URL normalization

- Only canonical public-domain HTTPS links in storage; HTTP input is normalized to HTTPS **without fetching**. URL parser refuses userinfo, raw IP/loopback, unusual ports, invalid or local domains.
- Trackable `utm_*`, `fbclid`, etc. are stripped; meaningful query parameters are retained.
- TikTok `@user/video/ID`, YouTube Shorts/watch/youtu.be and Instagram Reel/post codes produce canonical platform/video identity; `/@account` and `/username/` remain account identities.
- TikTok short URLs are preserved and tagged `shortlink` with no guessed `native_id`. Network resolution belongs in future video processing; no surprise download.
- `identity_key` uniqueness is project scoped. Different projects may intentionally register the same URL.
- Domain spelling normalization is not evidence of page content or availability. Domain DNS/redirect restrictions and final URL verification remain Stage 05 responsibilities.

## API (all localhost-only, JSON, same origin and X-ContentMachine mutation header)

- `GET /api/projects/{project_id}/sources?include_archived=0|1` -> `{sources,count,processing_started:false}`
- `POST /api/projects/{project_id}/sources` with `{url,source_type,label?,user_notes?}` -> `201` new source
- `PATCH /api/projects/{project_id}/sources/{source_id}` with `{label?,user_notes?,archived?,expected_updated_at}` -> update guarded by optimistic timestamp check. Archived records can be restored via `archived:false` if unique identity is free.
- `DELETE /api/projects/{project_id}/sources/{source_id}` with `{expected_updated_at}` -> tombstone, not physical deletion. Previous versions stay intact.
- `POST /api/projects/{project_id}/sources/import-legacy` with `{dry_run?:true}` -> deterministic import from `product_url`, `competitors`, `competitor_socials`, `promoter_accounts`, `video_references`. Unsupported or malformed lines are reported, not silently passed to the model. Repeated import is idempotent by `project_source_origins.import_key`.

`404`: unknown project/source; `409`: duplicates/stale edits; `422`: invalid input/type; `503`: registry database disabled/unavailable. Legacy nine-step wizard and current RTB confirmation behavior are unchanged. Source mutation does not update profile_revision or historical RTB snapshot.

### Web model selected for a later stage

For future Stage 05+ explicit web analysis: OpenRouter model ID `anthropic/claude-sonnet-4.6`, OpenRouter server tool `openrouter:web_fetch` (`engine:openrouter`) to retrieve page content on behalf of the model. Stage 02 registers `ingestion_mode=web_ai` only; it does not issue any API request. Prompted web-fetch cannot guarantee content for blocked/JS-only/login pages and cannot be treated as automatically verified citation without captured evidence. Model/tool calls require budget approval. Documentation: https://openrouter.ai/tool/web-fetch/.

## QA

Offline: `python -m unittest tests.test_project_reference_normalize -v` and JS syntax check `node --check site/project-sources.js`. PostgreSQL integration requires `CM_TEST_PG_DSN`, migrations 030+031, and 17.x; test `python -m unittest tests.test_project_reference_registry_db -v`. HTTP mocks: `python -m unittest tests.test_project_reference_http -v`. CI executes full repository regressions and Windows D1 smoke tests. Never run live external fetch/LLM during stage02 QA.
