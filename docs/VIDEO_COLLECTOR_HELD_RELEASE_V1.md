# VC-MOD-06 — trusted QV manifest → Preview → Held Commit → Explicit Release

**Implementation status:** code implemented in branch `feature/video-collector-v1`. **QA status:** pending exact-HEAD Linux/Windows workflow and disposable PostgreSQL17 E2E. No production DTK search was triggered.

## Production entry points

- GET `/api/intelligence/collector/manifests?project_id=<32hex>`: project-scoped list of immutable published QV manifests, newest first. No network/provider operations.
- GET `/api/intelligence/collector/manifests/<manifest_uuid>?project_id=<32hex>`: project-scoped full coverage plus ordered shards and actual backend QuerySet IDs. Checks shard count, query count, hash, coverage totals and missing candidate links; rejects corrupt ledgers.
- POST `/api/intelligence/collector/manifest-preview`: exact JSON fields `project_id, manifest_id, query_set_id, source_id, limits`. Requires the QuerySet to be bound to the specified project and published manifest and uses **existing** `repository.trusted_preview` to revalidate approved taxonomy, project and source policy. No paid/network calls, `preview_only=true`.
- POST `/api/intelligence/collector/manifest-runs`: fields `project_id, manifest_id, query_set_id, source_id, limits, plan_hash, idempotency_key, confirm, max_spend_usd`. Requires actual DB shard membership and signed-in same-origin owner POST checks (existing host authorization). Passes only the verified request into **existing** `repository.commit_run` which locks the project and atomically writes `search_runs`, `collector_runs`, `source_requests`, `jobs`, `collector_task_queries`, audit ledger. Jobs are `planned_held` with `run_after=infinity`; no dispatch on reload.
- POST `/api/intelligence/collector/runs/<id>/release`: existing `execution.authorize_execution` is deliberately NOT automatically invoked from commit. Separate explicit operator confirmation, `idempotency_key`, budget cap `0.00`, exact bounded `max_provider_requests`, current source rights and policy checks, archive flag, provider read scope and quota reservation, atomically issuing a release grant. `CM_COLLECTOR_LIVE_ENABLED=0` keeps live worker disabled by default.
- `/api/intelligence/collector/runs/<id>/cancel` and GET run status remain available and project-scoped.

## Owner UI

The `site/index.html` Discovery screen has a distinct **Video Collector V1** section loaded from `site/collector.js`: explicit project ID entry; user-initiated manifest loading; per-manifest coverage/selected counts; shard selection; **read-only** preview; separate held-commit confirmation; separate release confirmation. Commit and release are always disabled until the preceding completed step. Fetches use the existing same-origin POST `X-ContentMachine: 1` protection. The UI generates random idempotency keys only at the relevant explicit click; no auto-search on script load, no periodic DTK submit, no paid API requests.

The pre-existing standalone `/preview` structural fixture endpoint remains **nonexecutable/offline** for legacy tests and must never be treated as production attestation. Production operator UI uses **only** manifest-specific endpoints above and does not accept hand-written QuerySet JSON. Production commit still requires a real `collector_query_sets` row already produced by QV Publisher (VC-MOD-04).

## Safety and provenance invariants

- Wrong project, manifest, QuerySet or an untrusted JSON blob fails closed before any commit.
- Source fingerprint and shard linkage are immutable. `trusted_preview` and `commit_run` verify current project, taxonomy, plan_hash and active source policy.
- `no_queries` manifests show coverage but have zero shards and cannot be previewed/committed.
- Verified query UUIDs, hypothesis IDs and direction IDs survive held-commit into `collector_task_queries`. No UUID→D1 BIGINT conversion.
- Existing `authorize_execution` accepts only the held run, no provider POST; a separate Worker in later VC-MOD-07 performs dispatch only with provider quota and release gates.
- One release applies to **one** shard-specific held run. Multi-shard manifest browsing/commit is explicit; no hidden launch of all 480 requests. VC-MOD-12 may add batch controls after safe budget/release model has been tested.

## QA

- `tests/test_video_collector_manifest_access_v1.py`: project-scoped read, missing/corrupt manifest, no_queries, rejected unbound shard, browser JSON rejection, held commit no dispatch, owner/consent/budget, HTTP routing and validation.
- `tests/test_video_collector_worker_stage8.py` has existing release idempotency/quota/rights/cancel tests.
- `tests/test_video_collector_stage2.py` has existing held-commit transaction and stale plan/idempotency tests.
- `tests/test_video_collector_manifest_planner_v1.py` checks all selected QV shards are accounted for.
- UI static/test `tests/test_video_collector_operator_ui_v1.py`, syntax `node --check site/collector.js`; no network tests.
- Final production-like D1→SRE E2E and actual PostgreSQL17 race testing are later VC-MOD-15 release gates; do not claim proven live TikTok execution.

**Next:** VC-MOD-07, robust DTK worker with durable submit intent/fencing and explicit released task eligibility.
