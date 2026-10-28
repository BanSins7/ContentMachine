# Group B — GB-MOD-27 Instrumentation, Throughput & Recovery

**Branch:** `feature/group-b-full-module`. **Status:** standalone GB27 implementation + tests/CI wired; true Python/Node/PG17 green and production media/provider integration **NOT VERIFIED**. Main, other modules, and existing migration `005_content_features_b.sql` are untouched. No automatic paid model POST.

## 1. Durable telemetry, no private data

New additive `intelligence/migrations/059_group_b_observability.sql` creates `group_b_execution_metrics` with immutable AFTER-job ledger rows scoped to `(project_id,job_id,lease_generation)`. Each row holds block, queue kind (local vs paid), outcome, observed monotonic duration milliseconds (nullable for crashes), bounded token/image **measurements only if actually verified**, separate nullable billed USD, deterministic non-secret trace SHA-256, safe reason code and source output digest only. A DB trigger revalidates exact job identity/generation and terminal outcome. `UPDATE`/`DELETE` raise PG exception. Idempotent replay verifies the exact previous event, never counts an attempt twice; mismatched double recording is denied.

The normal local B5 worker completes only through `orchestrator.finish_job(...,elapsed_ms=measured_ms)` and `monitoring.record_execution` within the **same transaction**: either both the fenced terminal job and metric exist or neither commits. No model prompt/response, API key, media path, user PII, full provider error, uncertain invoice amount or screenshot is stored as telemetry. Stage27 intentionally has no synthetic paid success receipts; provider billing is sourced strictly from the existing GB10 `group_b_model_attempts` ledger and independently authenticated reconciliation.

`monitoring.performance_snapshot` aggregates by original `project_id` and optional `run_id`: local/paid terminal outcomes, completed observation counts, total/max measured durations, currently active local vs paid leases, and reserved/uncertain/settled charges separately as decimal text. It does **not** mark a run `qualified` for Analytics. The Studio `GET .../runs/{id}/metrics` only reads these aggregates after actual project authorization **and** independently trusted `verify_metrics_read` source-rights/revocation/retention authority. No trusted host = no sensitive response; no UI-provided permission accepted.

## 2. Separate CPU and model work queues

The existing DAG has `kind=local` only for B5, and `kind=paid` for B1–B4/B6–B8. Paid worker and OpenRouter POST remain **unconditionally blocked**. `monitoring.ThroughputPolicy` defaults to **at most 2 active local B5 leases per project** and **0 paid active leases**, with conservative independently measured prompt-token, output-token, image count and context-byte ceilings. It enforces bounded 1..1000 recovery batch sizes and maximum allowed 300-second local observation time.

`orchestrator.claim_local` serializes project-scoped concurrent admission with a transaction-scoped PG advisory lock, checks only **currently active (unexpired)** local leases and then uses existing ordered `FOR UPDATE ... SKIP LOCKED` run/job lease claims and monotonically increasing generation tokens. An expired local lease may be claimed under a NEW lease token/generation only after the source QDP/A/media rights admit the normal safe worker preflight. An active 2/2 project cap returns **no job**, not a busy-spin retry. Model jobs are never selected by this path. Limit cannot be bypassed by concurrent local-worker instances sharing the same DB.

`LocalWorker.run_once` uses a monotonic clock to record measured duration without relying on system wall-time adjustments. A verified result past the max wall budget is `partial`, **never made complete**. Absent local executor, failed media rights, missing source attestation, ambiguous evidence and invalid source SHA remain partial, not an invented no-cuts/silent detection.

The GB26 `studio_service.start` now also requires independently authenticated `verified_workload_resources(project_id,run_id,conn,body,run)` to return **exact measured** values per requested block (`prompt_tokens`, `completion_tokens`, `images`, `context_bytes`) before trying to enqueue. The browser cannot claim these measurements. `admit_resource_request` refuses missing/boolean/negative/out-of-budget counters. Paid `kind` fails until a different reviewed release explicitly activates a properly authenticated paid worker.

## 3. Provider error classification and uncertain charges

