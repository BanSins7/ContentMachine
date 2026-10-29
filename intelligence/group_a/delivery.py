"""GA20 at-least-once outbox delivery with exact frozen A result pins.

A delivery is a host-selected B consumer call, NOT an in-process B import.
Only independently verified ACKs advance immutable GA054 outbox to delivered.
A timeout after remote delivery can repeat the same idempotency key: B must
deduplicate the exact GA result ID+SHA pair.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .cost_resources import (
    ResourceBudget,RuntimeDenied,POOL_LOCK_A,POOL_LOCK_B,
    safe_worker_id,safe_sha,retry_seconds
)
from .task_queue import _uuid


@dataclass(frozen=True,slots=True)
class DeliveryClaim:
    outbox_id: int
    attempt_id: int
    attempt_no: int
    token: str
    worker_id: str
    run_id: str
    ga_result_id: int
    result_sha256: str
    payload_sha256: str
    payload: dict
    lease_until: object


def _pool(conn):
    conn.execute("SELECT pg_advisory_xact_lock(%s,%s)",(POOL_LOCK_A,POOL_LOCK_B))


def _reconcile(conn):
    expired=conn.execute(
        """SELECT o.id,a.id
           FROM ga_outbox o JOIN ga_outbox_attempts a
             ON a.outbox_id=o.id AND a.attempt_no=o.attempts
           WHERE o.state='leased' AND a.state='leased'
             AND (o.lease_until<=now() OR a.lease_until<=now())
           ORDER BY o.lease_until,o.id LIMIT 64
           FOR UPDATE OF o,a SKIP LOCKED"""
    ).fetchall()
    for oid,aid in expired:
        conn.execute(
            """UPDATE ga_outbox_attempts SET state='lost',
                  finished_at=now() WHERE id=%s AND state='leased'""",
            (aid,)
        )
        conn.execute(
            """UPDATE ga_outbox SET state='failed',
                  worker_id=NULL,lease_until=NULL WHERE id=%s""",(oid,)
        )


def recover_outbox(conn):
    with conn.transaction():
        _pool(conn)
        _reconcile(conn)


def claim_outbox(conn,*,worker_id,verify_current,lease_seconds=60,
                 project_id=None):
    """One pending event is leased only if its GA run/result is still current."""
    safe_worker_id(worker_id)
    if project_id is not None:
        from intelligence.group_ab.bridge import require_scope
        require_scope(project_id)
    if type(lease_seconds) is not int or not 5<=lease_seconds<=300:
        raise RuntimeDenied("invalid_delivery_lease")
    if not callable(verify_current):
        raise RuntimeDenied("outbox_current_verifier_missing")
    with conn.transaction():
        _pool(conn)
        _reconcile(conn)
        rows=conn.execute(
            """SELECT o.id,o.run_id::text,o.result_id,o.payload_sha256,
                      o.payload,o.attempts,g.result_sha256,
                      r.project_id,r.qdp_run_id::text,r.video_id,r.source_id,
                      r.source_policy_version_id,r.qdp_input_sha256,
                      r.manifest_sha256,
                      last.finished_at
               FROM ga_outbox o
               JOIN ga_results g ON g.id=o.result_id AND g.run_id=o.run_id
               JOIN ga_runs r ON r.id=o.run_id
               JOIN sources s ON s.id=r.source_id
               JOIN source_policy_versions p
                 ON p.id=r.source_policy_version_id AND p.source_id=r.source_id
               LEFT JOIN ga_outbox_attempts last ON last.outbox_id=o.id
                 AND last.attempt_no=o.attempts
               WHERE o.state IN ('pending','failed') AND o.attempts<20
                 AND (%s::text IS NULL OR r.project_id=%s)
                 AND r.state='completed' AND g.state='completed'
                 AND r.invalidated_at IS NULL
                 AND s.status='enabled' AND s.can_analyze_media=true
                 AND p.is_active AND p.permissions->>'can_analyze_media'='true'
                 AND NOT EXISTS (
                   SELECT 1 FROM ga_media_refs mr
                   LEFT JOIN media_assets a ON a.id=mr.media_asset_id
                   WHERE mr.run_id=r.id AND
                     (a.id IS NULL OR a.sha256 IS DISTINCT FROM mr.asset_sha256
                      OR a.video_id<>r.video_id OR a.source_id<>r.source_id
                      OR a.permission IS DISTINCT FROM 'analysis_allowed'
                      OR (a.expires_at IS NOT NULL AND a.expires_at<=now()))
                 )
                 AND (SELECT count(*) FROM ga_media_refs mr
                      WHERE mr.run_id=r.id)=r.expected_media_items
               ORDER BY o.created_at,o.id
               LIMIT 32 FOR UPDATE OF o SKIP LOCKED""",
            (project_id,project_id),
        ).fetchall()
        now=conn.execute("SELECT now()").fetchone()[0]
        for row in rows:
            (oid,rid,result_id,payload_sha,payload,attempt_no,result_sha,
             pid,qrun,vid,source,policy,qsha,msha,last_finished)=row
            if last_finished is not None and (
                now<last_finished+timedelta(seconds=retry_seconds(attempt_no))
            ):
                continue
            scope={
                "run_id":rid,"project_id":pid,"qdp_run_id":qrun,
                "video_id":vid,"source_id":source,
                "source_policy_version_id":policy,
                "qdp_input_sha256":qsha,"manifest_sha256":msha,
                "ga_result_id":result_id,"result_sha256":result_sha,
            }
            if verify_current(dict(scope)) is not True:
                continue
            from .contracts_v1 import canonical_sha256
            if (type(payload) is not dict or
                canonical_sha256(payload)!=payload_sha or
                payload.get("ga_result_id")!=result_id or
                payload.get("result_sha256")!=result_sha or
                payload.get("ga_run_id")!=rid):
                raise RuntimeDenied("outbox_payload_integrity_failed")
            next_no=attempt_no+1
            lease=conn.execute(
                """UPDATE ga_outbox SET state='leased',attempts=attempts+1,
                       worker_id=%s,
                       lease_until=now()+%s*interval '1 second'
                   WHERE id=%s AND state IN ('pending','failed')
                   RETURNING lease_until""",
                (worker_id,lease_seconds,oid)
            ).fetchone()
            if lease is None:
                raise RuntimeDenied("outbox_concurrent_claim")
            aid,token=conn.execute(
                """INSERT INTO ga_outbox_attempts
                   (outbox_id,attempt_no,worker_id,lease_until)
                   VALUES(%s,%s,%s,%s) RETURNING id,token::text""",
                (oid,next_no,worker_id,lease[0])
            ).fetchone()
            return DeliveryClaim(oid,aid,next_no,token,worker_id,
                                 rid,result_id,result_sha,payload_sha,
                                 dict(payload),lease[0])
    return None


def _owned(conn,claim):
    if not isinstance(claim,DeliveryClaim):
        raise RuntimeDenied("invalid_outbox_claim")
    row=conn.execute(
        """SELECT o.state,o.attempts,o.worker_id,o.lease_until,o.run_id::text,
                  o.result_id,o.payload_sha256,r.state,g.result_sha256
           FROM ga_outbox o JOIN ga_runs r ON r.id=o.run_id
           JOIN ga_results g ON g.id=o.result_id
           WHERE o.id=%s FOR UPDATE OF o""",(claim.outbox_id,)
    ).fetchone()
    attempt=conn.execute(
        """SELECT token::text,state,lease_until FROM ga_outbox_attempts
           WHERE id=%s AND outbox_id=%s FOR UPDATE""",
        (claim.attempt_id,claim.outbox_id)
    ).fetchone()
    now=conn.execute("SELECT now()").fetchone()[0]
    if (row is None or attempt is None
        or row[0]!="leased" or row[1]!=claim.attempt_no
        or row[2]!=claim.worker_id or row[3] is None or row[3]<=now
        or row[4]!=claim.run_id or row[5]!=claim.ga_result_id
        or row[6]!=claim.payload_sha256 or row[7]!="completed"
        or row[8]!=claim.result_sha256 or
        attempt!=(claim.token,"leased",claim.lease_until)
        or attempt[2]<=now):
        raise RuntimeDenied("outbox_lease_stale_or_revoked")
    return row


def _check_current(conn,claim,verify_current):
    scope=conn.execute(
        """SELECT project_id,qdp_run_id::text,video_id,source_id,
                  source_policy_version_id,qdp_input_sha256,manifest_sha256
           FROM ga_runs WHERE id=%s::uuid""",(claim.run_id,)
    ).fetchone()
    if scope is None or not callable(verify_current):
        raise RuntimeDenied("outbox_currentness_unavailable")
    env={
        "run_id":claim.run_id,"project_id":scope[0],"qdp_run_id":scope[1],
        "video_id":scope[2],"source_id":scope[3],
        "source_policy_version_id":scope[4],
        "qdp_input_sha256":scope[5],"manifest_sha256":scope[6],
        "ga_result_id":claim.ga_result_id,"result_sha256":claim.result_sha256,
    }
    if verify_current(dict(env)) is not True:
        raise RuntimeDenied("outbox_currentness_revoked")
    return env


def acknowledge_delivery(conn,claim,receipt,*,verify_current,verify_receipt):
    """ACK after trusted B receipt; commit is idempotent under token fencing.

    This is the ONLY method that marks an event delivered; sending an event
    alone is not delivery success.
    """
    if type(receipt) is not dict or set(receipt)!={
        "ga_result_id","result_sha256","consumer_receipt_sha256"
    } or receipt.get("ga_result_id")!=claim.ga_result_id or (
        receipt.get("result_sha256")!=claim.result_sha256
    ):
        raise RuntimeDenied("invalid_consumer_receipt")
    safe_sha(receipt["consumer_receipt_sha256"])
    if not callable(verify_receipt):
        raise RuntimeDenied("consumer_receipt_verifier_required")
    with conn.transaction():
        _pool(conn)
        _owned(conn,claim)
        env=_check_current(conn,claim,verify_current)
        if verify_receipt({"scope":env,"claim":claim,
                           "receipt":dict(receipt)}) is not True:
            raise RuntimeDenied("consumer_receipt_untrusted")
        _check_current(conn,claim,verify_current)
        conn.execute(
            """UPDATE ga_outbox_attempts SET state='acknowledged',
                  finished_at=now() WHERE id=%s AND state='leased'""",
            (claim.attempt_id,)
        )
        conn.execute(
            """INSERT INTO ga_outbox_ack_receipts
               (outbox_id,attempt_id,result_id,result_sha256,consumer_receipt_sha256)
               VALUES(%s,%s,%s,%s,%s)""",
            (claim.outbox_id,claim.attempt_id,claim.ga_result_id,
             claim.result_sha256,receipt["consumer_receipt_sha256"])
        )
        conn.execute(
            """UPDATE ga_outbox SET state='delivered',worker_id=NULL,
                  lease_until=NULL,delivered_at=now() WHERE id=%s""",
            (claim.outbox_id,)
        )
    return "delivered"


def fail_delivery(conn,claim,*,verify_current):
    """Mark a transport timeout/unknown outcome; exact event can be retried.

    A B consumer MUST deduplicate (ga_result_id,result_sha256) because the
    remote side may have committed prior to this local failure.
    """
    with conn.transaction():
        _pool(conn)
        _owned(conn,claim)
        _check_current(conn,claim,verify_current)
        conn.execute(
            """UPDATE ga_outbox_attempts SET state='failed',finished_at=now()
               WHERE id=%s""",(claim.attempt_id,)
        )
        conn.execute(
            """UPDATE ga_outbox SET state='failed',worker_id=NULL,
                  lease_until=NULL WHERE id=%s""",(claim.outbox_id,)
        )
    return "retry_pending"


def deliver_once(conn,*,worker_id,verify_current,transport,
                 verify_receipt,lease_seconds=60,project_id=None):
    """Invoke injected network transport OUTSIDE SQL transaction, once.

    transport(payload,idempotency_key) -> receipt. Empty/timeout may have
    actually been delivered; retry with identical key, never regenerate.
    """
    if not callable(transport):
        raise RuntimeDenied("consumer_transport_missing")
    claim=claim_outbox(
        conn,worker_id=worker_id,verify_current=verify_current,
        lease_seconds=lease_seconds,project_id=project_id
    )
    if claim is None:
        return None
    # Revalidate the exact immutable result at point of use immediately
    # before dispatching the network request, not just when leased.
    with conn.transaction():
        _owned(conn,claim)
        _check_current(conn,claim,verify_current)
    # The consumer's idempotency key is stable across all retry attempts.
    key=claim.run_id+":"+str(claim.ga_result_id)+":"+claim.result_sha256
    try:
        received=transport(dict(claim.payload),idempotency_key=key)
        return acknowledge_delivery(
            conn,claim,received,verify_current=verify_current,
            verify_receipt=verify_receipt
        )
    except Exception:
        # A network timeout can mean the receiver processed the message.
        # Re-delivery must use the same immutable key and exact SHA.
        try:
            fail_delivery(conn,claim,verify_current=verify_current)
        except RuntimeDenied:
            pass  # Revocation or stale token: never overwrite terminal state.
        raise
