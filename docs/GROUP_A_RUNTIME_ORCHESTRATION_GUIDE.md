# GA-MOD-20 — Durable Local Runtime, DAG, Leases, Revocation & Outbox

**Branch:** `feature/group-a-full-module`. **Implementation:** committed; **actual PG17/Windows/Linux/multi-worker execution:** NOT VERIFIED. No `main` or other-module modification, no automatic model/provider billing, HTTP registration or worker activation. Stage GA21 owns opt-in HTTP/UI; GA23 owns real end-to-end runtime qualification.

## Components & forward-only storage

- `intelligence/migrations/057_group_a_runtime_orchestration.sql` adds tables exclusively in Group A's namespace: `ga_step_dependencies`, `ga_step_resource_specs`, `ga_step_attempts`, `ga_resource_claims`, `ga_run_control`, `ga_outbox_attempts`, `ga_outbox_ack_receipts`, `ga_invalidation_audit`, plus `ga20_locally_current_results` local-authority-only view. GA 054/055/056, B 005/055/056, QDP/Analytics and legacy 017 are **not changed**. Existing GA04 `ga_runs`/`ga_steps.initial:v1` and GA19 `ga_results`/`ga_outbox` are the source of truth.
- `cost_resources.py`: deterministic versioned stage DAG for `video`, `slideshow_video`, `image_post`, `photo_carousel`, `mixed_carousel`. Stage roots preserve GA04's original `initial:v1` identity. Video DAG sequences technical → frame/audio branches → OCR/ASR/subtitle/audio-events → assembly; each dependency must have genuine `completed` or source-declared `partial` output before downstream stage runs. Partial remains partial: no text/ASR/audio full-coverage is invented by scheduling. Mixed video stages operate on **all verified video slide assets** under the ordered GA05 manifest. No Group B semantic block or payment is a GA20 stage.
- `task_queue.py`: `install_run_dag` verifies GA04 run+manifest+QDP source lineage and immutable stage spec, and creates only missing jobs before execution. PostgreSQL `FOR UPDATE ... SKIP LOCKED` plus global `pg_advisory_xact_lock` jointly prevent duplicate claim and enforce **logical** CPU/GPU/concurrency slot budgets; logical slots are not OS cgroups/physical device isolation. Attempt number increments monotonically (max 20 per SQL, GA04 default 3), unique UUID token + worker ID + exact deadline and lease timestamps fence all heartbeats and final writes. A worker whose lease expires, run is cancelled, or token was replaced cannot commit; only a source-authorized, separately verified SHA output is accepted.
- Crash/restart: `recover_worker_leases` marks expired attempts `lost`, releases any resource claim and moves the unfinished step to `failed`. Safe **local** retry uses monotonic remaining attempts and bounded exponential 2–256 second backoff. Permanent/nonretryable failure blocks the run and cancels/releases other active work; no spinning retries or resurrecting terminal GA outputs. `pause_run` blocks new claims, `cancel_run` cancels the run and outstanding leases, `invalidate_run` permanently withdraws run visibility and in-flight permissions. Completed step history is not rewritten.
- `reuse_verified_cache` optionally marks a fresh, never-attempted GA20 step completed using an **existing same-run GA054 cache_ref**, exact stage input SHA, GA05 pinned `media_asset_id`/asset SHA and an independently authenticated `verify_cache` callback for current on-disk bytes/provenance. It cannot automatically cross project/run boundaries, re-use expired caches or claim an arbitrary model-generated hash is sufficient.
- `worker.py`: new opt-in `LocalGAOrchestrator(connect,executors,verify_current,verify_output,budget,...)` with explicit registered **local-only** executor functions `executor(claim,should_stop)`. Heavy processing occurs **after PostgreSQL transaction release**, with separate DB heartbeat and cooperative cancellation checks. `run_bounded_local_command(argv,timeout_sec)` executes with `shell=False`, no stdout/stderr capture, deadline, Unix process-group or Windows taskkill tree termination. Python callbacks that ignore stop requests are not forcibly killed; **the trusted host must use bounded subprocesses for all potentially blocking media/model workloads**. OS-level memory/GPU hard isolation, live model scheduling and app startup are not claimed.
- `invalidation.py`: explicit `verify_authority` for QDP, source policy, media hash, reviewer supersession and project access revocation, atomically transitions GA054 run to `invalidated` (including formerly `completed`, as specifically allowed by 054), cancels uncommitted work and disables pending outbox dispatch. Old A003/A004/GA054 result rows are kept for audit, **not erased**. A local `ga20_locally_current_results` SQL view checks run completion, active policy, non-expired permission and SHA-pinned media, but **cannot authenticate current external QDP rights alone**. `read_current_result` requires exact `(ga_run_id,ga_result_id,result_sha256)`, run lock, fresh `verify_current` QDP+project+media authorization and `verify_result` origin/byte provenance; no `latest` lookup or stale-snapshot fallback.
- `delivery.py`: new GA19 `pending` outbox events can be claimed only while GA054 run and result remain completed and media/policy active. Each attempt has its own unique token and lease. **Before** external transport, the host must re-validate current QDP/run rights. Network transport is invoked **outside** transaction, using a stable consumer idempotency key `<ga_run_id>:<ga_result_id>:<result_sha256>`. A remote timeout after successful B execution can cause re-delivery; **Group B must deduplicate exact ID+SHA**. Only a separately authenticated `verify_receipt` callback and matching exact `ga_result_id/result_sha256/consumer_receipt_sha256` advance `ga_outbox` to `delivered` and insert an append-only receipt. Unverified ACK → failed/retry after backoff, never fake completion. A revoked/invalidated run is not dispatched. GA20 does **not** import, start or modify Group B.
- `GroupAService` exports opt-in wiring methods: `install_local_runtime_dag`, `make_local_runtime_worker`, `pause_local_runtime_run`, `cancel_local_runtime_run`, `invalidate_local_runtime_run`, `read_live_group_a_snapshot`, `deliver_group_b_event_once`. No always-on workers start by importing `group_a`; the preexisting legacy `worker.serve` remains unchanged and gated.