`monitoring.classify_failure` is a pure, privacy-safe classifier. No raw HTTP error body, URL, token or request headers appear in the output. Known pre-submission 429 is `rate_limit` (retriable at upstream scheduling only if NO attempt was submitted); 408/5xx/timeouts are `transient`, but **any already-submitted request without a certified receipt is `unknown_charge` with automatic retry disabled**, regardless of HTTP response, even 429. A submitted request WITH a verified receipt is still a consumed attempt and NEVER retries on its original ID. Independent authenticated `no_charge` reconciliation and a **new explicit approval/quote** are required for any later attempt. 401/403, invalid JSON/refusal, lease stolen, rights revoked and local engine failure get safe codes and no implicit paid fallback.

`ledger.mark_ambiguous` retains full reserved budget for unknown provider charges, and `ledger.reconcile_attempt` remains the only trusted reconciliation path. `cancel_run`, `recover_expired_run` and process restart do **not** zero a reserved/uncertain charge, invent a receipt, reset a paid job or give it a retry.

## 4. Crash/lease expiration recovery and cleanup

`recovery.read_recovery(conn,project_id,run_id,policy)` exposes active local, reclaimable expired local, exhausted expired local and unresolved submitted/uncertain paid counts, without raw source or provider content. `recover_expired_run` locks the run, checks no previous historical `group_b.invalidated.v1`, terminalization, **local source/policy media-rights revocation under locks** and a mandatory external authenticated `verify_recovery(project_id,run_id,conn)`. Missing proof is denied, never treated as no-revocation. It then invokes the existing exhausted-lease reap (max 1000 per bounded call) and writes one immutable `lease_expired` metric for each terminalized source job with `duration_ms=NULL` — never an estimated elapsed time from an untrusted clock. The same transaction writes job event, job status, metric and block state. Rerunning the recovery query is idempotent, does not create duplicate metrics and does not touch original SQL005 rows.

A worker with a stolen/cancelled/expired lease cannot complete or add metric against the old generation; fencing uses original source, run, job, token and generation. Reclaimable local work is **not** blindly retried by the recovery function: the next normal `claim_local` + GB07 source preflight must approve it. Cleanup means terminalizing exhausted leases and clearing expired lease tokens. Raw media and provider payload retention remain owned by upstream systems; GB27 telemetry contains neither and is append-only for audit, so no unsafe deletion of immutable accounting records is performed.

## 5. QA and existing compatibility

- `tests/test_group_b_recovery.py`: strict resource and paid disabled defaults, 429 before/after submission, 408/5xx charge uncertainty, bad HTTP code/boolean, source rights/lease classification, verified trace/version and nullable crash duration, synthetic 1k-policy limit.
- `tests/test_group_b_recovery_db.py`: optional disposable PostgreSQL17 real worker queue/cap, unique monotonic telemetry with exact job generation, no second metric on replay, immutable trigger, crash lease expiry and safe idempotent recovery (no paid retry), cross-project scope.
- Existing `tests/test_group_b_jobs.py` fake claim cursor updated for the additional project-wide capacity query. Existing `tests/test_group_b_b5.py` continues to require independently verified measured B5 output; no provider is run.
- Linux, Windows pure tests plus PostgreSQL17 jobs in `.github/workflows/group-b-module.yml` include both suites. Not yet claimed passing.

```bash
python -m unittest discover -s tests -p 'test_group_b_recovery.py' -v
CM_TEST_PG_DSN='postgresql://.../cm_group_b_test' python -m unittest discover -s tests -p 'test_group_b_recovery_db.py' -v
```

## Blockers and next stage

**Real QA still outstanding:** no mounted full local checkout or disposable PG17/Windows job available within this GitHub-only environment, pending CI results are not an observed PASS. Full production throughput requires a real externally authenticated paid model dispatcher, exact current QDP/A/rights/media-byte permissions, real independent reviewer provider receipts and downstream UAE qualification; none is invented here. Next roadmap **GB-MOD-28** will run full cross-platform QA, migration baseline/upgrade, synthetic golden media and performance baselines on a controlled machine.
