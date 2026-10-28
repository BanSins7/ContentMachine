"""GC-MOD-21: bounded durable Group C score worker with strict lease fencing.

All operations use existing GC03 group_c_jobs/group_c_runs durable SQL.
No external scheduler, daemon, provider, LLM, QDP ACK, or Analytics delivery.
Only score jobs are processed; other job kinds are reserved for later stages.

Crucial invariant: a fenced worker holds group_c_jobs row FOR UPDATE while
GC20 C7 recomputes AND commits the score + receipt + run/job completion in
the very same SERIALIZABLE transaction. No stale token may write a score.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from .c7_persistence import (
    C7PersistenceApprovalVerifier, PostgreSQLC7Persistence, PersistedC7Receipt,
)
from .contracts import ContractError
from .d1 import _record
from .ports import GroupCPorts

WORKER_VERSION = "group-c-durable-worker/v1"
_ID = re.compile(r"[0-9a-f]{32}\Z")
_WORKER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}\Z")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_GLOBAL_LOCK_KEY = 82931674021


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ContractError("invalid_worker_option", f"{name} must be a bounded integer")
    return value


def _identity(project_id: str, worker_id: str | None = None) -> None:
    if type(project_id) is not str or _ID.fullmatch(project_id) is None:
        raise ContractError("invalid_project_id", "Canonical 32-hex project_id required")
    if worker_id is not None and (type(worker_id) is not str
                               or _WORKER.fullmatch(worker_id) is None):
        raise ContractError("invalid_worker_id", "Nonblank bounded worker identity required")


def _uuid(value: str, name: str) -> str:
    if type(value) is not str:
        raise ContractError("invalid_worker_lease", name + " must be canonical UUID")
    try:
        identifier = UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ContractError("invalid_worker_lease", name + " invalid UUID") from exc
    if str(identifier) != value:
        raise ContractError("invalid_worker_lease", name + " noncanonical UUID")
    return value


def _db(conn: Any) -> None:
    if getattr(conn, "autocommit", None) is not True:
        raise ContractError("transaction_contract_unavailable",
                            "Use a dedicated autocommit PG17 connection")


@dataclass(frozen=True)
class WorkerPolicy:
    max_running_per_project: int = 2
    max_running_global: int = 16
    lease_seconds: int = 180
    retry_base_seconds: int = 5
    retry_max_seconds: int = 300
    recovery_batch: int = 128

    def __post_init__(self):
        _integer(self.max_running_per_project, "max_running_per_project", 1, 256)
        _integer(self.max_running_global, "max_running_global", 1, 1024)
        _integer(self.lease_seconds, "lease_seconds", 10, 3600)
        _integer(self.retry_base_seconds, "retry_base_seconds", 1, 3600)
        _integer(self.retry_max_seconds, "retry_max_seconds", 1, 86400)
        _integer(self.recovery_batch, "recovery_batch", 1, 1000)
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ContractError("invalid_worker_option", "Retry max must be >= base")


def retry_delay_seconds(attempts: int, policy: WorkerPolicy) -> int:
    """Deterministic exponential capped retry; no random jitter/float."""
    if not isinstance(policy, WorkerPolicy):
        raise ContractError("invalid_worker_option", "Typed WorkerPolicy required")
    _integer(attempts, "attempts", 1, 100)
    # Avoid giant powers and keep repeatable scheduling for audit/tests.
    power = min(attempts - 1, 30)
    return min(policy.retry_max_seconds, policy.retry_base_seconds * (1 << power))


@dataclass(frozen=True)
class WorkerLease:
    job_id: int
    project_id: str
    run_id: str
    video_id: int
    worker_id: str
    token: str
    generation: int
    attempt: int
    expires_at: datetime
    schema_version: str = WORKER_VERSION


@dataclass(frozen=True)
class WorkerCompletion:
    lease: WorkerLease
    score: PersistedC7Receipt
    schema_version: str = WORKER_VERSION
    state: str = "completed"


@dataclass(frozen=True)
class WorkerFailure:
    job_id: int
    state: str
    error_code: str
    next_attempt_at: datetime | None
    schema_version: str = WORKER_VERSION


class PostgreSQLGroupCWorker:
    """Explicit, caller-driven worker; local DB queue, no managed background task."""

    def __init__(self, policy: WorkerPolicy | None = None):
        if policy is not None and not isinstance(policy, WorkerPolicy):
            raise ContractError("invalid_worker_option", "Typed WorkerPolicy required")
        self.policy = policy or WorkerPolicy()

    def _recover_locked(self, conn: Any, project_id: str) -> int:
        """Reclaim expired leases under row locks; never touch an active worker."""
        rows = conn.execute(
            """SELECT id,attempts,max_attempts,run_id
                 FROM group_c_jobs
                WHERE project_id=%s AND state='running'
                  AND lease_until <= clock_timestamp()
                ORDER BY lease_until,id
                LIMIT %s FOR UPDATE SKIP LOCKED""",
            (project_id, self.policy.recovery_batch),
        ).fetchall()
        for job_id, attempts, max_attempts, run_id in rows:
            # A stale worker becomes fenced out immediately. Expiry at
            # equality is expired, no second worker can retain old token.
            terminal = attempts >= max_attempts
            wait = retry_delay_seconds(attempts, self.policy) if not terminal else 0
            conn.execute(
                """UPDATE group_c_jobs
                      SET state=%s,lease_token=NULL,lease_until=NULL,
                          worker_id=NULL,heartbeat_at=NULL,
                          lease_generation=lease_generation+1,
                          error_code='lease_expired',
                          available_at=clock_timestamp()+(%s*interval '1 second'),
                          updated_at=clock_timestamp()
                    WHERE id=%s AND project_id=%s AND state='running'""",
                ("failed" if terminal else "queued",wait,job_id,project_id),
            )
            if terminal:
                conn.execute(
                    """UPDATE group_c_runs SET state='failed',
                       reason_code='lease_expired',updated_at=clock_timestamp()
                     WHERE id=%s AND project_id=%s
                       AND state IN ('queued','running','partial')""",
                    (run_id,project_id),
                )
            else:
                conn.execute(
                    """UPDATE group_c_runs SET state='queued',reason_code='lease_expired',
                       updated_at=clock_timestamp()
                     WHERE id=%s AND project_id=%s AND state IN ('running','partial')""",
                    (run_id,project_id),
                )
        return len(rows)

    def recover_expired(self, conn: Any, *, project_id: str) -> int:
        _db(conn)
        _identity(project_id)
        with conn.transaction():
            return self._recover_locked(conn,project_id)

    def claim(
        self, conn: Any, *, project_id: str, worker_id: str,
    ) -> WorkerLease | None:
        """Claim one score job with SKIP LOCKED and global/project budgets.

        Advisory transaction lock serializes ALL claim budgets across projects.
        A busy/locked job is skipped, never assigned twice.
        """
        _db(conn)
        _identity(project_id,worker_id)
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)",(_GLOBAL_LOCK_KEY,))
            self._recover_locked(conn,project_id)
            global_running, project_running = conn.execute(
                """SELECT COUNT(*) FILTER (WHERE state='running'
                          AND lease_until>clock_timestamp()),
                          COUNT(*) FILTER (WHERE state='running'
                          AND lease_until>clock_timestamp()
                          AND project_id=%s)
                   FROM group_c_jobs""",
                (project_id,),
            ).fetchone()
            if (global_running >= self.policy.max_running_global
                    or project_running >= self.policy.max_running_per_project):
                return None
            row = _record(conn.execute(
                """SELECT j.id,j.run_id,j.video_id,j.attempts,j.max_attempts
                     FROM group_c_jobs j
                     JOIN group_c_runs r ON r.id=j.run_id
                       AND r.project_id=j.project_id AND r.video_id=j.video_id
                    WHERE j.project_id=%s AND j.job_kind='score'
                      AND j.state='queued' AND j.available_at<=clock_timestamp()
                      AND j.attempts<j.max_attempts
                      AND r.state IN ('queued','running','partial')
                      AND NOT EXISTS (
                          SELECT 1 FROM group_c_jobs active
                           WHERE active.project_id=j.project_id
                             AND active.run_id=j.run_id
                             AND active.id<>j.id
                             AND active.state='running'
                             AND active.lease_until>clock_timestamp()
                      )
                    ORDER BY j.available_at,j.id
                    LIMIT 1 FOR UPDATE OF j SKIP LOCKED""",
                (project_id,),
            ))
            if row is None:
                return None
            token = str(uuid4())
            claimed = _record(conn.execute(
                """UPDATE group_c_jobs
                      SET state='running',
                          attempts=attempts+1,
                          lease_generation=lease_generation+1,
                          lease_token=%s::uuid,
                          lease_until=clock_timestamp()+(%s*interval '1 second'),
                          heartbeat_at=clock_timestamp(),
                          worker_id=%s,error_code=NULL,
                          updated_at=clock_timestamp()
                    WHERE id=%s AND project_id=%s AND state='queued'
                    RETURNING id,run_id,video_id,attempts,lease_generation,lease_until""",
                (token,self.policy.lease_seconds,worker_id,row["id"],project_id),
            ))
            if claimed is None:
                raise ContractError("worker_claim_conflict","Lost selected job claim")
            run = _record(conn.execute(
                """UPDATE group_c_runs SET state='running',reason_code=NULL,
                          updated_at=clock_timestamp()
                    WHERE id=%s AND project_id=%s
                      AND state IN ('queued','running','partial')
                    RETURNING id""",(row["run_id"],project_id),
            ))
            if run is None:
                raise ContractError("worker_claim_conflict","Run no longer claimable")
            return WorkerLease(
                job_id=claimed["id"],project_id=project_id,
                run_id=str(claimed["run_id"]),video_id=claimed["video_id"],
                worker_id=worker_id,token=token,
                generation=claimed["lease_generation"],
                attempt=claimed["attempts"],expires_at=claimed["lease_until"],
            )

    @staticmethod
    def _lease(lease: WorkerLease) -> None:
        if not isinstance(lease,WorkerLease):
            raise ContractError("invalid_worker_lease","Typed WorkerLease required")
        _identity(lease.project_id,lease.worker_id)
        _integer(lease.job_id,"job_id",1,2**63-1)
        _integer(lease.video_id,"video_id",1,2**63-1)
        _integer(lease.generation,"generation",1,2**63-1)
        _integer(lease.attempt,"attempt",1,100)
        _uuid(lease.token,"lease_token")
        _uuid(lease.run_id,"run_id")
        if not isinstance(lease.expires_at,datetime) or lease.expires_at.tzinfo is None:
            raise ContractError("invalid_worker_lease","Timezone-aware lease expiration required")

    def heartbeat(self, conn: Any, *, lease: WorkerLease) -> datetime:
        _db(conn)
        self._lease(lease)
        row = _record(conn.execute(
            """UPDATE group_c_jobs SET
                  heartbeat_at=clock_timestamp(),
                  lease_until=clock_timestamp()+(%s*interval '1 second'),
                  updated_at=clock_timestamp()
                WHERE id=%s AND project_id=%s AND run_id=%s::uuid
                  AND video_id=%s AND state='running'
                  AND worker_id=%s AND lease_token=%s::uuid
                  AND lease_generation=%s
                  AND lease_until>clock_timestamp()
                  AND EXISTS (SELECT 1 FROM group_c_runs r
                       WHERE r.id=group_c_jobs.run_id
                         AND r.state='running')
                RETURNING lease_until""",
            (self.policy.lease_seconds,lease.job_id,lease.project_id,
             lease.run_id,lease.video_id,lease.worker_id,lease.token,lease.generation),
        ))
        if row is None:
            raise ContractError("worker_lease_lost","Heartbeat denied: stale/expired/cancelled lease")
        return row["lease_until"]

    def fail(
        self, conn: Any, *, lease: WorkerLease,
        error_code: str, retryable: bool,
    ) -> WorkerFailure:
        _db(conn)
        self._lease(lease)
        if (type(error_code) is not str or _CODE.fullmatch(error_code) is None
                or type(retryable) is not bool):
            raise ContractError("invalid_worker_option","Stable error code/retryable bool required")
        with conn.transaction():
            job = self._locked_job(conn,lease)
            if job is None:
                raise ContractError("worker_lease_lost","Cannot fail/retry a stale lease")
            can_retry = retryable and job["attempts"] < job["max_attempts"]
            seconds = retry_delay_seconds(job["attempts"],self.policy) if can_retry else 0
            state = "queued" if can_retry else "failed"
            updated = _record(conn.execute(
                """UPDATE group_c_jobs SET state=%s,worker_id=NULL,
                       lease_token=NULL,lease_until=NULL,heartbeat_at=NULL,
                       lease_generation=lease_generation+1,error_code=%s,
                       available_at=clock_timestamp()+(%s*interval '1 second'),
                       updated_at=clock_timestamp()
                    WHERE id=%s AND project_id=%s
                      AND state='running' AND lease_token=%s::uuid
                      AND lease_generation=%s
                    RETURNING available_at""",
                (state,error_code,seconds,lease.job_id,lease.project_id,
                 lease.token,lease.generation),
            ))
            if updated is None:
                raise ContractError("worker_lease_lost","Failure lost fence")
            conn.execute(
                """UPDATE group_c_runs SET state=%s,reason_code=%s,
                          updated_at=clock_timestamp()
                    WHERE project_id=%s AND id=%s::uuid
                      AND state='running'""",
                ("queued" if can_retry else "failed",error_code,
                 lease.project_id,lease.run_id),
            )
            return WorkerFailure(
                job_id=lease.job_id,state=state,error_code=error_code,
                next_attempt_at=updated["available_at"] if can_retry else None,
            )

    def cancel(
        self, conn: Any, *, project_id: str, job_id: int,
        reason_code: str = "operator_cancelled",
    ) -> bool:
        """Cancel queued or running jobs. Atomic row lock prevents late commit."""
        _db(conn)
        _identity(project_id)
        _integer(job_id,"job_id",1,2**63-1)
        if type(reason_code) is not str or not _CODE.fullmatch(reason_code):
            raise ContractError("invalid_worker_option","Canonical cancellation reason required")
        with conn.transaction():
            job = _record(conn.execute(
                """SELECT id,run_id,state FROM group_c_jobs
                    WHERE project_id=%s AND id=%s FOR UPDATE""",
                (project_id,job_id),
            ))
            if job is None:
                raise ContractError("worker_job_missing","Exact project-scoped job missing")
            if job["state"]=="cancelled":
                return False
            if job["state"] in ("completed","failed"):
                raise ContractError("worker_terminal","Cannot cancel completed/failed job")
            conn.execute(
                """UPDATE group_c_jobs SET state='cancelled',
                       lease_token=NULL,lease_until=NULL,worker_id=NULL,
                       heartbeat_at=NULL,lease_generation=lease_generation+1,
                       error_code=%s,updated_at=clock_timestamp()
                    WHERE project_id=%s AND id=%s""",
                (reason_code,project_id,job_id),
            )
            run = conn.execute(
                """UPDATE group_c_runs SET state='cancelled',
                          reason_code=%s,updated_at=clock_timestamp()
                    WHERE project_id=%s AND id=%s AND state<>'completed'""",
                (reason_code,project_id,job["run_id"]),
            )
            if run.rowcount!=1:
                raise ContractError("worker_terminal","Cannot cancel completed run")
            return True

    def _locked_job(self, conn: Any, lease: WorkerLease) -> dict | None:
        return _record(conn.execute(
            """SELECT j.id,j.run_id,j.video_id,j.attempts,j.max_attempts,
                      j.lease_until
                 FROM group_c_jobs j
                WHERE j.id=%s AND j.project_id=%s
                  AND j.run_id=%s::uuid AND j.video_id=%s
                  AND j.state='running' AND j.worker_id=%s
                  AND j.lease_token=%s::uuid AND j.lease_generation=%s
                  AND j.lease_until>clock_timestamp()
                FOR UPDATE""",
            (lease.job_id,lease.project_id,lease.run_id,lease.video_id,
             lease.worker_id,lease.token,lease.generation),
        ))

    def execute_score(
        self, conn: Any, *, lease: WorkerLease,
        review_request_id: str,
        approval_verifier: C7PersistenceApprovalVerifier | None,
        ports: GroupCPorts | None = None,
    ) -> WorkerCompletion:
        """Fenced GC20 score + run/job completion, exactly one commit.

        The score writer is called INSIDE this serializable transaction,
        without opening a second transaction. Any error rolls back C7 and
        both job/run completion. Recovery will fence stale attempts.
        """
        _db(conn)
        self._lease(lease)
        _uuid(review_request_id,"review_request_id")
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            job = self._locked_job(conn,lease)
            if job is None:
                raise ContractError("worker_lease_lost","Lease stale, cancelled or expired")
            run = _record(conn.execute(
                """SELECT id,project_id,video_id,source_id,
                          snapshot_id,previous_snapshot_id,published_metadata_id,
                          calculation_config_id,source_policy_version_id,
                          state
                     FROM group_c_runs
                    WHERE id=%s::uuid AND project_id=%s AND video_id=%s
                    FOR UPDATE""",
                (lease.run_id,lease.project_id,lease.video_id),
            ))
            if run is None or run["state"]!="running":
                raise ContractError("worker_run_invalid","Run not active or wrong project")
            if any(run[k] is None for k in (
                    "snapshot_id","previous_snapshot_id","published_metadata_id",
                    "calculation_config_id","source_policy_version_id",
            )):
                raise ContractError("worker_run_incomplete","QDP run is not score-ready")
            current = _record(conn.execute(
                """SELECT source_metric_version FROM metric_snapshots
                   WHERE id=%s AND video_id=%s""",
                (run["snapshot_id"],lease.video_id),
            ))
            if (current is None or type(current["source_metric_version"]) is not str
                    or not current["source_metric_version"].strip()):
                raise ContractError("incompatible_metric_version",
                                    "Missing pinned D1 metric version")
            result = PostgreSQLC7Persistence().persist(
                conn,project_id=lease.project_id,video_id=lease.video_id,
                snapshot_id=run["snapshot_id"],
                previous_snapshot_id=run["previous_snapshot_id"],
                metadata_id=run["published_metadata_id"],
                source_id=run["source_id"],
                source_policy_version_id=run["source_policy_version_id"],
                metric_version=current["source_metric_version"],
                config_id=run["calculation_config_id"],
                review_request_id=review_request_id,
                approval_verifier=approval_verifier,ports=ports,
                transaction_managed=True,
            )
            # Re-check the clock even though the DB lock prevents another
            # claim; an expired lease is NEVER allowed to commit score.
            if conn.execute(
                "SELECT lease_until>clock_timestamp() FROM group_c_jobs WHERE id=%s",
                (lease.job_id,),
            ).fetchone()[0] is not True:
                raise ContractError("worker_lease_lost","Lease expired during score calculation")
            score = _record(conn.execute(
                """SELECT id,creator_cohort_id,niche_cohort_id,prior_cohort_id,
                          score_status
                     FROM calculated_metrics WHERE id=%s""",
                (result.calculated_metric_id,),
            ))
            if score is None or score["score_status"]!="complete":
                raise ContractError("worker_score_missing","GC20 must commit a complete C7 score")
            changed = conn.execute(
                """UPDATE group_c_runs
                      SET state='completed',reason_code=NULL,
                          creator_cohort_id=%s,niche_cohort_id=%s,
                          prior_cohort_id=%s,calculated_metric_id=%s,
                          completed_at=clock_timestamp(),updated_at=clock_timestamp()
                    WHERE id=%s::uuid AND project_id=%s
                      AND state='running'""",
                (score["creator_cohort_id"],score["niche_cohort_id"],
                 score["prior_cohort_id"],result.calculated_metric_id,
                 lease.run_id,lease.project_id),
            )
            if changed.rowcount != 1:
                raise ContractError("worker_run_invalid","Score run state was changed")
            changed = conn.execute(
                """UPDATE group_c_jobs SET state='completed',lease_token=NULL,
                       lease_until=NULL,worker_id=NULL,heartbeat_at=NULL,
                       error_code=NULL,updated_at=clock_timestamp()
                    WHERE id=%s AND project_id=%s AND state='running'
                      AND worker_id=%s AND lease_token=%s::uuid
                      AND lease_generation=%s
                      AND lease_until>clock_timestamp()""",
                (lease.job_id,lease.project_id,lease.worker_id,
                 lease.token,lease.generation),
            )
            if changed.rowcount!=1:
                raise ContractError("worker_lease_lost","Fence changed before job completion")
            return WorkerCompletion(lease=lease,score=result)

    @staticmethod
    def _transient(exc: Exception) -> bool:
        """Only known PG errors/dependency delays; never loop on bad proof."""
        if isinstance(exc,ContractError):
            return exc.code in {
                "dependency_unavailable","source_temporarily_unavailable",
                "transaction_retryable","worker_claim_conflict",
            }
        sqlstate=getattr(exc,"sqlstate",None)
        return sqlstate in ("40001","40P01","55P03","08000","08003","08006")

    def run_once(
        self, conn: Any, *, project_id: str, worker_id: str,
        review_request_id: str,
        approval_verifier: C7PersistenceApprovalVerifier | None,
        ports: GroupCPorts | None = None,
    ) -> WorkerCompletion | WorkerFailure | None:
        """Optional one-step orchestration, no background loop/sleep.

        On a caught error an ACTIVE lease is marked retriable or terminal;
        if lost/connection broken, do not falsely report a transition.
        """
        lease=self.claim(conn,project_id=project_id,worker_id=worker_id)
        if lease is None:
            return None
        try:
            return self.execute_score(
                conn,lease=lease,review_request_id=review_request_id,
                approval_verifier=approval_verifier,ports=ports,
            )
        except Exception as exc:
            if getattr(conn,"closed",False):
                raise
            retryable=self._transient(exc)
            code = (exc.code if isinstance(exc,ContractError)
                    else "pg_retryable" if retryable else "score_execution_failed")
            if not _CODE.fullmatch(code):
                code="score_execution_failed"
            # If this fails due to expiry/cancellation, propagate lease-lost.
            return self.fail(conn,lease=lease,error_code=code,retryable=retryable)
