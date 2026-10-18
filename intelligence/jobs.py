"""PostgreSQL-backed durable queue. Each leased task is claimed once per transaction."""
from __future__ import annotations
import json
from datetime import timedelta
from psycopg.rows import dict_row

def enqueue(conn, job_type: str, idempotency_key: str, payload: dict,
            priority: int = 0, max_attempts: int = 3) -> int:
    if not job_type or not idempotency_key or len(idempotency_key) > 250:
        raise ValueError("Job type and bounded idempotency_key are required")
    if not 1 <= max_attempts <= 20:
        raise ValueError("max_attempts must be in 1..20")
    row = conn.execute("""INSERT INTO jobs(job_type,idempotency_key,payload,priority,max_attempts)
        VALUES (%s,%s,%s::jsonb,%s,%s)
        ON CONFLICT(idempotency_key) DO UPDATE SET idempotency_key=EXCLUDED.idempotency_key
        RETURNING id""",
        (job_type, idempotency_key, json.dumps(payload), priority, max_attempts)).fetchone()
    return row[0]

def lease_next(conn, worker_id: str, lease_seconds: int = 120) -> dict | None:
    if not worker_id or len(worker_id) > 120 or not 5 <= lease_seconds <= 3600:
        raise ValueError("Invalid worker_id or lease_seconds")
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""SELECT id FROM jobs
               WHERE (state='queued' AND run_after <= now())
                  OR (state='running' AND lease_until < now() AND attempts < max_attempts)
               ORDER BY priority DESC, created_at ASC
               FOR UPDATE SKIP LOCKED LIMIT 1""")
            item = cur.fetchone()
            if item is None:
                return None
            cur.execute("""UPDATE jobs SET state='running', locked_by=%s,
                 lease_until=now() + (%s * INTERVAL '1 second'), attempts=attempts+1,
                 updated_at=now() WHERE id=%s RETURNING *""",
                 (worker_id, lease_seconds, item["id"]))
            return cur.fetchone()

def complete(conn, job_id: int, worker_id: str, result: dict | None = None) -> bool:
    row = conn.execute("""UPDATE jobs SET state='completed', result=%s::jsonb,
        lease_until=NULL, locked_by=NULL, updated_at=now()
        WHERE id=%s AND state='running' AND locked_by=%s RETURNING id""",
        (json.dumps(result or {}), job_id, worker_id)).fetchone()
    return bool(row)

def fail(conn, job_id: int, worker_id: str, message: str, retry_seconds: int = 60) -> bool:
    if not 0 <= retry_seconds <= 86400:
        raise ValueError("Invalid retry_seconds")
    row = conn.execute("""UPDATE jobs SET
        state=CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END,
        run_after=now()+(%s * INTERVAL '1 second'),
        last_error=%s, lease_until=NULL, locked_by=NULL, updated_at=now()
        WHERE id=%s AND state='running' AND locked_by=%s RETURNING id""",
        (retry_seconds, message[:500], job_id, worker_id)).fetchone()
    return bool(row)

def expire_exhausted_leases(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("""UPDATE jobs SET state='failed', locked_by=NULL,
            lease_until=NULL, last_error='Lease expired after last attempt',
            updated_at=now() WHERE state='running' AND lease_until < now()
            AND attempts >= max_attempts""")
        return cur.rowcount
