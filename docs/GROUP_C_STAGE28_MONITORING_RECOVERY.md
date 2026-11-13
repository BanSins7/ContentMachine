# GC-MOD-28 — Monitoring, Recovery and operations runbook

**Branch:** `feature/group-c-full-module`. **Status:** standalone monitoring, alert thresholds, redacted events, signed/fenced PostgreSQL job recovery, non-destructive cleanup preview and immutable audit **CODE IMPLEMENTED**. **Exact-head unit tests, PG17 recovery smoke, CI, installed metric exporter/alert manager and real IAM NOT VERIFIED.** Therefore the plan's "real recovery smoke and observability verified" production DoD remains **BLOCKED_EXTERNAL / QA_NOT_VERIFIED**, not green. No main/peer edits.

## Source of truth and boundaries

- `intelligence/group_c/operations.py`: `PostgreSQLGroupCOperations`, `MonitoringPolicy`, `ProjectPulse`, `OperationalAlert`, `MaintenancePreview`, `RecoveryReceipt` and `RecoveryAuthorityVerifier`.
- `intelligence/migrations/063_group_c_monitoring_recovery.sql`: additive project-scoped `group_c_recovery_operations` immutable signed-administration audit (UUID operation ID, canonical request SHA, authority metadata SHA, recovered exact job IDs/count, timestamp). Append-only trigger; no signed assertion/token stored; earlier migration SHA is unchanged.
- `intelligence/group_c/durable_worker.py`: existing `_recover_locked` accepts optional internally supplied `recovered_ids` list and records exactly the jobs it fenced within the same transaction; ordinary public worker API and claim/retry semantics remain unchanged.
- `intelligence/group_c/http_api.py`: inherited GC26 Host/HTTPS/Origin/bearer/CSRF dispatch, new **separate project IAM action grants**:
  - `GET /api/group-c/v1/projects/{project}/operations/health` requires `group_c:ops_read`.
  - `GET /api/group-c/v1/projects/{project}/operations/cleanup-preview` requires `group_c:ops_read`.
  - `POST /api/group-c/v1/projects/{project}/operations/recover` requires `group_c:ops_recover`, exact trusted Origin + action-bound CSRF, JSON `{operation_id:canonical_UUID,signed_assertion:string}` and separately injected real `RecoveryAuthorityVerifier`. Without verifier returns 503, not a fake success.
- **No scheduler, HTTP side effects, provider requests, independent C7 scores, remote ACK retries, DB-role grants, batch erasure or automatic deletion** are implemented in operational routes.

## Health pulse and alerts

`snapshot(conn,project_id)` validates exact project in the registry and runs **REPEATABLE READ, READ ONLY** on an independently autocommitted PostgreSQL connection. Counts only project-owned operational metadata: queued/running/failed jobs, expired running worker leases, blocked runs, newly received QDP events and inbox conflicts, pending/failed outbox and expired leased outbox, latest source observation missing/stale counts for project videos, queue/inbox/outbox oldest pending ages and latest observation max age, and count of signed recovery operations during last 24 hours. It NEVER returns raw video views, peer cohort numerics, user contact data, signed auth or Breakout Score. Newest observation freshness is an **operational warning only**, not a GC24 retention/permissions proof: historical C7 can never be authorized by this endpoint.

`alerts_for(data,MonitoringPolicy)` deterministically derives stable warning/critical codes. Configurable thresholds default to queue 600s/3600s, inbox 300s/1800s, raw metric age 21600s/86400s, outbox 300s/1800s (warn/critical). Other immediately actionable alerts: `worker_lease_expired` (critical), `outbox_lease_expired_manual_ack_required` (critical), `worker_failed`, `outbox_failed`, `inbox_conflicting_provenance`, `metric_observation_missing`, `blocked_runs_review_required`. These are policy thresholds, **not measured production SLOs**. No caller should interpret an empty pulse as a live source authorization.

`_event` emits allowlisted JSON shape (`version,action,project_fingerprint,outcome,count,operation_id,trace_id,duration_ms`) to Python logger `contentmachine.group_c.operations` and optional injected observer callback; the trace UUID is generated per call and duration measured by a monotonic clock; it never logs a bearer token, signed assertion, raw project ID or original score. If telemetry callback fails AFTER a committed recovery, DB audit remains true and call does not incorrectly imply the transaction failed. Real collector/metrics exporter, dashboards, tracing sink, alert routing, SLO thresholds and paging must be connected by SRE owner before production. The callback does NOT guarantee telemetry delivery.

## Signed recovery, exact concurrency and replay

