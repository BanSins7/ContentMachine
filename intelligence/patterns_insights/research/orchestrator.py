"""PIE-MOD-33 durable, project-scoped orchestration over pie_jobs/pie_outbox.

The worker can only claim explicitly classified safe deterministic tasks.
Paid LLM work is parked in blocked/manual state: never claim, retry or send
from the queue. Model dispatch remains ONLY the stage29 claim-once boundary.
All durable lifecycle/lease/checkpoint/outbox writes share a DB transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Protocol
from uuid import UUID
import json
import re

from ..contracts import PIEBoundaryError,canonical_hash

_SCHEMA='pie-job-event/v1'
_CHECKPOINT='pie-job-checkpoint/v1'
_SHA=re.compile(r'[0-9a-f]{64}\Z')
_ACTOR=re.compile(r'[A-Za-z0-9_.:@-]{1,120}\Z')
_PROJECT=re.compile(r'[0-9a-f]{32}\Z')
_TASKS={
    'dataset_snapshot':'dataset',
    'pattern_discovery':'discovery',
    'pattern_validation':'validation',
    'insight_deterministic':'insight',
    'insight_verification':'insight',
    'review_internal':'review',
    'source_invalidation':'invalidation',
    'paid_model_manual_handoff':'insight',
}
_TRANSIENT=frozenset(('transient_internal','upstream_unavailable','source_not_ready'))
_FAILURES=_TRANSIENT|frozenset(('invalid_source','rights_denied','validation_failed',
                               'policy_blocked','unknown_failure'))


def _fail(code:str,detail:str)->None:
    raise PIEBoundaryError(code,detail)


def _uuid(v)->bool:
    if type(v) is not str:return False
    try:return str(UUID(v))==v.lower()
    except (ValueError,TypeError,AttributeError):return False


def _actor(v)->bool:return type(v) is str and bool(_ACTOR.fullmatch(v))


def _project(v)->bool:return type(v) is str and bool(_PROJECT.fullmatch(v))


def _hash(v)->bool:return type(v) is str and bool(_SHA.fullmatch(v))


def _safe_type(v)->bool:return type(v) is str and v in _TASKS


def _json(v:dict)->str:
    return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


class JobAuthority(Protocol):
    """Host-authenticated current scoped rights; fake is NOT authentication."""
    def require_current_job_access(self,project_id:str,actor_id:str,
                                   operation:str)->bool:...


def _authorize(authority:JobAuthority,project:str,actor:str,operation:str)->None:
    if (not _project(project) or not _actor(actor)
        or authority is None
        or not callable(getattr(authority,'require_current_job_access',None))):
        _fail('job_authority_unavailable','Current scoped operator identity required')
    try:ok=authority.require_current_job_access(project,actor,operation)
    except Exception:ok=False
    if ok is not True:
        _fail('job_access_denied','No current project workflow permission')


@dataclass(frozen=True,slots=True)
class JobIntent:
    project_id:str
    run_id:str
    request_id:str
    task_code:str
    input_sha256:str
    max_attempts:int=3

    def __post_init__(self):
        if (not _project(self.project_id) or not _uuid(self.run_id)
            or not _uuid(self.request_id) or not _safe_type(self.task_code)
            or not _hash(self.input_sha256)
            or type(self.max_attempts) is not int or not 1<=self.max_attempts<=20
            or (self.task_code=='paid_model_manual_handoff' and self.max_attempts!=1)):
            _fail('invalid_job_intent','Bounded scoped immutable execution metadata required')

    @property
    def idempotency_key(self)->str:
        return canonical_hash({'v':'pie-job-idempotency/v1',
                               'project_id':self.project_id,'run_id':self.run_id,
                               'request_id':self.request_id})

    @property
    def spec_sha256(self)->str:
        return canonical_hash({'v':'pie-job-spec/v1','project_id':self.project_id,
                               'run_id':self.run_id,'task_code':self.task_code,
                               'input_sha256':self.input_sha256,
                               'max_attempts':self.max_attempts})


@dataclass(frozen=True,slots=True)
class JobReceipt:
    project_id:str
    run_id:str
    job_id:str
    task_code:str
    state:str
    idempotent:bool
    automatic_paid_dispatch:bool=False


@dataclass(frozen=True,slots=True)
class LeasedJob:
    project_id:str
    run_id:str
    job_id:str
    task_code:str
    input_sha256:str
    fence_token:int
    attempts:int
    max_attempts:int
    leased_by:str
    lease_until:datetime
    may_issue_paid_request:bool=False


@dataclass(frozen=True,slots=True)
class JobTransition:
    project_id:str
    job_id:str
    state:str
    fence_token:int
    event_sha256:str
    retry_after_seconds:int|None=None
    can_publish:bool=False


class DeterministicTaskHandler(Protocol):
    """Only idempotent work; external writes MUST also be fenced by lease."""
    def run_deterministic(self,lease:LeasedJob)->str:...


def _event(connection,project:str,run_id:str,job_id:str,
           code:str,fence:int,actor:str,*,detail_sha:str|None=None)->str:
    """Parent job row must already be locked by caller."""
    latest=connection.execute(
        """SELECT event_seq,event_sha256 FROM pie_job_orchestration_events
           WHERE project_id=%s AND job_id=%s ORDER BY event_seq DESC LIMIT 1""",
        (project,job_id)).fetchone()
    seq=1 if latest is None else latest[0]+1
    parent=None if latest is None else latest[1]
    if seq>100000:
        _fail('job_event_limit','Bounded immutable event chain')
    d={'schema_version':_SCHEMA,'project_id':project,'run_id':run_id,
       'job_id':job_id,'event_seq':seq,'prev_event_sha256':parent,
       'event_code':code,'fence_token':fence,'actor_id':actor,
       'detail_sha256':detail_sha,
       'can_publish':False,'may_issue_paid_request':False}
    event_sha=canonical_hash(d)
    from psycopg.types.json import Jsonb
    stored=connection.execute(
        """INSERT INTO pie_job_orchestration_events
           (project_id,job_id,event_seq,prev_event_sha256,event_sha256,
            event_code,fence_token,payload)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING event_sha256""",
        (project,job_id,seq,parent,event_sha,code,fence,Jsonb(d))).fetchone()
    if stored is None or stored[0]!=event_sha:
        _fail('job_event_conflict','Immutable event was not recorded')
    # Same-transaction outbox: at-least-once consumers must dedupe event_key.
    key=f'pie-job:{job_id}:{seq}'
    out={'schema_version':'pie-job-outbox/v1',
         'project_id':project,'run_id':run_id,'job_id':job_id,
         'event_seq':seq,'event_sha256':event_sha,'event_code':code,
         'can_publish':False,'may_issue_paid_request':False}
    written=connection.execute(
        """INSERT INTO pie_outbox
           (project_id,run_id,event_key,event_type,payload_sha256,payload)
           VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
        (project,run_id,key,'pie_job_'+code,canonical_hash(out),Jsonb(out))
    ).fetchone()
    if written is None:
        _fail('job_outbox_unavailable','No durable outbox receipt')
    return event_sha


