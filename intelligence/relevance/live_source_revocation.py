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
SQL_POLICY_CLOSED="""SELECT 1 FROM source_policy_versions p
    JOIN sources s ON s.id=p.source_id
    WHERE p.id=%s
      AND (p.is_active=FALSE OR s.status<>'enabled'
        OR s.can_store_metadata=FALSE OR p.reviewed_at IS NULL
        OR p.permissions->>'can_semantic_classify' IS DISTINCT FROM 'true')
    FOR SHARE OF p,s"""
SQL_PRIMARY_CONTENT="""SELECT content_sha256
    FROM sre_content_versions WHERE project_id=%s AND id=%s FOR SHARE"""
SQL_PRIMARY_PURGE="""DELETE FROM sre_content_versions c
    USING sre_live_source_tombstones t
    WHERE c.project_id=%s AND c.id=%s
      AND t.project_id=c.project_id AND t.content_version_id=c.id
      AND t.content_sha256=c.content_sha256
      AND t.reason_code IN ('privacy_delete','source_deleted','retention_expired')
      AND NOT EXISTS (
        SELECT 1 FROM sre_live_items i
        WHERE i.project_id=c.project_id AND i.content_version_id=c.id)
      AND NOT EXISTS (
        SELECT 1 FROM sre_offline_items o
        WHERE o.project_id=c.project_id AND o.content_version_id=c.id)
      AND NOT EXISTS (
        SELECT 1 FROM sre_classification_results x
        WHERE x.project_id=c.project_id AND x.content_version_id=c.id)
      AND NOT EXISTS (
        SELECT 1 FROM sre_local_embedding_cache v
        WHERE v.project_id=c.project_id AND v.content_version_id=c.id)
    RETURNING c.id"""
SQL_PURGE_CERT_INSERT="""INSERT INTO sre_source_payload_purge_certificates
    (project_id,content_version_id,content_sha256,actor_id)
    VALUES (%s,%s,%s,%s)
    ON CONFLICT DO NOTHING RETURNING content_version_id"""
SQL_PURGE_CERT_READ="""SELECT content_sha256,actor_id,purged_at
    FROM sre_source_payload_purge_certificates
    WHERE project_id=%s AND content_version_id=%s"""
SQL_EXPIRED="""SELECT c.id,c.content_sha256
    FROM sre_content_versions c
    JOIN sources s ON s.id=c.source_id
    WHERE c.project_id=%s
      AND ((c.expires_at IS NOT NULL
             AND c.expires_at<=clock_timestamp())
        OR (s.retention_days IS NOT NULL
             AND c.created_at+s.retention_days*interval '1 day'
                 <=clock_timestamp()))
      AND NOT EXISTS (
        SELECT 1 FROM sre_live_source_tombstones t
        WHERE t.project_id=c.project_id AND t.content_version_id=c.id
      )
    ORDER BY c.id LIMIT %s"""
SQL_SCOPE="""SELECT id,content_sha256 FROM sre_content_versions
    WHERE project_id=%s AND source_policy_version_id=%s
    ORDER BY id LIMIT 1001"""
