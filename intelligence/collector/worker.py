"""Specialized PostgreSQL worker for approved local DTK keyword tasks.

Never invoke intelligence.jobs.lease_next: it can claim unrelated work.
Collector jobs stay run_after=infinity, and only this worker's exact SQL
can process released, source-rights-checked tasks.

A submit intent is committed BEFORE HTTP; a crash/timeout cannot trigger
blind re-submit. Known task IDs are recovered with safe GET polling.
"""
from __future__ import annotations

import argparse
import json
import signal
import time
from uuid import uuid4

from intelligence.connectors import dtk
from intelligence.taxonomy_builder.pipeline_contracts import canonical
from .adapters.dtk_tiktok import prepare_keyword_task
from .contracts import CollectorError
from .execution import _PERMISSIONS, archive_attested, worker_enabled
from .policy_gate import inspect_source_policy
from .repository import _connection

LEASE_SECONDS = 45
POLL_INTERVAL_SECONDS = 5
_ALLOWED_JOB_TYPE = "collector.search.held"


def _assert_worker_config():
    if not worker_enabled() or not archive_attested():
        raise CollectorError("execution_disabled", "Collector live execution is disabled",409)
    # Read local settings without making an upstream request.
    dtk.settings()


def _finalize(conn, run_id: int) -> None:
    """Consolidate terminal per-task states, never report false success."""
    group=conn.execute(
        """SELECT state,count(*) FROM collector_provider_tasks WHERE run_id=%s
           GROUP BY state""",(run_id,),
    ).fetchall()
    counts={state:count for state,count in group}
    active=sum(counts.get(s,0) for s in ("not_submitted","submit_intent","submitted"))
    if active:
        conn.execute(
            """UPDATE search_runs SET state='running',started_at=COALESCE(started_at,now())
               WHERE id=%s AND state IN ('created','queued','running')""",(run_id,),
        )
        return
    cancel=conn.execute(
        "SELECT cancel_requested FROM search_runs WHERE id=%s",(run_id,),
    ).fetchone()[0]
    done=counts.get("completed",0)
    failed=counts.get("failed",0)
    unknown=counts.get("unknown",0)
    if cancel:
        stage="cancelled"; state="cancelled"
    elif unknown:
        stage="partial" if done else "blocked"
        state=stage
    elif failed:
        stage="partial" if done else "failed"
        state=stage
    else:
        stage="completed";state="completed"
    conn.execute("UPDATE collector_runs SET stage=%s WHERE run_id=%s",
                 (stage,run_id))
    conn.execute(
        """UPDATE search_runs SET state=%s,
           started_at=COALESCE(started_at,now()),
           finished_at=COALESCE(finished_at,now()) WHERE id=%s""",
        (state,run_id),
    )


def recover_expired(*, connect=None) -> int:
    """Crash-safe recovery: never repeat a possibly sent submit."""
    with _connection(connect) as conn:
        with conn.transaction():
            stale=conn.execute(
                """SELECT t.id,t.state,t.run_id,j.id,j.lease_generation,t.source_request_id
                   FROM collector_provider_tasks t JOIN jobs j ON j.id=t.job_id
                   WHERE j.job_type=%s AND j.state='running'
                     AND j.lease_until < now()
                     AND t.state IN ('submit_intent','submitted')
                   ORDER BY j.id FOR UPDATE OF j,t SKIP LOCKED""",
                (_ALLOWED_JOB_TYPE,),
            ).fetchall()
            for task_id,state,run_id,job_id,lease,source_req in stale:
                if state=="submit_intent":
                    conn.execute(
                        """UPDATE collector_provider_tasks SET state='unknown',
                           last_error_code='lease_expired_after_submit_intent',
                           finished_at=now() WHERE id=%s""",(task_id,),
                    )
                    conn.execute(
                        """UPDATE source_requests SET state='unknown',
                           error_code='submit_uncertain',finished_at=now()
                           WHERE id=%s""",(source_req,),
                    )
                    conn.execute(
                        """UPDATE jobs SET state='failed',locked_by=NULL,lease_until=NULL,
                           last_error='Collector submit outcome uncertain',
                           updated_at=now() WHERE id=%s""",(job_id,),
                    )
                else:
                    # Provider task ID was already durably recorded; GET may retry.
                    conn.execute(
                        """UPDATE jobs SET state='queued',locked_by=NULL,lease_until=NULL,
                           updated_at=now() WHERE id=%s""",(job_id,),
                    )
                _finalize(conn,run_id)
            return len(stale)


