# PIE-MOD-33 — Orchestrator, Workers, Queue, Leases & Outbox v1

**Branch:** feature/patterns-insights-full-module; other module branches and main untouched.
**Module:** intelligence/patterns_insights/research/orchestrator.py
**Migration:** intelligence/migrations/207_pie_orchestrator_queue.sql (additive)
**Tests:** tests/test_pie_orchestrator.py — 44 authored unittest methods including optional disposable PostgreSQL17.
**Status:** implementation authored/committed; local full suite, real PostgreSQL concurrency and live host authorization NOT VERIFIED.

## Architecture

Migration200 already defines pie_research_runs, pie_jobs (state, max_attempts, attempts, fence_token, lease_until, leased_by, available_at), and an immutable pie_outbox. Stage33 **reuses these existing tables**, not a second queue. Migration207 adds immutable pie_job_execution_specs, pie_job_orchestration_events, pie_job_checkpoints and PL/pgSQL integrity triggers. Source run FKs, existing registry/source rights and the nonpublishable candidate policy remain unchanged.

Every job is bound to the exact project, UUID run, idempotency request UUID and SHA256 of bounded input metadata. Current host JobAuthority.require_current_job_access(project,actor,operation) must independently authenticate the caller; fake unit-test authority is not live authorization. Enqueue additionally checks that the project-scoped run is still draft. Reusing the same idempotency request with changed inputs, code or retry policy fails closed. Only SHA/enum metadata is stored, never original UAE media, creator IDs, video references, raw model text or secrets.

## Separation of deterministic and paid work

Safe-deterministic task types: dataset_snapshot, pattern_discovery, pattern_validation, insight_deterministic, insight_verification, review_internal, source_invalidation. Each maps to the existing pie_jobs.kind enum with immutable execution_policy=safe_deterministic. The worker must obtain fresh current rights both for "claim" and "execute:<task_code>", and must revalidate producer/source rights inside the task handler before using real data.

paid_model_manual_handoff is a different execution policy: manual_paid_only. It is inserted as state=blocked with max_attempts=1 and is deliberately **never** eligible for worker claim, heartbeat/recovery or retry. Cancellation only cancels this *queue metadata*, not an already-sent provider request or actual charge. All actual LLM requests remain in stage29 quote/authorization/pre-send-claim protocol; stage33 does not know OpenRouter credentials or expose a paid-task handler.

## Worker claim and fencing

claim_deterministic_job uses a single PostgreSQL transaction:

    WITH next_job AS (
      SELECT j.id FROM pie_jobs j
      JOIN pie_job_execution_specs s ON s.project_id=j.project_id AND s.job_id=j.id
      JOIN pie_research_runs r ON r.project_id=j.project_id AND r.id=j.run_id
      WHERE j.project_id=:project AND j.state='queued'
        AND j.available_at<=clock_timestamp()
        AND j.attempts<j.max_attempts AND r.status='draft'
        AND s.execution_policy='safe_deterministic'
      ORDER BY j.available_at,j.created_at,j.id
      FOR UPDATE OF j SKIP LOCKED LIMIT 1
    )
    UPDATE pie_jobs SET state='leased',attempts=attempts+1,
      fence_token=fence_token+1,leased_by=:worker,lease_until=...

The database clock controls lease/expiry and bounded 10–600 second TTL. Every heartbeat, checkpoint, completion or failure requires the same project/job, original worker identity, current fence_token and live (nonexpired) lease. PostgreSQL triggers also check lease and prevent illegal state transitions. Stale workers cannot commit completion after another worker reclaims, a cancellation, lease expiry, or run invalidation. A heartbeat extends, never shortens, the active lease.

Checkpoint payload is a canonical SHA-bound minimal object with job/fence/ordinal, state_digest_sha256 and explicit can_publish=false and may_issue_paid_request=false. No source observations, media or provider response data are permitted. The SQL checkpoint guard locks the current job and rejects changed/expired fence before INSERT. A generation-specific monotonically increasing event_seq and previous SHA link every transition.

