"""S4 durable, fenced OFFLINE worker: only deterministic local fake evaluation.

No model connector, HTTP, network requests, token accounting or paid dispatch.
One complete item transaction records all per-hypothesis labels and stage trace,
or records none. A stale worker can never commit a result after a reclaim.
"""
from __future__ import annotations

import json
from uuid import uuid4

from .live_content_gate import read_live_content_gate
from .offline_fake import FAKE_MODEL, evaluate_fake
from .offline_runs import _lock_run, _refresh
from .v1_contracts import SREContractError, canonical_uuid, fingerprint, reject


def _stale_item(conn, run, project, ident):
    """With run row locked, safely retire an exhausted lease."""
    conn.execute(
        """UPDATE sre_offline_items SET state='failed',worker_token=NULL,
             lease_until=NULL,error_code='lease_exhausted',updated_at=now(),
             finished_at=now()
           WHERE id=%s AND run_id=%s::uuid AND project_id=%s
             AND state='running' AND lease_until<now()
             AND attempts>=max_attempts""", (ident,run,project),
    )
    _refresh(conn,project,run)


def reap_exhausted(conn, *, limit=100):
    """Permanently fail exhausted expired leases; never auto-repeat endlessly."""
    if type(limit) is not int or not 1<=limit<=1000:
        reject("limit","expected integer 1..1000")
    n=0
    # Lock order is always run FIRST and item SECOND (claim / finish / cancel).
    for _ in range(limit):
        with conn.transaction():
            row=conn.execute(
                """SELECT r.run_id::text,r.project_id,i.id
                   FROM sre_offline_runs r
                   JOIN sre_offline_items i ON i.run_id=r.run_id
                     AND i.project_id=r.project_id
                   WHERE r.status IN ('queued','running')
                     AND i.state='running' AND i.lease_until<now()
                     AND i.attempts>=i.max_attempts
                   ORDER BY i.lease_until,i.id
                   FOR UPDATE OF r,i SKIP LOCKED LIMIT 1""",
            ).fetchone()
            if not row:
                break
            _stale_item(conn,*row)
            n+=1
    return n


def claim_item(conn, *, lease_seconds=60):
    """Claim a durable item with two-layer run/item row lock + fencing token."""
    if type(lease_seconds) is not int or not 5<=lease_seconds<=3600:
        reject("lease_seconds","expected integer 5..3600")
    with conn.transaction():
        row=conn.execute(
            """SELECT r.run_id::text,r.project_id,i.id,i.video_id,
                      i.content_version_id,i.content_sha256,
                      i.contexts,i.contexts_sha256,i.lease_generation
               FROM sre_offline_runs r
               JOIN sre_offline_items i ON i.run_id=r.run_id
                 AND i.project_id=r.project_id
               WHERE r.status IN ('queued','running') AND NOT r.cancel_requested
                 AND (i.state='queued' OR
                     (i.state='running' AND i.lease_until<now()
                      AND i.attempts<i.max_attempts))
               ORDER BY i.created_at,i.id
               FOR UPDATE OF r,i SKIP LOCKED LIMIT 1""",
        ).fetchone()
        if not row:
            return None
        run,project,ident,video,content_id,content_sha,contexts,ctx_sha,generation=row
        token=str(uuid4())
        next_generation=generation+1
        conn.execute(
            """UPDATE sre_offline_items SET
                 state='running',attempts=attempts+1,
                 worker_token=%s::uuid,lease_generation=%s,
                 lease_until=now()+(%s * interval '1 second'),
                 error_code=NULL,updated_at=now()
               WHERE id=%s""",
            (token,next_generation,lease_seconds,ident),
        )
        conn.execute(
            """UPDATE sre_offline_runs SET status='running',updated_at=now()
               WHERE run_id=%s::uuid AND project_id=%s""", (run,project),
        )
        conn.execute(
            """UPDATE sre_classification_runs SET status='running'
               WHERE id=%s::uuid AND project_id=%s""", (run,project),
        )
        return {"run_id":run,"project_id":project,"item_id":ident,
                "video_id":video,"content_version_id":content_id,
                "content_sha256":content_sha,"contexts":contexts,
                "contexts_sha256":ctx_sha,"generation":next_generation,
                "worker_token":token}


def heartbeat(conn, claim, *, lease_seconds=60):
    if type(lease_seconds) is not int or not 5<=lease_seconds<=3600:
        reject("lease_seconds","expected integer 5..3600")
    with conn.transaction():
        run=_lock_run(conn,claim["project_id"],claim["run_id"])
        if run[2] or run[1] not in ("queued","running"):
            return False
        row=conn.execute(
            """UPDATE sre_offline_items SET
               lease_until=now()+(%s * interval '1 second'),updated_at=now()
               WHERE id=%s AND run_id=%s::uuid AND project_id=%s
                AND state='running' AND lease_until>now()
                AND lease_generation=%s AND worker_token=%s::uuid
               RETURNING id""",
            (lease_seconds,claim["item_id"],claim["run_id"],
             claim["project_id"],claim["generation"],claim["worker_token"]),
        ).fetchone()
        return row is not None


