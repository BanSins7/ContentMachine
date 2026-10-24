# Video Collector Stage 8 — PostgreSQL Worker + локальный Evil0ctal DTK

## Status

Implemented: explicit **second authorization** for execution, quota reservation,
a dedicated PostgreSQL worker for exact Collector jobs, durable submit intent,
local DTK task ID, safe polling, cancellation, uncertain outcomes and restart
recovery. No automatic startup, no import to D1 and no scheduled pagination.

## Hard gates

A Collector run must have a `collector_query_sets` record sealed by the future
**trusted Query Validation publisher**. A user-supplied query-set JSON remains
**preview-only**. `sqg_generations.status` must be `completed`, approved
taxonomy publication/hash/ancestry must match. With no SQG publisher yet, normal
users cannot legitimately release a live run; never fake an approved query set.

After the held commit (Stage 6), execution requires an **additional**
`POST /api/intelligence/collector/runs/:id/release`. Body:

```json
{
  "project_id":"<32 lowercase hex>",
  "run_id":123,
  "confirm":true,
  "acknowledge_archive_configuration":true,
  "max_provider_requests":1,
  "max_spend_usd":"0.00",
  "idempotency_key":"collector-live-consent-001"
}
```

Before release, check the distinct **local server-side** settings (not supplied
by browser and gitignored):

```dotenv
CM_DTK_ENABLED=1
CM_DTK_BASE_URL=http://127.0.0.1:8000
CM_DTK_API_KEY=YOUR_LOCAL_SCOPED_KEY
CM_COLLECTOR_LIVE_ENABLED=1
CM_DTK_ARCHIVE_DISABLED_CONFIRMED=1
```

These flags are human/operator **attestations**, not a technical inspection of
actual DTK `archive.enabled`, provider license, TikTok identity or cost.
First independently verify provider permissions, operator legal authority,
actual upstream archive/cache settings and no provider usage charge.
`DTK.status()` then performs an explicit local auth/me request to verify
read scope; it never performs TikTok search. One active DB source policy must
grant `can_search`, `can_store_metadata`, `capabilities.keyword_search`
and additionally explicitly reviewed `permissions`:

```json
{
  "can_search":true,
  "can_store_metadata":true,
  "automated_collection_authorized":true,
  "provider_usage_no_charge_verified":true,
  "upstream_archive_disabled_confirmed":true
}
```

The source must have approved retention and available `collector.requests`
quota. Release pins source policy version, reserves the exact request count,
stores an immutable snapshot of consent without any credentials, and moves
Collector run from `planned_held` to `released` in one transaction.

## Dedicated worker

```bash
python -m intelligence.migrate
python -m intelligence.collector.worker --once
python -m intelligence.collector.worker --interval 4
```

These commands do nothing when the live enable/attestation flags are off.
The worker **must be explicitly launched** as a separate process; starting
ContentMachine or reading Preview does not start any provider work.
Use Ctrl+C/SIGTERM to stop its loop.

It selects only `collector.search.held` jobs tied to an explicitly released
Collector run; `run_after='infinity'` remains set. The general-purpose
`intelligence.jobs.lease_next` cannot claim these jobs.
- A single worker claim commits `submit_intent`, a fenced lease generation,
  `source_requests.started` and one reserved→consumed quota unit **BEFORE**
  calling DTK `search_posts`.
- DTK returns a UUID `task_id` which is stored in `collector_provider_tasks`.
  Once present, the worker only calls `dtk.task(task_id)` (GET).
- `queued/running`: keep polling at a bounded interval (min 5 seconds).
- `done`: store a tiny status summary only, **not** raw personal TikTok JSON,
  engagement metrics, fetched MP4, photos or third-party data. Marked
  `inspection_pending=true`, not falsely counted as a qualified video.
- `failed/cancelled`: mark provider task failed.
- A lost/timeout submit after intent becomes `unknown`. **Never repeat**
  a submit with unknown side effects. Manually reconcile against DTK logs.
- Crash after a *known stored* task ID: expired lease is returned for GET polling.
  Lease fencing stops stale workers from persisting a late completion.
- Worker silently refuses a revoked/expired source policy and disabled config;
  no automatic downgrade to a different provider or payment method.
- Quota reservations are booked per run on release, consumed on intent and
  released for never-submitted tasks upon cancellation.

`POST /api/intelligence/collector/runs/:id/cancel` now stops all future
submissions and frees their quota. A task already in `submit_intent` or
`submitted` may still exist in upstream DTK: cancellation **cannot undo an
external call**. Worker may finish read-only polling that known task ID and
report final status accurately. `GET .../runs/:id?project_id=...`
shows consumed intents, provider task IDs, unknown outcomes and live state.

## Safety and scope

No credentials in API responses, no secrets in audit, no GET initiating
submissions. All release/stop POST routes retain app.py's existing Host,
Origin and `X-ContentMachine:1` checks.
This stage uses **one approved keyword/page/task**. Paginated searches,
response parsing, media downloading, metric persistence, ingestion, SRE and
qualified results are later stages.
No actual external TikTok search can be established by GitHub CI alone.

## QA

```bash
python -m unittest discover -s tests -p 'test_video_collector_worker_stage8.py' -v
python -m unittest discover -s tests -v
python -m intelligence.migrate
```

Tests use fake DTK responses; they check zero default consent,
strict shape/rights, no auto-submit, durable task IDs, poll-only retries,
unknown failures, aggregate run states and API scope. PostgreSQL 17 migrations
and full regression are checked by GitHub CI. A real DTK E2E requires a
licensed/configured local DTK server and published queries from Search Planner.