## Completion, safe retry, expiry recovery, cancellation

A successful deterministic handler returns only a SHA256 result; complete_job atomically marks state=completed, clears lease and records its SHA event+outbox. A failure has one of the enumerated codes. Only transient_internal, upstream_unavailable or source_not_ready can be retried, and only on safe deterministic jobs with attempts<max_attempts. All rights_denied, policy_blocked, invalid_source, validation_failed, unknown_failure and unexpected handler errors are terminal. Backoff in seconds:

    delay(attempt) = min(3600, 2**min(attempt,10))

Hand oracle for attempts (1,2,3,10,20) is (2,4,8,1024,1024) seconds. No automatic retry of unknown billing outcome or previously claimed stage29 model request.

recover_expired_jobs selects only expired, safe_deterministic leased jobs with FOR UPDATE OF j SKIP LOCKED, one bounded transaction per recovered task. It marks each as queued with backoff if the run is still draft and attempts remain, otherwise failed. Future claim increments fence_token. Paid/manual blocked tasks are never recovered into automated execution.

cancel_job atomically transitions queued/leased/blocked to cancelled under the job lock, revokes an active lease, emits the cancellation outbox event and does not rewrite completed/failed jobs. An idempotent cancellation returns its previous immutable cancellation event SHA. A run changing away from draft prevents subsequent claim/completion; an expired lease is reconciled as failed if the run is not eligible.

## Atomic outbox and observability

Every enqueue, claim, heartbeat, checkpoint, retry/failure, recover, cancellation and completion inserts an append-only pie_job_orchestration_events row AND a same-transaction pie_outbox row. event_key=pie-job:<jobUUID>:<event_seq> is unique per project; a failed outbox INSERT rolls back the entire state transition. The event payload is only metadata: project/run/job IDs, bounded enum, SHA digests, fence token and explicit no-publication/no-paid flags.

This is durable **at-least-once** outbox storage, NOT automatic delivery to a broker or publication surface. A future consumer must deduplicate by project_id/event_key and independently recheck source/user rights. No background process, notification transport or publisher was activated by this stage.

execute_one_deterministic accepts an injected trusted DeterministicTaskHandler, leases one safe task, executes it, records a SHA result or terminal error. Such handlers MUST recheck their source rights and fence all durable writes themselves. The coordinator guarantees DB fencing/state transitions, not exactly-once arbitrary external side effects or rollback of external I/O.

## Tests and QA truth

**44 authored unittest methods** include: positive enqueue/claim/heartbeat/checkpoint/complete, exact idempotency/replay, modified idempotency conflict, host denial, cancelled run, per-task current worker authorization, wrong worker/fence, duplicate claim/completion, cancellation under active lease, transient/nontransient retry oracle, exhausted attempts, expired recovery with project isolation, blocked paid manual job, fake deterministic executor, event/outbox nonpublication, and migration static assertions. Optional tests require explicit disposable CM_TEST_PG_DSN (name contains test) for PostgreSQL17 fresh+upgrade/schema triggers and foreign orphan-job guard.

Run on the user's full source checkout:

    python -m unittest discover -s tests -p 'test_pie_orchestrator.py' -q
    python -m unittest discover -s tests -p 'test_pie_*.py' -q
    python -m compileall -q intelligence/patterns_insights tests/test_pie_orchestrator.py
    python -m intelligence.migrate

Actual multiworker PostgreSQL17 isolation, crash simulation, rights adapters, scheduler, source-specific handlers, outbox consumer and live paid model remain **unverified and unconnected**. The synthetic SQL object is NOT an actual PG test. No local unittest/compileall/PG17 tests or green CI were observed when committing. Existing migration203 checksum repair from PIE-MOD-30 must be reconciled against any already-deployed DB before applying migrations.

## Next

PIE-MOD-34 — HTTP API, Studio and Pattern Explorer. All read endpoints require current project/user/source rights; no unauthorized candidate or public Insight, and revoked/stale/empty states must be explicitly represented. Other branches remain untouched.