## Explicit integration example

```python
from intelligence.group_a import (
    GroupAService, ResourceBudget, LocalStepOutput,
)

service = GroupAService()
budget = ResourceBudget(
    cpu_slots=4, gpu_slots=0,
    max_concurrent_jobs=2, lease_seconds=60,
)

# GA04 has ALREADY committed the QDP inbox and returned its run_id.
# manifest was independently verified at GA05, not invented by GA20.
service.install_local_runtime_dag(
    conn, ga05_authoritative_manifest,
    run_id=ga04_durable_receipt["run_id"],
    verify_current=host_verify_live_qdp_and_source_media,
    budget=budget,
)

# Register trusted local stage functions, NOT arbitrary paid provider calls.
# Every function receives a step token and a cooperative abort check.
# The host must implement actual GA06–18 evidence processing; this example
# does not imply an installed executor registry.
executors = {
    "technical": trusted_local_ga06_executor,
    "frames": trusted_local_ga07_executor,
    "audio_decode": trusted_local_ga09_executor,
    "vad": trusted_local_vad_executor,
    "asr": trusted_local_asr_executor,
    "speech_metrics": trusted_local_ga11_executor,
    "ocr": trusted_local_ga12_executor,
    "text_tracking": trusted_local_ga13_executor,
    "subtitles": trusted_local_ga14_executor,
    "audio_events": trusted_local_ga15_executor,
    "assembly": trusted_local_ga18_executor,
}
worker = service.make_local_runtime_worker(
    connect=owned_psycopg_connection_factory,
    executors=executors,
    verify_current=host_verify_live_qdp_and_source_media,
    verify_output=host_authenticate_source_SHA_and_local_output_bytes,
    budget=budget, heartbeat_period_sec=10,
)
# One explicit synchronous unit of work. The host owns stop/start:
worker.once(worker_id="ga-worker-unique-01")

# After GA19 has committed a completed A snapshot/outbox:
# service.deliver_group_b_event_once(
#     conn, worker_id="ga-b-outbox-unique-01",
#     verify_current=host_verify_live_qdp_and_source_media,
#     transport=host_group_b_exact_id_and_sha_consumer,
#     verify_receipt=host_verify_genuine_B_commit_receipt,
# )
```