SQL_AUDIT="""SELECT
  (SELECT count(*) FROM sre_local_embedding_cache c
   WHERE c.project_id=%s AND c.content_version_id=%s),
  (SELECT count(*) FROM sre_offline_items o
   WHERE o.project_id=%s AND o.content_version_id=%s),
  (SELECT count(*) FROM sre_classification_results v
   JOIN sre_classification_runs r
     ON r.project_id=v.project_id AND r.id=v.run_id
   WHERE v.project_id=%s AND v.content_version_id=%s
     AND r.execution_mode='offline_fixture'),
  (SELECT count(*) FROM sre_stage_executions e
   JOIN sre_offline_revocation_fences f
     ON f.project_id=e.project_id AND f.run_id=e.run_id
   JOIN sre_live_source_tombstones t
     ON t.project_id=f.project_id
       AND t.content_version_id=f.content_version_id
       AND t.video_id=e.video_id
   WHERE f.project_id=%s AND f.content_version_id=%s),
  (SELECT count(*) FROM sre_readable_content_versions c
   WHERE c.project_id=%s AND c.id=%s),
  (SELECT count(*) FROM sre_live_classification_results v
   WHERE v.project_id=%s AND v.content_version_id=%s),
  (SELECT count(*) FROM sre_content_versions c
   WHERE c.project_id=%s AND c.id=%s
     AND c.content IS NOT NULL AND c.content <> '{}'::jsonb),
  (SELECT count(*) FROM sre_live_opus_quotes q
   JOIN sre_live_items i
     ON i.project_id=q.project_id AND i.id=q.item_id
       AND i.run_id=q.run_id
   WHERE i.project_id=%s AND i.content_version_id=%s),
  (SELECT count(*) FROM sre_live_opus_consents c
   JOIN sre_live_items i
     ON i.project_id=c.project_id AND i.run_id=c.run_id
       AND i.id=c.item_id
   WHERE i.project_id=%s AND i.content_version_id=%s
     AND c.revoked_at IS NULL
     AND NOT EXISTS (
       SELECT 1 FROM sre_live_opus_dispatch_intents d
       WHERE d.project_id=c.project_id AND d.consent_id=c.consent_id)
     AND NOT EXISTS (
       SELECT 1 FROM sre_live_attempt_fences f
       WHERE f.project_id=c.project_id AND f.run_id=c.run_id
         AND f.item_id=c.item_id AND f.state IN
         ('dispatch_intent','unknown','completed','failed_known'))),
  (SELECT count(*) FROM sre_live_opus_receipts t
   JOIN sre_live_items i
     ON i.project_id=t.project_id AND i.id=t.item_id
       AND i.run_id=t.run_id
   WHERE i.project_id=%s AND i.content_version_id=%s),
  (SELECT count(*) FROM sre_live_budget_reservations res
   JOIN sre_live_items i
     ON i.project_id=res.project_id AND i.run_id=res.run_id
       AND i.id=res.item_id
   WHERE i.project_id=%s AND i.content_version_id=%s
     AND res.state='reserved'
     AND NOT EXISTS (
       SELECT 1 FROM sre_live_opus_dispatch_intents d
       WHERE d.project_id=res.project_id
         AND d.reservation_id=res.reservation_id)
     AND NOT EXISTS (
       SELECT 1 FROM sre_live_attempt_fences f
       WHERE f.project_id=res.project_id AND f.run_id=res.run_id
         AND f.item_id=res.item_id AND f.state IN
         ('dispatch_intent','unknown','completed','failed_known'))),
  (SELECT count(*) FROM sre_live_budget_reservations res
   JOIN sre_live_items i
     ON i.project_id=res.project_id AND i.run_id=res.run_id
       AND i.id=res.item_id
   WHERE i.project_id=%s AND i.content_version_id=%s
     AND res.state='reserved'
     AND (
       EXISTS (
         SELECT 1 FROM sre_live_opus_dispatch_intents d
         WHERE d.project_id=res.project_id
           AND d.reservation_id=res.reservation_id)
       OR EXISTS (
         SELECT 1 FROM sre_live_attempt_fences f
         WHERE f.project_id=res.project_id AND f.run_id=res.run_id
           AND f.item_id=res.item_id AND f.state IN
           ('dispatch_intent','unknown','completed','failed_known')))),
  (SELECT count(*) FROM sre_live_verified_pair_results v
   JOIN sre_live_items i
     ON i.project_id=v.project_id AND i.id=v.item_id
       AND i.run_id=v.run_id
   WHERE i.project_id=%s AND i.content_version_id=%s),
  (SELECT count(*) FROM sre_live_human_reviews h
   JOIN sre_live_items i
     ON i.project_id=h.project_id AND i.id=h.item_id
       AND i.run_id=h.run_id
   WHERE i.project_id=%s AND i.content_version_id=%s),
  (SELECT count(*) FROM sre_live_gold_benchmark_snapshots b
   WHERE b.project_id=%s
     AND EXISTS (
       SELECT 1 FROM jsonb_array_elements(b.manifest) AS x(sample)
       WHERE x.sample->>'content_version_id'=%s::text)),
  (SELECT count(*) FROM sre_live_revocation_outbox o
   WHERE o.project_id=%s AND o.content_version_id=%s
     AND o.state='pending'),
  (SELECT count(*) FROM sre_content_versions c
   WHERE c.project_id=%s AND c.id=%s),
  (SELECT count(*) FROM sre_source_payload_purge_certificates cert
   WHERE cert.project_id=%s AND cert.content_version_id=%s),
  (SELECT count(DISTINCT r.run_id) FROM sre_live_runs r
   JOIN sre_live_items i ON i.project_id=r.project_id AND i.run_id=r.run_id
   WHERE i.project_id=%s AND i.content_version_id=%s
     AND r.state IN ('planned','pending_consent','queued','running',
                     'waiting_source','paused_budget','paused_rights')
     AND r.cancel_requested=FALSE),
  (SELECT count(DISTINCT r.run_id) FROM sre_offline_runs r
   JOIN sre_offline_revocation_fences f
     ON f.project_id=r.project_id AND f.run_id=r.run_id
   WHERE f.project_id=%s AND f.content_version_id=%s
     AND (r.status IN ('queued','running') OR r.cancel_requested=FALSE))
"""


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
        if row is None:
            # After an explicit M34 S3 primary-row purge, the permanent
            # tombstone and its immutable purge certificate preserve replay
            # identity. Never re-create deleted source data.
            existing=conn.execute(SQL_EXISTS,(pid,cid)).fetchone()
            cert=conn.execute(SQL_PURGE_CERT_READ,(pid,cid)).fetchone()
            if (existing is None or existing[1]!=digest or cert is None
                    or cert[0]!=digest):
                reject("content_version_id","foreign or changed D1 content SHA",
                       "rights_denied")
            video,content_sha,policy=existing[:3]
            added=False
        else:
            if row[1]!=digest:
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
        "paid_unknown_hold_protected":counts["protected_paid_or_unknown_holds"]>0,
        "financial_audit_physically_erased":False,
        "third_party_copies_deleted":False,
        "paid_post_calls":0,
    }


