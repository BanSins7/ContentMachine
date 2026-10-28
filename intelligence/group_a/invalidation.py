"""GA20 source/QDP/media revocation and exact-version current read fencing.

Historical A003/A004/GA054 snapshots remain immutable for audit, but an
invalidated result is never returned as a current fact or dispatched to B.
"""
from __future__ import annotations

from .cost_resources import RuntimeDenied,POOL_LOCK_A,POOL_LOCK_B,safe_error
from .task_queue import _uuid,_scope,_run_row,_release

REASONS=frozenset((
    "qdp_revoked","project_access_revoked","source_policy_revoked",
    "media_rights_revoked","media_sha_changed","review_superseded",
    "source_deleted","manual_cancel","deadline_exceeded",
))


def _guard(verify_authority,run_scope,reason):
    if not callable(verify_authority) or verify_authority({
        **run_scope,"reason_code":reason
    }) is not True:
        raise RuntimeDenied("revocation_authority_missing")


def _terminalize_active(conn,run_id,reason):
    rows=conn.execute(
        """SELECT s.id,a.id
           FROM ga_steps s LEFT JOIN ga_step_attempts a
             ON a.step_id=s.id AND a.attempt_no=s.attempts
             AND a.state='leased'
           WHERE s.run_id=%s::uuid AND s.state='running'
           FOR UPDATE OF s""",
        (run_id,)
    ).fetchall()
    for sid,aid in rows:
        if aid is not None:
            conn.execute(
                """UPDATE ga_step_attempts SET state='cancelled',
                    finished_at=now(),error_code=%s
                   WHERE id=%s AND state='leased'""",(reason,aid)
            )
            _release(conn,aid)
    # Completed steps are never rewritten, even after source revocation.
    conn.execute(
        """UPDATE ga_steps SET state='cancelled',
               worker_id=NULL,lease_until=NULL,
               failure_code=%s,updated_at=now(),finished_at=now()
           WHERE run_id=%s::uuid AND state IN ('running','queued','failed','blocked')""",
        (reason,run_id)
    )
    rows=conn.execute(
        """SELECT o.id,a.id
           FROM ga_outbox o LEFT JOIN ga_outbox_attempts a
             ON a.outbox_id=o.id AND a.attempt_no=o.attempts
             AND a.state='leased'
           WHERE o.run_id=%s::uuid AND o.state='leased'
           FOR UPDATE OF o""",(run_id,)
    ).fetchall()
    for oid,aid in rows:
        if aid is not None:
            conn.execute(
                """UPDATE ga_outbox_attempts SET state='lost',finished_at=now()
                   WHERE id=%s AND state='leased'""",(aid,)
            )
    # Retain a pending/failed/leased outbox entry historically, but make it
    # non-dispatchable. Old 054 allows state failed but not cancelled.
    conn.execute(
        """UPDATE ga_outbox SET state='failed',worker_id=NULL,lease_until=NULL
           WHERE run_id=%s::uuid AND state IN ('pending','failed','leased')""",
        (run_id,)
    )


def invalidate_run(conn,*,run_id,reason_code,verify_authority):
    """Atomically revoke access to a run and any unfinished work.

    The origin of revocation must be independently authorized (QDP, live
    policy, human review or project owner). Never infer it from the caller.
    """
    _uuid(run_id)
    if reason_code not in REASONS:
        raise RuntimeDenied("invalid_invalidation_reason")
    with conn.transaction():
        conn.execute(
            "SELECT pg_advisory_xact_lock(%s,%s)",(POOL_LOCK_A,POOL_LOCK_B)
        )
        row=_run_row(conn,run_id,lock=True)
        if row is None:
            raise RuntimeDenied("ga_run_missing")
        _guard(verify_authority,_scope(row),reason_code)
        if row[9]=="invalidated":
            prior=conn.execute(
                "SELECT reason_code FROM ga_invalidation_audit WHERE run_id=%s::uuid",
                (run_id,)
            ).fetchone()
            if prior is None or prior[0]!=reason_code:
                raise RuntimeDenied("conflicting_invalidation_reason")
            return "invalidated"
        if row[9]=="cancelled":
            raise RuntimeDenied("already_cancelled")
        prior=row[9]
        _terminalize_active(conn,run_id,reason_code)
        conn.execute(
            """UPDATE ga_runs SET state='invalidated',
                   worker_id=NULL,lease_until=NULL,
                   invalidated_at=now(),invalidated_reason=%s,
                   updated_at=now()
               WHERE id=%s::uuid""",(reason_code,run_id)
        )
        conn.execute(
            """INSERT INTO ga_invalidation_audit(run_id,previous_state,reason_code)
               VALUES (%s::uuid,%s,%s)""",(run_id,prior,reason_code)
        )
    return "invalidated"


