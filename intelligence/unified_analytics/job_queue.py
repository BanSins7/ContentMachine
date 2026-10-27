"""UA-MOD-17: PostgreSQL-backed, fail-closed qualification job queue.

Queue admission re-reads current source rights, active source policy,
project membership and three verified UAE QDP handoff receipts. A receipt
is NOT SRE/A/B/C producer approval. No job performs analysis, publishes
content or creates qualified snapshots. UA-MOD-18 owns worker execution.

Requires psycopg connection with dict_row and IDLE transaction status.
The caller supplies a project-authorized DB session; IDs are not an
authorization mechanism. Never attach this repository to a public route.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID
import re

from .contracts import AnalyticsBoundaryError, PROJECT_RE, SHA256_RE, canonical_hash

QUEUE_WIRE = "uae-qualification-job/v1"
_KEY_RE = re.compile(r"[A-Za-z0-9_.:-]{8,180}\Z")
_BATCH_RE = re.compile(r"[A-Za-z0-9_.:-]{1,120}\Z")
_WORKER_RE = re.compile(r"[A-Za-z0-9_.:-]{3,120}\Z")
_ERROR_RE = re.compile(r"[a-z][a-z0-9_]{1,79}\Z")
MAX_BATCH = 100
MAX_CLAIM = 100

INSERT_SQL = """
INSERT INTO uae_qualification_jobs(
 project_id,request_id,idempotency_key,batch_key,request_sha256,
 job_sha256,priority,max_attempts
)
SELECT r.project_id,r.id,%s,%s,r.request_sha256,%s,%s,%s
FROM uae_qualification_requests r
WHERE r.project_id=%s AND r.id=%s::uuid AND r.request_sha256=%s
 AND uae_queue_request_ready(r.project_id,r.id)
ON CONFLICT DO NOTHING
RETURNING id::text AS id,job_sha256,state
"""
EXISTING_SQL = """
SELECT id::text AS id,job_sha256,state
FROM uae_qualification_jobs
WHERE project_id=%s
 AND (idempotency_key=%s OR (request_id=%s::uuid AND batch_key=%s))
ORDER BY created_at,id
"""
CLAIM_SQL = """
WITH pick AS (
 SELECT j.id FROM uae_qualification_jobs j
 WHERE j.project_id=%s AND j.state IN ('pending','retry')
  AND j.available_at<=clock_timestamp() AND j.attempts<j.max_attempts
  AND uae_queue_request_ready(j.project_id,j.request_id)
 ORDER BY j.priority DESC,j.available_at,j.created_at,j.id
 FOR UPDATE OF j SKIP LOCKED LIMIT %s
)
UPDATE uae_qualification_jobs j SET
 state='leased',attempts=j.attempts+1,
 lease_generation=j.lease_generation+1,
 lease_token=gen_random_uuid(),lease_owner=%s,
 leased_until=clock_timestamp()+make_interval(secs=>%s),
 updated_at=clock_timestamp(),last_error_code=NULL
FROM pick WHERE j.id=pick.id
RETURNING j.id::text AS id,j.project_id,
 j.request_id::text AS request_id,j.job_sha256,
 j.lease_token::text AS lease_token,j.lease_generation,
 j.lease_owner,j.leased_until,j.attempts,j.max_attempts
"""
RENEW_SQL = """
UPDATE uae_qualification_jobs j SET
 leased_until=clock_timestamp()+make_interval(secs=>%s),
 updated_at=clock_timestamp()
WHERE j.project_id=%s AND j.id=%s::uuid
 AND j.state='leased' AND j.lease_token=%s::uuid
 AND j.lease_generation=%s AND j.lease_owner=%s
 AND j.leased_until>clock_timestamp()
 AND uae_queue_request_ready(j.project_id,j.request_id)
RETURNING j.id::text AS id,j.project_id,
 j.request_id::text AS request_id,j.job_sha256,
 j.lease_token::text AS lease_token,j.lease_generation,
 j.lease_owner,j.leased_until,j.attempts,j.max_attempts
"""
SUCCEED_SQL = """
UPDATE uae_qualification_jobs j SET
 state='succeeded',snapshot_id=%s,lease_token=NULL,lease_owner=NULL,
 leased_until=NULL,finished_at=clock_timestamp(),updated_at=clock_timestamp(),
 last_error_code=NULL
