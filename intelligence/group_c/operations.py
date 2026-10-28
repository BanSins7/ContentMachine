"""GC-MOD-28: project-scoped observability and authorized fenced recovery.

No background scheduler, provider calls, raw C7 score reads, secret logging,
automated outbox lease replay or physical erase. Operator must supply a real
external IAM verifier before mutating jobs. All state transitions and immutable
recovery evidence are in one SERIALIZABLE PostgreSQL transaction.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Any, Callable, Mapping, Protocol
from uuid import UUID

from .contracts import ContractError, canonical_hash
from .d1 import _record
from .durable_worker import PostgreSQLGroupCWorker, WorkerPolicy, _GLOBAL_LOCK_KEY

OPS_VERSION="group-c-operations/v1"
_PROJECT=re.compile(r"[0-9a-f]{32}\Z")
_CODE=re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_LOG=logging.getLogger("contentmachine.group_c.operations")


def _project(v:Any)->str:
    if type(v) is not str or _PROJECT.fullmatch(v) is None:
        raise ContractError("invalid_project_id","Canonical project required")
    return v


def _uuid(value:Any)->str:
    if type(value) is not str:
        raise ContractError("invalid_operation_id","Canonical UUID required")
    try:
        v=UUID(value)
    except (ValueError,TypeError,AttributeError) as exc:
        raise ContractError("invalid_operation_id","Canonical UUID required") from exc
    if str(v)!=value:
        raise ContractError("invalid_operation_id","Canonical lowercase UUID required")
    return value


def _require_project(conn:Any,project_id:str)->None:
    if conn.execute(
        "SELECT 1 FROM project_registry WHERE id=%s",
        (project_id,),
    ).fetchone() is None:
        raise ContractError("project_not_found","Unknown project")


def _connection(conn:Any)->None:
    if getattr(conn,"autocommit",None) is not True:
        raise ContractError("transaction_contract_unavailable",
                            "Operations require dedicated autocommit PostgreSQL connection")


def _seconds(value:Any)->int|None:
    if value is None:
        return None
    if type(value) not in (Decimal,int,float) or value<0:
        return 0
    return int(value)


@dataclass(frozen=True)
class MonitoringPolicy:
    queue_warn_seconds:int=600
    queue_critical_seconds:int=3600
    inbox_warn_seconds:int=300
    inbox_critical_seconds:int=1800
    metric_stale_seconds:int=21600
    metric_critical_seconds:int=86400
    outbox_warn_seconds:int=300
    outbox_critical_seconds:int=1800

    def __post_init__(self):
        for field in ("queue_warn_seconds","queue_critical_seconds",
                      "inbox_warn_seconds","inbox_critical_seconds",
                      "metric_stale_seconds","metric_critical_seconds",
                      "outbox_warn_seconds","outbox_critical_seconds"):
            val=getattr(self,field)
            if type(val) is not int or not 30<=val<=2592000:
                raise ContractError("invalid_monitoring_policy",
                                    "Explicit positive bounded alert thresholds required")
        for a,b in (("queue_warn_seconds","queue_critical_seconds"),
                    ("inbox_warn_seconds","inbox_critical_seconds"),
                    ("metric_stale_seconds","metric_critical_seconds"),
                    ("outbox_warn_seconds","outbox_critical_seconds")):
            if getattr(self,a)>=getattr(self,b):
                raise ContractError("invalid_monitoring_policy","Warn must precede critical")


@dataclass(frozen=True)
class OperationalAlert:
    code:str
    severity:str
    observed_count:int
    age_seconds:int|None=None


@dataclass(frozen=True)
class ProjectPulse:
    project_id:str
    observed_at:str
    queued_jobs:int
    running_jobs:int
    expired_job_leases:int
    failed_jobs:int
    blocked_runs:int
    received_inbox:int
    inbox_conflicts:int
    pending_outbox:int
    expired_outbox_leases:int
    failed_outbox:int
    missing_metric_videos:int
    stale_metric_videos:int
    oldest_queue_seconds:int|None
    oldest_inbox_seconds:int|None
    oldest_outbox_seconds:int|None
    max_metric_age_seconds:int|None
    recent_recovery_operations:int
    alerts:tuple[OperationalAlert,...]
    version:str=OPS_VERSION


@dataclass(frozen=True)
class RecoveryReceipt:
    operation_id:str
    project_id:str
    recovered_job_ids:tuple[int,...]
    recovered_count:int
    request_sha256:str
    authority_sha256:str
    replayed:bool
    state:str="audited"
    version:str=OPS_VERSION


@dataclass(frozen=True)
class MaintenancePreview:
    project_id:str
    observed_at:str
    recommended_actions:tuple[str,...]
    blocked_automations:tuple[str,...]
    destructive_changes:int=0
    version:str=OPS_VERSION


def maintenance_for(pulse:ProjectPulse)->MaintenancePreview:
    """A cleanup/recovery plan is always read-only.

    Historic C7, signed reviews, cohort freezes, source policy proofs,
    incident audits and possibly delivered remote outbox events cannot be
    destroyed or replayed by a generic janitor.
    """
    if not isinstance(pulse,ProjectPulse):
        raise ContractError("invalid_monitoring_measurement","Typed pulse required")
    actions=[]
    if pulse.expired_job_leases:
        actions.append("recover_expired_jobs_with_external_signed_iam")
    if pulse.expired_outbox_leases:
        actions.append("inspect_analytics_ack_before_any_outbox_transition")
    if pulse.pending_outbox:
        actions.append("check_analytics_port_and_current_c7_rights")
    if pulse.failed_jobs or pulse.blocked_runs:
        actions.append("inspect_terminal_or_quality_failure")
    if pulse.received_inbox:
        actions.append("inspect_qdp_inbox_age_and_contract")
    if pulse.missing_metric_videos or pulse.stale_metric_videos:
        actions.append("review_source_permissions_and_observation_freshness")
    return MaintenancePreview(
        project_id=pulse.project_id,observed_at=pulse.observed_at,
        recommended_actions=tuple(actions),
        blocked_automations=(
            "no_automatic_replay_of_ambiguous_analytics_leases",
            "no_physical_erasure_without_external_legal_executor",
            "no_delete_of_signed_immutable_c7_review_or_cohort_evidence",
        ),
    )


class RecoveryAuthorityVerifier(Protocol):
    """External IAM verifies signed recovery authority, project/action/nonce."""
    def verify_recovery(
        self,conn:Any,*,project_id:str,operation_id:str,
        action:str,signed_assertion:str,request_sha256:str,
    ) -> Mapping[str,Any]|None: ...


def alerts_for(metrics:Mapping[str,Any],policy:MonitoringPolicy)->tuple[OperationalAlert,...]:
    """Deterministic alert derivation; never derive numeric C7/viral probabilities."""
    if not isinstance(policy,MonitoringPolicy):
        raise ContractError("invalid_monitoring_policy","Typed policy required")
    out:list[OperationalAlert]=[]
    def count(key:str)->int:
        v=metrics.get(key)
        if type(v) is not int or v<0:
            raise ContractError("invalid_monitoring_measurement",
                                "Invalid project-scoped operational count")
        return v
    def age(key:str)->int|None:
        v=metrics.get(key)
        if v is None:
            return None
        if type(v) is not int or v<0:
            raise ContractError("invalid_monitoring_measurement","Invalid observed duration")
        return v
    def immediate(code:str,key:str,severity:str="critical"):
        n=count(key)
        if n:
            out.append(OperationalAlert(code=code,severity=severity,observed_count=n))
    def aged(code:str,key:str,age_key:str,warn:int,critical:int):
        n=count(key)
        t=age(age_key)
        if n and t is not None and t>=warn:
            out.append(OperationalAlert(
                code=code,severity="critical" if t>=critical else "warning",
                observed_count=n,age_seconds=t,
            ))
    immediate("worker_lease_expired","expired_job_leases")
    immediate("outbox_lease_expired_manual_ack_required","expired_outbox_leases")
    immediate("worker_failed","failed_jobs")
    immediate("outbox_failed","failed_outbox")
    immediate("inbox_conflicting_provenance","inbox_conflicts")
    aged("queue_backlog","queued_jobs","oldest_queue_seconds",
         policy.queue_warn_seconds,policy.queue_critical_seconds)
    aged("inbox_unaccepted","received_inbox","oldest_inbox_seconds",
         policy.inbox_warn_seconds,policy.inbox_critical_seconds)
    aged("outbox_undelivered","pending_outbox","oldest_outbox_seconds",
         policy.outbox_warn_seconds,policy.outbox_critical_seconds)
    n=count("missing_metric_videos")
    if n:
        out.append(OperationalAlert(
            code="metric_observation_missing",severity="warning",observed_count=n,
        ))
    aged("metric_observation_stale","stale_metric_videos","max_metric_age_seconds",
         policy.metric_stale_seconds,policy.metric_critical_seconds)
    if count("blocked_runs"):
        out.append(OperationalAlert(
            code="blocked_runs_review_required",severity="warning",
            observed_count=count("blocked_runs"),
        ))
    return tuple(out)


def _elapsed(row:Mapping[str,Any],key:str)->int|None:
    return _seconds(row.get(key))


def _event(action:str,project_id:str,*,outcome:str,count:int=0,
           operation_id:str|None=None)->dict:
    """Static-key, redacted JSON audit line; never include signed assertions."""
    if action not in ("monitor","recover_expired_jobs"):
        raise ContractError("invalid_operation","Unsupported operation trace")
    if outcome not in ("observed","replayed","committed","rejected"):
        raise ContractError("invalid_operation","Unsupported trace outcome")
    if type(count) is not int or count<0:
        raise ContractError("invalid_operation","Invalid event count")
    return {
        "version":OPS_VERSION,
        "action":action,
        "project_fingerprint":sha256(project_id.encode("ascii")).hexdigest()[:16],
        "outcome":outcome,
        "count":count,
        "operation_id":operation_id,
    }


def _emit(observer:Callable[[Mapping[str,Any]],Any]|None,event:dict)->bool:
    """Monitoring delivery is best-effort; durable DB result is authoritative.

    Do not let a telemetry outage undo/turn an already committed recovery into
    a request error that might prompt users to repeat it under a new ID.
    """
    _LOG.info("group_c.operations %s",json.dumps(event,sort_keys=True))
    if observer is None:
        return False
    try:
        observer(dict(event))
    except Exception:
        _LOG.warning("group_c.operations telemetry_callback_unavailable")
        return False
    return True


class PostgreSQLGroupCOperations:
    def __init__(self,policy:MonitoringPolicy|None=None,
                 observer:Callable[[Mapping[str,Any]],Any]|None=None,
                 worker_policy:WorkerPolicy|None=None):
        if policy is not None and not isinstance(policy,MonitoringPolicy):
            raise ContractError("invalid_monitoring_policy","Typed policy required")
        if worker_policy is not None and not isinstance(worker_policy,WorkerPolicy):
            raise ContractError("invalid_worker_option","Typed worker policy required")
        if observer is not None and not callable(observer):
            raise ContractError("invalid_observer","Observer must be callable")
        self.policy=policy or MonitoringPolicy()
        self.worker=PostgreSQLGroupCWorker(worker_policy)
        self.observer=observer

    def snapshot(self,conn:Any,*,project_id:str)->ProjectPulse:
        """One project-scoped REPEATABLE READ, READ ONLY operational pulse.

        Counts are operational metadata, never permission to read score/frozen
        peer numeric evidence. IAM must be checked separately at HTTP/ops host.
        """
        _connection(conn)
        _project(project_id)
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            _require_project(conn,project_id)
            now=conn.execute("SELECT now()").fetchone()[0]
            j=_record(conn.execute(
                """SELECT
                  count(*) FILTER(WHERE state='queued') AS queued_jobs,
                  count(*) FILTER(WHERE state='running') AS running_jobs,
                  count(*) FILTER(WHERE state='running' AND lease_until<=now())
                    AS expired_job_leases,
                  count(*) FILTER(WHERE state='failed') AS failed_jobs,
                  extract(epoch FROM(now()-min(available_at)
                    FILTER(WHERE state='queued' AND available_at<=now())))
                    AS oldest_queue_seconds
                FROM group_c_jobs WHERE project_id=%s""",(project_id,),
            ))
            i=_record(conn.execute(
                """SELECT count(*) FILTER(WHERE state='received') AS received_inbox,
                          extract(epoch FROM(now()-min(received_at)
                            FILTER(WHERE state='received'))) AS oldest_inbox_seconds
                   FROM group_c_inbox WHERE project_id=%s""",(project_id,),
            ))
            c=conn.execute(
                """SELECT count(*) FROM group_c_inbox_conflicts cf
                   JOIN group_c_inbox ib ON ib.event_id=cf.event_id
                   WHERE ib.project_id=%s""",(project_id,),
            ).fetchone()[0]
            r=conn.execute(
                """SELECT count(*) FROM group_c_runs
                   WHERE project_id=%s AND state='blocked'""",(project_id,),
            ).fetchone()[0]
            o=_record(conn.execute(
                """SELECT
                  count(*) FILTER(WHERE state='pending') AS pending_outbox,
                  count(*) FILTER(WHERE state='leased' AND lease_until<=now())
                    AS expired_outbox_leases,
                  count(*) FILTER(WHERE state='failed') AS failed_outbox,
                  extract(epoch FROM(now()-min(created_at)
                    FILTER(WHERE state='pending'))) AS oldest_outbox_seconds
                 FROM group_c_outbox WHERE project_id=%s""",(project_id,),
            ))
            # Keep this operational: only count observations, do not reveal
            # metric values or promise live source eligibility. Current GC24
            # read remains the sole score/peer authorization boundary.
            m=_record(conn.execute(
                """SELECT
                  count(*) FILTER(WHERE latest.observed_at IS NULL)
                    AS missing_metric_videos,
                  count(*) FILTER(WHERE latest.observed_at IS NOT NULL
                    AND latest.observed_at < now()-
                      (%s::integer * interval '1 second'))
                    AS stale_metric_videos,
                  extract(epoch FROM(now()-min(latest.observed_at)))
                    AS max_metric_age_seconds
                 FROM sre_project_videos pv
                 LEFT JOIN LATERAL (
                   SELECT ms.observed_at FROM metric_snapshots ms
                   WHERE ms.video_id=pv.video_id
                     AND ms.source_metric_definition='platform_post_views'
                   ORDER BY ms.observed_at DESC,ms.id DESC LIMIT 1
                 ) latest ON TRUE
                 WHERE pv.project_id=%s""",
                (self.policy.metric_stale_seconds,project_id),
            ))
            n=conn.execute(
                """SELECT count(*) FROM group_c_recovery_operations
                   WHERE project_id=%s AND created_at>=now()-interval '24 hours'""",
                (project_id,),
            ).fetchone()[0]
            data={
                "queued_jobs":j["queued_jobs"],"running_jobs":j["running_jobs"],
                "expired_job_leases":j["expired_job_leases"],
                "failed_jobs":j["failed_jobs"],"blocked_runs":r,
                "received_inbox":i["received_inbox"],"inbox_conflicts":c,
                "pending_outbox":o["pending_outbox"],
                "expired_outbox_leases":o["expired_outbox_leases"],
                "failed_outbox":o["failed_outbox"],
                "missing_metric_videos":m["missing_metric_videos"],
                "stale_metric_videos":m["stale_metric_videos"],
                "oldest_queue_seconds":_elapsed(j,"oldest_queue_seconds"),
                "oldest_inbox_seconds":_elapsed(i,"oldest_inbox_seconds"),
                "oldest_outbox_seconds":_elapsed(o,"oldest_outbox_seconds"),
                "max_metric_age_seconds":_elapsed(m,"max_metric_age_seconds"),
            }
            pulse=ProjectPulse(
                project_id=project_id,observed_at=now.isoformat(),
                recent_recovery_operations=n,
                alerts=alerts_for(data,self.policy),**data,
            )
        _emit(self.observer,_event("monitor",project_id,outcome="observed",
                                   count=len(pulse.alerts)))
        return pulse

    def maintenance_preview(self,conn:Any,*,project_id:str)->MaintenancePreview:
        """Current read-only cleanup plan. No DELETE or replay."""
        return maintenance_for(self.snapshot(conn,project_id=project_id))

    def recover_expired_jobs(
        self,conn:Any,*,project_id:str,operation_id:str,
        signed_assertion:str,verifier:RecoveryAuthorityVerifier|None,
    )->RecoveryReceipt:
        """Explicit administrative recovery, fenced and idempotent.

        A verified, project-scoped human action + original worker global
        advisory claim lock prevents bypassing worker concurrency budgets.
        No replay/cleanup of possibly externally delivered outbox records.
        """
        _connection(conn)
        _project(project_id)
        _uuid(operation_id)
        if type(signed_assertion) is not str or len(signed_assertion)<32:
            raise ContractError("recovery_auth_unavailable",
                                "Trusted signed administrator action required")
        if verifier is None or not callable(getattr(verifier,"verify_recovery",None)):
            raise ContractError("recovery_auth_unavailable",
                                "External recovery IAM required")
        request={
            "version":OPS_VERSION,"action":"recover_expired_jobs",
            "project_id":project_id,"operation_id":operation_id,
        }
        digest=canonical_hash(request)
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            _require_project(conn,project_id)
            authority=verifier.verify_recovery(
                conn,project_id=project_id,operation_id=operation_id,
                action="recover_expired_jobs",signed_assertion=signed_assertion,
                request_sha256=digest,
            )
            if (not isinstance(authority,Mapping)
                    or authority.get("verified") is not True
                    or authority.get("project_id")!=project_id
                    or authority.get("action")!="recover_expired_jobs"
                    or authority.get("operation_id")!=operation_id
                    or type(authority.get("principal_id")) is not str
                    or len(authority["principal_id"].strip())<3
                    or type(authority.get("authentication_event_id")) is not str
                    or len(authority["authentication_event_id"].strip())<8):
                raise ContractError("recovery_auth_unavailable",
                                    "Unverified project recovery authority")
            authority_hash=canonical_hash({
                "version":OPS_VERSION,
                "project_id":project_id,
                "operation_id":operation_id,
                "principal_id":authority["principal_id"],
                "authentication_event_id":authority["authentication_event_id"],
            })
            # Same lock as all worker claims. Replay is checked under it, then
            # exact recovered job IDs are captured inside the very same txn.
            conn.execute("SELECT pg_advisory_xact_lock(%s)",(_GLOBAL_LOCK_KEY,))
            old=_record(conn.execute(
                """SELECT project_id,request_sha256,authority_sha256,
                          recovered_job_ids,recovered_count
                   FROM group_c_recovery_operations
                   WHERE id=%s::uuid""",(operation_id,),
            ))
            if old is not None:
                if (old["project_id"]!=project_id
                        or old["request_sha256"]!=digest
                        or old["authority_sha256"]!=authority_hash):
                    raise ContractError("recovery_operation_conflict",
                                        "Recovery ID already bound to different authority")
                receipt=RecoveryReceipt(
                    operation_id=operation_id,project_id=project_id,
                    recovered_job_ids=tuple(old["recovered_job_ids"]),
                    recovered_count=old["recovered_count"],
                    request_sha256=digest,authority_sha256=authority_hash,
                    replayed=True,
                )
            else:
                ids:list[int]=[]
                count=self.worker._recover_locked(conn,project_id,ids)
                if count!=len(ids):
                    raise ContractError("recovery_audit_conflict",
                                        "Fenced recovery count mismatch")
                conn.execute(
                    """INSERT INTO group_c_recovery_operations(
                         id,project_id,action,request_sha256,authority_sha256,
                         recovered_job_ids,recovered_count)
                       VALUES(%s::uuid,%s,'recover_expired_jobs',%s,%s,%s::jsonb,%s)""",
                    (operation_id,project_id,digest,authority_hash,
                     json.dumps(ids,separators=(",",":")),count),
                )
                receipt=RecoveryReceipt(
                    operation_id=operation_id,project_id=project_id,
                    recovered_job_ids=tuple(ids),recovered_count=count,
                    request_sha256=digest,authority_sha256=authority_hash,
                    replayed=False,
                )
        _emit(self.observer,_event(
            "recover_expired_jobs",project_id,
            outcome="replayed" if receipt.replayed else "committed",
            count=receipt.recovered_count,operation_id=operation_id,
        ))
        return receipt
