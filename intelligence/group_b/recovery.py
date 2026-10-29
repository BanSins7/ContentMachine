"""GB-MOD-27 safe local crash recovery and resource snapshots.

No background process, automatic paid replay or charge release. Retrying an
expired LOCAL B5 lease occurs only via the normal fenced claim_local path.
An unknown provider charge ALWAYS remains reserved/uncertain pending an
independently authenticated invoice, even after worker cancellation.
"""
from __future__ import annotations

from dataclasses import dataclass

from .monitoring import (
    MonitoringDenied,ThroughputPolicy,ExecutionMetric,record_execution,
)
from .orchestrator import reap_expired_jobs,_uuid
from .integration.invalidation import INVALIDATED


@dataclass(frozen=True,slots=True)
class RecoveryReport:
    project_id:str
    run_id:str
    active_local:int
    expired_reclaimable:int
    expired_exhausted:int
    uncertain_paid:int
    paid_replay_authorized:bool=False


def _deny(code):
    raise MonitoringDenied(code)


def _scope(project_id,run_id,policy):
    if (type(project_id) is not str or not project_id or
        not _uuid(run_id) or not isinstance(policy,ThroughputPolicy)):
        _deny("recovery_identity_or_policy_invalid")


def read_recovery(conn,*,project_id,run_id,policy:ThroughputPolicy):
    """Project-scoped status only, without revealing provider text or media."""
    _scope(project_id,run_id,policy)
    run=conn.execute(
        """SELECT state FROM group_b_runs
           WHERE project_id=%s AND id=%s::uuid""",(project_id,run_id)
    ).fetchone()
    if run is None:
        _deny("recovery_run_not_found")
    counts=conn.execute(
        """SELECT
           count(*) FILTER(WHERE kind='local' AND state='running'
                            AND lease_until>now()),
           count(*) FILTER(WHERE kind='local' AND state='running'
                            AND lease_until<=now() AND attempts<max_attempts),
           count(*) FILTER(WHERE kind='local' AND state='running'
                            AND lease_until<=now() AND attempts>=max_attempts)
           FROM group_b_jobs WHERE project_id=%s AND run_id=%s::uuid""",
        (project_id,run_id)
    ).fetchone()
    paid=conn.execute(
        """SELECT count(*) FROM group_b_model_attempts
           WHERE project_id=%s AND run_id=%s::uuid
             AND charge_state IN ('submitted','uncertain')""",
        (project_id,run_id)
    ).fetchone()
    if counts is None or paid is None:
        _deny("recovery_lease_state_unavailable")
    return RecoveryReport(project_id,run_id,counts[0],counts[1],counts[2],
                          paid[0])


def recover_expired_run(conn,*,project_id,run_id,policy:ThroughputPolicy,
                        verifier):
    """Idempotently terminalize only exhaustion and write unknown-duration
    immutable metrics in the same DB transaction. Caller owns connection.
    A real current-rights operator must authorize this particular run.
    """
    _scope(project_id,run_id,policy)
    if not callable(getattr(conn,"transaction",None)):
        _deny("recovery_transaction_missing")
    permission=getattr(verifier,"verify_recovery",None)
    if not callable(permission):
        _deny("recovery_host_not_connected")
    with conn.transaction():
        # Serialize source rights checks with other run-state writers.
        row=conn.execute(
            """SELECT state FROM group_b_runs WHERE
                   project_id=%s AND id=%s::uuid FOR UPDATE""",
            (project_id,run_id)
        ).fetchone()
        if row is None:
            _deny("recovery_run_not_found")
        stale=conn.execute(
            """SELECT EXISTS(SELECT 1 FROM group_b_outbox
               WHERE project_id=%s AND run_id=%s::uuid AND event_type=%s)""",
            (project_id,run_id,INVALIDATED)
        ).fetchone()
        if (stale is None or stale[0] or
            row[0] in ("cancelled","failed","completed","needs_review")):
            _deny("recovery_old_or_terminal_version")
        rights=conn.execute(
            """SELECT s.status,s.can_analyze_media,
                      r.source_policy_version_id,p.is_active,
                      p.permissions
               FROM group_b_runs r
               JOIN sources s ON s.id=r.source_id
               LEFT JOIN source_policy_versions p
                 ON p.id=r.source_policy_version_id AND p.source_id=r.source_id
               WHERE r.project_id=%s AND r.id=%s::uuid
               FOR SHARE OF r,s""",
            (project_id,run_id)
        ).fetchone()
        if (rights is None or rights[0]!="enabled" or
            rights[1] is not True or
            (rights[2] is not None and
             (rights[3] is not True or
              type(rights[4]) is not dict or
              rights[4].get("can_analyze_media") is not True))):
            _deny("recovery_local_rights_revoked")
        if rights[2] is not None:
            # PostgreSQL cannot FOR SHARE a nullable LEFT JOIN side.
            # Re-lock the concrete policy row, and re-evaluate it under lock.
            p=conn.execute(
                """SELECT source_id,is_active,permissions
                   FROM source_policy_versions WHERE id=%s FOR SHARE""",
                (rights[2],)
            ).fetchone()
            source=conn.execute(
                """SELECT source_id FROM group_b_runs
                   WHERE project_id=%s AND id=%s::uuid""",
                (project_id,run_id)
            ).fetchone()
            if (p is None or source is None or p[0]!=source[0] or
                p[1] is not True or type(p[2]) is not dict or
                p[2].get("can_analyze_media") is not True):
                _deny("recovery_policy_revoked")
        try:
            admitted=permission(project_id,run_id,conn) is True
        except Exception:
            admitted=False
        if not admitted:
            _deny("recovery_rights_not_verified")
        summary=read_recovery(
            conn,project_id=project_id,run_id=run_id,policy=policy
        )
        if summary.expired_exhausted>policy.max_batch_jobs:
            _deny("recovery_batch_exceeds_limit")
        # No retries of paid work, no release of submitted/uncertain costs.
        repaired=reap_expired_jobs(conn,project_id=project_id,run_id=run_id)
        if repaired>policy.max_batch_jobs:
            _deny("recovery_batch_exceeds_limit")
        entries=conn.execute(
            """SELECT j.id::text,j.block,j.kind,j.lease_generation,
                      j.state
               FROM group_b_jobs j
               WHERE j.project_id=%s AND j.run_id=%s::uuid
                 AND j.state IN ('failed','blocked')
                 AND j.last_error_code='lease_exhausted'
                 AND EXISTS (
                   SELECT 1 FROM group_b_job_events e
                   WHERE e.project_id=j.project_id AND e.job_id=j.id
                     AND e.lease_generation=j.lease_generation
                     AND e.event_type='lease_expired'
                 )
                 AND NOT EXISTS (
                   SELECT 1 FROM group_b_execution_metrics m
                   WHERE m.project_id=j.project_id AND m.job_id=j.id
                     AND m.lease_generation=j.lease_generation
                 )
               ORDER BY j.id
               LIMIT %s""",
            (project_id,run_id,policy.max_batch_jobs+1)
        ).fetchall()
        if len(entries)>policy.max_batch_jobs:
            _deny("recovery_metric_batch_exceeds_limit")
        for job_id,block,kind,generation,state in entries:
            record_execution(conn,ExecutionMetric(
                project_id,run_id,job_id,block,kind,generation,
                "lease_expired",None,None,"lease_exhausted"
            ))
        return {
            "terminalized":repaired,
            "retained_uncertain_paid":summary.uncertain_paid,
            "expired_local_reclaimable":summary.expired_reclaimable,
            "new_unknown_duration_metrics":len(entries),
            "paid_auto_retry":False,
        }
