"""M15 fail-stop LIVE cancellation, pause, pre-dispatch restart and crash recovery.

The service is internal and requires a separately authenticated project actor.
Every mutation serializes with M14 claims via EXCLUSIVE workspace row lock,
then locks the LIVE run and its technical attempt witnesses. No provider, paid
POST, finance approval, automatic reconciliation or cost claims here.

Crucial rule: a durable dispatch_intent may indicate a charged remote call.
Without an audited receipt such work becomes UNKNOWN and is NEVER auto-retried.
"""
from __future__ import annotations

from .v1_contracts import canonical_uuid, fingerprint, project_id, reject
from .live_planner import _verified_inputs

_WORKSPACE = "SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR UPDATE"
_RUN = """SELECT state,cancel_requested,manifest,manifest_sha256,
                 publication_id::text,pipeline_config_id,execution_mode
          FROM sre_live_runs WHERE project_id=%s AND run_id=%s::uuid FOR UPDATE"""
_ATTEMPTS = """SELECT item_id,state FROM sre_live_attempt_fences
               WHERE project_id=%s AND run_id=%s::uuid
               ORDER BY item_id,created_at,attempt_id FOR UPDATE"""
_POST = frozenset(("dispatch_intent","unknown","completed","failed_known"))
_TERMINAL = frozenset(("cancelled","completed","partial","failed"))
_PAUSES = frozenset(("paused_budget","paused_rights","waiting_source"))
_STOPPABLE = frozenset(("planned","pending_consent","queued","running")) | _PAUSES


def _lock(conn,pid,rid):
    if conn.execute(_WORKSPACE,(pid,)).fetchone() is None:
        reject("project_id","SRE workspace missing","rights_denied")
    row=conn.execute(_RUN,(pid,rid)).fetchone()
    if row is None or row[6]!="live":
        reject("run_id","LIVE run unavailable in project","rights_denied")
    return row


def _attempts(conn,pid,rid):
    rows=conn.execute(_ATTEMPTS,(pid,rid)).fetchall()
    post={item for item,state in rows if state in _POST}
    # Never treat an already UNKNOWN item as safe merely because an older
    # provider-intent row was lost or a legacy service failed to attach it.
    more=conn.execute(
        """SELECT id FROM sre_live_items
           WHERE project_id=%s AND run_id=%s::uuid AND state='unknown'""",
        (pid,rid),
    ).fetchall()
    post.update(row[0] for row in more)
    return rows,post


def _abandon_reserved(conn,pid,rid,*,items=None):
    if items is None:
        conn.execute(
            """UPDATE sre_live_attempt_fences
               SET state='abandoned_before_dispatch'
               WHERE project_id=%s AND run_id=%s::uuid AND state='reserved'""",
            (pid,rid),
        )
    else:
        conn.execute(
            """UPDATE sre_live_attempt_fences
               SET state='abandoned_before_dispatch'
               WHERE project_id=%s AND run_id=%s::uuid
                 AND item_id=ANY(%s::bigint[]) AND state='reserved'""",
            (pid,rid,items),
        )


def _unknown_intents(conn,pid,rid):
    conn.execute(
        """UPDATE sre_live_attempt_fences SET state='unknown'
           WHERE project_id=%s AND run_id=%s::uuid AND state='dispatch_intent'""",
        (pid,rid),
    )


def _invalidate_all(conn,pid,rid,new_state,post_items,cancel):
    """One transaction, with workspace and run locked by caller beforehand."""
    unknown_ids=sorted(post_items)
    conn.execute(
        """UPDATE sre_live_items
           SET state=CASE
                 WHEN state IN ('cancelled','completed','partial','failed')
                      THEN state
                 WHEN id=ANY(%s::bigint[]) OR state='unknown' THEN 'unknown'
                 ELSE %s END,
               lease_generation=lease_generation+
                 CASE WHEN worker_token IS NOT NULL THEN 1 ELSE 0 END,
               worker_token=NULL,lease_until=NULL,updated_at=clock_timestamp()
           WHERE project_id=%s AND run_id=%s::uuid""",
        (unknown_ids,new_state,pid,rid),
    )
    conn.execute(
        """UPDATE sre_live_batches b SET
           state=CASE
             WHEN b.state IN ('cancelled','completed','partial','failed')
                  THEN b.state
             WHEN b.state='unknown' OR EXISTS (
                SELECT 1 FROM sre_live_batch_members m
                WHERE m.batch_id=b.batch_id AND m.item_id=ANY(%s::bigint[])
             ) THEN 'unknown'
             ELSE %s END,
           lease_generation=lease_generation+
                CASE WHEN worker_token IS NOT NULL THEN 1 ELSE 0 END,
           worker_token=NULL,lease_until=NULL,updated_at=clock_timestamp()
           WHERE b.project_id=%s AND b.run_id=%s::uuid""",
        (unknown_ids,new_state,pid,rid),
    )
    run_state="unknown" if unknown_ids else new_state
    conn.execute(
        """UPDATE sre_live_runs
           SET state=%s,cancel_requested=%s,
               lease_generation=lease_generation+
                 CASE WHEN worker_token IS NOT NULL THEN 1 ELSE 0 END,
               worker_token=NULL,lease_until=NULL,updated_at=clock_timestamp()
           WHERE project_id=%s AND run_id=%s::uuid""",
        (run_state,cancel,pid,rid),
    )
    return run_state


