"""UA-MOD-18: explicit, bounded UAE qualification worker and scheduler.

No self-start, network/provider inference, Group C calculation, publication,
or automatic purging. The operator injects a TRUSTED server-side candidate
resolver with current DB-backed JoinedCandidatePins; absent discovery is
UNAVAILABLE and never inferred from queue receipt data.

Every snapshot write and corresponding lease acknowledgement occur inside
ONE PostgreSQL transaction. Expired/stale/cancelled workers roll back BOTH.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re
import signal
import threading
import time
from typing import Any, Callable, Protocol, Mapping

from .contracts import AnalyticsBoundaryError, PROJECT_RE, SHA256_RE
from .ops_observability import SafeEventCounters, classify_failure
from .dto import InputScope
from .joined_context import JoinedContextAssembler, JoinedCandidatePins, MAX_CANDIDATES
from .job_queue import (
    QualificationJobQueue, QueueLease, SUCCEED_SQL, _rows, _uuid,
)
from .snapshot_assembly import (
    ImmutableSnapshotAssembler, ImmutableSnapshotRepository, PersistedSnapshotReceipt,
)

WORKER_WIRE = "uae-qualification-worker/v1"
WORKER_LEASE_SQL = """
SELECT j.id::text AS job_id,j.project_id,
 j.request_id::text AS request_id,j.job_sha256,
 j.lease_token::text AS lease_token,j.lease_owner,
 j.lease_generation,j.leased_until,
 r.video_id,r.source_id,r.source_policy_version_id,
 r.content_kind,r.qdp_run_id::text AS qdp_run_id,
 r.qdp_input_sha256,r.request_sha256
FROM uae_qualification_jobs j
JOIN uae_qualification_requests r
 ON r.id=j.request_id AND r.project_id=j.project_id
WHERE j.project_id=%s AND j.id=%s::uuid
 AND j.state='leased' AND j.lease_token=%s::uuid
 AND j.lease_generation=%s AND j.lease_owner=%s
 AND j.leased_until>clock_timestamp()
 AND j.job_sha256=%s
 AND j.request_sha256=r.request_sha256
 AND uae_queue_request_ready(j.project_id,j.request_id)