There is **no auto-paid retry**. Provider attempts/budget reservations and external transport are outside `ga_steps` and subject to their own receipts. Resource budgets are admission controls; external OS isolation remains a host responsibility.

## Trust, safety and limitations

- **No long media subprocess while DB row locks held.** A short live authorization callback runs at claim/commit and the host must bound its own latency. Every `verify_current`, `verify_authority`, `verify_output`, `verify_cache`, `verify_receipt` callback is mandatory where specified; unconditional `True` callbacks exist only in unit fixtures and must NEVER be installed in production.
- **DB claim fencing vs execution:** each step is at-most-once *committed* for a lease token, not exactly-once executed. A crash can cause the same local stage to run again, and retry must be idempotent and local. For B delivery, semantics are at-least-once until independently verified ACK, not exactly-once send. Group B's immutable `ga_result_id+sha256` pin is its deduplication key.
- **No privileged currentness bypass:** historical immutable facts can remain even after revocation, but `read_current_result` and `claim_outbox` recheck local QDP run, policy, hash/expiry and injected host attestations. The SQL-only view is never sufficient for project-level access permission.
- Source authentication/reviewer change origin comes from host callbacks, not from the old GA054 local snapshot or model text. A pending source change may occur between network send and ACK; B MUST independently revalidate source/QDP at point of use as it already expects.
- **Known integration gaps:** full project-worker registry and actual GA06–18 extractor orchestration registration, OS memory/cgroup/GPU hard quotas, automatic QDP/human-review event subscription, real Group B transport/ACK endpoint, live media SHA verifier and human-reviewed coverage are host integrations still needing GA21/GA23 end-to-end tests. Group A does not automatically enable itself on import.

## Tests and evidence status

```bash
python -m compileall -q intelligence/group_a tests/test_group_a_runtime.py tests/test_group_a_delivery.py
python -m unittest discover -s tests -p 'test_group_a_runtime.py' -v
python -m unittest discover -s tests -p 'test_group_a_delivery.py' -v
python -m unittest discover -s tests -p 'test_group_a_*.py' -v

# **Disposable PostgreSQL 17 test database only**, not production:
CM_TEST_PG_DSN=postgresql://postgres:password@localhost:5432/ga20_test \
 python -m unittest discover -s tests -p 'test_group_a_runtime.py' -v
CM_TEST_PG_DSN=postgresql://postgres:password@localhost:5432/ga20_test \
 python -m unittest discover -s tests -p 'test_group_a_delivery.py' -v
```

Committed test scenarios: exact DAG for all five content types; invalid dependencies/cycles, CPU/GPU request, backend version; two competing workers for the same GA04 job, no double claim/commit; parallel resource slot capacity and priority; worker heartbeats, expired lease restart/recovery, old token fenced after replacement; source revocation during running step, pause/resume and manual cancellation; failure/terminal poison and no runaway retry; extra exact cache proof; outbox lease expiration and recovery, no delivery before genuine ACK, transport timeout + exactly the same stable ID/SHA after retry, consumer receipt mismatch and a post-revocation snapshot read blocked while historical hash preserved.

**QA status:** GitHub connector-based source/SQL/test wiring audit **90/90 structural checks PASS**. This DOES NOT mean Python files were compiled or executed or that PostgreSQL 17 migrations and worker concurrency tests have passed. End-to-end live QDP/currentness, actual resource isolation, genuine B ACK and Windows/Linux/PG17 results remain **NOT VERIFIED**.

**Next:** GA-MOD-21 adds owner-authenticated project-scoped HTTP API/diagnostics/UI, then GA22 real accuracy/performance tests, GA23 actual CI/multiplatform/PG17/combined B integration, GA24 merge-readiness gates.
