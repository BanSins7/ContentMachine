"""GB-MOD-27 bounded, privacy-safe monitoring and resource admission.

This module does NOT fetch media, POST to providers, release paid reservations
or claim that a recorded count is a verified invoice. All rights and billed
usage must originate from independently trusted, current source/ledger data.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from collections.abc import Mapping
from uuid import UUID
import re

from .contracts import canonical_hash

SHA=re.compile(r"[0-9a-f]{64}\Z")
CODE=re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
VERSION="group-b-monitor/v1"


class MonitoringDenied(Exception):
    def __init__(self,code):
        self.code=code
        super().__init__(code)


def _deny(code):
    raise MonitoringDenied(code)


@dataclass(frozen=True,slots=True)
class ThroughputPolicy:
    max_running_local:int=2
    max_running_paid:int=0
    max_prompt_tokens:int=65536
    max_completion_tokens:int=8192
    max_images:int=32
    max_context_bytes:int=2_000_000
    max_wall_ms:int=300_000
    max_batch_jobs:int=1000

    def __post_init__(self):
        for attr in self.__dataclass_fields__:
            x=getattr(self,attr)
            if type(x) is not int or x<0:
                _deny("throughput_policy_invalid")
        if (not 1<=self.max_running_local<=16 or
            self.max_running_paid!=0 or
            not 1<=self.max_prompt_tokens<=1_000_000 or
            not 1<=self.max_completion_tokens<=1_000_000 or
            not 1<=self.max_images<=10_000 or
            not 1<=self.max_context_bytes<=10_000_000 or
            not 5000<=self.max_wall_ms<=86_400_000 or
            not 1<=self.max_batch_jobs<=1000):
            _deny("throughput_policy_invalid")


def admit_resource_request(policy:ThroughputPolicy,*,kind,
                           prompt_tokens,completion_tokens,images,context_bytes):
    """Independent resource limit; NOT a paid permission or a provider POST.

    Image count must be determined from separately verified context refs,
    not asserted by the UI. Unknown or negative usage is never 0.
    """
    if not isinstance(policy,ThroughputPolicy):
        _deny("throughput_policy_required")
    values=(prompt_tokens,completion_tokens,images,context_bytes)
    if kind not in ("local","paid") or any(
        type(x) is not int or x<0 for x in values
    ):
        _deny("resource_measurement_unavailable")
    if kind=="paid" and policy.max_running_paid==0:
        _deny("paid_queue_not_activated")
    if (prompt_tokens>policy.max_prompt_tokens or
        completion_tokens>policy.max_completion_tokens or
        images>policy.max_images or
        context_bytes>policy.max_context_bytes):
        _deny("resource_limit_exceeded")
    return True


@dataclass(frozen=True,slots=True)
class BatchPlan:
    local_waves:tuple[tuple[str,...],...]
    paid_deferred:tuple[str,...]
    total:int

    @property
    def max_concurrency(self):
        return max((len(w) for w in self.local_waves),default=0)


def plan_queue_batch(policy:ThroughputPolicy,items):
    """Plan up to 1000 IDs without claiming jobs or paid model attempts.

    Input comes from project-scoped trusted DB query; pure planning cannot
    make stale rights/current media true. Paid slots are always deferred.
    """
    if not isinstance(policy,ThroughputPolicy) or type(items) not in (list,tuple):
        _deny("batch_inputs_invalid")
    if len(items)>policy.max_batch_jobs:
        _deny("batch_over_limit")
    local=[]
    paid=[]
    seen=set()
    for item in items:
        if (type(item) not in (tuple,list) or len(item)!=2 or
            type(item[0]) is not str or type(item[1]) is not str or
            item[1] not in ("local","paid")):
            _deny("batch_identity_invalid")
        try:
            if str(UUID(item[0]))!=item[0]:
                _deny("batch_job_id_invalid")
        except ValueError:
            _deny("batch_job_id_invalid")
        if item[0] in seen:
            _deny("batch_duplicate_job_id")
        seen.add(item[0])
        (local if item[1]=="local" else paid).append(item[0])
    waves=tuple(tuple(local[i:i+policy.max_running_local])
                for i in range(0,len(local),policy.max_running_local))
    return BatchPlan(waves,tuple(paid),len(items))


class FailureClass(str,Enum):
    RATE_LIMIT="rate_limit"
    TRANSIENT="transient"
    UNKNOWN_CHARGE="unknown_charge"
    AUTH_DENIED="auth_denied"
    INVALID_PAYLOAD="invalid_payload"
    REVOKED_SOURCE="revoked_source"
    LEASE_STOLEN="lease_stolen"
    LOCAL_ENGINE="local_engine"
    UNKNOWN="unknown"


@dataclass(frozen=True,slots=True)
class FailureDecision:
    category:FailureClass
    retryable:bool
    requires_receipt_reconciliation:bool
    code:str


# NEVER log error body, HTTP query params, token, exception.args or media text.
def classify_failure(*,status_code=None,provider_code=None,
                     request_submitted=False,charge_verified=False):
    if (status_code is not None and
        (type(status_code) is not int or not 100<=status_code<=599)):
        _deny("provider_status_invalid")
    if provider_code is not None and (
        type(provider_code) is not str or
        not CODE.fullmatch(provider_code)):
        _deny("provider_code_invalid")
    if type(request_submitted) is not bool or type(charge_verified) is not bool:
        _deny("provider_charge_state_invalid")
    if request_submitted:
        if not charge_verified:
            return FailureDecision(FailureClass.UNKNOWN_CHARGE,False,True,
                                   "provider_charge_unverified")
        # A verified already-submitted attempt is consumed, even for 429.
        # Only an independent explicit NO_CHARGE receipt plus a NEW quote/
        # approval could create a new attempt. Never retry this identity.
        return FailureDecision(FailureClass.UNKNOWN_CHARGE,False,False,
                               "provider_attempt_consumed")
    if status_code==429 or provider_code=="rate_limited":
        return FailureDecision(FailureClass.RATE_LIMIT,True,False,
                               "provider_rate_limited")
    if status_code in (408,500,502,503,504) or provider_code in (
        "timeout","upstream_temporary","network_unavailable"):
        return FailureDecision(FailureClass.TRANSIENT,True,False,
                               "provider_temporarily_unavailable")
    if status_code in (401,403) or provider_code=="unauthorized":
        return FailureDecision(FailureClass.AUTH_DENIED,False,False,
                               "provider_auth_denied")
    if provider_code in ("lease_expired","lease_stolen_or_cancelled"):
        return FailureDecision(FailureClass.LEASE_STOLEN,False,False,
                               "lease_not_current")
    if provider_code in ("source_revoked","source_invalidated","rights_revoked"):
        return FailureDecision(FailureClass.REVOKED_SOURCE,False,False,
                               "source_rights_revoked")
    if provider_code in ("local_engine_error","local_result_invalid"):
        return FailureDecision(FailureClass.LOCAL_ENGINE,False,False,
                               "local_execution_failed")
    if provider_code in ("response_not_json","response_shape_invalid",
                         "response_incomplete","usage_unavailable"):
        return FailureDecision(FailureClass.INVALID_PAYLOAD,False,False,
                               "provider_result_not_authorized")
    return FailureDecision(FailureClass.UNKNOWN,False,False,
                           "unclassified_provider_failure")


@dataclass(frozen=True,slots=True)
class ExecutionMetric:
    project_id:str
    run_id:str
    job_id:str
    block:str
    queue_kind:str
    lease_generation:int
    outcome:str
    duration_ms:int|None
    result_sha256:str|None=None
    failure_code:str|None=None
    prompt_tokens:int|None=None
    completion_tokens:int|None=None
    image_count:int|None=None
    charged_usd:Decimal|None=None

    def validate(self):
        try:
            if (type(self.project_id) is not str or not self.project_id or
                type(self.run_id) is not str or str(UUID(self.run_id))!=self.run_id or
                type(self.job_id) is not str or str(UUID(self.job_id))!=self.job_id or
                self.block not in tuple("B"+str(i) for i in range(1,9)) or
                self.queue_kind not in ("local","paid") or
                type(self.lease_generation) is not int or self.lease_generation<1 or
                self.outcome not in ("complete","partial","failed","blocked","lease_expired") or
                (self.duration_ms is not None and (type(self.duration_ms) is not int or
                    not 0<=self.duration_ms<=86_400_000)) or
                (self.outcome=="lease_expired" and self.duration_ms is not None) or
                (self.outcome=="complete" and (
                    type(self.result_sha256) is not str or
                    SHA.fullmatch(self.result_sha256) is None or
                    self.failure_code is not None)) or
                (self.outcome!="complete" and self.result_sha256 is not None) or
                (self.failure_code is not None and (
                    type(self.failure_code) is not str or
                    CODE.fullmatch(self.failure_code) is None))):
                return False
            for value in (self.prompt_tokens,self.completion_tokens,
                          self.image_count):
                if value is not None and (type(value) is not int or
                                           not 0<=value<=1_000_000):
                    return False
            if self.charged_usd is not None and (
                type(self.charged_usd) is not Decimal or
                not self.charged_usd.is_finite() or self.charged_usd<0):
                return False
            if self.queue_kind=="paid" and any(v is not None for v in (
                self.prompt_tokens,self.completion_tokens,
                self.image_count,self.charged_usd)):
                return False
            return True
        except (ValueError,TypeError):
            return False

    @property
    def trace_sha256(self):
        if not self.validate():
            _deny("metric_invalid")
        return canonical_hash({
            "schema_version":VERSION,"project_id":self.project_id,
            "run_id":self.run_id,"job_id":self.job_id,
            "lease_generation":self.lease_generation,
            "block":self.block,"queue_kind":self.queue_kind,
        })


def record_execution(conn,event:ExecutionMetric):
    """Called inside SAME DB transaction AFTER an exact fenced terminal job.

    A unique (project,job,generation) event prevents 2x elapsed/cost.
    Duplicate replay verifies exact original row. No SQL005 result write.
    """
    if not isinstance(event,ExecutionMetric) or not event.validate():
        _deny("metric_invalid")
    from .orchestrator import _row
    digest=event.trace_sha256
    row=_row(conn.execute(
        """INSERT INTO group_b_execution_metrics(
             project_id,run_id,job_id,lease_generation,block,queue_kind,
             outcome,duration_ms,prompt_tokens,completion_tokens,image_count,
             charged_usd,result_sha256,failure_code,trace_sha256)
           VALUES (%s,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT(project_id,job_id,lease_generation) DO NOTHING
           RETURNING id,trace_sha256,outcome,duration_ms,
                     result_sha256,failure_code""",
        (event.project_id,event.run_id,event.job_id,event.lease_generation,
         event.block,event.queue_kind,event.outcome,event.duration_ms,
         event.prompt_tokens,event.completion_tokens,event.image_count,
         event.charged_usd,event.result_sha256,event.failure_code,digest)
    ))
    if row is None:
        row=_row(conn.execute(
            """SELECT id,trace_sha256,outcome,duration_ms,result_sha256,
                      failure_code,queue_kind,block,prompt_tokens,
                      completion_tokens,image_count,charged_usd
               FROM group_b_execution_metrics
               WHERE project_id=%s AND job_id=%s::uuid
                     AND lease_generation=%s""",
            (event.project_id,event.job_id,event.lease_generation)
        ))
        if (row is None or row["trace_sha256"]!=digest or
            row["outcome"]!=event.outcome or
            row["duration_ms"]!=event.duration_ms or
            row["result_sha256"]!=event.result_sha256 or
            row["failure_code"]!=event.failure_code or
            row["queue_kind"]!=event.queue_kind or row["block"]!=event.block or
            row["prompt_tokens"]!=event.prompt_tokens or
            row["completion_tokens"]!=event.completion_tokens or
            row["image_count"]!=event.image_count or
            row["charged_usd"]!=event.charged_usd):
            _deny("metric_replay_mismatch")
        return row["id"],True
    return row["id"],False


def performance_snapshot(conn,*,project_id,run_id=None):
    """Safe aggregate: no provider prompt/raw response/sensitive cost secrets.

    Authentic billing stays in GB10 ledger; metric charged_usd for local
    actions is NEVER considered a real external payment.
    """
    if type(project_id) is not str or not project_id:
        _deny("scope_invalid")
    if run_id is not None:
        try:
            if type(run_id) is not str or str(UUID(run_id))!=run_id:
                _deny("run_scope_invalid")
        except ValueError:
            _deny("run_scope_invalid")
    scope="AND run_id=%s::uuid" if run_id is not None else ""
    args=(project_id,run_id) if run_id is not None else (project_id,)
    rows=conn.execute(
        """SELECT queue_kind,outcome,count(*) AS samples,
                  sum(duration_ms)::bigint AS elapsed_ms_total,
                  max(duration_ms) AS max_observed_ms
           FROM group_b_execution_metrics
           WHERE project_id=%s """+scope+
        """ GROUP BY queue_kind,outcome ORDER BY queue_kind,outcome""",args
    ).fetchall()
    cols=("queue_kind","outcome","samples","elapsed_ms_total","max_observed_ms")
    metrics=[dict(zip(cols,row)) for row in rows]
    running=conn.execute(
        """SELECT kind,count(*) FROM group_b_jobs
           WHERE project_id=%s """+
        ("AND run_id=%s::uuid " if run_id is not None else "")+
        """AND state='running' AND lease_until>now()
           GROUP BY kind ORDER BY kind""",args
    ).fetchall()
    charge=conn.execute(
        """SELECT charge_state,count(*),
                  coalesce(sum(reserved_usd),0),
                  coalesce(sum(charged_usd),0)
           FROM group_b_model_attempts WHERE project_id=%s """+scope+
        """ GROUP BY charge_state ORDER BY charge_state""",args
    ).fetchall()
    return {
        "schema_version":VERSION,"project_id":project_id,"run_id":run_id,
        "metrics":metrics,
        "running_by_queue":[{"kind":k,"active":n} for k,n in running],
        "paid_ledger":[{"state":st,"attempts":n,
                        "reserved_usd":str(reserved),
                        "charged_usd_verified":str(billed)}
                       for st,n,reserved,billed in charge],
        "paid_model_dispatch_activated":False,
    }