FOR UPDATE OF j
"""
WORKER_CLOCK_SQL = "SELECT clock_timestamp() AS db_now"
TIMEOUT_SQL = (
    "SELECT set_config('statement_timeout',%s,true), "
    "set_config('lock_timeout',%s,true)"
)
_ERROR_RE = re.compile(r"[a-z][a-z0-9_]{1,79}\Z")


def _reject(code: str) -> None:
    raise AnalyticsBoundaryError(code,"UAE worker rejected: "+code)


def _utc(value: Any) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None:
        return None
    try:
        x=value.astimezone(timezone.utc)
        if x.utcoffset() is None:
            return None
        return x
    except (OverflowError,ValueError):
        return None


@dataclass(frozen=True, slots=True)
class WorkerLimits:
    project_id: str
    worker_id: str
    batch_size: int = 1
    max_jobs: int = 20
    lease_seconds: int = 60
    poll_seconds: float = 2.0
    retry_seconds: int = 30
    statement_timeout_ms: int = 30000
    lock_timeout_ms: int = 3000
    recovery_batch: int = 25

    def __post_init__(self) -> None:
        if (type(self.project_id) is not str
            or PROJECT_RE.fullmatch(self.project_id) is None
            or type(self.worker_id) is not str
            or re.fullmatch(r"[A-Za-z0-9_.:-]{3,120}",self.worker_id) is None
            or type(self.batch_size) is not int or not 1<=self.batch_size<=25
            or type(self.max_jobs) is not int or not 1<=self.max_jobs<=10000
            or type(self.lease_seconds) is not int or not 5<=self.lease_seconds<=900
            or type(self.poll_seconds) not in (int,float)
            or not 0.25<=self.poll_seconds<=60.0
            or type(self.retry_seconds) is not int or not 0<=self.retry_seconds<=3600
            or type(self.statement_timeout_ms) is not int
            or not 1000<=self.statement_timeout_ms<=120000
            or type(self.lock_timeout_ms) is not int
            or not 100<=self.lock_timeout_ms<=15000
            or type(self.recovery_batch) is not int
            or not 1<=self.recovery_batch<=100
            # A single DB attempt must fit inside an unexpired lease with
            # margin for return/update/commit, never depend on a background
            # renew thread that could silently lose the fence.
            or self.statement_timeout_ms+5000>=self.lease_seconds*1000):
            _reject("worker_limits_invalid")


class CandidateResolver(Protocol):
    """Server-side provider; must read current source-backed candidates.

    NEVER populated from arbitrary HTTP parameters or snapshot cache.
    The assembler itself revalidates each returned pin from PostgreSQL.
    """
    def resolve(self, db: Any, scope: InputScope
                ) -> tuple[JoinedCandidatePins, ...]: ...


@dataclass(frozen=True, slots=True)
class WorkOutcome:
    job_id: str
    state: str  # succeeded | retry | dead | lost
    reason_code: str | None = None
    snapshot_id: int | None = None

    def __post_init__(self) -> None:
        if (not _uuid(self.job_id)
            or self.state not in ("succeeded","retry","dead","lost")
            or (self.state=="succeeded") != (self.snapshot_id is not None)
            or (self.reason_code is not None and
                (type(self.reason_code) is not str
                 or _ERROR_RE.fullmatch(self.reason_code) is None))):
            _reject("worker_outcome_invalid")


class QualificationWorker:
    """Single-threaded lease consumer; concurrency belongs to PostgreSQL.

    connect_factory returns a context-managed, autocommit=True psycopg
    connection with dict_row. Each command uses a fresh/IDLE transaction.
    """

    def __init__(self, connect_factory: Callable[[],Any],
                 limits: WorkerLimits, *, resolver: CandidateResolver | None = None,
                 stop_event: threading.Event | None = None,
                 sleep: Callable[[float],None] = time.sleep,
                 telemetry: SafeEventCounters | None = None,
                 group_c_source_ports=None):
        if (not callable(connect_factory) or not isinstance(limits,WorkerLimits)
            or (resolver is not None and
                not callable(getattr(resolver,"resolve",None)))
            or not callable(sleep)
            or (telemetry is not None and not isinstance(telemetry,SafeEventCounters))):
            _reject("worker_configuration_invalid")
        self._connect=connect_factory
        self.limits=limits
        self.resolver=resolver
        self.stop=stop_event if stop_event is not None else threading.Event()
        self._sleep=sleep
        self.telemetry=telemetry
        if group_c_source_ports is not None:
            from intelligence.group_c.ports import GroupCPorts
            if not isinstance(group_c_source_ports,GroupCPorts):
                _reject("worker_group_c_ports_invalid")
        self.group_c_source_ports=group_c_source_ports

    def _observe(self,code:str)->None:
        # Safe fixed-event counters only, never job/project IDs or exception
        # strings. Instrumentation cannot change job or lease transitions.
        if self.telemetry is not None:
            try:self.telemetry.record(code)
            except Exception:pass

    @contextmanager
    def _session(self):
        try:
            db=self._connect()
            if not callable(getattr(db,"__enter__",None)) or not callable(
                getattr(db,"__exit__",None)
            ):
                _reject("worker_connection_context_required")
            with db as active:
                if getattr(getattr(active,"info",None),"transaction_status",1)!=0:
                    _reject("worker_idle_connection_required")
                yield active
        except AnalyticsBoundaryError:
            raise
        except Exception as exc:
            raise AnalyticsBoundaryError(
                "worker_storage_unavailable","UAE worker database unavailable"
            ) from exc

    @staticmethod
    def _require_lease(db:Any,lease:QueueLease) -> InputScope:
        records=_rows(db.execute(WORKER_LEASE_SQL,(
            lease.project_id,lease.job_id,lease.token,
            lease.generation,lease.worker,lease.job_sha256,
        )))
        if not records:
            _reject("worker_lease_lost")
        if len(records)!=1:
            _reject("worker_lease_ambiguous")
        r=records[0]
        if (r.get("request_id")!=lease.request_id
            or r.get("job_id")!=lease.job_id
            or r.get("project_id")!=lease.project_id
            or r.get("lease_generation")!=lease.generation
            or r.get("lease_token")!=lease.token
            or r.get("lease_owner")!=lease.worker
            or r.get("job_sha256")!=lease.job_sha256
            or type(r.get("request_sha256")) is not str
            or SHA256_RE.fullmatch(r["request_sha256"]) is None):
            _reject("worker_lease_identity_mismatch")
        try:
            return InputScope(
                project_id=r["project_id"],video_id=r["video_id"],
                source_id=r["source_id"],
                source_policy_version_id=r["source_policy_version_id"],
                content_kind=r["content_kind"],qdp_run_id=r["qdp_run_id"],
                qdp_input_sha256=r["qdp_input_sha256"],
            )
        except (KeyError,TypeError) as exc:
            raise AnalyticsBoundaryError(
                "worker_request_shape_invalid","UAE canonical request missing"
            ) from exc

    def _atomic_snapshot_and_ack(self,db:Any,lease:QueueLease
                                 ) -> PersistedSnapshotReceipt:
        if self.resolver is None:
            _reject("worker_candidate_discovery_unavailable")
        if getattr(getattr(db,"info",None),"transaction_status",1)!=0:
            _reject("worker_fresh_transaction_required")
        with db.transaction():
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            db.execute(TIMEOUT_SQL,(
                str(self.limits.statement_timeout_ms),
                str(self.limits.lock_timeout_ms),
            ))
            scope=self._require_lease(db,lease)  # FOR UPDATE job row
            # DB clock, not caller-provided timestamps or client host time.
            clocks=_rows(db.execute(WORKER_CLOCK_SQL))
            if len(clocks)!=1 or _utc(clocks[0].get("db_now")) is None:
                _reject("worker_clock_unavailable")
            as_of=_utc(clocks[0]["db_now"])
            candidates=self.resolver.resolve(db,scope)
            if (type(candidates) is not tuple
                or not 1<=len(candidates)<=MAX_CANDIDATES
                or any(not isinstance(c,JoinedCandidatePins)
                       or c.scope!=scope for c in candidates)):
                _reject("worker_candidate_discovery_unavailable")
            assembler=JoinedContextAssembler(
                db,scope,candidates,as_of=as_of,
                group_c_source_ports=self.group_c_source_ports,
            )
            draft=ImmutableSnapshotAssembler(assembler).assemble()
            if draft.decision.qualified or draft.decision.status.value=="qualified":
                _reject("worker_unapproved_qualification_denied")
            # Private repository _write only inside the same fenced SQL txn.
            # Historical write and job success always commit or roll back
            # together. NEVER call assemble_and_save() from here because
            # it opens a second independent transaction.
            receipt=ImmutableSnapshotRepository(
                assembler,lease.request_id
            )._write(db,draft)
            acknowledged=_rows(db.execute(SUCCEED_SQL,(
                receipt.snapshot_id,lease.project_id,lease.job_id,
                lease.token,lease.generation,lease.worker,receipt.snapshot_id,
            )))
            if len(acknowledged)!=1 or acknowledged[0].get("id")!=lease.job_id:
                _reject("worker_lease_lost")
            return receipt

    def process(self,lease:QueueLease) -> WorkOutcome:
        if not isinstance(lease,QueueLease) or lease.project_id!=self.limits.project_id:
            _reject("worker_wrong_project_lease")
        try:
            with self._session() as db:
                receipt=self._atomic_snapshot_and_ack(db,lease)
            self._observe("queue_succeeded")
            return WorkOutcome(lease.job_id,"succeeded",
                               snapshot_id=receipt.snapshot_id)
        except AnalyticsBoundaryError as exc:
            code=exc.code if _ERROR_RE.fullmatch(exc.code or "") else "worker_failed"
        except Exception:
            # Never reveal SQL exceptions, DSN, peer payload, personal data.
            code="worker_failed"
        self._observe(classify_failure(code))
        if code in ("worker_lease_lost","worker_lease_identity_mismatch"):
            return WorkOutcome(lease.job_id,"lost",reason_code=code)
        try:
            with self._session() as db:
                result=QualificationJobQueue(db).fail(
                    lease,error_code=code,
                    backoff_seconds=self.limits.retry_seconds,
                )
        except AnalyticsBoundaryError:
            # Lease remains recoverable after its DB expiry.
            self._observe("pg_retry")
            return WorkOutcome(lease.job_id,"lost",
                               reason_code="worker_storage_unavailable")
        if result in ("retry","dead"):
            self._observe("queue_retry" if result=="retry" else "queue_dead")
            return WorkOutcome(lease.job_id,result,reason_code=code)
        return WorkOutcome(lease.job_id,"lost",reason_code="worker_lease_lost")

    def recover(self) -> tuple[tuple[str,str],...]:
        with self._session() as db:
            rows=QualificationJobQueue(db).recover_expired(
                self.limits.project_id,limit=self.limits.recovery_batch,
            )
        for _ in rows:
            self._observe("queue_lease_recovered")
        return rows

    def run(self,*,watch:bool=False,recover_only:bool=False
            ) -> tuple[WorkOutcome,...]:
        if type(watch) is not bool or type(recover_only) is not bool:
            _reject("worker_mode_invalid")
        if self.resolver is None and not recover_only:
            # No candidate discovery built into UAE. Do not consume a job
            # merely to write an ungrounded 'blocked' snapshot or exhaust DLQ.
            _reject("worker_candidate_discovery_unavailable")
        outcomes:list[WorkOutcome]=[]
        while not self.stop.is_set():
            self.recover()  # bounded crash/TTL maintenance; no rights purge
            if recover_only:
                return tuple(outcomes)
            room=self.limits.max_jobs-len(outcomes)
            if room<=0:
                break
            with self._session() as db:
                leases=QualificationJobQueue(db).claim(
                    self.limits.project_id,self.limits.worker_id,
                    limit=min(room,self.limits.batch_size),
                    lease_seconds=self.limits.lease_seconds,
                )
            for lease in leases:
                # Claimed leases not processed after stop will expire and
                # be recovered, never silently acknowledged.
                if self.stop.is_set():
                    break
                outcomes.append(self.process(lease))
                if len(outcomes)>=self.limits.max_jobs:
                    break
            if len(outcomes)>=self.limits.max_jobs or not watch:
                # --once drains at most ONE bounded claim batch, and stops.
                break
            if not leases:
                # Signal-triggered stop wakes the normal watcher immediately.
                # Custom sleep remains injectible for deterministic unit tests.
                if self._sleep is time.sleep:
                    self.stop.wait(self.limits.poll_seconds)
                else:
                    self._sleep(self.limits.poll_seconds)
        return tuple(outcomes)


def main(argv: list[str] | None = None, *,\n         resolver: CandidateResolver | None = None) -> int:
    parser=argparse.ArgumentParser(
        prog="python -m intelligence.unified_analytics.worker",
        description="Explicit UAE job maintenance; no built-in candidate discovery"
    )
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once",action="store_true")
    mode.add_argument("--watch",action="store_true")
    mode.add_argument("--recover-only",action="store_true")
    parser.add_argument("--project",required=True)
    parser.add_argument("--worker",required=True)
    parser.add_argument("--limit",type=int,default=20)
    parser.add_argument("--batch",type=int,default=1)
    parser.add_argument("--lease-seconds",type=int,default=60)
    parser.add_argument("--poll-seconds",type=float,default=2)
    args=parser.parse_args(argv)
    # Explicit DSN variable. Never fall back to the application's CM_DB_DSN
    # or an implicit production database. No automatic startup/import.
    dsn=os.environ.get("CM_UAE_WORKER_DSN")
    if not dsn:
        parser.error("CM_UAE_WORKER_DSN must be set explicitly")
    if not args.recover_only and resolver is None:
        parser.error(
            "No trusted server-side candidate resolver installed; "
            "use --recover-only or inject a trusted resolver via main(..., resolver=...)"
        )
    try:
        from psycopg import connect
        from psycopg.rows import dict_row
        limits=WorkerLimits(args.project,args.worker,
            batch_size=args.batch,max_jobs=args.limit,
            lease_seconds=args.lease_seconds,poll_seconds=args.poll_seconds)
        stop=threading.Event()
        def stop_now(*_unused): stop.set()
        signal.signal(signal.SIGINT,stop_now)
        if hasattr(signal,"SIGTERM"):
            signal.signal(signal.SIGTERM,stop_now)
        runner=QualificationWorker(
            lambda:connect(dsn,autocommit=True,row_factory=dict_row),
            limits,resolver=resolver,stop_event=stop,
        )
        runner.run(watch=args.watch,recover_only=args.recover_only)
        return 0
    except AnalyticsBoundaryError as exc:
        parser.exit(2,"UAE worker unavailable: "+exc.code+"\n")
    return 2


if __name__=="__main__":
    raise SystemExit(main())


__all__=(
    "WORKER_WIRE","WorkerLimits","CandidateResolver","WorkOutcome",
    "QualificationWorker","WORKER_LEASE_SQL","WORKER_CLOCK_SQL",
    "TIMEOUT_SQL","main",
)