def _result(pid,rid,state,*,idempotent=False,unknown_items=0):
    return {
        "project_id":pid, "run_id":rid, "status":state,
        "idempotent":idempotent,"unknown_items":unknown_items,
        "execution_mode":"live","dispatch_allowed":False,
        "automatic_retry_allowed":False,
    }


def cancel_live_run(conn, project, run_id):
    """Terminal cancel before dispatch, or fail-stop UNKNOWN after intent.

    Repeated cancellation is idempotent. Never erase the intent audit, and
    never misreport post-send work as safely cancelled.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    with conn.transaction():
        current=_lock(conn,pid,rid)
        state,cancel_requested=current[:2]
        if state=="cancelled" or (state=="unknown" and cancel_requested):
            return _result(pid,rid,state,idempotent=True)
        if state not in _STOPPABLE:
            reject("run_id","terminal or UNKNOWN run cannot be cancelled blindly",
                   "invalid_transition")
        _,post=_attempts(conn,pid,rid)
        _abandon_reserved(conn,pid,rid)
        _unknown_intents(conn,pid,rid)
        new=_invalidate_all(conn,pid,rid,"cancelled",post,True)
        return _result(pid,rid,new,unknown_items=len(post))


def pause_live_run(conn, project, run_id, *, reason="paused_rights"):
    """Stop every lease, never release a possibly charged POST for retry."""
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    if type(reason) is not str or reason not in _PAUSES:
        reject("reason","unsupported pause state")
    with conn.transaction():
        state,cancelled,*_=_lock(conn,pid,rid)
        if state==reason and not cancelled:
            return _result(pid,rid,state,idempotent=True)
        if cancelled or state not in (_STOPPABLE - {"planned"}):
            reject("run_id","run terminal or cancellation already requested",
                   "invalid_transition")
        _,post=_attempts(conn,pid,rid)
        _abandon_reserved(conn,pid,rid)
        _unknown_intents(conn,pid,rid)
        new=_invalidate_all(conn,pid,rid,reason,post,False)
        return _result(pid,rid,new,unknown_items=len(post))


def restart_live_run(conn, project, run_id):
    """Safe pre-dispatch restart creates *new consent boundary*, not a worker.

    Requires original D1/C9/pipeline exact snapshot still authorized,
    no possible paid dispatch, no blocked/mutated C9 pair. Keeps audit and
    immutable identity, invalidates all tokens and resets to pending_consent.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    with conn.transaction():
        state,cancelled,manifest,sha,pub,config,_=_lock(conn,pid,rid)
        if cancelled or state not in _PAUSES:
            reject("run_id","only paused/waiting pre-dispatch run may restart",
                   "invalid_transition")
        _,post=_attempts(conn,pid,rid)
        if post:
            reject("run_id","possible charged dispatch requires reconciliation",
                   "ambiguous_charge")
        unsafe=conn.execute(
            """SELECT count(*) FROM sre_live_items
               WHERE project_id=%s AND run_id=%s::uuid
                 AND state IN ('unknown','completed','partial','failed','cancelled')""",
            (pid,rid),
        ).fetchone()[0]
        unsafe_batches=conn.execute(
            """SELECT count(*) FROM sre_live_batches
               WHERE project_id=%s AND run_id=%s::uuid
                 AND state IN ('unknown','completed','partial','failed','cancelled')""",
            (pid,rid),
        ).fetchone()[0]
        if unsafe or unsafe_batches:
            reject("run_id","unreconciled or terminal child cannot be restarted",
                   "ambiguous_charge")
        if (type(manifest) is not dict or fingerprint(manifest)!=sha or
                manifest.get("project_id")!=pid or
                manifest.get("execution_mode")!="live" or
                manifest.get("publication_id")!=pub or
                manifest.get("pipeline_config_id")!=config):
            reject("run_id","original LIVE immutable pins have drifted",
                   "snapshot_mismatch")
        # Re-authorize actual D1 rights + human-approved C9 + existing pipeline
        # in the same transaction. No client-provided snapshot may override it.
        _,computed=_verified_inputs(
            conn,pid,pub,manifest["selected_content_versions"],config,
        )
        if computed!=sha:
            reject("run_id","LIVE snapshots changed since planning",
                   "snapshot_mismatch")
        forbidden=conn.execute(
            """SELECT count(*) FROM sre_live_expected_pairs
               WHERE project_id=%s AND run_id=%s::uuid
                 AND state<>'not_evaluated'""",(pid,rid),
        ).fetchone()[0]
        if forbidden:
            reject("run_id","blocked/evaluated C9 pairs require a new approved run",
                   "incomplete_coverage")
        _abandon_reserved(conn,pid,rid)
        conn.execute(
            """UPDATE sre_live_items SET state='not_evaluated',
               lease_generation=lease_generation+
                    CASE WHEN worker_token IS NOT NULL THEN 1 ELSE 0 END,
               worker_token=NULL,lease_until=NULL,updated_at=clock_timestamp()
               WHERE project_id=%s AND run_id=%s::uuid
                 AND state NOT IN ('completed','partial','failed','cancelled','unknown')""",
            (pid,rid),
        )
        conn.execute(
            """UPDATE sre_live_batches SET state='queued',
               lease_generation=lease_generation+
                    CASE WHEN worker_token IS NOT NULL THEN 1 ELSE 0 END,
               worker_token=NULL,lease_until=NULL,updated_at=clock_timestamp()
               WHERE project_id=%s AND run_id=%s::uuid
                 AND state NOT IN ('completed','partial','failed','cancelled','unknown')""",
            (pid,rid),
        )
        conn.execute(
            """UPDATE sre_live_runs SET state='pending_consent',
               cancel_requested=FALSE,
               lease_generation=lease_generation+
                    CASE WHEN worker_token IS NOT NULL THEN 1 ELSE 0 END,
               worker_token=NULL,lease_until=NULL,updated_at=clock_timestamp()
               WHERE project_id=%s AND run_id=%s::uuid""",
            (pid,rid),
        )
        return _result(pid,rid,"pending_consent")