def enqueue_job(connection,intent:JobIntent,actor_id:str,
                authority:JobAuthority)->JobReceipt:
    if type(intent) is not JobIntent:
        _fail('invalid_job_intent','Typed request required')
    _authorize(authority,intent.project_id,actor_id,'enqueue')
    paid=intent.task_code=='paid_model_manual_handoff'
    state='blocked' if paid else 'queued'
    policy='manual_paid_only' if paid else 'safe_deterministic'
    try:
        with connection.transaction():
            run=connection.execute(
                """SELECT status FROM pie_research_runs
                   WHERE project_id=%s AND id=%s FOR SHARE""",
                (intent.project_id,intent.run_id)).fetchone()
            if run is None or run[0]!='draft':
                _fail('job_run_unavailable','Current project run is not eligible')
            r=connection.execute(
                """INSERT INTO pie_jobs
                   (project_id,run_id,kind,idempotency_key,state,max_attempts)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(project_id,idempotency_key) DO NOTHING
                   RETURNING id::text""",
                (intent.project_id,intent.run_id,_TASKS[intent.task_code],
                 intent.idempotency_key,state,intent.max_attempts)).fetchone()
            if r is None:
                current=connection.execute(
                    """SELECT j.id::text,j.run_id::text,j.state,
                              s.task_code,s.input_sha256,s.spec_sha256,j.max_attempts
                         FROM pie_jobs j
                         JOIN pie_job_execution_specs s
                           ON s.project_id=j.project_id AND s.job_id=j.id
                        WHERE j.project_id=%s AND j.idempotency_key=%s""",
                    (intent.project_id,intent.idempotency_key)).fetchone()
                if (current is None or current[1]!=intent.run_id
                    or current[3]!=intent.task_code or current[4]!=intent.input_sha256
                    or current[5]!=intent.spec_sha256
                    or current[6]!=intent.max_attempts):
                    _fail('job_idempotency_conflict','Request reused with changed task identity')
                return JobReceipt(intent.project_id,intent.run_id,current[0],
                                  intent.task_code,current[2],True)
            job_id=r[0]
            connection.execute(
                """INSERT INTO pie_job_execution_specs
                   (project_id,job_id,task_code,execution_policy,input_sha256,spec_sha256)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (intent.project_id,job_id,intent.task_code,policy,
                 intent.input_sha256,intent.spec_sha256))
            _event(connection,intent.project_id,intent.run_id,job_id,
                   'blocked_paid_manual' if paid else 'enqueued',0,actor_id,
                   detail_sha=intent.spec_sha256)
            return JobReceipt(intent.project_id,intent.run_id,job_id,
                              intent.task_code,state,False)
    except PIEBoundaryError:raise
    except Exception as exc:
        raise PIEBoundaryError('job_enqueue_unavailable','No durable scoped job created') from exc


def claim_deterministic_job(connection,project_id:str,worker_id:str,
                            authority:JobAuthority,*,lease_seconds:int=60)->LeasedJob|None:
    _authorize(authority,project_id,worker_id,'claim')
    if type(lease_seconds) is not int or not 10<=lease_seconds<=600:
        _fail('invalid_job_lease','Lease interval must be 10–600 seconds')
    try:
        with connection.transaction():
            row=connection.execute(
                """WITH next_job AS (
                     SELECT j.id FROM pie_jobs j
                     JOIN pie_job_execution_specs s
                       ON s.project_id=j.project_id AND s.job_id=j.id
                     JOIN pie_research_runs r
                       ON r.project_id=j.project_id AND r.id=j.run_id
                    WHERE j.project_id=%s AND j.state='queued'
                      AND j.available_at<=clock_timestamp()
                      AND j.attempts<j.max_attempts AND r.status='draft'
                      AND s.execution_policy='safe_deterministic'
                    ORDER BY j.available_at,j.created_at,j.id
                    FOR UPDATE OF j SKIP LOCKED LIMIT 1
                   )
                   UPDATE pie_jobs j
                      SET state='leased',leased_by=%s,
                          lease_until=clock_timestamp()+(%s * interval '1 second'),
                          attempts=j.attempts+1,fence_token=j.fence_token+1
                     FROM next_job n WHERE j.id=n.id
                   RETURNING j.id::text,j.run_id::text,j.fence_token,
                             j.attempts,j.max_attempts,j.lease_until""",
                (project_id,worker_id,lease_seconds)).fetchone()
            if row is None:return None
            job_id,run_id,fence,attempts,max_attempts,until=row
            spec=connection.execute(
                """SELECT task_code,input_sha256 FROM pie_job_execution_specs
                   WHERE project_id=%s AND job_id=%s
                     AND execution_policy='safe_deterministic'""",
                (project_id,job_id)).fetchone()
            if spec is None or spec[0]=='paid_model_manual_handoff':
                _fail('job_paid_dispatch_forbidden','Worker cannot lease paid tasks')
            _event(connection,project_id,run_id,job_id,'claimed',fence,worker_id)
            return LeasedJob(project_id,run_id,job_id,spec[0],spec[1],
                             fence,attempts,max_attempts,worker_id,until)
    except PIEBoundaryError:raise
    except Exception as exc:
        raise PIEBoundaryError('job_claim_unavailable','Scoped SKIP LOCKED claim failed') from exc


def _lease_row(connection,lease:LeasedJob,worker_id:str,
               *,lock=True)->tuple:
    if (type(lease) is not LeasedJob or not _project(lease.project_id)
        or not _uuid(lease.job_id) or not _actor(worker_id)
        or worker_id!=lease.leased_by
        or type(lease.fence_token) is not int or lease.fence_token<=0):
        _fail('job_lease_invalid','Typed original worker and fence required')
    suffix=' FOR UPDATE' if lock else ''
    row=connection.execute(
        """SELECT j.run_id::text,j.state,j.fence_token,j.attempts,
                  j.max_attempts,j.lease_until,j.leased_by,
                  s.task_code,s.input_sha256,r.status
             FROM pie_jobs j
             JOIN pie_job_execution_specs s
               ON s.project_id=j.project_id AND s.job_id=j.id
             JOIN pie_research_runs r ON r.project_id=j.project_id AND r.id=j.run_id
            WHERE j.project_id=%s AND j.id=%s"""+suffix,
        (lease.project_id,lease.job_id)).fetchone()
    if (row is None or row[0]!=lease.run_id or row[1]!='leased'
        or row[2]!=lease.fence_token or row[3]!=lease.attempts
        or row[6]!=worker_id or row[7]!=lease.task_code
        or row[8]!=lease.input_sha256 or row[9]!='draft'
        or row[5] is None):
        _fail('job_lease_lost','Lease fenced, cancelled or run changed')
    # Time itself is checked by SQL predicates for writes, not client's clock.
    return row


def heartbeat_job(connection,lease:LeasedJob,worker_id:str,
                  authority:JobAuthority,*,extend_seconds:int=60)->JobTransition:
    _authorize(authority,lease.project_id,worker_id,'heartbeat')
    if type(extend_seconds) is not int or not 10<=extend_seconds<=600:
        _fail('invalid_job_lease','Bounded heartbeat extension required')
    try:
        with connection.transaction():
            _lease_row(connection,lease,worker_id)
            row=connection.execute(
                """UPDATE pie_jobs SET
                     lease_until=GREATEST(lease_until,
                                clock_timestamp()+(%s*interval '1 second'))
                   WHERE project_id=%s AND id=%s AND state='leased'
                     AND fence_token=%s AND leased_by=%s
                     AND lease_until>clock_timestamp()
                   RETURNING run_id::text,fence_token""",
                (extend_seconds,lease.project_id,lease.job_id,
                 lease.fence_token,worker_id)).fetchone()
            if row is None:_fail('job_lease_lost','Expired heartbeat cannot renew old work')
            event=_event(connection,lease.project_id,lease.run_id,
                         lease.job_id,'heartbeat',lease.fence_token,worker_id)
            return JobTransition(lease.project_id,lease.job_id,'leased',
                                 lease.fence_token,event)
    except PIEBoundaryError:raise
    except Exception as exc:
        raise PIEBoundaryError('job_heartbeat_unavailable','Heartbeat was not committed') from exc


def checkpoint_job(connection,lease:LeasedJob,worker_id:str,
                   authority:JobAuthority,state_digest_sha256:str)->str:
    _authorize(authority,lease.project_id,worker_id,'checkpoint')
    if not _hash(state_digest_sha256):
        _fail('job_checkpoint_invalid','Only opaque immutable state digest, no media')
    try:
        with connection.transaction():
            _lease_row(connection,lease,worker_id)
            index=connection.execute(
                """SELECT COALESCE(MAX(checkpoint_seq),0)+1 FROM pie_job_checkpoints
                   WHERE project_id=%s AND job_id=%s AND fence_token=%s""",
                (lease.project_id,lease.job_id,lease.fence_token)).fetchone()[0]
            if index>10000:_fail('job_checkpoint_limit','Checkpoint limit')
            data={'schema_version':_CHECKPOINT,'project_id':lease.project_id,
                  'job_id':lease.job_id,'fence_token':lease.fence_token,
                  'checkpoint_seq':index,'state_digest_sha256':state_digest_sha256,
                  'can_publish':False,'may_issue_paid_request':False}
            digest=canonical_hash(data)
            from psycopg.types.json import Jsonb
            result=connection.execute(
                """INSERT INTO pie_job_checkpoints
                   (project_id,job_id,fence_token,checkpoint_seq,
                    checkpoint_sha256,state_digest_sha256,safe_metadata)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   RETURNING checkpoint_sha256""",
                (lease.project_id,lease.job_id,lease.fence_token,index,digest,
                 state_digest_sha256,Jsonb(data))).fetchone()
            if result is None or result[0]!=digest:
                _fail('job_checkpoint_conflict','Checkpoint missing')
            _event(connection,lease.project_id,lease.run_id,lease.job_id,
                   'checkpoint',lease.fence_token,worker_id,detail_sha=digest)
            return digest
    except PIEBoundaryError:raise
    except Exception as exc:
        raise PIEBoundaryError('job_checkpoint_unavailable','Fenced checkpoint not stored') from exc


def complete_job(connection,lease:LeasedJob,worker_id:str,
                 authority:JobAuthority,result_sha256:str)->JobTransition:
    _authorize(authority,lease.project_id,worker_id,'complete')
    if not _hash(result_sha256):
        _fail('job_completion_invalid','Bounded deterministic result SHA required')
    try:
        with connection.transaction():
            _lease_row(connection,lease,worker_id)
            row=connection.execute(
                """UPDATE pie_jobs SET state='completed',
                     leased_by=NULL,lease_until=NULL
                   WHERE project_id=%s AND id=%s AND state='leased'
                     AND fence_token=%s AND leased_by=%s
                     AND lease_until>clock_timestamp()
                   RETURNING run_id::text""",
                (lease.project_id,lease.job_id,lease.fence_token,worker_id)).fetchone()
            if row is None:_fail('job_lease_lost','Stale worker cannot finalize result')
            event=_event(connection,lease.project_id,lease.run_id,
                         lease.job_id,'completed',lease.fence_token,worker_id,
                         detail_sha=result_sha256)
            return JobTransition(lease.project_id,lease.job_id,'completed',
                                 lease.fence_token,event)
    except PIEBoundaryError:raise
    except Exception as exc:
        raise PIEBoundaryError('job_complete_unavailable','Completion not committed') from exc


def _backoff(attempt:int)->int:
    if type(attempt) is not int or not 1<=attempt<=20:
        _fail('job_attempt_invalid','Expected bounded attempt')
    return min(3600,2**min(attempt,10))


def fail_job(connection,lease:LeasedJob,worker_id:str,
             authority:JobAuthority,failure_code:str)->JobTransition:
    _authorize(authority,lease.project_id,worker_id,'fail')
    if type(failure_code) is not str or failure_code not in _FAILURES:
        _fail('job_failure_invalid','Use bounded recorded failure code only')
    try:
        with connection.transaction():
            row=_lease_row(connection,lease,worker_id)
            retry=(failure_code in _TRANSIENT and row[3]<row[4])
            seconds=_backoff(row[3]) if retry else None
            state='queued' if retry else 'failed'
            result=connection.execute(
                """UPDATE pie_jobs SET state=%s,leased_by=NULL,lease_until=NULL,
                     available_at=CASE WHEN %s::int IS NOT NULL THEN
                       clock_timestamp()+(%s*interval '1 second') ELSE available_at END
                   WHERE project_id=%s AND id=%s AND state='leased'
                     AND fence_token=%s AND leased_by=%s
                     AND lease_until>clock_timestamp()
                   RETURNING run_id::text""",
                (state,seconds,seconds or 0,lease.project_id,lease.job_id,
                 lease.fence_token,worker_id)).fetchone()
            if result is None:_fail('job_lease_lost','Cannot fail expired or cancelled task')
            event=_event(connection,lease.project_id,lease.run_id,lease.job_id,
                         'retry_scheduled' if retry else 'failed_terminal',
                         lease.fence_token,worker_id,
                         detail_sha=canonical_hash({'failure_code':failure_code}))
            return JobTransition(lease.project_id,lease.job_id,state,
                                 lease.fence_token,event,seconds)
    except PIEBoundaryError:raise
    except Exception as exc:
        raise PIEBoundaryError('job_failure_unavailable','Failure transition not committed') from exc


def cancel_job(connection,project_id:str,job_id:str,actor_id:str,
               authority:JobAuthority)->JobTransition:
    _authorize(authority,project_id,actor_id,'cancel')
    if not _uuid(job_id):_fail('job_cancel_invalid','Exact job id required')
    try:
        with connection.transaction():
            row=connection.execute(
                """SELECT run_id::text,state,fence_token FROM pie_jobs
                   WHERE project_id=%s AND id=%s FOR UPDATE""",
                (project_id,job_id)).fetchone()
            if row is None:_fail('job_not_found','No current project job')
            run_id,state,fence=row
            if state=='cancelled':
                return JobTransition(project_id,job_id,'cancelled',fence,'')
            if state in ('completed','failed'):
                _fail('job_terminal','Cannot rewrite terminal job')
            updated=connection.execute(
                """UPDATE pie_jobs SET state='cancelled',
                     leased_by=NULL,lease_until=NULL
                   WHERE project_id=%s AND id=%s
                     AND state IN ('queued','leased','blocked')
                   RETURNING id""",
                (project_id,job_id)).fetchone()
            if updated is None:_fail('job_cancel_conflict','Could not atomically cancel')
            event=_event(connection,project_id,run_id,job_id,
                         'cancelled',fence,actor_id)
            return JobTransition(project_id,job_id,'cancelled',fence,event)
    except PIEBoundaryError:raise
    except Exception as exc:
        raise PIEBoundaryError('job_cancel_unavailable','Cancellation uncommitted') from exc


def recover_expired_jobs(connection,project_id:str,worker_id:str,
                         authority:JobAuthority,*,limit:int=10)->list[JobTransition]:
    """Requeue safe tasks ONLY; paid tasks are unclaimable from the start."""
    _authorize(authority,project_id,worker_id,'recover')
    if type(limit) is not int or not 1<=limit<=100:
        _fail('job_recovery_invalid','Bounded recovery batch 1–100')
    result=[]
    try:
        for _ in range(limit):
            with connection.transaction():
                row=connection.execute(
                    """SELECT j.id::text,j.run_id::text,j.fence_token,
                              j.attempts,j.max_attempts,r.status
                       FROM pie_jobs j
                       JOIN pie_job_execution_specs s
                         ON s.project_id=j.project_id AND s.job_id=j.id
                       JOIN pie_research_runs r
                         ON r.project_id=j.project_id AND r.id=j.run_id
                      WHERE j.project_id=%s AND j.state='leased'
                        AND j.lease_until<=clock_timestamp()
                        AND s.execution_policy='safe_deterministic'
                      ORDER BY j.lease_until,j.id
                      FOR UPDATE OF j SKIP LOCKED LIMIT 1""",
                    (project_id,)).fetchone()
                if row is None:break
                job_id,run_id,fence,attempts,max_attempts,run_state=row
                retry=attempts<max_attempts and run_state=='draft'
                delay=_backoff(attempts) if retry else None
                state='queued' if retry else 'failed'
                connection.execute(
                    """UPDATE pie_jobs SET state=%s,leased_by=NULL,lease_until=NULL,
                         available_at=CASE WHEN %s::int IS NOT NULL THEN
                           clock_timestamp()+(%s*interval '1 second')
                           ELSE available_at END
                       WHERE project_id=%s AND id=%s AND state='leased'
                         AND fence_token=%s""",
                    (state,delay,delay or 0,project_id,job_id,fence))
                event=_event(connection,project_id,run_id,job_id,
                             'expired_requeued' if retry else 'failed_terminal',
                             fence,worker_id)
                result.append(JobTransition(project_id,job_id,state,fence,event,delay))
        return result
    except PIEBoundaryError:raise
    except Exception as exc:
        raise PIEBoundaryError('job_recovery_unavailable','Expired safe work not reconciled') from exc


def execute_one_deterministic(connection,project_id:str,worker_id:str,
                              authority:JobAuthority,handler:DeterministicTaskHandler,
                              *,lease_seconds:int=120)->JobTransition|None:
    """Dispatch ONLY explicitly safe deterministic handlers (never OpenRouter).

    The injected handler MUST enforce fence_token on all persistent writes.
    Python cannot certify an arbitrary plugin/handler's internal side effects.
    """
    if handler is None or not callable(getattr(handler,'run_deterministic',None)):
        _fail('job_handler_unavailable','A trusted fenced deterministic executor required')
    job=claim_deterministic_job(connection,project_id,worker_id,authority,
                                lease_seconds=lease_seconds)
    if job is None:return None
    try:
        output=handler.run_deterministic(job)
        if not _hash(output):
            _fail('job_handler_invalid','Deterministic result must be SHA256 metadata only')
    except Exception:
        return fail_job(connection,job,worker_id,authority,'unknown_failure')
    return complete_job(connection,job,worker_id,authority,output)