WHERE j.project_id=%s AND j.id=%s::uuid
 AND j.state='leased' AND j.lease_token=%s::uuid
 AND j.lease_generation=%s AND j.lease_owner=%s
 AND j.leased_until>clock_timestamp()
 AND uae_queue_request_ready(j.project_id,j.request_id)
 AND EXISTS (
  SELECT 1 FROM uae_unified_snapshots s
  WHERE s.project_id=j.project_id AND s.request_id=j.request_id
   AND s.id=%s AND s.status IN ('partial','blocked')
   AND NOT EXISTS (
    SELECT 1 FROM uae_snapshot_invalidations i
    WHERE i.project_id=s.project_id AND i.snapshot_id=s.id
   )
 )
RETURNING j.id::text AS id
"""
FAIL_SQL = """
UPDATE uae_qualification_jobs j SET
 state=CASE WHEN j.attempts>=j.max_attempts THEN 'dead' ELSE 'retry' END,
 available_at=clock_timestamp()+make_interval(secs=>%s),
 lease_token=NULL,lease_owner=NULL,leased_until=NULL,
 finished_at=CASE WHEN j.attempts>=j.max_attempts
                  THEN clock_timestamp() ELSE NULL END,
 last_error_code=%s,updated_at=clock_timestamp()
WHERE j.project_id=%s AND j.id=%s::uuid
 AND j.state='leased' AND j.lease_token=%s::uuid
 AND j.lease_generation=%s AND j.lease_owner=%s
 AND j.leased_until>clock_timestamp()
RETURNING j.state
"""
CANCEL_SQL = """
UPDATE uae_qualification_jobs SET
 state='cancelled',lease_token=NULL,lease_owner=NULL,leased_until=NULL,
 finished_at=clock_timestamp(),updated_at=clock_timestamp()
WHERE project_id=%s AND id=%s::uuid
 AND state IN ('pending','retry','leased')
RETURNING id::text AS id
"""
RECOVER_SQL = """
WITH expired AS (
 SELECT j.id FROM uae_qualification_jobs j
 WHERE j.project_id=%s AND j.state='leased'
   AND j.leased_until<=clock_timestamp()
 ORDER BY j.leased_until,j.id
 FOR UPDATE OF j SKIP LOCKED LIMIT %s
)
UPDATE uae_qualification_jobs j SET
 state=CASE WHEN j.attempts>=j.max_attempts THEN 'dead' ELSE 'retry' END,
 available_at=clock_timestamp(),
 lease_token=NULL,lease_owner=NULL,leased_until=NULL,
 finished_at=CASE WHEN j.attempts>=j.max_attempts
                  THEN clock_timestamp() ELSE NULL END,
 last_error_code='lease_expired',updated_at=clock_timestamp()
FROM expired WHERE j.id=expired.id
RETURNING j.id::text AS id,j.state
"""
DEAD_SQL = """
SELECT id::text AS id,request_id::text AS request_id,
 batch_key,attempts,max_attempts,last_error_code,finished_at
