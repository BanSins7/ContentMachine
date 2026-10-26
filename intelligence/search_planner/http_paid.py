"""SP-13 HTTP paid one-shot idempotency barrier above canonical SQG ledger.

Project+key is durable BEFORE calling the one paid batch. Unknown outcomes
are marked uncertain and never submitted again. This layer does not grant
financial consent; the SQG worker independently validates consent, quote,
budget, source locks and dispatch_intent before any provider POST.
"""
from __future__ import annotations

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, canonical, sha256
from intelligence.taxonomy_builder.pipeline_repository import _pid,_connect
from intelligence.search_planner.contracts import publication_uuid

ACTION="execute_one"
SCHEMA="search-planner-http-paid-dispatch/v1"


def _key(v):
    import re
    if type(v) is not str or re.fullmatch(r"[A-Za-z0-9_.:-]{12,128}",v) is None:
        raise PipelineError("Invalid HTTP paid dispatch idempotency key",422)
    return v


def one_paid_batch_with_idempotency(project_id,generation_id,idempotency_key,
                                    *,executor,connect=None):
    """One explicit owner-gated HTTP operation with durable retry protection.

    executor is a trusted Python method assembled server-side, never JSON.
    No exceptions escape with provider API keys, raw request/response or
    source tokens. An ambiguous exception is NOT evidence of zero charge.
    """
    pid,gid=_pid(project_id),publication_uuid(generation_id)
    key=_key(idempotency_key)
    if not callable(executor):
        raise PipelineError("Paid executor is not an installed server port",503)
    digest=sha256({"schema_version":SCHEMA,"project_id":pid,
                   "generation_id":gid,"action":ACTION})
    factory=connect if connect is not None else _connect
    with factory() as conn:
        with conn.transaction():
            scope=conn.execute(
                "SELECT id FROM project_registry WHERE id=%s FOR UPDATE",
                (pid,),
            ).fetchone()
            if scope is None:
                raise PipelineError("Project not registered in PostgreSQL",404)
            previous=conn.execute(
                """SELECT id,generation_id,request_sha256,state,result
                     FROM sp_http_paid_actions
                    WHERE project_id=%s AND idempotency_key=%s""",
                (pid,key),
            ).fetchone()
            if previous is not None:
                rowid,origin,sha,state,result=previous
                if str(origin)!=gid or sha!=digest:
                    raise PipelineError("Paid HTTP key reused for another request",409)
                if state=="completed":
                    if type(result) is not dict:
                        raise PipelineError("Completed paid HTTP snapshot corrupted",409)
                    return {
                        "schema_version":SCHEMA,"project_id":pid,
                        "generation_id":gid,"idempotency_key":key,
                        "status":"completed","reused":True,"result":result,
                        "worker_calls_in_this_request":0,
                    }
                # An in-flight, lost, or uncertain submit result can never
                # authorize the NEXT pending batch on the same key.
                return {
                    "schema_version":SCHEMA,"project_id":pid,
                    "generation_id":gid,"idempotency_key":key,
                    "status":"needs_attention","reused":True,
                    "reason":"previous_paid_submit_may_have_run_no_auto_retry",
                    "worker_calls_in_this_request":0,
                }
            inserted=conn.execute(
                """INSERT INTO sp_http_paid_actions
                      (project_id,generation_id,idempotency_key,request_sha256)
                   VALUES (%s,%s::uuid,%s,%s) RETURNING id""",
                (pid,gid,key,digest),
            ).fetchone()
            if inserted is None:
                raise PipelineError("Unable to durably reserve paid HTTP call",409)
            action_id=str(inserted[0])
    # Reservation is COMMITTED. Never hold a DB lock around an external POST.
    # The HTTP server's bounded semaphore prevents intra-process parallel
    # requests; SQL project lock + unique key handles cross-process replay.
    try:
        result=executor(pid,gid)
        if type(result) is not dict:
            raise PipelineError("Paid worker returned an invalid outcome",409)
        serialized=canonical(result)
    except Exception:
        with factory() as conn:
            with conn.transaction():
                conn.execute(
                    """UPDATE sp_http_paid_actions
                          SET state='uncertain',finished_at=now()
                        WHERE id=%s::uuid AND state='in_progress'""",
                    (action_id,),
                )
        # Unknown charge/outcome MUST NOT become a retryable 500 in the UI.
        return {
            "schema_version":SCHEMA,"project_id":pid,
            "generation_id":gid,"idempotency_key":key,
            "status":"needs_attention","reused":False,
            "reason":"paid_worker_outcome_unknown_reconcile_financial_ledger",
            "worker_calls_in_this_request":1,
        }
    with factory() as conn:
        with conn.transaction():
            done=conn.execute(
                """UPDATE sp_http_paid_actions
                      SET state='completed',result=%s::jsonb,finished_at=now()
                    WHERE id=%s::uuid AND state='in_progress'
                    RETURNING id""",
                (serialized,action_id),
            ).fetchone()
            if done is None:
                raise PipelineError("Paid operation cannot be completed twice",409)
    return {
        "schema_version":SCHEMA,"project_id":pid,
        "generation_id":gid,"idempotency_key":key,
        "status":"completed","reused":False,"result":result,
        "worker_calls_in_this_request":1,
    }