def purge_revoked_sre_primary_payload(
    conn,project,content_version_id,*,expected_content_sha256,
    actor_id,actor_authorizer,
):
    """Explicit, separately authorized delete of an unreferenced S3 row.

    Never delete any version pinned by LIVE items or remaining S4/results/
    vectors. A real FK/trigger blockage aborts the delete, leaving the
    previously committed source tombstone and audit intact.
    """
    pid=project_id(project)
    cid=positive(content_version_id,"content_version_id",
                 maximum=9223372036854775807)
    sha=sha256_digest(expected_content_sha256,"expected_content_sha256")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"purge_sre_source_payload")
    _committable(conn)
    with conn.transaction():
        if conn.execute(SQL_WORKSPACE,(pid,)).fetchone() is None:
            reject("project_id","SRE workspace missing","rights_denied")
        row=_scope(conn,pid,cid)
        if row[1]!=sha:
            reject("content_version_id","wrong immutable source SHA",
                   "rights_denied")
        if row[3] not in ("privacy_delete","source_deleted","retention_expired"):
            reject("reason_code","rights-only revocation does not authorize "
                   "S3 primary-row deletion","rights_denied")
        existing_cert=conn.execute(SQL_PURGE_CERT_READ,(pid,cid)).fetchone()
        if existing_cert is not None and existing_cert[0]!=sha:
            reject("purge_certificate","foreign or divergent source certificate",
                   "snapshot_mismatch")
        original=conn.execute(SQL_PRIMARY_CONTENT,(pid,cid)).fetchone()
        created=False
        if original is not None:
            if original[0]!=sha:
                reject("content_sha256","S3 original SHA differs",
                       "snapshot_mismatch")
            removed=conn.execute(SQL_PRIMARY_PURGE,(pid,cid)).fetchone()
            if removed is None:
                reject("content_version_id","S3 version still has LIVE or "
                       "derived dependents; never cascade paid evidence",
                       "retention_incomplete")
            issued=conn.execute(SQL_PURGE_CERT_INSERT,(
                pid,cid,sha,actor,
            )).fetchone()
            if issued is None:
                reject("purge_certificate","concurrent or missing S3 deletion "
                       "certificate","retention_incomplete")
            created=True
        elif existing_cert is None:
            reject("content_version_id","missing S3 original without an "
                   "immutable M34 purge certificate","retention_incomplete")
        cert=conn.execute(SQL_PURGE_CERT_READ,(pid,cid)).fetchone()
        remain=conn.execute(SQL_PRIMARY_CONTENT,(pid,cid)).fetchone()
        if (cert is None or cert[0]!=sha or remain is not None):
            reject("purge_certificate","primary DB delete not attested",
                   "retention_incomplete")
    return {
        "schema_version":VERSION,"project_id":pid,
        "content_version_id":cid,"content_sha256":sha,
        "primary_db_s3_row_absent":True,
        "primary_db_purge_certificate_created":created,
        "certificate_actor_id":cert[1],
        "certificate_purged_at":cert[2].isoformat(),
        "live_paid_evidence_deleted":False,
        "backup_or_disk_secure_erasure_verified":False,
        "upstream_d1_source_deleted":False,
        "full_physical_deletion_complete":False,"paid_post_calls":0,
    }