def _claim(conn, worker_id: str):
    """Claim exactly one DTK task with fencing and quota consumption."""
    conn.execute("SELECT pg_advisory_xact_lock(%s)",(82931674018,))
    # Advisory lock only serializes Collector workers; lease generation fences
    # late results from an expired worker and row locks preserve cancellation.
    row=conn.execute(
        """SELECT t.id,t.run_id,t.task_sha256,t.state,t.provider_task_id,
                  j.id,j.lease_generation,t.source_request_id,
                  p.snapshot,c.policy_version_id,g.source_id,
                  g.quota_window_start,g.reserved_remaining
           FROM collector_provider_tasks t
           JOIN jobs j ON j.id=t.job_id
           JOIN collector_runs c ON c.run_id=t.run_id
           JOIN search_runs r ON r.id=c.run_id
           JOIN collector_plans p ON p.id=c.plan_id
           JOIN collector_execution_grants g ON g.run_id=t.run_id
           WHERE j.job_type=%s AND j.state='queued'
             AND j.run_after='infinity'::timestamptz
             AND ((t.state='submitted' AND c.stage IN ('released','cancelling')
                   AND (t.polled_at IS NULL
                        OR t.polled_at < now()-interval '5 seconds'))
                  OR (t.state='not_submitted' AND c.stage='released'
                      AND r.cancel_requested=FALSE
                      AND g.reserved_remaining>0))
           ORDER BY CASE WHEN t.state='submitted' THEN 0 ELSE 1 END,
                    j.created_at,j.id
           FOR UPDATE OF t,j,c,g,r SKIP LOCKED LIMIT 1""",
        (_ALLOWED_JOB_TYPE,),
    ).fetchone()
    if row is None:
        return None
    (task_id,run_id,digest,state,provider_task_id,job_id,lease_gen,
     source_request_id,snapshot,policy_version,source_id,window_start,reserved)=row
    if source_id!="dtk_tiktok" or not isinstance(snapshot,dict):
        raise CollectorError("invalid_job", "Unrecognized source or plan snapshot",409)
    items=[t for t in snapshot.get("tasks",[])
           if t.get("task_fingerprint")==digest]
    if len(items)!=1:
        raise CollectorError("invalid_job", "Task does not match immutable plan",409)
    prepared=prepare_keyword_task(items[0])
    if state=="not_submitted":
        # Holds the search_run row lock too, so a cancellation committed
        # before this claim cannot be missed by a stale snapshot.
        # Provider use permissions are rechecked *before* the irrevocable
        # intent is recorded; original grant must be the current policy.
        policy=inspect_source_policy(conn,source_id,max_requests=0,max_pages=1)
        if not policy["allowed_for_held_plan"] or policy["policy_version_id"]!=policy_version:
            raise CollectorError("policy_changed", "Source policy revoked or expired",409)
        perms=conn.execute(
            """SELECT permissions FROM source_policy_versions
               WHERE id=%s AND is_active=TRUE AND source_id=%s""",
            (policy_version,source_id),
        ).fetchone()
        if (not perms or not isinstance(perms[0],dict)
            or any(perms[0].get(key) is not True for key in _PERMISSIONS)):
            raise CollectorError("policy_changed", "Automated collection rights revoked",409)
        valid=conn.execute(
            """SELECT 1 FROM source_quota_ledger WHERE source_id=%s
               AND bucket_name='collector.requests' AND window_start_at=%s
               AND window_end_at>now() AND reserved_units>=1""",
            (source_id,window_start),
        ).fetchone()
        if not valid:
            raise CollectorError("quota_expired","Provider quota window expired",409)
    assigned=conn.execute(
        """UPDATE jobs SET state='running',locked_by=%s,
           attempts=attempts+CASE WHEN %s THEN 1 ELSE 0 END,
           lease_generation=lease_generation+1,
           lease_until=now()+(%s*interval '1 second'),updated_at=now()
           WHERE id=%s AND state='queued' RETURNING lease_generation""",
        (worker_id,state=="not_submitted",LEASE_SECONDS,job_id),
    ).fetchone()
    if not assigned:
        return None
    generation=assigned[0]
    if state=="not_submitted":
        conn.execute(
            """UPDATE collector_provider_tasks SET state='submit_intent'
               WHERE id=%s AND state='not_submitted'""",(task_id,),
        )
        conn.execute(
            """UPDATE source_requests SET state='started',started_at=now(),
               quota_units=1 WHERE id=%s AND state='planned'""",(source_request_id,),
        )
        conn.execute(
            """UPDATE collector_execution_grants SET
               reserved_remaining=reserved_remaining-1,consumed=consumed+1
               WHERE run_id=%s AND reserved_remaining>0""",(run_id,),
        )
        conn.execute(
            """UPDATE source_quota_ledger SET
               reserved_units=reserved_units-1,consumed_units=consumed_units+1,
               updated_at=now()
               WHERE source_id=%s AND bucket_name='collector.requests'
                   AND window_start_at=%s AND reserved_units>0""",
            (source_id,window_start),
        )
    conn.execute(
        """UPDATE search_runs SET state='running',started_at=COALESCE(started_at,now())
           WHERE id=%s AND state IN ('created','queued','running')""",(run_id,),
    )
    return {
        "id":task_id,"run_id":run_id,"job_id":job_id,"request_id":source_request_id,
        "generation":generation,"worker_id":worker_id,"state":state,
        "provider_task_id":provider_task_id,"prepared":prepared,
    }


