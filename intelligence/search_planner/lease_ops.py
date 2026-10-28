"""SP-15B: PostgreSQL fenced lease for *non-paid* QV -> SP inbox cycles.

It is not a license or scheduler for OpenRouter, Collector accept() or
provider searches. Expired lease holders must stop before the next cycle;
SP12's atomic QV row-lock/ACK remains authoritative after any crash/race.
"""
from __future__ import annotations

from uuid import uuid4

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid
from .contracts import publication_uuid
from .supervision import run_safe_inbox_cycle

KIND = "qv_inbox"
LEASE_SECONDS = 120


def acquire_inbox_lease(project_id, owner_id, *, lease_seconds=LEASE_SECONDS,
                        connect=None):
    pid, oid = _pid(project_id), publication_uuid(owner_id)
    if type(lease_seconds) is not int or not 30 <= lease_seconds <= 300:
        raise PipelineError("Invalid safe supervisor lease duration", 422)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            # Atomic unique-PK UPSERT: only one holder can win. On expiry a new
            # owner receives a strictly larger fence; stale owner cannot renew.
            row = conn.execute(
                """INSERT INTO sp_supervisor_leases
                       (project_id,worker_kind,owner_id,heartbeat_at,lease_until)
                   VALUES (%s,'qv_inbox',%s::uuid,now(),
                           now()+(%s * INTERVAL '1 second'))
                   ON CONFLICT (project_id,worker_kind) DO UPDATE
                     SET owner_id=EXCLUDED.owner_id,
                         fence_generation=sp_supervisor_leases.fence_generation+1,
                         acquired_at=now(),heartbeat_at=now(),
                         lease_until=EXCLUDED.lease_until
                   WHERE sp_supervisor_leases.lease_until <= now()
                   RETURNING fence_generation""",
                (pid, oid, lease_seconds),
            ).fetchone()
    if row is None:
        return None
    if type(row[0]) is not int or row[0] < 1:
        raise PipelineError("Invalid durable supervisor fence", 409)
    return {"project_id": pid, "owner_id": oid, "fence_generation": row[0]}


def heartbeat_inbox_lease(project_id, owner_id, fence_generation, *,
                          lease_seconds=LEASE_SECONDS, connect=None):
    pid, oid = _pid(project_id), publication_uuid(owner_id)
    if (type(fence_generation) is not int or fence_generation < 1 or
            type(lease_seconds) is not int or not 30 <= lease_seconds <= 300):
        raise PipelineError("Invalid supervisor heartbeat fence/TTL", 422)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            row = conn.execute(
                """UPDATE sp_supervisor_leases
                     SET heartbeat_at=now(),
                         lease_until=now()+(%s * INTERVAL '1 second')
                   WHERE project_id=%s AND worker_kind='qv_inbox'
                     AND owner_id=%s::uuid AND fence_generation=%s
                     AND lease_until>now()
                   RETURNING fence_generation""",
                (lease_seconds, pid, oid, fence_generation),
            ).fetchone()
    if row is None or row[0] != fence_generation:
        raise PipelineError("Lost supervisor lease; do not start more work", 409)
    return True


def release_inbox_lease(project_id, owner_id, fence_generation, *, connect=None):
    pid, oid = _pid(project_id), publication_uuid(owner_id)
    if type(fence_generation) is not int or fence_generation < 1:
        raise PipelineError("Invalid supervisor release fence", 422)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            row = conn.execute(
                """UPDATE sp_supervisor_leases
                   SET lease_until=now(),heartbeat_at=now()
                   WHERE project_id=%s AND worker_kind='qv_inbox'
                     AND owner_id=%s::uuid AND fence_generation=%s
                   RETURNING fence_generation""",
                (pid, oid, fence_generation),
            ).fetchone()
    return row is not None and row[0] == fence_generation


def run_fenced_inbox_cycle(project_id, *, limit=10, service=None,
                           acquirer=acquire_inbox_lease,
                           heartbeater=heartbeat_inbox_lease,
                           releaser=release_inbox_lease):
    """One explicitly invoked short cycle. No timed daemon or paid work.

    The external DB lease can expire during a long SQL projection, but the
    SP12 QV transaction still prevents duplicate durable inbox effects.
    This function never uses a lease as authorization for paid execution.
    """
    pid = _pid(project_id)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise PipelineError("Invalid fenced inbox limit", 422)
    if any(not callable(p) for p in (acquirer, heartbeater, releaser)):
        raise PipelineError("Invalid trusted supervisor ports", 422)
    oid = str(uuid4())
    lease = acquirer(pid, oid)
    if lease is None:
        return {
            "schema_version": "search-planner-fenced-inbox-cycle/v1",
            "project_id": pid, "lease_status": "busy",
            "ingested_events": 0, "paid_http_requests": 0,
            "collector_accept_calls": 0, "provider_search_calls": 0,
        }
    if (type(lease) is not dict or lease.get("project_id") != pid or
            lease.get("owner_id") != oid or
            type(lease.get("fence_generation")) is not int or
            lease["fence_generation"] < 1):
        raise PipelineError("Untrusted supervisor lease acquisition", 409)
    generation = lease["fence_generation"]
    try:
        if heartbeater(pid, oid, generation) is not True:
            raise PipelineError("Cannot verify supervisor lease", 409)
        result = run_safe_inbox_cycle(pid, limit=limit, service=service)
        if heartbeater(pid, oid, generation) is not True:
            raise PipelineError("Supervisor lease lost after inbox cycle", 409)
        return {
            "schema_version": "search-planner-fenced-inbox-cycle/v1",
            "project_id": pid, "lease_status": "released",
            "ingested_events": result["ingested_events"],
            "no_work_events": result["no_work_events"],
            "paid_http_requests": 0,
            "collector_accept_calls": 0, "provider_search_calls": 0,
        }
    finally:
        # Fenced CAS prevents a late owner from releasing a stolen lease.
        releaser(pid, oid, generation)