def record_sre_source_policy_revocation(
    conn,project,source_policy_version_id,*,reason_code,actor_id,
    actor_authorizer,
):
    """Crash-replayable per-version callback after a D1 policy revocation.

    The D1 policy must already be inactive/disabled. Every version is
    committed independently; a crash can be retried safely, but a
    >1000-version policy is blocked rather than silently partly handled.
    """
    pid=project_id(project)
    policy=positive(source_policy_version_id,"source_policy_version_id",
                    maximum=9223372036854775807)
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"revoke_sre_source")
    _committable(conn)
    with conn.transaction():
        if conn.execute(SQL_WORKSPACE,(pid,)).fetchone() is None:
            reject("project_id","SRE workspace missing","rights_denied")
        # A policy-wide callback must run AFTER the authoritative D1 owner
        # actually disables this policy or source. While it is still active,
        # another intake could attach an unenumerated version between pages.
        if conn.execute(SQL_POLICY_CLOSED,(policy,)).fetchone() is None:
            reject("source_policy_version_id",
                   "close D1 policy/source before a policy-wide purge",
                   "rights_denied")
        rows=conn.execute(SQL_SCOPE,(pid,policy)).fetchall()
        if len(rows)>1000:
            reject("policy","too many source versions for bounded revocation",
                   "retention_incomplete")
    processed=0
    created=0
    for content_id,digest in rows:
        result=record_sre_source_revocation(
            conn,pid,content_id,expected_content_sha256=digest,
            reason_code=reason_code,actor_id=actor,
            actor_authorizer=actor_authorizer,
        )
        processed+=1
        created+=int(result["tombstone_created"])
    return {
        "schema_version":VERSION,"project_id":pid,
        "source_policy_version_id":policy,
        "source_versions_processed":processed,
        "tombstones_created":created,
        "permanently_suppressed":True,
        "all_runs_cancelled":False,
        "full_physical_deletion_complete":False,
        "paid_post_calls":0,
    }