FROM uae_qualification_jobs
WHERE project_id=%s AND state='dead'
ORDER BY finished_at DESC,id LIMIT %s
"""


def _deny(code: str) -> None:
    raise AnalyticsBoundaryError(code, "UAE job queue denied: " + code)


def _uuid(v: Any) -> bool:
    if type(v) is not str:
        return False
    try:
        return str(UUID(v)) == v
    except (ValueError, TypeError, AttributeError):
        return False


def _project(v: Any) -> bool:
    return type(v) is str and PROJECT_RE.fullmatch(v) is not None


def _rows(cursor: Any) -> list[dict]:
    result = cursor.fetchall()
    if type(result) is not list or any(not isinstance(x, Mapping) for x in result):
        _deny("queue_dict_row_required")
    return [dict(v) for v in result]


@dataclass(frozen=True, slots=True)
class QueueSpec:
    project_id: str
    request_id: str
    request_sha256: str
    idempotency_key: str
    batch_key: str
    priority: int = 0
    max_attempts: int = 4

    def __post_init__(self) -> None:
        if (not _project(self.project_id)
            or not _uuid(self.request_id)
            or type(self.request_sha256) is not str
            or SHA256_RE.fullmatch(self.request_sha256) is None
            or type(self.idempotency_key) is not str
            or _KEY_RE.fullmatch(self.idempotency_key) is None
            or type(self.batch_key) is not str
            or _BATCH_RE.fullmatch(self.batch_key) is None
            or type(self.priority) is not int or not -10 <= self.priority <= 10
            or type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 8):
            _deny("queue_spec_invalid")

    @property
    def job_sha256(self) -> str:
        return canonical_hash({
            "wire": QUEUE_WIRE, "project": self.project_id,
            "request": self.request_id, "request_sha": self.request_sha256,
            "key": self.idempotency_key, "batch": self.batch_key,
            "priority": self.priority, "max_attempts": self.max_attempts,
        })


@dataclass(frozen=True, slots=True)
class QueueReceipt:
    job_id: str
    state: str  # created|replayed
    job_sha256: str


@dataclass(frozen=True, slots=True)
class QueueLease:
    project_id: str
    job_id: str
    request_id: str
    job_sha256: str
    token: str
    generation: int
    worker: str
    leased_until: datetime
    attempts: int
    max_attempts: int

    @classmethod
    def from_row(cls, row: dict) -> "QueueLease":
        try:
            obj = cls(
                row["project_id"],row["id"],row["request_id"],
                row["job_sha256"],row["lease_token"],
                row["lease_generation"],row["lease_owner"],
                row["leased_until"],row["attempts"],row["max_attempts"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AnalyticsBoundaryError(
                "queue_lease_shape_invalid","UAE queue DB lease malformed"
            ) from exc
        if (not _project(obj.project_id) or not _uuid(obj.job_id)
            or not _uuid(obj.request_id) or not _uuid(obj.token)
            or type(obj.job_sha256) is not str
            or SHA256_RE.fullmatch(obj.job_sha256) is None
            or type(obj.worker) is not str or
            _WORKER_RE.fullmatch(obj.worker) is None
            or type(obj.generation) is not int or obj.generation<1
            or type(obj.attempts) is not int or obj.attempts<1
            or type(obj.max_attempts) is not int or obj.attempts>obj.max_attempts
            or type(obj.leased_until) is not datetime
            or obj.leased_until.tzinfo is None):
            _deny("queue_lease_shape_invalid")
        return obj


class QualificationJobQueue:
    """Transactional queue facade. No IO other than injected PostgreSQL."""

    def __init__(self, conn: Any):
        if (conn is None or not callable(getattr(conn, "execute", None))
            or not callable(getattr(conn, "transaction", None))):
            _deny("queue_connection_required")
        self._conn = conn

    def _transaction(self, operation: Any) -> Any:
        db = self._conn
        if getattr(getattr(db, "info", None), "transaction_status", 1) != 0:
            _deny("queue_fresh_transaction_required")
        try:
            with db.transaction():
                db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                return operation(db)
        except AnalyticsBoundaryError:
            raise
        except Exception as exc:
            raise AnalyticsBoundaryError(
                "queue_storage_unavailable", "UAE qualification queue transaction failed"
            ) from exc

    @staticmethod
    def _enqueue_one(db: Any, spec: QueueSpec) -> QueueReceipt:
        rows = _rows(db.execute(INSERT_SQL, (
            spec.idempotency_key,spec.batch_key,spec.job_sha256,
            spec.priority,spec.max_attempts,
            spec.project_id,spec.request_id,spec.request_sha256,
        )))
        if rows:
            return QueueReceipt(rows[0]["id"],"created",spec.job_sha256)
        existing = _rows(db.execute(EXISTING_SQL, (
            spec.project_id,spec.idempotency_key,spec.request_id,spec.batch_key,
        )))
        if existing:
            if len(existing)!=1 or existing[0]["job_sha256"]!=spec.job_sha256:
                _deny("queue_identity_collision")
            return QueueReceipt(existing[0]["id"],"replayed",spec.job_sha256)
        _deny("queue_authoritative_request_unavailable")

    def enqueue(self, spec: QueueSpec) -> QueueReceipt:
        if not isinstance(spec, QueueSpec):
            _deny("queue_spec_required")
        return self._transaction(lambda db:self._enqueue_one(db,spec))

    def enqueue_batch(self, specs: tuple[QueueSpec, ...]) -> tuple[QueueReceipt, ...]:
        """Atomic 1..100 chunk enqueue; no half-committed batch."""
        if (type(specs) is not tuple or not 1 <= len(specs) <= MAX_BATCH
            or any(not isinstance(x, QueueSpec) for x in specs)
            or len({x.project_id for x in specs})!=1):
            _deny("queue_batch_invalid")
        return self._transaction(
            lambda db: tuple(self._enqueue_one(db, spec) for spec in specs)
        )

    def claim(self, project_id: str, worker: str, *,
              limit: int = 1, lease_seconds: int = 60) -> tuple[QueueLease, ...]:
        if (not _project(project_id)
            or type(worker) is not str or _WORKER_RE.fullmatch(worker) is None
            or type(limit) is not int or not 1 <= limit <= MAX_CLAIM
            or type(lease_seconds) is not int or not 5 <= lease_seconds <= 900):
            _deny("queue_claim_invalid")
        return self._transaction(lambda db: tuple(
            QueueLease.from_row(row) for row in _rows(db.execute(
                CLAIM_SQL,(project_id,limit,worker,lease_seconds)
            ))
        ))

    def renew(self, lease: QueueLease, *, lease_seconds: int = 60) -> QueueLease | None:
        if (not isinstance(lease, QueueLease)
            or type(lease_seconds) is not int or not 5<=lease_seconds<=900):
            _deny("queue_renew_invalid")
        def work(db: Any) -> QueueLease | None:
            rows=_rows(db.execute(RENEW_SQL,(
                lease_seconds,lease.project_id,lease.job_id,lease.token,
                lease.generation,lease.worker,
            )))
            return QueueLease.from_row(rows[0]) if rows else None
        return self._transaction(work)

    def complete(self, lease: QueueLease, *, snapshot_id: int) -> bool:
        """Only persisted nonqualified, noninvalidated snapshot on SAME request."""
        if (not isinstance(lease, QueueLease)
            or type(snapshot_id) is not int or snapshot_id<=0):
            _deny("queue_completion_invalid")
        return self._transaction(lambda db:bool(_rows(db.execute(
            SUCCEED_SQL,(
                snapshot_id,lease.project_id,lease.job_id,lease.token,
                lease.generation,lease.worker,snapshot_id,
            )
        ))))

    def fail(self, lease: QueueLease, *, error_code: str,
             backoff_seconds: int = 30) -> str | None:
        if (not isinstance(lease,QueueLease)
            or type(error_code) is not str or _ERROR_RE.fullmatch(error_code) is None
            or type(backoff_seconds) is not int
            or not 0<=backoff_seconds<=86400):
            _deny("queue_failure_invalid")
        def work(db: Any) -> str | None:
            rows=_rows(db.execute(FAIL_SQL,(
                backoff_seconds,error_code,lease.project_id,lease.job_id,
                lease.token,lease.generation,lease.worker,
            )))
            return rows[0]["state"] if rows else None
        return self._transaction(work)

    def cancel(self, project_id: str, job_id: str) -> bool:
        if not _project(project_id) or not _uuid(job_id):
            _deny("queue_cancel_invalid")
        return self._transaction(lambda db:bool(_rows(db.execute(
            CANCEL_SQL,(project_id,job_id)
        ))))

    def recover_expired(self, project_id: str, *, limit: int = 100
                        ) -> tuple[tuple[str,str], ...]:
        if (not _project(project_id) or type(limit) is not int
            or not 1<=limit<=MAX_CLAIM):
            _deny("queue_recover_invalid")
        return self._transaction(lambda db:tuple(
            (row["id"],row["state"]) for row in _rows(
                db.execute(RECOVER_SQL,(project_id,limit))
            )
        ))

    def dead_letters(self, project_id: str, *, limit: int = 50
                     ) -> tuple[dict, ...]:
        if (not _project(project_id) or type(limit) is not int
            or not 1<=limit<=100):
            _deny("queue_dlq_invalid")
        return self._transaction(lambda db:tuple(
            _rows(db.execute(DEAD_SQL,(project_id,limit)))
        ))


__all__ = (
    "QUEUE_WIRE","QueueSpec","QueueReceipt","QueueLease",
    "QualificationJobQueue","INSERT_SQL","EXISTING_SQL","CLAIM_SQL",
    "RENEW_SQL","SUCCEED_SQL","FAIL_SQL","CANCEL_SQL",
    "RECOVER_SQL","DEAD_SQL",
)