def _claim_next(worker_id, *, connect=None):
    with _connection(connect) as conn:
        with conn.transaction():
            return _claim(conn,worker_id)


def _update_after_provider(ctx, *, next_state: str, summary=None, error_code=None,
                           provider_task_id=None, provider_request_id=None, connect=None):
    """Compare-and-set fence: expired job owners cannot overwrite recovered state."""
    if next_state not in ("submitted","completed","failed","unknown"):
        raise ValueError("Invalid collector transition")
    with _connection(connect) as conn:
        with conn.transaction():
            # Follow cancel_run lock ordering (search_runs first, collector_runs
            # second) to avoid a cancel/completion lock inversion.
            conn.execute("SELECT id FROM search_runs WHERE id=%s FOR UPDATE",
                         (ctx["run_id"],))
            conn.execute("SELECT run_id FROM collector_runs WHERE run_id=%s FOR UPDATE",
                         (ctx["run_id"],))
            fenced=conn.execute(
                """SELECT id FROM jobs WHERE id=%s AND state='running'
                   AND locked_by=%s AND lease_generation=%s AND lease_until>now()
                   FOR UPDATE""",
                (ctx["job_id"],ctx["worker_id"],ctx["generation"]),
            ).fetchone()
            if fenced is None:
                return False
            if next_state=="submitted":
                conn.execute(
                    """UPDATE collector_provider_tasks SET state='submitted',
                       provider_task_id=%s,submitted_at=now(),
                       polled_at=NULL,last_error_code=NULL WHERE id=%s
                       AND state='submit_intent'""",
                    (provider_task_id,ctx["id"]),
                )
                conn.execute(
                    """UPDATE source_requests SET provider_request_id=%s
                       WHERE id=%s""",
                    (provider_request_id or provider_task_id,ctx["request_id"]),
                )
                job_state="queued"
            elif next_state=="completed":
                conn.execute(
                    """UPDATE collector_provider_tasks SET state='completed',
                       response_summary=%s::jsonb,polled_at=now(),
                       finished_at=now(),last_error_code=NULL WHERE id=%s
                       AND state='submitted'""",
                    (canonical(summary or {}),ctx["id"]),
                )
                conn.execute(
                    """UPDATE source_requests SET state='succeeded',
                       finished_at=now() WHERE id=%s""",(ctx["request_id"],),
                )
                job_state="completed"
            elif next_state=="failed":
                conn.execute(
                    """UPDATE collector_provider_tasks SET state='failed',
                       last_error_code=%s,polled_at=now(),finished_at=now()
                       WHERE id=%s AND state='submitted'""",
                    (error_code or "provider_failed",ctx["id"]),
                )
                conn.execute(
                    """UPDATE source_requests SET state='failed',error_code=%s,
                       finished_at=now() WHERE id=%s""",
                    (error_code or "provider_failed",ctx["request_id"]),
                )
                job_state="failed"
            else:
                conn.execute(
                    """UPDATE collector_provider_tasks SET state='unknown',
                       last_error_code=%s,finished_at=now()
                       WHERE id=%s AND state='submit_intent'""",
                    (error_code or "submit_uncertain",ctx["id"]),
                )
                conn.execute(
                    """UPDATE source_requests SET state='unknown',
                       error_code='submit_uncertain',finished_at=now()
                       WHERE id=%s""",(ctx["request_id"],),
                )
                job_state="failed"
            conn.execute(
                """UPDATE jobs SET state=%s,locked_by=NULL,lease_until=NULL,
                   updated_at=now(),result=%s::jsonb WHERE id=%s""",
                (job_state,canonical({"provider_state":next_state,
                                      "payload_persisted":False}),ctx["job_id"]),
            )
            _finalize(conn,ctx["run_id"])
            return True


