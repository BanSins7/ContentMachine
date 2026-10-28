"""GA20 transactional PostgreSQL step claims with fencing and worker leases.

Only GA04-created runs can be scheduled. Host attests QDP/media/current
rights; no paid/model calls, media reading or background execution here.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .contracts_v1 import media_manifest_v1
from .cost_resources import (
    RuntimeDenied,ResourceBudget,Stage,POOL_LOCK_A,POOL_LOCK_B,
    plan_for_kind,verify_plan,step_input_sha,safe_worker_id,safe_sha,
    safe_error,retry_seconds
)


@dataclass(frozen=True,slots=True)
class StepClaim:
    step_id: int
    run_id: str
    project_id: str
    step_key: str
    step_kind: str
    attempt_no: int
    attempt_id: int
    token: str
    worker_id: str
    input_sha256: str
    lease_until: object
    deadline_at: object
    cpu_slots: int
    gpu_slots: int


def _live(verify_current, context):
    if not callable(verify_current) or verify_current(dict(context)) is not True:
        raise RuntimeDenied("qdp_or_asset_rights_not_current")


def _uuid(value):
    if type(value) is not str:
        raise RuntimeDenied("run_id_invalid")
    try:
        if str(UUID(value))!=value:
            raise ValueError("noncanonical")
    except (ValueError,AttributeError) as exc:
        raise RuntimeDenied("run_id_invalid") from exc
    return value


def _scope(row):
    return {
        "run_id":str(row[0]),"project_id":row[1],"qdp_run_id":str(row[2]),
        "video_id":row[3],"source_id":row[4],
        "source_policy_version_id":row[5],
        "qdp_input_sha256":row[6],"manifest_sha256":row[7],
    }


def _run_row(conn,run_id,*,lock=False):
    return conn.execute(
        """SELECT id,project_id,qdp_run_id,video_id,source_id,
                  source_policy_version_id,qdp_input_sha256,manifest_sha256,
                  content_kind,state,expected_media_items
           FROM ga_runs WHERE id=%s::uuid""" + (" FOR UPDATE" if lock else ""),
        (run_id,)
    ).fetchone()


def _valid_run(conn,run_id,verify_current,*,lock=False):
    row=_run_row(conn,run_id,lock=lock)
    if row is None:
        raise RuntimeDenied("ga_run_missing")
    _live(verify_current,_scope(row))
    return row


def install_run_dag(conn,manifest,*,run_id,verify_current,budget=None,
                    stage_plan=None):
    """Declare/verify one immutable GA20 plan for an existing GA04 receipt.

    Must be called before first worker claim, not with an invented run id.
    """
    m=media_manifest_v1(manifest)
    _uuid(run_id)
    if not callable(getattr(conn,"transaction",None)):
        raise RuntimeDenied("transactional_connection_required")
    budget=ResourceBudget() if budget is None else budget
    if not isinstance(budget,ResourceBudget):
        raise RuntimeDenied("invalid_resource_budget")
    stages=verify_plan(
        plan_for_kind(m["content_kind"]) if stage_plan is None else stage_plan
    )
    expected_kind={
        "video":"technical","slideshow_video":"technical",
        "photo_carousel":"slides","image_post":"slides",
        "mixed_carousel":"mixed_slides"
    }[m["content_kind"]]
    if stages[0].key!="initial:v1" or stages[0].kind!=expected_kind:
        raise RuntimeDenied("dag_must_preserve_ga04_initial")
    if any(x.cpu_slots>budget.cpu_slots or x.gpu_slots>budget.gpu_slots
           for x in stages):
        raise RuntimeDenied("stage_exceeds_worker_budget")
    with conn.transaction():
        row=_valid_run(conn,run_id,verify_current,lock=True)
        if (row[1]!=m["project_id"] or str(row[2])!=m["qdp_run_id"]
            or row[3]!=m["video_id"] or row[4]!=m["source_id"]
            or row[5]!=m["source_policy_version_id"]
            or row[6]!=m["qdp_input_sha256"] or row[7]!=m["manifest_sha256"]
            or row[8]!=m["content_kind"] or row[10]!=len(m["items"])):
            raise RuntimeDenied("ga20_manifest_run_mismatch")
        if row[9] not in ("queued","partial"):
            raise RuntimeDenied("run_not_schedulable")
        conn.execute(
            """INSERT INTO ga_run_control(run_id) VALUES(%s::uuid)
               ON CONFLICT(run_id) DO NOTHING""",(run_id,)
        )
        for stage in stages:
            sha=step_input_sha(m["manifest_sha256"],stage)
            conn.execute(
                """INSERT INTO ga_steps(run_id,project_id,step_key,step_kind,
                                      input_sha256)
                   VALUES(%s::uuid,%s,%s,%s,%s)
                   ON CONFLICT(run_id,step_key) DO NOTHING""",
                (run_id,m["project_id"],stage.key,stage.kind,sha)
            )
            step=conn.execute(
                """SELECT id,step_kind,input_sha256 FROM ga_steps
                   WHERE run_id=%s::uuid AND step_key=%s FOR SHARE""",
                (run_id,stage.key)
            ).fetchone()
            if step is None or step[1:]!=(stage.kind,sha):
                raise RuntimeDenied("ga20_step_input_conflict")
            conn.execute(
                """INSERT INTO ga_step_resource_specs
                   (step_id,cpu_slots,gpu_slots,priority,max_runtime_sec,allow_retry)
                   VALUES(%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(step_id) DO NOTHING""",
                (step[0],stage.cpu_slots,stage.gpu_slots,stage.priority,
                 stage.max_runtime_sec,stage.allow_retry)
            )
            current=conn.execute(
                """SELECT cpu_slots,gpu_slots,priority,max_runtime_sec,allow_retry
                   FROM ga_step_resource_specs WHERE step_id=%s""",
                (step[0],)
            ).fetchone()
            if current != (
                stage.cpu_slots,stage.gpu_slots,stage.priority,
                stage.max_runtime_sec,stage.allow_retry
            ):
                raise RuntimeDenied("ga20_resource_profile_conflict")
        expected_edges={(x.key,y) for x in stages for y in x.depends_on}
        for key,depends in sorted(expected_edges):
            conn.execute(
                """INSERT INTO ga_step_dependencies(run_id,step_key,depends_on_step_key)
                   VALUES(%s::uuid,%s,%s) ON CONFLICT DO NOTHING""",
                (run_id,key,depends)
            )
        edges=conn.execute(
            """SELECT step_key,depends_on_step_key
               FROM ga_step_dependencies WHERE run_id=%s::uuid""",
            (run_id,)
        ).fetchall()
        if {tuple(x) for x in edges}!=expected_edges:
            raise RuntimeDenied("ga20_dag_conflict")
        count=conn.execute(
            "SELECT count(*) FROM ga_steps WHERE run_id=%s::uuid",(run_id,)
        ).fetchone()[0]
        if count!=len(stages):
            raise RuntimeDenied("ga20_foreign_step_in_existing_plan")
        _live(verify_current,_scope(row))
    return {"run_id":run_id,"stage_count":len(stages),
            "dag_sha256":__import__("hashlib").sha256(
                ("\n".join(
                    x.key+"|"+x.kind+"|"+",".join(x.depends_on)
                    for x in stages
                )).encode("utf-8")
            ).hexdigest()}


def _pool(conn):
    conn.execute("SELECT pg_advisory_xact_lock(%s,%s)",(POOL_LOCK_A,POOL_LOCK_B))


def _release(conn,attempt_id):
    conn.execute(
        """UPDATE ga_resource_claims
              SET state='released',released_at=now()
            WHERE attempt_id=%s AND state='active'""",
        (attempt_id,)
    )


def _expired(conn, *, limit=64):
    """Reclaim expired step leases, leaving the historical attempt as lost."""
    rows=conn.execute(
        """SELECT s.id,s.run_id,a.id,r.state
           FROM ga_steps s
           JOIN ga_step_attempts a ON a.step_id=s.id AND a.attempt_no=s.attempts
           JOIN ga_runs r ON r.id=s.run_id
           WHERE s.state='running' AND a.state='leased'
             AND (s.lease_until<=now() OR a.deadline_at<=now())
           ORDER BY s.lease_until,s.id
           LIMIT %s FOR UPDATE OF s,a SKIP LOCKED""",
        (limit,)
    ).fetchall()
    for step_id,run_id,attempt_id,state in rows:
        conn.execute(
            """UPDATE ga_step_attempts SET state='lost',finished_at=now(),
                   error_code='lease_expired'
               WHERE id=%s AND state='leased'""",(attempt_id,)
        )
        _release(conn,attempt_id)
        conn.execute(
            """UPDATE ga_steps SET state=%s,worker_id=NULL,lease_until=NULL,
                   failure_code='lease_expired',updated_at=now(),finished_at=now()
               WHERE id=%s""",
            ("failed" if state in ("queued","running","partial")
             else "cancelled",step_id)
        )


def _ready_retries(conn,limit=64):
    rows=conn.execute(
        """SELECT s.id,s.attempts,a.finished_at
           FROM ga_steps s
           JOIN ga_run_control c ON c.run_id=s.run_id
           JOIN ga_runs r ON r.id=s.run_id
           JOIN ga_step_resource_specs spec ON spec.step_id=s.id
           JOIN ga_step_attempts a ON a.step_id=s.id AND a.attempt_no=s.attempts
           WHERE s.state='failed' AND s.attempts<s.max_attempts
             AND spec.allow_retry=true AND c.paused=false
             AND r.state IN ('queued','running','partial')
             AND a.state IN ('failed','lost')
           ORDER BY a.finished_at,s.id
           LIMIT %s FOR UPDATE OF s SKIP LOCKED""",(limit,)
    ).fetchall()
    now=conn.execute("SELECT now()").fetchone()[0]
    for step_id,attempts,finished in rows:
        from datetime import timedelta
        if finished is not None and now>=finished+timedelta(seconds=retry_seconds(attempts)):
            conn.execute(
                """UPDATE ga_steps SET state='queued',worker_id=NULL,
                      lease_until=NULL,updated_at=now(),failure_code=NULL
                   WHERE id=%s""",(step_id,)
            )


def _terminal_failures(conn,limit=64):
    """Poison an unfinishable run rather than leave it silently queued forever."""
    rows=conn.execute(
        """SELECT r.id::text
           FROM ga_runs r
           WHERE r.state IN ('queued','running','partial')
             AND EXISTS (
               SELECT 1 FROM ga_steps s
               JOIN ga_step_resource_specs sp ON sp.step_id=s.id
               WHERE s.run_id=r.id AND s.state='failed'
                 AND (s.attempts>=s.max_attempts OR sp.allow_retry=false)
             )
           ORDER BY r.created_at LIMIT %s
           FOR UPDATE OF r SKIP LOCKED""",(limit,)
    ).fetchall()
    for (rid,) in rows:
        active=conn.execute(
            """SELECT s.id,a.id FROM ga_steps s
               JOIN ga_step_attempts a ON a.step_id=s.id
                 AND a.attempt_no=s.attempts AND a.state='leased'
               WHERE s.run_id=%s::uuid AND s.state='running'
               FOR UPDATE OF s,a""",(rid,)
        ).fetchall()
        for sid,aid in active:
            conn.execute(
                """UPDATE ga_step_attempts SET state='cancelled',
                     finished_at=now(),error_code='dependency_failed'
                   WHERE id=%s""",(aid,)
            )
            _release(conn,aid)
            conn.execute(
                """UPDATE ga_steps SET state='cancelled',worker_id=NULL,
                    lease_until=NULL,updated_at=now(),
                    failure_code='dependency_failed',finished_at=now()
                   WHERE id=%s""",(sid,)
            )
        conn.execute(
            """UPDATE ga_steps SET state='blocked',updated_at=now(),
                   failure_code='dependency_failed',finished_at=now()
               WHERE run_id=%s::uuid AND state='queued'""",
            (rid,)
        )
        conn.execute(
            """UPDATE ga_runs SET state='blocked',worker_id=NULL,
                   lease_until=NULL,updated_at=now(),finished_at=now()
               WHERE id=%s::uuid""",(rid,)
        )


def recover_worker_leases(conn,*,limit=64):
    """Host may call on process restart; never re-executes paid operations."""
    if not callable(getattr(conn,"transaction",None)):
        raise RuntimeDenied("transactional_connection_required")
    if type(limit) is not int or not 1<=limit<=256:
        raise RuntimeDenied("invalid_recovery_limit")
    with conn.transaction():
        _pool(conn)
        _expired(conn,limit=limit)
        _ready_retries(conn,limit=limit)
        _terminal_failures(conn,limit=limit)


def claim_next_step(conn,*,worker_id,budget,verify_current):
    """SKIP LOCKED claim + global pool advisory lock: no duplicate live claim.

    Returns None on empty/backpressure. All long processing happens AFTER
    the transaction exits. Host current-rights verification is mandatory.
    """
    safe_worker_id(worker_id)
    if not isinstance(budget,ResourceBudget):
        raise RuntimeDenied("invalid_resource_budget")
    if not callable(getattr(conn,"transaction",None)):
        raise RuntimeDenied("transactional_connection_required")
    with conn.transaction():
        _pool(conn)
        _expired(conn)
        _ready_retries(conn)
        _terminal_failures(conn)
        usage=conn.execute(
            """SELECT coalesce(sum(cpu_slots),0),coalesce(sum(gpu_slots),0),
                      count(*) FROM ga_resource_claims WHERE state='active'"""
        ).fetchone()
        candidates=conn.execute(
            """SELECT s.id,s.run_id::text,s.project_id,s.step_key,s.step_kind,
                      s.attempts,s.input_sha256,s.max_attempts,
                      sp.cpu_slots,sp.gpu_slots,sp.max_runtime_sec,
                      r.id,r.project_id,r.qdp_run_id,r.video_id,r.source_id,
                      r.source_policy_version_id,r.qdp_input_sha256,r.manifest_sha256
               FROM ga_steps s
               JOIN ga_runs r ON r.id=s.run_id
               JOIN ga_step_resource_specs sp ON sp.step_id=s.id
               JOIN ga_run_control c ON c.run_id=s.run_id
               WHERE s.state='queued' AND s.attempts<s.max_attempts
                 AND r.state IN ('queued','running','partial')
                 AND c.paused=false
                 AND NOT EXISTS (
                   SELECT 1 FROM ga_step_dependencies d
                   LEFT JOIN ga_steps upstream
                     ON upstream.run_id=d.run_id
                    AND upstream.step_key=d.depends_on_step_key
                   WHERE d.run_id=s.run_id AND d.step_key=s.step_key
                     AND (upstream.id IS NULL OR
                          upstream.state NOT IN ('completed','partial'))
                 )
               ORDER BY sp.priority DESC,s.created_at,s.id
               LIMIT 64 FOR UPDATE OF s SKIP LOCKED"""
        ).fetchall()
        for c in candidates:
            (sid,rid,pid,key,kind,attempts,input_sha,max_attempt,
             cpu,gpu,max_runtime,*r)=c
            if (usage[0]+cpu>budget.cpu_slots
                or usage[1]+gpu>budget.gpu_slots
                or usage[2]+1>budget.max_concurrent_jobs):
                continue
            context=_scope(r)
            if not callable(verify_current) or verify_current(context) is not True:
                # Never claim potentially revoked source/QDP.
                continue
            update=conn.execute(
                """UPDATE ga_steps SET state='running',attempts=attempts+1,
                       worker_id=%s,
                       lease_until=LEAST(now()+%s*interval '1 second',
                                         now()+%s*interval '1 second'),
                       deadline_at=now()+%s*interval '1 second',
                       updated_at=now(),failure_code=NULL,finished_at=NULL
                   WHERE id=%s AND state='queued'
                   RETURNING attempts,lease_until,deadline_at""",
                (worker_id,budget.lease_seconds,max_runtime,max_runtime,sid)
            ).fetchone()
            if update is None:
                raise RuntimeDenied("concurrent_step_claim_lost")
            num,leased,deadline=update
            attempt=conn.execute(
                """INSERT INTO ga_step_attempts
                   (step_id,run_id,attempt_no,worker_id,lease_until,deadline_at)
                   VALUES(%s,%s::uuid,%s,%s,%s,%s)
                   RETURNING id,token::text""",
                (sid,rid,num,worker_id,leased,deadline)
            ).fetchone()
            conn.execute(
                """INSERT INTO ga_resource_claims(attempt_id,cpu_slots,gpu_slots,state)
                   VALUES(%s,%s,%s,'active')""",
                (attempt[0],cpu,gpu)
            )
            return StepClaim(sid,rid,pid,key,kind,num,attempt[0],attempt[1],
                             worker_id,input_sha,leased,deadline,cpu,gpu)
    return None


def _owned_attempt(conn,claim):
    if not isinstance(claim,StepClaim):
        raise RuntimeDenied("invalid_step_claim")
    step=conn.execute(
        """SELECT s.state,s.attempts,s.worker_id,s.lease_until,
                  s.input_sha256,r.state,s.deadline_at
           FROM ga_steps s JOIN ga_runs r ON r.id=s.run_id
           WHERE s.id=%s AND s.run_id=%s::uuid FOR UPDATE OF s""",
        (claim.step_id,claim.run_id)
    ).fetchone()
    attempt=conn.execute(
        """SELECT token::text,state,lease_until,deadline_at
           FROM ga_step_attempts WHERE id=%s AND step_id=%s
           FOR UPDATE""",(claim.attempt_id,claim.step_id)
    ).fetchone()
    now=conn.execute("SELECT now()").fetchone()[0]
    if (step is None or attempt is None
        or step[0]!="running" or step[1]!=claim.attempt_no
        or step[2]!=claim.worker_id or step[4]!=claim.input_sha256
        or step[5] not in ("queued","running","partial")
        or attempt[0]!=claim.token or attempt[1]!="leased"
        or step[3] is None or step[3]<=now
        or attempt[2]<=now or attempt[3]<=now or step[6]<=now):
        raise RuntimeDenied("stale_or_revoked_step_lease")
    return step,attempt


def heartbeat_step(conn,claim,*,lease_seconds,verify_current):
    if type(lease_seconds) is not int or not 5<=lease_seconds<=300:
        raise RuntimeDenied("invalid_lease_extension")
    with conn.transaction():
        _pool(conn)
        _owned_attempt(conn,claim)
        _valid_run(conn,claim.run_id,verify_current)
        row=conn.execute(
            """UPDATE ga_steps SET
                  lease_until=LEAST(now()+%s*interval '1 second',deadline_at)
               WHERE id=%s RETURNING lease_until""",
            (lease_seconds,claim.step_id)
        ).fetchone()
        conn.execute(
            """UPDATE ga_step_attempts SET lease_until=%s WHERE id=%s""",
            (row[0],claim.attempt_id)
        )
    return row[0]


def finish_step(conn,claim,*,output_sha256=None,partial=False,
                error_code=None,verify_current,verify_output=None):
    """Write only a source-authenticated output, and fence stale workers.

    Failed stage writes are not results; retry is only from GA-owned local
    step specs with explicit max attempts and exponential backoff.
    """
    if (error_code is None)==(output_sha256 is None):
        raise RuntimeDenied("provide_exactly_one_output_or_error")
    if type(partial) is not bool:
        raise RuntimeDenied("invalid_partial_status")
    if error_code is not None:
        safe_error(error_code)
        if partial:
            raise RuntimeDenied("partial_error_conflict")
    else:
        safe_sha(output_sha256)
        if not callable(verify_output):
            raise RuntimeDenied("trusted_output_verifier_required")
    with conn.transaction():
        _pool(conn)
        _owned_attempt(conn,claim)
        _valid_run(conn,claim.run_id,verify_current)
        if output_sha256 is not None and verify_output(claim,output_sha256) is not True:
            raise RuntimeDenied("unverified_step_output")
        state=("failed" if error_code else "partial" if partial else "completed")
        conn.execute(
            """UPDATE ga_step_attempts SET state=%s,finished_at=now(),
                  output_sha256=%s,error_code=%s WHERE id=%s""",
            (state,output_sha256,error_code,claim.attempt_id)
        )
        conn.execute(
            """UPDATE ga_steps SET state=%s,worker_id=NULL,lease_until=NULL,
                  output_sha256=%s,failure_code=%s,
                  finished_at=now(),updated_at=now()
               WHERE id=%s""",
            (state,output_sha256,error_code,claim.step_id)
        )
        _release(conn,claim.attempt_id)
    return state


def lease_is_live(conn,claim):
    """Cooperative worker cancellation check, not permission attestation."""
    if not isinstance(claim,StepClaim):
        return False
    result=conn.execute(
        """SELECT 1 FROM ga_steps s
           JOIN ga_runs r ON r.id=s.run_id
           JOIN ga_step_attempts a ON a.step_id=s.id
             AND a.attempt_no=s.attempts
           WHERE s.id=%s AND s.run_id=%s::uuid
             AND s.worker_id=%s AND s.state='running'
             AND s.lease_until>now() AND s.deadline_at>now()
             AND r.state IN ('queued','running','partial')
             AND a.id=%s AND a.token=%s::uuid AND a.state='leased'
             AND a.lease_until>now() AND a.deadline_at>now()""",
        (claim.step_id,claim.run_id,claim.worker_id,claim.attempt_id,
         claim.token)
    ).fetchone()
    return result is not None


def pause_run(conn,run_id,*,paused,verify_current):
    _uuid(run_id)
    if type(paused) is not bool:
        raise RuntimeDenied("invalid_pause_value")
    with conn.transaction():
        _pool(conn)
        r=_valid_run(conn,run_id,verify_current,lock=True)
        if r[9] not in ("queued","running","partial"):
            raise RuntimeDenied("run_not_pausable")
        conn.execute(
            """INSERT INTO ga_run_control(run_id,paused,updated_at)
               VALUES(%s::uuid,%s,now())
               ON CONFLICT(run_id) DO UPDATE SET
                 paused=EXCLUDED.paused,updated_at=now()""",
            (run_id,paused)
        )
    return "paused" if paused else "resumed"


def reuse_verified_cache(conn,*,run_id,step_key,media_asset_id,
                         verify_current,verify_cache):
    """Commit exact GA-owned per-run cache only with fresh byte attestation.

    Never assumes a cache SHA is authenticated or shares a result between
    unrelated QDP runs. This is an optional local cache optimization.
    """
    from .cost_resources import CACHE_KINDS,STREAM_REFS
    _uuid(run_id)
    if type(step_key) is not str or type(media_asset_id) is not int or media_asset_id<1:
        raise RuntimeDenied("invalid_cache_selection")
    if not callable(verify_cache):
        raise RuntimeDenied("trusted_cache_verifier_required")
    with conn.transaction():
        _pool(conn)
        r=_valid_run(conn,run_id,verify_current,lock=True)
        if r[9] not in ("queued","partial"):
            raise RuntimeDenied("cache_run_not_active")
        step=conn.execute(
            """SELECT s.id,s.step_kind,s.input_sha256,s.state,s.attempts,
                      s.max_attempts,c.paused
               FROM ga_steps s JOIN ga_run_control c ON c.run_id=s.run_id
               WHERE s.run_id=%s::uuid AND s.step_key=%s FOR UPDATE OF s""",
            (run_id,step_key)
        ).fetchone()
        if (step is None or step[3]!="queued" or step[4]!=0 or step[6]):
            raise RuntimeDenied("cache_step_not_fresh_queued")
        kind=STREAM_REFS.get(step[1])
        if kind is None:
            raise RuntimeDenied("cache_not_supported_for_stage")
        cache=conn.execute(
            """SELECT c.result_sha256,mr.asset_sha256,
                      (c.expires_at IS NULL OR c.expires_at>now())
               FROM ga_cache_refs c JOIN ga_media_refs mr
                 ON mr.run_id=c.run_id AND mr.media_asset_id=c.media_asset_id
               WHERE c.run_id=%s::uuid AND c.media_asset_id=%s
                 AND c.cache_kind=%s AND c.input_sha256=%s
               FOR SHARE OF c,mr""",
            (run_id,media_asset_id,kind,step[2])
        ).fetchone()
        if cache is None or cache[2] is not True:
            raise RuntimeDenied("cache_missing_or_expired")
        src={
            **_scope(r),"step_key":step_key,"step_kind":step[1],
            "media_asset_id":media_asset_id,"asset_sha256":cache[1],
            "cache_output_sha256":cache[0],
        }
        _live(verify_current,src)
        if verify_cache(dict(src)) is not True:
            raise RuntimeDenied("cached_media_bytes_or_origin_invalid")
        safe_sha(cache[0])
        attempt=conn.execute(
            """INSERT INTO ga_step_attempts
               (step_id,run_id,attempt_no,worker_id,state,lease_until,
                deadline_at,finished_at,output_sha256)
               VALUES (%s,%s::uuid,1,'verified-cache','completed',
                       now(),now(),now(),%s) RETURNING id""",
            (step[0],run_id,cache[0])
        ).fetchone()
        conn.execute(
            """UPDATE ga_steps SET state='completed',attempts=1,
                  output_sha256=%s,finished_at=now(),updated_at=now(),
                  worker_id=NULL,lease_until=NULL WHERE id=%s""",
            (cache[0],step[0])
        )
        _live(verify_current,src)
        if verify_cache(dict(src)) is not True:
            raise RuntimeDenied("cache_revoked_before_commit")
    return {"state":"completed","step_key":step_key,
            "output_sha256":cache[0],"source":"verified_local_cache"}


def retry_local_step(conn,*,run_id,step_key,verify_current):
    """Explicit operator retry, only after a safely failed *local* attempt.

    Never resets counters, terminal completed outputs, paid/nonlocal jobs,
    active leases or a blocked/cancelled/invalidated run. Backoff still applies.
    """
    from datetime import timedelta
    from .cost_resources import KEY,retry_seconds
    _uuid(run_id)
    if type(step_key) is not str or KEY.fullmatch(step_key) is None:
        raise RuntimeDenied("invalid_stage_key")
    with conn.transaction():
        _pool(conn)
        run=_valid_run(conn,run_id,verify_current,lock=True)
        if run[9] not in ("queued","running","partial"):
            raise RuntimeDenied("run_not_retryable")
        current=conn.execute(
            """SELECT s.id,s.state,s.attempts,s.max_attempts,
                      sp.allow_retry,sp.cpu_slots,sp.gpu_slots,
                      a.state,a.finished_at
               FROM ga_steps s
               JOIN ga_step_resource_specs sp ON sp.step_id=s.id
               LEFT JOIN ga_step_attempts a ON a.step_id=s.id
                  AND a.attempt_no=s.attempts
               WHERE s.run_id=%s::uuid AND s.step_key=%s
               FOR UPDATE OF s""",(run_id,step_key)
        ).fetchone()
        if (current is None or current[1]!="failed" or
            current[2]<1 or current[2]>=current[3] or
            current[4] is not True or current[7] not in ("failed","lost")):
            raise RuntimeDenied("stage_not_safe_to_retry")
        now=conn.execute("SELECT now()").fetchone()[0]
        if current[8] is None or now<current[8]+timedelta(
            seconds=retry_seconds(current[2])
        ):
            raise RuntimeDenied("retry_backoff_active")
        conn.execute(
            """UPDATE ga_steps SET state='queued',updated_at=now(),
                  worker_id=NULL,lease_until=NULL,failure_code=NULL
               WHERE id=%s AND state='failed'""",(current[0],)
        )
        _live(verify_current,_scope(run))
    return {"run_id":run_id,"step_id":current[0],
            "step_key":step_key,"state":"queued",
            "previous_attempts":current[2]}
