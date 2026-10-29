# UA-MOD-17 — Durable qualification Job Queue

**Canonical branch:** `feature/unified-analytics-full-module`. **Migration:** `intelligence/migrations/088_unified_analytics_qualification_jobs.sql`. **Facade:** `intelligence/unified_analytics/job_queue.py`. **Tests:** `tests/test_unified_analytics_job_queue.py` and `tests/test_unified_analytics_job_queue_pg17.py`.

This stage provides **durable scheduling state only**. It does not run Group A/B/C, create reviewed C scores, approve current SRE LIVE, grant read permissions, or deliver Patterns events. Stage UA-MOD-18 will own the dedicated worker. Nothing starts on boot, main is unchanged, and no HTTP/API endpoint is made public.

## Safety model

1. A producer-supplied job ID, checksum or `QueueSpec` is **not authorization**. Every call requires an authorized server-side PostgreSQL connection for exactly the requested project. The caller establishes the authenticated project role/scope; the facade does not provide web auth or RLS. A raw SQL writer with DML privileges is a trusted system actor; deploy using least-privilege DB roles.
2. `QueueSpec` binds exact immutable `uae_qualification_requests` ID, project, request SHA, idempotency key, batch key, bounded priority and attempts. Repeated identical submissions return `replayed`; colliding keys/batch identities with different canonical metadata fail closed. `enqueue_batch` accepts 1–100 entries and commits as one transaction, or rolls back all on error.
3. The SQL `uae_queue_request_ready(project,request)` rechecks real UAE/D1 tables at **enqueue, claim, heartbeat and completion**. The original request must still be active, belong to the exact project and video membership, and match an enabled source + uniquely active reviewed source policy with affirmative source and policy permissions and explicit positive approved derived retention. Three exact run/project/video UAE QDP handoff receipts (A, B, C) must be present. These receipts come from UA-MOD-07 validation at ingestion; **their presence alone never proves that upstream live SRE, reviewed B or Group C approvals still hold.** Those checks remain required at UA-MOD-18 execution and UA-MOD-19 visibility.
4. `claim` uses `FOR UPDATE SKIP LOCKED` for batches of 1–100, ordered by priority / available time. All lease tokens are DB-generated UUIDs. Every claim increments both `attempts` and `lease_generation`. SQL heartbeat, complete and fail require exact project/job/token/generation/worker and an unexpired lease. A late worker cannot complete after crash recovery or another lease. Success additionally requires an existing, noninvalidated, **partial or blocked** immutable snapshot of that same request. `succeeded` means this job completed, **not that a video is qualified**.
5. `fail` stores a bounded machine-readable error code, with 0–86400 second retry delay. At `attempts=max_attempts` it atomically enters `dead` (DLQ). `recover_expired` is a bounded, explicit maintenance operation; no scheduler runs automatically. An expired final attempt moves to `dead`. `cancel` permanently invalidates a pending/retry/leased job and fences an old token. Terminal `succeeded/dead/cancelled` rows cannot return to queue. All state transitions are appended to `uae_qualification_job_events`; heartbeat-only changes do not create spurious events.
6. `dead_letters(project)` only returns metadata, never media bytes or source credentials. Only caller-authorized project-scoped reads are permitted. There is no automatic replay of terminal dead jobs; a new approved request with a new batch identity is needed for future work. Outbox dispatch, retention cleanup and job execution are deliberately absent.
7. SQL migration 088 is additive and UAE-owned. Historical 080–087, PR #23, main, peer branches and Group C score calculation are not modified.

## Facade API

```python
from intelligence.unified_analytics import (
    QueueSpec, QualificationJobQueue,
)

# conn is an already-authenticated, IDLE, psycopg dict_row connection,
# on a disposable DB in tests (not public request handler).
queue = QualificationJobQueue(conn)
receipt = queue.enqueue(QueueSpec(
    project_id=project_id,
    request_id=request_uuid,
    request_sha256=request_sha256,
    idempotency_key="analysis.batch001",
    batch_key="batch001",
))
leases = queue.claim(project_id, "uae.worker.1", limit=10, lease_seconds=60)
# Stage UA-MOD-18 will perform a new authorized current-evidence check
# and persist a UA-MOD-15 unapproved snapshot before calling complete().
for lease in leases:
    queue.fail(lease, error_code="peer_unavailable", backoff_seconds=30)
# Explicit maintenance (not auto scheduled):
queue.recover_expired(project_id)
```

If `claim` returns no rows, this is **not proof** of readiness, success or permanent absence: rights, QDP handoffs or future availability may be missing. An older lease cannot be acknowledged by claiming `worker` equality alone; the token and epoch must match. Missing PostgreSQL features/tables reject transactions rather than silently switching to an in-memory queue.

## QA and release gates

**Authored tests**: deterministic mock tests for duplicate identity, conflicting keys, batch rollback, two-worker race, stale lease/recovery, bounded retry/dead-letter, cancellation, revoked readiness and error redaction; SQL static checks for SKIP LOCKED, fencing, owner isolation and nonpublication. Separate conditional **real PostgreSQL 17** tests cover duplicate batch + two concurrent claimers, QDP revocation readiness and a real clock-expired final lease/dead-letter. The PG17 fixture inserts synthetic handoff receipts solely to exercise SQL constraints, never claiming a real approved peer.

```sh
python -m compileall -q intelligence/unified_analytics
python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
# Real PG17 tests require isolated credentials:
CM_TEST_PG_DSN="postgresql://... disposable PG17 ..." python -m unittest \
  tests.test_unified_analytics_job_queue_pg17 -v
```

**Not verified in this tool-only GitHub edit session:** Python compile/unittest execution, actual PG17 migration 088, two-process PG17 race, peer A/B/C + LIVE SRE integration, downstream UA-MOD-18 worker and CI conclusions. Keep these blockers explicit in the MD tracker. The actual tests' presence is not a PASS. CI must report its own result; Draft PR #38 remains open and unmerged.

**Next planned stage:** UA-MOD-18 — a controlled Python `--once/--limit/--watch` worker that freshly authorizes each leased job, executes actual qualification through existing UAE joined context/immutable repository, uses queue fencing, has bounded resources and a deliberate stop, without provider/LLM or autostart.
