# GB-MOD-11 — Durable Group B worker / orchestrator

**Implementation branch:** `feature/group-b-full-module`. **Owned files:** `intelligence/group_b/{orchestrator,worker}.py`, optional `repository.require_worker_schema`, additive `intelligence/migrations/058_group_b_worker_jobs.sql`. **Integration status:** stage implementation committed, not a working live QDP→A→B or paid-OpenRouter pipeline. Existing `005/055/056/057` are unchanged.

## 1. Per-block state machine

`group_b_jobs` stores one immutable-identity job for each `(project_id,run_id,block)`, with exact stage06 `evidence_sha256`, `depends_on`, `kind`, a bounded attempt counter, a UUID `lease_token`, a monotonically increasing integer `lease_generation`, deadline and optionally a SHA of a separately verified **local** output. `group_b_job_events` is an append-only event history with an UPDATE/DELETE guard. The database trigger protects identity, fencing generations and transition order. No task becomes `complete` without a real `result_sha256`. An input hash alone is not a validated B result.

| Stored state | Meaning |
|---|---|
| `ready` | Enqueued, exact initial evidence pinned. No worker or provider request yet |
| `running` | One bounded leased local attempt; lease token + fencing generation required |
| `complete` | Local stage-specific trusted verifier attested the exact output bytes/hash |
| `partial` | Missing input, adapter/verifier, or locally unverified result. Not `false`/zero |
| `failed` | Terminal bounded local lease attempts exhausted or explicit failure |
| `blocked` | Terminal disallowed/unsafe job; **paid** claims are disabled |
| `cancelled` | Terminal cancellation; late heartbeat/output commit is fenced |

The B run itself is never automatically `completed`. The canonical B analysis and output-validation pipeline (`content_b_analyses`) remains a GB-MOD-21/22 responsibility. A successful local job would move the run to `partial` until an approved canonical full B analysis exists.

## 2. Frozen DAG and free/paid partition

`DAG_VERSION=group-b-dag/v1`:

- **Independent roots:** B1, B2, B5, B7, B8
- **B3 and B4:** require a completed B2 job
- **B6:** requires a completed B4 job
- **Local:** B5 only. Implementing its real calculations/verification is **GB-MOD-12**.
- **Paid / LLM:** B1/B2/B3/B4/B6/B7/B8. A ready job requires a real prior `reserved` stage10 attempt bound to the same project, run, block, request hash and evidence SHA. It may be enqueued for scheduling/visibility, but it is **never claimed or sent** by the current worker and PostgreSQL 058 rejects moving paid jobs to `running`. A future release must explicitly add a separate authenticated paid execution path with fresh QDP/GA/media rights, durable provider idempotency and verified receipt reconciliation. No fictional transport approval is accepted.

`enqueue_jobs(conn,project_id,run_id,readiness,selected_blocks,paid_attempts,dry_run)` only considers READY block decisions from an identical run and exact stage06 evidence hash, ensures all DAG dependencies are selected/ready, locks run then block rows in one transaction, prevents duplicate jobs/reuse of a changed evidence pin, and checks an accepted Group B QDP inbox and existing A snapshot/manifest. `dry_run=True` returns eligible block names **without database writes**, even when no paid reservations exist; it does not mean authorized to run. A stored job requires project authorization on the calling host; do not expose these low-level functions to unauthenticated HTTP users.

`JobOrchestrator(connection_factory)` is the opt-in transaction facade; except for dry-run it verifies schema 058 installed with the exact checksum before operating, then owns the SQL transaction. It never starts a daemon on import. Existing Group B intake only requires 055/056; stage11 stays an additive optional integration.

## 3. Claims / fencing / cancellation

`claim_local` takes the **run lock first** and then the local job lock with `FOR UPDATE ... SKIP LOCKED`. The stable run→job lock order is also used by enqueue, cancellation and finalization, avoiding lock inversion deadlocks. It claims only READY B5 jobs or their expired RUNNING lease with `attempts < max_attempts`. Every claim increments `lease_generation`, replaces `lease_token`, extends lease by 5–300 s, increments attempts and appends a claim/reclaim event. Two workers cannot claim the same job concurrently. A previous worker's token and generation are invalid after reclaim.

