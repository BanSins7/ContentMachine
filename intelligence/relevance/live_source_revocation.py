"""M34 source-scoped SRE retention/revocation: deny first, purge disposable vectors.

A trusted external D1 policy/retention owner MUST call this host-only port
for each affected content version. Only original project-owned source rows
are accepted; the primary tombstone is permanent even if policy is regranted.
The first transaction atomically hides reads, purges M18 vectors and creates
durable LIVE cancellation outbox rows. Later reconciliation is idempotent
and never guesses a paid request unpaid. Historical immutable finance
evidence/receipts/consents are retained, not falsely called physically purged.
"""
from __future__ import annotations

from .opus_consent import _actor,_authorized
from .opus_dispatch_intent import _committable
from .live_recovery import cancel_live_run
from .v1_contracts import (
    SREContractError,positive,project_id,reject,sha256_digest,
)

VERSION="sre-live-source-revocation/v1"
REASONS=frozenset((
    "rights_revoked","source_deleted","retention_expired","privacy_delete",
))
SQL_WORKSPACE="SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR UPDATE"
SQL_WORKSPACE_READ="SELECT 1 FROM sre_workspaces WHERE project_id=%s"
SQL_CONTENT="""SELECT video_id,content_sha256,source_policy_version_id
    FROM sre_content_versions
    WHERE project_id=%s AND id=%s FOR SHARE"""
SQL_INSERT="""INSERT INTO sre_live_source_tombstones (
    project_id,content_version_id,video_id,content_sha256,
    source_policy_version_id,reason_code,actor_id)
    VALUES (%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT DO NOTHING RETURNING content_version_id"""
SQL_EXISTS="""SELECT video_id,content_sha256,source_policy_version_id,
    reason_code,tombstoned_at FROM sre_live_source_tombstones
    WHERE project_id=%s AND content_version_id=%s"""
SQL_CACHE_LEFT="""SELECT count(*) FROM sre_local_embedding_cache
    WHERE project_id=%s AND content_version_id=%s"""
SQL_PENDING="""SELECT e.event_id,e.run_id::text,r.state,
    r.cancel_requested
    FROM sre_live_revocation_outbox e
    JOIN sre_live_runs r ON r.project_id=e.project_id AND r.run_id=e.run_id
    WHERE e.project_id=%s AND e.content_version_id=%s
      AND e.state='pending'
    ORDER BY e.event_id LIMIT %s"""
SQL_ACK="""UPDATE sre_live_revocation_outbox
    SET state='acknowledged',actor_id=%s,acknowledged_at=clock_timestamp()
    WHERE project_id=%s AND content_version_id=%s AND event_id=%s
      AND state='pending' RETURNING event_id"""
SQL_COUNTS="""SELECT state,count(*)::integer
    FROM sre_live_revocation_outbox
    WHERE project_id=%s AND content_version_id=%s
    GROUP BY state ORDER BY state"""
SQL_GATED="""SELECT count(*) FROM sre_live_items
    WHERE project_id=%s AND content_version_id=%s"""
SQL_READ_OUTBOX="""SELECT run_id::text,state,created_at,acknowledged_at
    FROM sre_live_revocation_outbox
    WHERE project_id=%s AND content_version_id=%s
    ORDER BY event_id LIMIT 501"""
SQL_SCOPE="""SELECT id FROM sre_content_versions
    WHERE project_id=%s AND source_policy_version_id=%s
    ORDER BY id LIMIT 1001"""


def _scope(conn,pid,cid):
    if conn.execute(SQL_WORKSPACE_READ,(pid,)).fetchone() is None:
        reject("project_id","project workspace missing","rights_denied")
    row=conn.execute(SQL_EXISTS,(pid,cid)).fetchone()
    if row is None:
        reject("content_version_id","source not revoked or belongs to another tenant",
               "rights_denied")
    return row


def record_sre_source_revocation(
    conn,project,content_version_id,*,expected_content_sha256,
    reason_code,actor_id,actor_authorizer,
):
    """Atomic DB-bound tombstone + cache purge + outbox creation.

    Not a D1 policy mutation and not a promise that external provider copies
    or immutable accounting records were physically erased.
    """
    pid=project_id(project)
    cid=positive(content_version_id,"content_version_id",
                 maximum=9223372036854775807)
    digest=sha256_digest(expected_content_sha256,"expected_content_sha256")
    if type(reason_code) is not str or reason_code not in REASONS:
        reject("reason_code","unsupported structured retention/revocation reason",
               "invalid_transition")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"revoke_sre_source")
    _committable(conn)
    with conn.transaction():
        if conn.execute(SQL_WORKSPACE,(pid,)).fetchone() is None:
            reject("project_id","project workspace missing","rights_denied")
        row=conn.execute(SQL_CONTENT,(pid,cid)).fetchone()
        if row is None or row[1]!=digest:
            reject("content_version_id","foreign or changed D1 content SHA",
                   "rights_denied")
        video,content_sha,policy=row
        added=conn.execute(SQL_INSERT,(
            pid,cid,video,content_sha,policy,reason_code,actor,
        )).fetchone() is not None
        actual=_scope(conn,pid,cid)
        if actual[:3]!=(video,content_sha,policy):
            reject("source","revocation identity diverged",
                   "snapshot_mismatch")
        remaining=conn.execute(SQL_CACHE_LEFT,(pid,cid)).fetchone()
        if remaining is None or remaining[0]!=0:
            reject("cache","revoked local vectors still present",
                   "retention_incomplete")
        pending=conn.execute(SQL_COUNTS,(pid,cid)).fetchall()
        stats=dict(pending)
    return {
        "schema_version":VERSION,
        "project_id":pid,"content_version_id":cid,
        "video_id":video,"source_policy_version_id":policy,
        "reason_code":actual[3],
        "tombstone_created":added,"revocation_permanent":True,
        "local_embedding_vectors_remaining":0,
        "cancellation_outbox_pending":stats.get("pending",0),
        "cancellation_outbox_acknowledged":stats.get("acknowledged",0),
        "source_content_physically_erased":False,
        "financial_audit_physically_erased":False,
        "third_party_copies_deleted":False,
        "paid_post_calls":0,
    }