def _live_claim(conn, claim):
    run=_lock_run(conn,claim["project_id"],claim["run_id"])
    row=conn.execute(
        """SELECT id,state,lease_until,lease_generation,worker_token::text,
                  content_sha256,contexts,contexts_sha256,content_version_id,
                  video_id
           FROM sre_offline_items
           WHERE id=%s AND project_id=%s AND run_id=%s::uuid
           FOR UPDATE""",
        (claim["item_id"],claim["project_id"],claim["run_id"]),
    ).fetchone()
    if (row is None or run[2] or run[1] not in ("running","queued")
        or row[1] != "running" or row[2] is None
        or row[3] != claim["generation"] or row[4] != claim["worker_token"]
        or row[5] != claim["content_sha256"]
        or row[7] != claim["contexts_sha256"]
        or row[8] != claim["content_version_id"]
        or row[9] != claim["video_id"]):
        return None
    valid=conn.execute("SELECT %s::timestamptz>now()", (row[2],)).fetchone()[0]
    if not valid:
        return None
    if fingerprint(row[6]) != row[7] or fingerprint(claim["contexts"]) != row[7]:
        reject("contexts","pinned hypothesis context was modified","snapshot_mismatch")
    return run,row


def finish_item(conn, claim, *, fault=None):
    """Atomic all-or-nothing settlement. fault is test-only crash injection.

    The offline evaluation is repeatable. For a simulation, compute under the
    same DB transaction as result insertion; this also rechecks revocable D1
    rights. No external call under this transaction (ever).
    """
    with conn.transaction():
        live=_live_claim(conn,claim)
        if live is None:
            return False
        run,row=live
        project=claim["project_id"]
        ident=claim["item_id"]
        status="completed"
        error_code=None
        try:
            gate=read_live_content_gate(
                conn,project,row[8],phase="before_offline_compute",
                pins={"video_id":row[9],"content_version_id":row[8],
                      "content_sha256":row[5]},
            )
            content=gate["content"]
            labels=evaluate_fake(content,row[6])["labels"]
            # This is deliberately a test seam for transaction rollback
            # verification; never selected by a public HTTP API.
            if fault=="before_commit":
                raise RuntimeError("injected_before_commit")
        except SREContractError as exc:
            if exc.code in ("rights_denied","rights_expired"):
                status="blocked_rights"
                error_code=exc.code
            else:
                raise
        if status=="completed":
            input_sha=fingerprint({
                "content_sha256":row[5],"contexts_sha256":row[7],
                "mode":"offline_fixture_s4",
            })
            output_sha=fingerprint(labels)
            fake_trace=[{"stage_id":"offline_fake_s4","provider":"offline_fixture",
                         "model_id":FAKE_MODEL["model_id"],
                         "model_version":FAKE_MODEL["model_version"],
                         "input_sha256":input_sha,"output_sha256":output_sha,
                         "synthetic":True,"cost_usd":"0.00000000"}]
            tax_id=run[3]
            for label in labels:
                conn.execute(
                    """INSERT INTO sre_classification_results
                       (run_id,project_id,video_id,content_version_id,
                        taxonomy_version_id,category_key,decision,reason,
                        evidence,stage_trace)
                       VALUES(%s::uuid,%s,%s,%s,%s,%s::uuid,%s,%s,%s::jsonb,%s::jsonb)""",
                    (claim["run_id"],project,row[9],row[8],tax_id,
                     label["category_key"],label["decision"],label["reason"],
                     json.dumps(label["evidence"],ensure_ascii=False),
                     json.dumps(fake_trace)),
                )
            conn.execute(
                """INSERT INTO sre_stage_executions
                   (run_id,project_id,video_id,stage_id,model_ref,provider,
                    model_id,model_version,prompt_version,input_sha256,
                    output_sha256,status,input_tokens,output_tokens,cost_usd,
                    finished_at)
                   VALUES (%s::uuid,%s,%s,'offline_fake_s4','offline_fake',
                           'offline_fixture',%s,%s,%s,%s,%s,
                           'completed',0,0,0,now())""",
                (claim["run_id"],project,row[9],FAKE_MODEL["model_id"],
                 FAKE_MODEL["model_version"],FAKE_MODEL["prompt_version"],
                 input_sha,output_sha),
            )
        conn.execute(
            """UPDATE sre_offline_items SET state=%s,error_code=%s,
               worker_token=NULL,lease_until=NULL,finished_at=now(),
               updated_at=now()
               WHERE id=%s AND lease_generation=%s""",
            (status,error_code,ident,claim["generation"]),
        )
        _refresh(conn,project,claim["run_id"])
        return True


def fail_item(conn, claim, error_code="offline_execution_error"):
    """Retry ONLY safe offline failures, bounded; stale owners cannot mutate."""
    if error_code not in ("offline_execution_error","invalid_fake_output"):
        reject("error_code","invalid offline failure code")
    with conn.transaction():
        live=_live_claim(conn,claim)
        if live is None:
            return False
        _,row=live
        attempts=conn.execute(
            "SELECT attempts,max_attempts FROM sre_offline_items WHERE id=%s",
            (claim["item_id"],),
        ).fetchone()
        terminal=attempts[0]>=attempts[1]
        conn.execute(
            """UPDATE sre_offline_items SET state=%s,error_code=%s,
               worker_token=NULL,lease_until=NULL,
               finished_at=CASE WHEN %s THEN now() ELSE NULL END,
               updated_at=now() WHERE id=%s""",
            ("failed" if terminal else "queued",error_code,terminal,claim["item_id"]),
        )
        _refresh(conn,claim["project_id"],claim["run_id"])
        return True


def work_once(conn, *, lease_seconds=60, fault=None):
    """One actual job or false when drained. Test faults are not production retries."""
    reap_exhausted(conn)
    claim=claim_item(conn,lease_seconds=lease_seconds)
    if not claim:
        return False
    try:
        result=finish_item(conn,claim,fault=fault)
    except Exception:
        # Runtime errors roll back ALL classification rows and stage receipts.
        fail_item(conn,claim)
        raise
    return result