def record_expired_sre_content_revocations(
    conn,project,*,actor_id,actor_authorizer,max_versions=50,
):
    """Explicit bounded retention sweeper, NOT an automatic background task.

    Only true PostgreSQL current time and DB source expiration determine
    eligibility; active content is never purged through this callback.
    Repeated invocation picks newly expired/unrevoked versions.
    """
    pid=project_id(project)
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"revoke_sre_source")
    maximum=positive(max_versions,"max_versions",maximum=100)
    _committable(conn)
    with conn.transaction():
        if conn.execute(SQL_WORKSPACE,(pid,)).fetchone() is None:
            reject("project_id","SRE workspace missing","rights_denied")
        rows=conn.execute(SQL_EXPIRED,(pid,maximum+1)).fetchall()
    processed=0
    for content_id,digest in rows[:maximum]:
        result=record_sre_source_revocation(
            conn,pid,content_id,expected_content_sha256=digest,
            reason_code="retention_expired",actor_id=actor,
            actor_authorizer=actor_authorizer,
        )
        processed+=int(result["tombstone_created"])
    return {
        "schema_version":VERSION,"project_id":pid,
        "newly_tombstoned_versions":processed,
        "scanned_expired_unrevoked":min(len(rows),maximum),
        "more_expired_versions_possible":len(rows)>maximum,
        "explicit_host_invocation_required":True,
        "all_downstream_run_cancellations_processed":False,
        "full_physical_deletion_complete":False,
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


def audit_sre_source_revocation_remnants(
    conn,project,content_version_id,*,actor_id,actor_authorizer,
):
    """Exact per-source, project-authorized post-revocation retention inventory.

    SQL reads only aggregates; never SELECTs source content/quotes/responses.
    'All disposable gone' does NOT imply complete physical deletion: S3 base
    content, immutable quote/receipt/gold audit and backups can remain.
    """
    pid=project_id(project)
    cid=positive(content_version_id,"content_version_id",
                 maximum=9223372036854775807)
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"read_sre_source_revocation")
    _committable(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _scope(conn,pid,cid)
        counters=conn.execute(SQL_AUDIT,(pid,cid)*20).fetchone()
        if (counters is None or len(counters)!=20 or any(
            type(n) is not int or n<0 for n in counters
        )):
            reject("audit","source retention inventory unavailable",
                   "retention_incomplete")
    names=(
        "local_embedding_vectors","offline_item_contexts",
        "offline_fixture_classification_outputs",
        "offline_fixture_stage_executions",
        "readable_d1_versions","visible_live_results",
        "original_s3_content_payloads",
        "immutable_opus_quotes","unrevoked_pre_dispatch_consents",
        "immutable_provider_receipts","releasable_pre_dispatch_holds","protected_paid_or_unknown_holds",
        "immutable_model_pair_results","immutable_human_revisions",
        "immutable_gold_snapshots","pending_cancellation_events",
        "primary_s3_rows_still_present","primary_db_purge_certificates",
        "live_runs_missing_immediate_stop",
        "offline_runs_missing_immediate_stop",
    )
    counts=dict(zip(names,counters))
    unwanted=names[:6]
    still_visible=[k for k in unwanted if counts[k]!=0]
    if counts["releasable_pre_dispatch_holds"]:
        still_visible.append("releasable_pre_dispatch_holds")
    if counts["unrevoked_pre_dispatch_consents"]:
        still_visible.append("unrevoked_pre_dispatch_consents")
    for kind in ("live_runs_missing_immediate_stop",
                 "offline_runs_missing_immediate_stop"):
        if counts[kind]:
            still_visible.append(kind)
    return {
        "schema_version":VERSION,"project_id":pid,
        "content_version_id":cid,
        "verified_in_one_read_only_snapshot":True,
        "counts":counts,
        "immediate_run_fail_stop_verified":not (
            counts["live_runs_missing_immediate_stop"] or
            counts["offline_runs_missing_immediate_stop"]),
        "primary_s3_row_absent_with_certificate":(
            counts["primary_s3_rows_still_present"]==0 and
            counts["primary_db_purge_certificates"]==1),
        "disposable_purge_verified":all(counts[k]==0 for k in unwanted[:4]),
        "post_revocation_sre_views_hidden":(
            counts["readable_d1_versions"]==0 and
            counts["visible_live_results"]==0),
        "remaining_protective_invariant_violations":still_visible,
        "permitted_purge_complete_for_checked_sre_tables":not still_visible,
        "original_s3_payload_detected":(
            counts["original_s3_content_payloads"]>0),
        # A zero payload count cannot establish that a deletion happened.
        # This service never physically deletes the original S3 row.
        "source_content_physically_erased":False,
        "source_content_erasure_verified":False,
        "financial_audit_physically_erased":False,
        "third_party_copies_deleted":False,
        "backup_and_log_retention_verified":False,
        "full_physical_deletion_complete":False,
        "paid_post_calls":0,
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
        "source_text_exposed":False,
        "source_content_physically_erased":False,
        "rights_reactivated":False,
        "full_physical_deletion_complete":False,
        "paid_retry_allowed":False,
    }


__all__=(
    "VERSION","record_sre_source_revocation",
    "purge_revoked_sre_primary_payload",
    "record_sre_source_policy_revocation",
    "record_expired_sre_content_revocations",
    "process_sre_source_revocation_outbox",
    "inspect_sre_source_revocation",
    "audit_sre_source_revocation_remnants",
)