def _defer_poll(ctx, *, error_code=None, connect=None):
    with _connection(connect) as conn:
        with conn.transaction():
            fenced=conn.execute(
                """SELECT id FROM jobs WHERE id=%s AND state='running'
                   AND locked_by=%s AND lease_generation=%s AND lease_until>now()
                   FOR UPDATE""",
                (ctx["job_id"],ctx["worker_id"],ctx["generation"]),
            ).fetchone()
            if fenced is None:
                return False
            conn.execute(
                """UPDATE collector_provider_tasks SET polled_at=now(),
                   last_error_code=%s WHERE id=%s AND state='submitted'""",
                (error_code,ctx["id"]),
            )
            conn.execute(
                """UPDATE jobs SET state='queued',locked_by=NULL,
                   lease_until=NULL,updated_at=now() WHERE id=%s""",
                (ctx["job_id"],),
            )
            return True


def process_one(*, worker_id=None, connect=None, provider=None) -> dict:
    """One safe claim and provider operation. Never repeats uncertain submits."""
    _assert_worker_config()
    worker_id=worker_id or "collector-"+str(uuid4())[:16]
    if type(worker_id) is not str or not 1 <= len(worker_id) <= 100:
        raise ValueError("Invalid worker ID")
    task=_claim_next(worker_id,connect=connect)
    if task is None:
        return {"status":"idle"}
    # Only these two existing, whitelisted methods issue outbound local HTTP.
    api=provider or dtk
    if task["state"]=="not_submitted":
        # Cancel and configuration revocation can race with the claimed intent.
        # Claim is already consumed, so report uncertain unless we can prove
        # no request started. Never blindly retry in either case.
        try:
            _assert_worker_config()
            answer=api.submit("search_posts",task["prepared"]["params"])
        except Exception:
            _update_after_provider(task,next_state="unknown",
                                   error_code="submit_uncertain",connect=connect)
            return {"status":"unknown","run_id":task["run_id"],
                    "manual_reconciliation_required":True}
        stored=_update_after_provider(
            task,next_state="submitted",provider_task_id=answer["task_id"],
            provider_request_id=answer.get("provider_request_id"),connect=connect)
        return {"status":"submitted" if stored else "lost_lease",
                "run_id":task["run_id"],"provider_task_id":answer["task_id"]}
    try:
        answer=api.task(task["provider_task_id"])
    except Exception:
        _defer_poll(task,error_code="poll_unavailable",connect=connect)
        return {"status":"poll_deferred","run_id":task["run_id"]}
    state=answer["state"]
    if state in ("queued","running"):
        _defer_poll(task,connect=connect)
        return {"status":state,"run_id":task["run_id"]}
    if state=="done":
        # Provider responses can contain third-party media and personal data.
        # Save only a bounded status summary, NOT raw JSON/items or metrics.
        summary={"provider_state":"done",
                 "raw_result_persisted":False,
                 "inspection_pending":True,
                 "provider_has_result":answer.get("data") is not None}
        _update_after_provider(task,next_state="completed",summary=summary,connect=connect)
        return {"status":"completed","run_id":task["run_id"],
                "inspection_pending":True}
    _update_after_provider(task,next_state="failed",
                           error_code="provider_task_failed",connect=connect)
    return {"status":"failed","run_id":task["run_id"]}


def run_loop(*, poll_seconds=4.0, connect=None, provider=None, once=False,
             stop_requested=None) -> dict:
    if not 1 <= poll_seconds <= 300:
        raise ValueError("poll_seconds must be 1..300")
    _assert_worker_config()
    worker_id="collector-"+str(uuid4())[:16]
    count=0
    while not (stop_requested is not None and stop_requested()):
        recover_expired(connect=connect)
        try:
            result=process_one(worker_id=worker_id,connect=connect,provider=provider)
        except CollectorError as exc:
            # Fail closed: do not loop forever on a revoked/malformed claim.
            return {"status":"blocked","error_code":exc.code,"processed":count}
        if result["status"]!="idle":
            count+=1
        if once:
            return {"status":"stopped","processed":count,"last":result}
        time.sleep(poll_seconds)
    return {"status":"stopped","processed":count}


def main(argv=None):
    parser=argparse.ArgumentParser(description="Opt-in local DTK Collector worker")
    parser.add_argument("--once",action="store_true",
                        help="Only one claim/poll step; no live search without release")
    parser.add_argument("--interval",type=float,default=4.0)
    args=parser.parse_args(argv)
    state={"stop":False}
    def stop(_signum,_frame):
        state["stop"]=True
    signal.signal(signal.SIGINT,stop)
    signal.signal(signal.SIGTERM,stop)
    try:
        result=run_loop(once=args.once,poll_seconds=args.interval,
                        stop_requested=lambda:state["stop"])
    except (CollectorError,dtk.DTKError) as exc:
        print(json.dumps({"ok":False,"error_code":getattr(exc,"code","disabled")}))
        return 2
    print(json.dumps(result))
    return 0 if result["status"]!="blocked" else 2


if __name__=="__main__":
    raise SystemExit(main())