def cancel_run(conn,*,run_id,verify_authority):
    _uuid(run_id)
    with conn.transaction():
        conn.execute(
            "SELECT pg_advisory_xact_lock(%s,%s)",(POOL_LOCK_A,POOL_LOCK_B)
        )
        row=_run_row(conn,run_id,lock=True)
        if row is None:
            raise RuntimeDenied("ga_run_missing")
        _guard(verify_authority,_scope(row),"manual_cancel")
        if row[9]=="cancelled":
            return "cancelled"
        if row[9] in ("completed","invalidated","blocked"):
            raise RuntimeDenied("terminal_run_not_cancellable")
        _terminalize_active(conn,run_id,"manual_cancel")
        conn.execute(
            """UPDATE ga_runs SET state='cancelled',worker_id=NULL,
                   lease_until=NULL,updated_at=now(),finished_at=now()
               WHERE id=%s::uuid""",(run_id,)
        )
    return "cancelled"


def read_current_result(conn,*,run_id,result_id,expected_sha256,
                        verify_current,verify_result):
    """Exact ID+SHA read, always live QDP/project/asset-provenance checked.

    No "latest" fallback; do not use the historical ga_local_readable_results
    as an authorization token. The SQL GA20 view is only *locally* current.
    """
    _uuid(run_id)
    if type(result_id) is not int or result_id<=0:
        raise RuntimeDenied("invalid_result_id")
    from .cost_resources import safe_sha
    safe_sha(expected_sha256)
    if not callable(verify_result):
        raise RuntimeDenied("result_origin_verifier_required")
    with conn.transaction():
        row=conn.execute(
            """SELECT r.run_id::text,g.result_id,g.project_id,g.video_id,
                      g.result_sha256,g.snapshot,r.qdp_run_id::text,
                      r.source_id,r.source_policy_version_id,
                      r.manifest_sha256,ga.qdp_input_sha256
               FROM ga20_locally_current_results g
               JOIN ga_runs ga ON ga.id=g.run_id
               JOIN ga20_locally_current_results r ON r.result_id=g.result_id
               WHERE g.run_id=%s::uuid AND g.result_id=%s
                 AND g.result_sha256=%s""",
            (run_id,result_id,expected_sha256)
        ).fetchone()
        if row is None:
            raise RuntimeDenied("result_not_current_or_revoked")
        scope={
            "run_id":run_id,"project_id":row[2],"video_id":row[3],
            "qdp_run_id":row[6],"source_id":row[7],
            "source_policy_version_id":row[8],
            "manifest_sha256":row[9],"qdp_input_sha256":row[10],
            "ga_result_id":result_id,"result_sha256":expected_sha256,
        }
        if not callable(verify_current) or verify_current(dict(scope)) is not True:
            raise RuntimeDenied("qdp_or_project_rights_not_current")
        from .contracts_v1 import canonical_sha256
        if canonical_sha256(row[5])!=expected_sha256:
            raise RuntimeDenied("result_digest_mismatch")
        if verify_result({"scope":dict(scope),"snapshot":dict(row[5])}) is not True:
            raise RuntimeDenied("result_origin_not_current")
        if verify_current(dict(scope)) is not True:
            raise RuntimeDenied("qdp_revoked_during_result_read")
        return {"ga_result_id":result_id,"result_sha256":expected_sha256,
                "snapshot":dict(row[5]),"run_id":run_id}