`heartbeat(conn,claim)` requires matching run, project, block, evidence, token, generation, state and nonexpired lease; it cannot revive an expired or cancelled job. `finish_job(conn,claim,state,reason,result_sha256)` has the same fence, releases lease, writes B block status+immutable event in the same transaction and leaves run `partial` when no work remains. It will not mark a job `complete` without a nonempty SHA. The actual B5 output verifier is an independent trusted component not yet shipped here.

`cancel_run` locks the run first, fences every remaining READY/RUNNING/PARTIAL job by clearing lease and marking cancelled, and updates run state. Late claim, heartbeat and completion fail safely. Cancellation never modifies or releases stage10 ledger reservations; uncertain provider charges remain liabilities.

`reap_expired_jobs` handles power loss after **bounded attempts are exhausted**. Expired local job transitions to FAILED; stale paid work would be BLOCKED without changing any reserved/uncertain financial liability. It appends a `lease_expired` audit event, updates B block state and leaves the run partial. It never invents a completed B result. The caller must invoke it as an operational recovery action; there is no background timer or recurring automation installed.

## 4. LocalWorker / PaidWorker boundaries

`LocalWorker(connection_factory,executor=None,result_verifier=None)` runs a **single** work cycle. It commits the claim before potentially expensive read/IO, repeats stage07 `require_fresh_block` through a trusted caller-provided `WorkerSources`, and checks the stage06 evidence SHA again. Without a connected actual B5 local algorithm and stage-specific authenticated verifier, it finalizes as `partial`, never a fabricated success. A `LocalResult` additionally binds block, original evidence hash and hash of a JSON output. A trusted `result_verifier.verify_local_result(claim,result)` must attest the output to accept `complete`; an untrusted self-supplied SHA is insufficient. User code may not substitute QDP hypotheses or unsupported OCR/ASR for measured evidence. The future GB12 implementation must provide the actual deterministic algorithm.

`LocalWorker.preview` / `run_once(dry_run=True)` are read-only and do not reserve, claim, heartbeat, infer, pay, modify run state or start services. The `PaidWorker.run_once` method unconditionally raises `ExecutionNotApproved` in this stage, and the stage09 OpenRouter direct POST remains blocked. Even having a valid `group_b_model_approvals` and budget reservation does **not** authorize HTTP by itself.

## 5. Failure semantics

- **Two workers:** `SKIP LOCKED` on run then job means exactly one acquires a ready local job; the other gets no job and cannot duplicate a result.
- **Power loss:** lease expires. Local work can be reclaimed up to `max_attempts` (default 3), with new fencing; no `completed` result is written on timeout. Exhausted attempts require explicit reaper.
- **Lease theft:** old token and generation cannot heartbeat or commit; stage 11 uses database time, not a worker's local clock.
- **Cancelled run:** query no longer claims; previous workers fail at finalization. No cancellation magically proves a remote provider wasn't billed.
- **Partial / missing evidence:** readiness or executor failure is reported as `partial`, and B output remains uncommitted.
- **Replay:** same run/block/evidence can be enqueued idempotently, while a different evidence hash or paid attempt identity is rejected.
- **Ambiguous paid charge:** stage10 `submitted/uncertain` liabilities remain reserved, and no paid task is claimed here.

## QA

`tests/test_group_b_jobs.py` covers pure DAG selection, hash/identity, B5-only local policy, mocked lease/token validation, paid denial, no fake completion and read-only preview. `tests/test_group_b_jobs_db.py` uses a disposable **PostgreSQL 17** database to verify two-worker claim race, idempotent enqueue, actual lease theft/fencing, partial result and run state, cancellation, exhaustion/reaper and append-only events.

```bash
python -m unittest discover -s tests -p 'test_group_b_jobs.py' -v
CM_TEST_PG_DSN=postgresql://.../cm_group_b_test python -m unittest discover -s tests -p 'test_group_b_jobs_db.py' -v
```

All tests use fake QDP/A lineage records, no actual peer producer, OpenRouter key, model request or bill. The workflow runs pure suite on Windows/Linux and the PG17 suite in its database job; **implementation is not the same as confirmed CI success**.

**Next:** GB-MOD-12 — local B5 editing calculation/measurement with authenticated evidence-based output verifier.