def process_sre_source_revocation_outbox(
    conn,project,content_version_id,*,actor_id,actor_authorizer,
    max_events=50,
):
    """Idempotently cancel safely cancellable LIVE runs; never rePOST UNKNOWN.

    Any exception/crash leaves durable outbox pending. An UNKNOWN run without
    a cancel flag needs separate operator reconciliation and remains pending.
    Terminal completed records are suppressed by read tombstone but retained.
    """
    pid=project_id(project)
    cid=positive(content_version_id,"content_version_id",
                 maximum=9223372036854775807)
    maximum=positive(max_events,"max_events",maximum=100)
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"process_sre_source_revocation")
    _committable(conn)
    with conn.transaction():
        _scope(conn,pid,cid)
        entries=conn.execute(SQL_PENDING,(pid,cid,maximum)).fetchall()
    acknowledged=0
    blockers=[]
    for event_id,run_id,state,cancel_requested in entries:
        if state=="unknown" and cancel_requested is not True:
            blockers.append({
                "run_id":run_id,
                "reason_code":"possibly_paid_unknown_needs_operator",
            })
            continue
        if state not in ("cancelled","completed","partial","failed","unknown"):
            # The existing M15 API preserves paid UNKNOWN and may fail-stop
            # if technical witnesses do not permit a safe transition.
            try:
                cancel_live_run(conn,pid,run_id)
            except SREContractError:
                blockers.append({
                    "run_id":run_id,
                    "reason_code":"m15_cancellation_requires_operator",
                })
                continue
        with conn.transaction():
            # SQL trigger independently requires terminal state or a
            # cancellation flag; callers cannot forge completed revocation.
            proof=conn.execute(SQL_ACK,(actor,pid,cid,event_id)).fetchone()
            if proof is not None:
                acknowledged+=1
    with conn.transaction():
        _scope(conn,pid,cid)
        stats=dict(conn.execute(SQL_COUNTS,(pid,cid)).fetchall())
    return {
        "schema_version":VERSION,"project_id":pid,"content_version_id":cid,
        "acknowledged_in_this_call":acknowledged,
        "pending":stats.get("pending",0),
        "acknowledged_total":stats.get("acknowledged",0),
        "blocked_runs":blockers,
        "outbox_fully_acknowledged":stats.get("pending",0)==0,
        "full_physical_deletion_complete":False,
        "paid_retry_allowed":False,"paid_post_calls":0,
    }


def inspect_sre_source_revocation(
    conn,project,content_version_id,*,actor_id,actor_authorizer,
):
    """Privacy-minimal project-specific observability, no content retrieval."""
    pid=project_id(project)
    cid=positive(content_version_id,"content_version_id",
                 maximum=9223372036854775807)
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"read_sre_source_revocation")
    _committable(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        row=_scope(conn,pid,cid)
        details=conn.execute(SQL_READ_OUTBOX,(pid,cid)).fetchall()
        if len(details)>500:
            reject("outbox","oversized revocation outbox; explicit pagination needed",
                   "retention_incomplete")
        remaining=conn.execute(SQL_CACHE_LEFT,(pid,cid)).fetchone()
    return {
        "schema_version":VERSION,"project_id":pid,"content_version_id":cid,
        "video_id":row[0],"source_policy_version_id":row[2],
        "reason_code":row[3],
        "tombstoned_at":row[4].isoformat() if row[4] is not None else None,
        "local_embedding_vectors_remaining":remaining[0],
        "run_outbox":[{
            "run_id":rid,"state":state,
            "created_at":created.isoformat(),
            "acknowledged_at":finished.isoformat() if finished else None,
        } for rid,state,created,finished in details],
        "source_text_present":False,"rights_reactivated":False,
        "full_physical_deletion_complete":False,
        "paid_retry_allowed":False,
    }


__all__=(
    "VERSION","record_sre_source_revocation",
    "process_sre_source_revocation_outbox",
    "inspect_sre_source_revocation",
)
