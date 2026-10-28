"""UA-MOD-28: bounded, privacy-preserving operational observations.

Internal, project-authorized, read-only postgres view; NEVER public API.
Existing UA080 evidence/invalidations, UA087 outbox and UA088 queue/job
events are source of truth. A historic 'qualified' is not live authorized.
No raw producer data, project/video/job/request IDs, token/DSN, log text,
labels from arbitrary reason_code or high-cardinality Prometheus tags.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from threading import Lock
from time import monotonic
from typing import Any, Mapping
from uuid import uuid4

from .contracts import AnalyticsBoundaryError, PROJECT_RE
from .readside_visibility import _utc

OPS_WIRE="uae-operational-observability/v1"
PORTS=("source_policy","d1","qdp","sre","group_a","group_b","group_c")
PORT_STATES=("complete","partial","missing","unavailable","revoked")
JOB_STATES=("pending","leased","retry","succeeded","dead","cancelled")
SNAPSHOT_STATES=("partial","blocked")
EVENT_CODES=(
    "queue_pending","queue_retry","queue_dead","queue_succeeded",
    "queue_lease_lost","queue_lease_recovered","pg_retry",
    "join_failed","stale_source","group_a_missing","group_b_missing",
    "group_c_missing","read_revoked","qualified_read_unavailable",
    "operation_denied",
)
EVENT_MAX=256
MAX_SAMPLE_ROWS=140
MAX_AGE_SECONDS=365*86400
CLOCK_SQL="SELECT clock_timestamp() AS db_now"
PROJECT_SQL="SELECT id FROM project_registry WHERE id=%s"
QUEUE_SQL="""
SELECT state,count(*)::bigint AS n,
 min(created_at) FILTER (WHERE state IN ('pending','retry')) AS oldest_waiting_at,
 count(*) FILTER (WHERE state='leased' AND leased_until<=%s::timestamptz)::bigint AS expired_leases,
 count(*) FILTER (WHERE attempts>1)::bigint AS retried_jobs,
 count(*) FILTER (WHERE state IN ('pending','retry') AND available_at>%s::timestamptz)::bigint AS delayed_jobs
FROM uae_qualification_jobs
WHERE project_id=%s GROUP BY state
"""
SNAPSHOT_SQL="""
SELECT status,count(*)::bigint AS n
FROM uae_unified_snapshots
WHERE project_id=%s AND status IN ('partial','blocked')
GROUP BY status
"""
EVIDENCE_SQL="""
SELECT e.port,e.state,e.checked,e.is_current,count(*)::bigint AS n
FROM uae_snapshot_evidence e
JOIN uae_unified_snapshots sn
  ON sn.project_id=e.project_id AND sn.id=e.snapshot_id
