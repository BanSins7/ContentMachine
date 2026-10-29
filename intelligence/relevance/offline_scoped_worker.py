"""M41 project-scoped OFFLINE_FIXTURE worker, never LIVE or paid.

The pre-M41 S4 claim_item/work_once scan all projects. The standalone CLI
must not silently process another tenant's fixture work. This worker copies
the *query shape* of that S4 claim with an explicit project equality before
any row lock/claim, then reuses S4's original fenced finish/fail logic.

NO provider import, no Opus, no M14 LIVE lease and no HTTP.
"""
from __future__ import annotations

from uuid import uuid4

from .offline_runs import _refresh
from .offline_worker import _stale_item, finish_item, fail_item
from .v1_contracts import project_id, positive, reject

VERSION="sre-cli-offline-fixture/v1"
EXHAUSTED_SQL="""SELECT r.run_id::text,r.project_id,i.id
 FROM sre_offline_runs r JOIN sre_offline_items i
 ON i.run_id=r.run_id AND i.project_id=r.project_id
 WHERE r.project_id=%s AND r.status IN ('queued','running')
 AND i.state='running' AND i.lease_until<now()
 AND i.attempts>=i.max_attempts
 ORDER BY i.lease_until,i.id
 FOR UPDATE OF r,i SKIP LOCKED LIMIT 1"""
CLAIM_SQL="""SELECT r.run_id::text,r.project_id,i.id,i.video_id,
 i.content_version_id,i.content_sha256,
 i.contexts,i.contexts_sha256,i.lease_generation
 FROM sre_offline_runs r JOIN sre_offline_items i ON i.run_id=r.run_id
 AND i.project_id=r.project_id
 WHERE r.project_id=%s AND r.status IN ('queued','running')
 AND NOT r.cancel_requested
 AND (i.state='queued' OR
 (i.state='running' AND i.lease_until<now() AND i.attempts<i.max_attempts))
 ORDER BY i.created_at,i.id
 FOR UPDATE OF r,i SKIP LOCKED LIMIT 1"""
UPDATE_ITEM="""UPDATE sre_offline_items SET
 state='running',attempts=attempts+1,worker_token=%s::uuid,
 lease_generation=%s,lease_until=now()+(%s * interval '1 second'),
 error_code=NULL,updated_at=now()
 WHERE id=%s AND project_id=%s AND state IN ('queued','running')
 RETURNING id"""
UPDATE_RUN="""UPDATE sre_offline_runs
 SET status='running',updated_at=now()
 WHERE run_id=%s::uuid AND project_id=%s"""
UPDATE_ALIAS="""UPDATE sre_classification_runs
 SET status='running' WHERE id=%s::uuid AND project_id=%s"""
WORKSPACE="SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR SHARE"


def _reap(conn,pid,maximum=32):
    changed=0
    for _ in range(maximum):
        with conn.transaction():
            if conn.execute(WORKSPACE,(pid,)).fetchone() is None:
                reject("project_id","SRE workspace absent","rights_denied")
            row=conn.execute(EXHAUSTED_SQL,(pid,)).fetchone()
            if row is None:break
            _stale_item(conn,*row)
            changed+=1
    return changed


def _claim(conn,pid,seconds):
    with conn.transaction():
        if conn.execute(WORKSPACE,(pid,)).fetchone() is None:
            reject("project_id","SRE workspace absent","rights_denied")
        row=conn.execute(CLAIM_SQL,(pid,)).fetchone()
        if row is None:return None
        run,owner,item,video,content_id,content_sha,contexts,ctx_sha,generation=row
        if owner!=pid or type(item) is not int or type(generation) is not int:
            reject("project_id","unscoped SRE offline claim","rights_denied")
        token=str(uuid4())
        next_generation=generation+1
        updated=conn.execute(UPDATE_ITEM,(
            token,next_generation,seconds,item,pid)).fetchone()
        if updated is None or updated[0]!=item:
            reject("worker_token","lost scoped offline claim","lease_lost")
        conn.execute(UPDATE_RUN,(run,pid))
        conn.execute(UPDATE_ALIAS,(run,pid))
        return {
            "run_id":run,"project_id":pid,"item_id":item,
            "video_id":video,"content_version_id":content_id,
            "content_sha256":content_sha,"contexts":contexts,
            "contexts_sha256":ctx_sha,"generation":next_generation,
            "worker_token":token,
        }


def work_once_for_project(conn,project,*,lease_seconds=60):
    """Safely process at most one approved offline fixture for this project.

    The CLI holds an operator-provided DB credential, NOT a browser claim.
    The connection MUST be dedicated to the permitted tenant/DB role. Only
    offline_fixture gets simulated labels; never serve as genuine LIVE M30.
    """
    pid=project_id(project)
    seconds=positive(lease_seconds,"lease_seconds",maximum=3600)
    if seconds<5:reject("lease_seconds","minimum 5 seconds")
    _reap(conn,pid)
    claim=_claim(conn,pid,seconds)
    if claim is None:
        return {"schema_version":VERSION,"project_id":pid,
                "status":"drained","processed":0,
                "paid_post_calls":0,"live_results_written":0}
    try:
        result=finish_item(conn,claim)
    except Exception:
        fail_item(conn,claim)
        raise
    return {
        "schema_version":VERSION,"project_id":pid,
        "status":"processed" if result else "lease_lost",
        "processed":1 if result else 0,
        "offline_fixture_only":True,
        "paid_post_calls":0,"live_results_written":0,
    }


__all__=["VERSION","CLAIM_SQL","EXHAUSTED_SQL","WORKSPACE",
         "work_once_for_project"]
