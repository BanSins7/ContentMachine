# Video Collector Stage 9 — DTK Response Inspector + Normalizer

**Branch:** `feature/video-collector-v1`. **Upstream pinned contract:** Evil0ctal DTK `v5.1.7`, `src/dtk/models/content.py`, `Page[Content]` and `GET /api/v1/tasks/{task_id}`. The upstream implementation returns normalized `Content` with `platform,content_id,kind,web_url,title,description,created_at,duration_ms,is_deleted,is_private,author,stats,media`. Page is `{items,cursor,has_more}`. An already unwrapped `intelligence.connectors.dtk.task(id)` result carries that Page in `answer["data"]`.

## Components

- `intelligence/collector/provider_inspector.py`: strict offline **`inspect_dtk_search_result(answer,source_task,provider_task_id,fetched_at=None,allow_metrics=False)`**. Returns immutable `InspectedPage` with one typed `ProviderPage` and `CandidateEnvelope` per accepted item.
- `intelligence/collector/contracts.py`: extended CandidateEnvelope with quality, quality reasons, author handle/stable ID, duration and nullable metrics. Each field is type-checked; canonical identity uses existing `intelligence.discovery.normalize.canonical_video_url`.
- `intelligence/collector/worker.py`: after a successful DTK task GET (`state=done`), runs inspector **in memory**. Stores **only aggregate QA report** in `collector_provider_tasks.response_summary` (accepted/partial/rejected/overflow and reason codes). Invalid provider pages fail the Collector task with `provider_inspection_failed`, not false success.
- `tests/test_video_collector_inspector_stage9.py`: real DTK v5 normalized fixture, invalid URL/ID/type/date/duration/stats, no network, safe pagination, no raw leakage, worker flow.

## Validation policy

- Only `state=done`, **matching UUID task id**, valid `Page[Content]` with 0–100 items. Upstream `result_meta` is optional; malformed page shape is rejected as a whole.
- Per-record `platform=tiktok`, 10–25 digit `content_id` **string**, canonical `https://www.tiktok.com/@handle/video/id` (or a validated handle+id fallback). Reject TikTok photo URLs, wrong platform, mismatched IDs, short links, spoofed host, private/deleted items and `kind=image_album/live`.
- Missing kind is `content_kind=unknown` and **partial**, never assumed to be a video. Missing title/date/duration/author handle are `null` plus explicit quality reasons. Normalized `created_at` must be ISO 8601 with offset (not guessed epoch milliseconds). Durations are `duration_ms/1000` only for finite, bounded integers.
- At most `SourceTask.max_results_per_page` items are inspected; overflow counted and flagged `local_result_cap`. Duplicate video IDs within the same page are rejected as `duplicate_page_video`. No silent truncation or simulated provider pagination.
- `views/likes/comments/shares/saves` map exclusively from normalized DTK `stats.play_count/digg_count/comment_count/share_count/collect_count`. Missing values remain `null`; `0` means observed zero. **Default allow_metrics=False** means no metrics are transferred into candidates. Even when explicitly enabled for an in-memory trusted caller, metrics are **never persisted** at this stage.
- `has_more/cursor` checked; unknown/absent cursor never invented. Stage 11 will control pagination and extra quota. Each output retains project, taxonomy publication/version, generation, task ID, query and hypothesis lineage.
- **Always drop** upstream `raw`, `media` / CDN signed links, private profiles, nicknames, thumbnails, music, tags and unnecessary author fields. No raw response JSON is stored or logged.
- Secrets and per-video details never occur in `InspectedPage.summary()` (only bounded counts and reason codes). In-memory DTOs are **not** an ingestion approval or a qualified relevance decision.

## Execution boundary and next step

The Stage 8 Worker still requires completed server-verified SQG Query Validation, reviewed source policy, explicit operator rights and budget attestation, an enabled local DTK, and a second release consent. The Inspector does **not** start the provider itself and does not alter these gates.

**Stage 10 remains unimplemented:** there is no video DB upsert, `discoveries` occurrence, author persistence, metric snapshot, SRE or qualified video claim. The normalized DTO lives in memory at the time the worker polls a completed task; Stage 10 must add **atomically gated ingest before acknowledging the page** or safely re-read a known DTK `task_id` within the provider task-result retention. This Stage 9 does not silently store raw pages for later replay.

## Local QA

```bash
python -m unittest discover -s tests -p 'test_video_collector_inspector_stage9.py' -v
python -m unittest discover -s tests -v
python -m compileall -q intelligence/collector/ intelligence/connectors/
python -m intelligence.migrate
```

Full GitHub Actions covers Linux+Windows, PostgreSQL 17 migrations, all existing regressions and the new synthetic fixtures. A *real TikTok E2E* cannot be claimed until an appropriately configured, authorized local DTK is separately verified.