def recover_expired_live_leases(conn, project, run_id):
    """Crash recovery: pre-dispatch may queue; post-intent is UNKNOWN.

    Database time is authoritative. No worker token/lease restored. No
    automatic retry of a potentially charged provider dispatch.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    with conn.transaction():
        state,cancel_requested,*_=_lock(conn,pid,rid)
        if cancel_requested or state not in ("queued","running"):
            reject("run_id","inactive LIVE run cannot be automatically recovered",
                   "invalid_transition")
        stale=conn.execute(
            """SELECT id FROM sre_live_items
               WHERE project_id=%s AND run_id=%s::uuid AND state='running'
                 AND worker_token IS NOT NULL
                 AND lease_until<=clock_timestamp()
               ORDER BY video_id,id FOR UPDATE""",
            (pid,rid),
        ).fetchall()
        ids=[row[0] for row in stale]
        if not ids:
            return {
                **_result(pid,rid,state,idempotent=True),
                "recovered_items":0,
            }
        _,post=_attempts(conn,pid,rid)
        if post:
            # One uncertain paid call anywhere in this run taints recovery:
            # do not release other potentially correlated work for retry.
            _abandon_reserved(conn,pid,rid)
            _unknown_intents(conn,pid,rid)
            new=_invalidate_all(conn,pid,rid,"paused_rights",post,True)
            return {
                **_result(pid,rid,new,unknown_items=len(post)),
                "recovered_items":0,
            }
        _abandon_reserved(conn,pid,rid,items=ids)
        conn.execute(
            """UPDATE sre_live_items SET state='queued',
               worker_token=NULL,lease_until=NULL,
               lease_generation=lease_generation+1,
               updated_at=clock_timestamp()
               WHERE project_id=%s AND run_id=%s::uuid
                 AND id=ANY(%s::bigint[])""",
            (pid,rid,ids),
        )
        return {**_result(pid,rid,state),"recovered_items":len(ids)}


__all__=(
    "cancel_live_run","pause_live_run","restart_live_run",
    "recover_expired_live_leases",
)
