# VC-MOD-07 — DTK Worker resilience / submit fencing / safe GET recovery

**Branch:** feature/video-collector-v1 · **Status:** IMPLEMENTED (code), QA PENDING (latest CI & PG17 race). Nothing was executed against live TikTok/DTK. Main remains untouched.

## Architecture and safety properties

Only the existing `intelligence.connectors.dtk` v5 transport can submit or GET DTK tasks. The Provider Registry maps **keyword** to `search_posts`; unsupported hashtag is rejected and never silently translated into a keyword. Worker code in `intelligence/collector/worker.py` is opt-in, gated by CM_COLLECTOR_LIVE_ENABLED and local archive attestation, and requires a previously held/explicitly released run, project-scoped trusted QuerySet, execution grant and finite provider quota. The ordinary jobs worker must NEVER claim `collector.search.held` jobs, which keep `run_after='infinity'`.

The workflow is:

1. Exactly one dedicated Collector worker obtains a PG advisory transaction lock; picks eligible released tasks with `SKIP LOCKED`. SQL lock order is **search_runs → collector_runs → task/job/grant** (same as cancellation and post-provider transitions); new claim still refuses cancelled search runs.
2. In the claim transaction, validate immutable task fingerprint, one-page max `search_posts` payload, active provider policy and rights, quota ledger window and grant; bump `jobs.lease_generation`, record `collector_provider_tasks.state='submit_intent'`, consume one quota reservation. **Both quota tables must have actually been updated**, or the claim transaction rolls back.
3. Immediately before a first provider POST, `_preflight_submit` reacquires run locks and checks `submit_intent`, `c.stage='released'`, uncancelled run, active reviewed policy and three operator-granted automated usage assertions, exact job worker/generation/lease. This is a **second gate**, not a promise HTTP can be atomic with PG.
4. One `api.submit('search_posts',params)` call follows only if all checks pass. Its return must contain a valid UUID DTK `task_id`, and a bounded, printable optional `provider_request_id`. Valid task ID and `submitted` state are stored in one fenced transaction; no blind retry if the POST fails, times out or returns malformed JSON. An uncertain attempt becomes `unknown` / manual reconciliation; losing the lease must not allow the former worker to overwrite new state.
5. On a task already in `submitted` with a **durably stored provider_task_id**, Worker uses `api.task(task_id)` (GET) ONLY. A response with another task ID, invalid state or a GET outage is deferred and rechecked via GET. No new POST; successful polling and terminal outcomes are lease-fenced.
6. `recover_expired` scans a bounded group (≤100) of expired running claims; this scan is **not sufficient to mutate**. Each candidate is rechecked inside a new transaction with run-first locks and the exact job lease generation. A now-current lease is left alone. An expired `submit_intent` becomes `unknown`; an expired `submitted` with valid stored task ID is safely requeued for GET only; `submitted` missing an ID becomes `unknown` for operator investigation. Never resubmit a potentially already sent task.
7. Held/late-cancelled tasks do not become executable. `_defer_poll` now uses the same run-first lock order and returns a lost-lease result if the fence is no longer valid. Provider `cancelled` is recorded as provider_task_cancelled rather than a generic failure.
8. No page-2 cursor inferred. A page 1 result is normalised by the existing Response Inspector; **do not mistake provider done for canonical D1 ingest**. VC-MOD-09/10 must change that handoff to require an atomic D1 checkpoint before terminal Collector success. Current provider-only `completed` remains an explicit outstanding cross-stage gap.

## Operational guidance

- Deployment flags default disabled. No running daemon or provider call merely because code is imported.
- Start by running read-only QV manifest preview; create held runs via VC-MOD-06; explicitly release each run with an operator idempotency key and zero-spend claim only after current source policy/limits/configuration checks.
- A manual `python -m intelligence.collector.worker --once` only makes external requests if the server flags and durable release are both present; use **only in an explicitly approved test environment**, not ordinary CI.
- On unknown submit outcome, **STOP automatic POST retry**. Review durable run/task/lease and DTK provider audit/task ID if available; resume only GET for independently verified task IDs. Never invent a provider task ID.
- On crashed polling with known task ID, restart the same worker; recovery requeues only existing GETs and uses generation fencing. Review `last_error_code`, source quota, lease owner and unknown outcomes.
- DTK search network and service configuration, actual rights/pricing, reliable provider paging and end-to-end D1/SRE ingest remain outside offline CI evidence.

## Tests and verification

- `tests/test_video_collector_worker_resilience_v1.py` — new 11 offline tests on malformed provider responses, wrong task IDs, policy/release race, fencing, GET-only retries, quota update races, dead poller and invalid stored task IDs.
- `tests/test_video_collector_worker_stage8.py` — existing submit-once, unknown timeout, successful GET polling, restart recovery (updated to test per-row generation fencing), transactional release quota and cancellation.
- Commands: `python -m unittest discover -s tests -p 'test_video_collector_worker_resilience_v1.py' -v`, then Stage8 tests, full `python -m unittest discover -s tests -v`, `python -m compileall -q intelligence/collector`.
- CI pending on exact HEAD: ContentMachine Linux (PostgreSQL17 service), Windows D1 smoke and KE fast QA. There is no successful PG17 concurrency, live network or D1-ingest E2E proof yet.
- No source/schema migration changes in this stage; all existing 001..057 checksums must remain intact.

**Next:** VC-MOD-08 — lock the exact DTK Page[Content] inspector and normalizer regression tests. VC-MOD-09/10 are the real atomic canonical D1 persist-and-complete stages.