`recover_expired_jobs(conn, project_id, operation_id, signed_assertion, verifier)`:
1. Enforce valid canonical project ID, canonical operation UUID, signed assertion length and a non-null externally injected verifier.
2. Begin SERIALIZABLE transaction. Verify project still exists; ask trusted IAM verifier to inspect signed action/project/operation/request SHA and return an exact verified `principal_id` + `authentication_event_id`. Caller-controlled body cannot supply these identities.
3. Lock the SAME global PG advisory transaction lock used by GC21 worker claims, then check audit ledger by global operation UUID. On identical replay (same project/request/authority hashes), return original receipt with `replayed:true`; if bound differently, reject `recovery_operation_conflict`, never overwrite the audit.
4. Use original GC21 `_recover_locked` with `FOR UPDATE SKIP LOCKED`: only jobs in `running` with `lease_until <= clock_timestamp()` can be recovered; unexpired/cancelled/completed jobs untouched. Each old lease token/generation gets fenced and cleared, retry wait follows original capped exponential policy, exhausted attempts become terminal; corresponding run status changes in the original transaction.
5. Persist exact recovered job IDs and canonical authority digest in append-only `063` audit **in the same SERIALIZABLE commit**; if audit or domain update fails, both roll back. Log a redacted outcome after commit. No blind replay of Analytics outbox leases.

A second worker holding an expired token must receive `worker_lease_lost` on heartbeat/fail/score; a new claim after `available_at` must obtain a new lease generation. Concurrent recovery/claim operations use the same advisory lock, and each recovery uses row-level SKIP LOCKED. A repeated HTTP call MUST reuse the same operation UUID and external signed action, not create a second operator authorization just to bypass idempotency.

**Existing GC21 worker behavior:** `PostgreSQLGroupCWorker.claim` and `recover_expired` already call internal `_recover_locked` to recover expired worker leases. The GC28 immutable 063 journal tracks **only explicit signed administrative `PostgreSQLGroupCOperations.recover_expired_jobs` requests**; it does NOT journal every existing automatic GC21 internal recovery. Consequently, `recent_recovery_operations=0` does not mean the worker never automatically recovered a job. Centralized full-lifecycle tracing and role-level audit for these internal GC21 paths remains a production SRE integration requirement. Existing GC21 automatic recovery has NOT been disabled by this stage.

## Safe cleanup and rollback policy

`maintenance_preview` returns **only a read-only plan** with recommended actions: audited worker recovery, inspect Analytics ACK for expired outbox lease, inspect failed/blocked run, investigate QDP inbox or source freshness, check eligible pending deliveries. It explicitly blocks:
- automatic requeue/delivery of an **expired Analytics outbox lease** (external sink may already have accepted the event; confirm provider ACK and exact idempotency SHA before any new delivery);
- deletion/physical erasure of source snapshots, immutable reviews/cohort freezes, completed C7, audit receipts or backups without separately verified legal authority and a migration-safe deletion executor;
- generic pruning of append-only historic C7 or deleting a project to "clear" pending recovery.

**Rollback/runbook** (operator-controlled, not automatic):
1. Disable/stop the process that invokes the Group C worker and recovery routes, without deleting tables or weakening IAM/SQL permissions. Maintain ingress of authenticated read-only health calls if needed.
2. Read scoped `/operations/health` and `/operations/cleanup-preview`; capture counts, alarm codes and the signed recovery operation UUID. Confirm whether failure is transient DB outage, IAM/source revocation, expired raw/peer TTL, worker lost lease, or Analytics ambiguous ACK.
3. For expired worker leases only, verify signed out-of-band admin approval, ensure no active worker with nonexpired lease, POST `/operations/recover` exactly once. Audit `group_c_recovery_operations` and confirm `expired_job_leases` decreases; observe (queued/failed) transitions and fence old worker token. Do not force completed/invalidated C7 back to `complete`.
4. For stuck leased Analytics outbox, stop delivery and confirm the durable sink side (same DB or separately verified ACK). **Do not set state `pending` solely because a lease expired**; no GC28 automatic replay path exists. Escalate to Analytics owner with original `event_key` and payload SHA, never signed auth.
5. If the GC28 deployment must be rolled back, disable the optional routes/observer/worker in the host process and return to prior compatible Group C software; keep additive `063` audit and already applied migrations intact. Do NOT roll back 003/004/056..063 by DROP/UPDATE or alter immutable history. Apply tested forward fixes as new migrations only.
6. For data deletion/retention conflicts, escalate to legal/privacy and IAM/DB ownership. Never "fix" incident telemetry by disclosing revoked C7 numeric/peer raw data. Record handoff to SRE owner.

**Operational smoke gates before production:** disposable PG17 old lease becomes queued or terminal with correct lease_generation and audit, heartbeat from old lease fails, active lease untouched, repeated operation returns same IDs, cross-project signed grants denied, unknown project denied, outbox ambiguous lease unchanged, audit UPDATE/DELETE denied, telemetry callback errors do not undo committed result; stage tests authored in `tests/test_group_c_operations.py` (pure and PG17), not actually executed on this branch yet. True production telemetry, real IAM, owner-controlled SRE alerts, backup/recovery, load tests and external Analytics ACK E2E remain **BLOCKED_EXTERNAL**.

Next planned stage **GC-MOD-29 — Load/performance QA**: 10k+ posts, cohorts/index plans, cold/warm throughput and backpressure. Do not publish metrics or throughput until actually measured.