WHERE e.project_id=%s AND sn.status IN ('partial','blocked')
GROUP BY e.port,e.state,e.checked,e.is_current
"""
INVALIDATIONS_SQL="""
SELECT count(*)::bigint AS n
FROM uae_snapshot_invalidations WHERE project_id=%s
"""
OUTBOX_SQL="""
SELECT delivery_state,count(*)::bigint AS n
FROM uae_revision_outbox WHERE project_id=%s
GROUP BY delivery_state
"""
EVENTS_SQL="""
SELECT to_state,count(*)::bigint AS n
FROM uae_qualification_job_events WHERE project_id=%s
GROUP BY to_state
"""
# Project-scoped, fixed-column grouped statistics, no SELECT * or sensitive
# joins to sources/videos/creator/score provider tables.


def _deny(code:str)->None:
    raise AnalyticsBoundaryError(code,"UAE operations refused: "+code)


def _count(x:Any)->int:
    if type(x) is not int or not 0<=x<=2**63-1:
        _deny("ops_count_invalid")
    return x


def _grouped(db:Any,sql:str,args:tuple,allowed:tuple[str,...],
             key:str)->dict[str,int]:
    rows=db.execute(sql,args).fetchall()
    if type(rows) is not list or len(rows)>len(allowed):
        _deny("ops_storage_shape_invalid")
    out={name:0 for name in allowed}
    seen=set()
    for row in rows:
        if (not isinstance(row,Mapping) or
            row.get(key) not in out or row[key] in seen):
            _deny("ops_storage_shape_invalid")
        seen.add(row[key])
        out[row[key]]=_count(row.get("n"))
    return out


def _one(db:Any,sql:str,args:tuple)->dict|None:
    rows=db.execute(sql,args).fetchall()
    if type(rows) is not list or len(rows)>1 or any(
        not isinstance(x,Mapping) for x in rows
    ):
        _deny("ops_storage_shape_invalid")
    return dict(rows[0]) if rows else None


def _age(now:datetime,old:Any)->int|None:
    if old is None:return None
    t=_utc(old)
    if t is None or t>now:
        _deny("ops_clock_invalid")
    seconds=int((now-t).total_seconds())
    if not 0<=seconds<=MAX_AGE_SECONDS:
        _deny("ops_clock_invalid")
    return seconds


@dataclass(frozen=True,slots=True)
class OpsReport:
    """Project scoped ONLY in the authorization context, not output logs."""
    observed_at:str
    queue_states:dict[str,int]
    snapshot_diagnostics:dict[str,int]
    missing_ports:dict[str,int]
    partial_ports:dict[str,int]
    revoked_ports:dict[str,int]
    queue_transition_counts:dict[str,int]
    outbox_states:dict[str,int]
    queue_lag_seconds:int|None
    delayed_jobs:int
    expired_leases:int
    retried_jobs:int
    invalidated_snapshots:int
    source_stale_evidence:int
    read_revocation_events:int
    qualified_live_readable:bool=False
    schema_version:str=OPS_WIRE

    def __post_init__(self)->None:
        try:
            dt=datetime.fromisoformat(self.observed_at)
        except (ValueError,TypeError) as exc:
            raise AnalyticsBoundaryError("ops_report_invalid","Invalid snapshot time") from exc
        if _utc(dt) is None:
            _deny("ops_report_invalid")
        for field,names in (
            ("queue_states",JOB_STATES),
            ("snapshot_diagnostics",SNAPSHOT_STATES),
            ("missing_ports",PORTS),
            ("partial_ports",PORTS),
            ("revoked_ports",PORTS),
            ("queue_transition_counts",JOB_STATES),
            ("outbox_states",("pending","leased","delivered","failed")),
        ):
            value=getattr(self,field)
            if type(value) is not dict or tuple(value)!=names:
                _deny("ops_report_invalid")
            for number in value.values():_count(number)
        for key in ("delayed_jobs","expired_leases","retried_jobs",
                    "invalidated_snapshots","source_stale_evidence",
                    "read_revocation_events"):
            _count(getattr(self,key))
        if self.queue_lag_seconds is not None:
            _count(self.queue_lag_seconds)
        if (self.qualified_live_readable is not False
            or self.schema_version!=OPS_WIRE):
            _deny("ops_report_invalid")

    def safe_json(self)->str:
        """No project, job, request, media, SQL, DSN, custom label or token."""
        from dataclasses import asdict
        return json.dumps(asdict(self),ensure_ascii=False,sort_keys=True,
                          separators=(",",":"))


class ProjectOpsReader:
    """Trusted project-authorized DB connection, dict_row and IDLE txn.

    In one fresh PG17 REPEATABLE READ transaction reads all fixed limited
    diagnostics. No mutation, no raw evidence, no job-by-ID enumeration.
    """

    def __init__(self,conn:Any):
        if (conn is None or not callable(getattr(conn,"execute",None))
            or not callable(getattr(conn,"transaction",None))):
            _deny("ops_connection_required")
        self.db=conn

    def inspect(self,project_id:str)->OpsReport:
        if type(project_id) is not str or PROJECT_RE.fullmatch(project_id) is None:
            _deny("ops_project_invalid")
        db=self.db
        if getattr(getattr(db,"info",None),"transaction_status",1)!=0:
            _deny("ops_fresh_transaction_required")
        try:
            with db.transaction():
                db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                clock=_one(db,CLOCK_SQL,())
                now=_utc(clock.get("db_now")) if clock else None
                if now is None:_deny("ops_clock_invalid")
                if _one(db,PROJECT_SQL,(project_id,)) is None:
                    _deny("ops_project_unavailable")
                state_rows=db.execute(QUEUE_SQL,(now,now,project_id)).fetchall()
                if type(state_rows) is not list or len(state_rows)>len(JOB_STATES):
                    _deny("ops_storage_shape_invalid")
                q={name:0 for name in JOB_STATES}
                expired=delayed=retried=0
                oldest=None
                seen_states=set()
                for row in state_rows:
                    if (not isinstance(row,Mapping)
                        or row.get("state") not in q
                        or row.get("state") in seen_states):
                        _deny("ops_storage_shape_invalid")
                    state=row["state"]
                    seen_states.add(state)
                    q[state]=_count(row.get("n"))
                    expired+=_count(row.get("expired_leases"))
                    delayed+=_count(row.get("delayed_jobs"))
                    retried+=_count(row.get("retried_jobs"))
                    stamp=_utc(row.get("oldest_waiting_at")) if (
                        row.get("oldest_waiting_at") is not None
                    ) else None
                    if stamp is not None and (oldest is None or stamp<oldest):
                        oldest=stamp
                snapshots=_grouped(db,SNAPSHOT_SQL,(project_id,),
                                   SNAPSHOT_STATES,"status")
                evidences=db.execute(EVIDENCE_SQL,(project_id,)).fetchall()
                if type(evidences) is not list or len(evidences)>MAX_SAMPLE_ROWS:
                    _deny("ops_storage_shape_invalid")
                missing={name:0 for name in PORTS}
                partial={name:0 for name in PORTS}
                revoked={name:0 for name in PORTS}
                source_stale=0
                seen=set()
                for row in evidences:
                    if (not isinstance(row,Mapping) or row.get("port") not in PORTS
                        or row.get("state") not in PORT_STATES
                        or type(row.get("checked")) is not bool
                        or type(row.get("is_current")) is not bool):
                        _deny("ops_storage_shape_invalid")
                    key=(row["port"],row["state"],row["checked"],row["is_current"])
                    if key in seen:_deny("ops_storage_shape_invalid")
                    seen.add(key)
                    n=_count(row.get("n"))
                    if row["state"] in ("missing","unavailable"):
                        missing[row["port"]]+=n
                    if row["state"]=="partial" or not row["checked"]:
                        partial[row["port"]]+=n
                    if row["state"]=="revoked":
                        revoked[row["port"]]+=n
                    if row["port"]=="source_policy" and not row["is_current"]:
                        source_stale+=n
                inv=_one(db,INVALIDATIONS_SQL,(project_id,))
                if inv is None:_deny("ops_storage_shape_invalid")
                invalidations=_count(inv.get("n"))
                outbox=_grouped(db,OUTBOX_SQL,(project_id,),
                                ("pending","leased","delivered","failed"),
                                "delivery_state")
                events=_grouped(db,EVENTS_SQL,(project_id,),JOB_STATES,
                                "to_state")
                return OpsReport(
                    observed_at=now.isoformat(timespec="microseconds"),
                    queue_states=q,snapshot_diagnostics=snapshots,
                    missing_ports=missing,partial_ports=partial,
                    revoked_ports=revoked,queue_transition_counts=events,
                    outbox_states=outbox,queue_lag_seconds=_age(now,oldest),
                    delayed_jobs=_count(delayed),
                    expired_leases=_count(expired),
                    retried_jobs=_count(retried),
                    invalidated_snapshots=invalidations,
                    source_stale_evidence=_count(source_stale),
                    read_revocation_events=invalidations,
                )
        except AnalyticsBoundaryError:
            raise
        except Exception as exc:
            raise AnalyticsBoundaryError(
                "ops_storage_unavailable","UAE operations database unavailable",
            ) from exc


class SafeEventCounters:
    """Explicit opt-in in-process telemetry; fixed enums, bounded cardinality.

    No global accumulator or log by default. Callers can inject it into worker
    or other trusted internal facades. Increment is fail-safe: invalid inputs
    are discarded, never turned into raw labels.
    """
    def __init__(self):
        self._counts=Counter()
        self._latencies=Counter()
        self._lock=Lock()

    def record(self,event:str,*,duration_seconds:float|None=None)->None:
        if type(event) is not str or event not in EVENT_CODES:
            return
        bucket=None
        if duration_seconds is not None:
            if type(duration_seconds) not in (int,float):
                return
            from math import isfinite
            if not isfinite(duration_seconds) or duration_seconds<0:
                return
            bucket=("lt_10ms" if duration_seconds<.01 else
                    "lt_100ms" if duration_seconds<.1 else
                    "lt_1s" if duration_seconds<1 else
                    "lt_10s" if duration_seconds<10 else "gte_10s")
        with self._lock:
            if sum(self._counts.values())>=2**63-1:return
            self._counts[event]+=1
            if bucket:self._latencies[bucket]+=1

    def snapshot(self)->dict:
        with self._lock:
            return {
                "schema_version":OPS_WIRE,
                "events":{e:self._counts[e] for e in EVENT_CODES},
                "latency_buckets":{e:self._latencies[e] for e in
                    ("lt_10ms","lt_100ms","lt_1s","lt_10s","gte_10s")},
            }


def classify_failure(error_code:str)->str:
    """Map arbitrary internal error text to ONLY fixed safe categories."""
    if type(error_code) is not str:return "operation_denied"
    if error_code in ("worker_lease_lost","worker_lease_identity_mismatch",
                       "lease_expired"):
        return "queue_lease_lost"
    if error_code in ("queue_storage_unavailable","worker_storage_unavailable",
                       "ops_storage_unavailable"):
        return "pg_retry"
    if error_code in ("worker_candidate_discovery_unavailable",
                       "worker_request_shape_invalid","join_evidence_incomplete"):
        return "join_failed"
    if error_code in ("source_policy_inactive","source_stale",
                       "source_policy_revoked"):
        return "stale_source"
    if error_code in ("group_a_missing","group_b_missing","group_c_missing"):
        return error_code
    if error_code in ("visibility_revoked","source_retention_expired",
                       "read_revoked"):
        return "read_revoked"
    return "operation_denied"


def new_trace_id()->str:
    """Correlation ID generated by UAE, not from arbitrary raw request."""
    return uuid4().hex


__all__=(
    "OPS_WIRE","PORTS","JOB_STATES","EVENT_CODES",
    "QUEUE_SQL","PROJECT_SQL","SNAPSHOT_SQL","EVIDENCE_SQL",
    "INVALIDATIONS_SQL","OUTBOX_SQL","EVENTS_SQL",
    "OpsReport","ProjectOpsReader","SafeEventCounters",
    "classify_failure","new_trace_id",
)
