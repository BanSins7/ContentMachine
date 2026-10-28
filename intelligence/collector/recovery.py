"""VC-MOD-13: bounded, explicit crash/cancellation/retention recovery.

No network, provider calls, duplicate POST, fabricated SRE classification or
destructive audit-row DELETE. Every write is project-scoped, fenced by the
existing run/task/job lock order and opt-in. Retention scrub targets ONLY
expired mutable source metadata, never immutable page/query/discovery receipts.
"""
from __future__ import annotations

from .contracts import CollectorError, PROJECT_RE, require_id, require_int
from .repository import _connection

_RECOVERY_LIMIT=100


def _scope(project_id):
    return require_id(project_id,"project_id",PROJECT_RE)


def _bound(limit):
    return require_int(limit,"limit",1,_RECOVERY_LIMIT)


def reconcile_cancelled(project_id: str, *, limit: int=50,
                        dry_run: bool=True,connect=None) -> dict:
    """Resolve ambiguous previously submitted tasks on a cancelled run.

    Never refund a consumed submit intent: remote POST may have happened.
    Known IDs are preserved for audit/manual reconciliation; no GET or POST.
    A cancelled run cannot become completed because of a late provider reply.
    """
    pid=_scope(project_id)
    max_items=_bound(limit)
    if type(dry_run) is not bool:
        raise CollectorError("invalid_recovery","Explicit boolean dry_run required",400)
    found=0
    resolved=0
    with _connection(connect) as conn:
        candidates=conn.execute(
            """SELECT t.id,t.run_id,t.job_id,t.source_request_id
               FROM collector_provider_tasks t
               JOIN collector_runs c ON c.run_id=t.run_id
               JOIN search_runs r ON r.id=t.run_id
               WHERE c.project_id=%s AND r.cancel_requested=TRUE
                 AND c.stage='cancelling'
                 AND t.state IN ('submit_intent','submitted')
               ORDER BY t.run_id,t.id LIMIT %s""",
            (pid,max_items),
        ).fetchall()
        found=len(candidates)
        if dry_run:
            return {"schema_version":"collector.cancel_recovery/v1",
                    "project_id":pid,"dry_run":True,
                    "candidates":found,"reconciled":0,"provider_calls":0,
                    "refunded_consumed_requests":0}
        for task_id,run_id,job_id,request_id in candidates:
            with conn.transaction():
                run=conn.execute(
                    "SELECT cancel_requested FROM search_runs WHERE id=%s FOR UPDATE",
                    (run_id,),
                ).fetchone()
                owner=conn.execute(
                    """SELECT project_id,stage FROM collector_runs
                       WHERE run_id=%s FOR UPDATE""",(run_id,),
                ).fetchone()
                if (run is None or run[0] is not True or owner is None
                    or owner!=(pid,"cancelling")):
                    continue
                task=conn.execute(
                    """SELECT t.state,t.provider_task_id,j.state,j.lease_until
                       FROM collector_provider_tasks t
                       JOIN jobs j ON j.id=t.job_id
                       WHERE t.id=%s AND t.run_id=%s AND t.job_id=%s
                         AND t.source_request_id=%s
                       FOR UPDATE OF t,j""",
                    (task_id,run_id,job_id,request_id),
                ).fetchone()
                if task is None or task[0] not in ("submit_intent","submitted"):
                    continue
                # Still-running owner may have an in-flight GET/ACK. Do not
                # clobber it: reconcile only after its DB lease expires.
                lease=conn.execute(
                    """SELECT 1 FROM jobs WHERE id=%s AND state='running'
                       AND lease_until>now()""",(job_id,),
                ).fetchone()
                if lease is not None:
                    continue
                updated=conn.execute(
                    """UPDATE collector_provider_tasks SET state='unknown',
                         last_error_code='cancelled_submit_uncertain',
                         finished_at=COALESCE(finished_at,now())
                       WHERE id=%s AND run_id=%s
                         AND state IN ('submit_intent','submitted')
                       RETURNING id""",(task_id,run_id),
                ).fetchone()
                if updated is None:
                    continue
                conn.execute(
                    """UPDATE source_requests SET state='unknown',
                         error_code='cancelled_submit_uncertain',
                         finished_at=COALESCE(finished_at,now())
                       WHERE id=%s AND run_id=%s AND state='started'""",
                    (request_id,run_id),
                )
                conn.execute(
                    """UPDATE jobs SET state='failed',locked_by=NULL,
                         lease_until=NULL,updated_at=now()
                       WHERE id=%s AND state IN ('queued','running')""",
                    (job_id,),
                )
                from .worker import _finalize
                _finalize(conn,run_id)
                resolved+=1
    return {"schema_version":"collector.cancel_recovery/v1",
            "project_id":pid,"dry_run":False,
            "candidates":found,"reconciled":resolved,
            "provider_calls":0,"refunded_consumed_requests":0}


def retention_audit(project_id: str, *, limit: int=50,connect=None) -> dict:
    """Non-destructive due-content audit; immutable audit identity remains."""
    pid=_scope(project_id)
    size=_bound(limit)
    with _connection(connect) as conn:
        rows=conn.execute(
            """SELECT DISTINCT m.id,m.video_id,m.expires_at,m.deleted_at
               FROM source_video_metadata m
               JOIN collector_ingest_page_videos v ON v.source_metadata_id=m.id
                    AND v.video_id=m.video_id
               JOIN collector_ingest_pages p ON p.id=v.page_id
                    AND p.project_id=v.project_id
               WHERE p.project_id=%s AND m.source_id='dtk_tiktok'
                 AND (m.expires_at<=now() OR m.expires_at IS NULL)
               ORDER BY m.id LIMIT %s""",(pid,size+1),
        ).fetchall()
    more=len(rows)>size
    rows=rows[:size]
    return {"schema_version":"collector.retention_audit/v1",
            "project_id":pid,
            "metadata":[{"metadata_id":x[0],"video_id":x[1],
                         "expires_at":x[2].isoformat() if x[2] else None,
                         "already_scrubbed":x[3] is not None,
                         "action_required":"verify_legacy_retention_policy"
                            if x[2] is None else
                            ("none" if x[3] is not None else "scrub_expired")}
                        for x in rows],
            "has_more":more,
            "read_only":True,"deleted_audit_rows":0}


def scrub_expired_metadata(project_id: str, *, limit: int=50,
                           confirm: bool=False,connect=None) -> dict:
    """Explicit tenant-authorized privacy scrub of due metadata content.

    Does NOT delete the linked source-metadata row, D1 video ID, discovery,
    immutable Collector pages/QV links or SRE outbox identity. An unexpired
    or NULL expiry row is NEVER scrubbed. No raw media ever stored here.
    """
    pid=_scope(project_id)
    size=_bound(limit)
    if type(confirm) is not bool or not confirm:
        raise CollectorError("retention_consent_required",
                             "Explicit metadata-scrub confirmation required",409)
    with _connection(connect) as conn:
        with conn.transaction():
            # Require at least one exact owning project checkpoint; metadata
            # is global source-scoped, so never change it before expiry.
            candidates=conn.execute(
                """SELECT m.id FROM source_video_metadata m
                   WHERE m.source_id='dtk_tiktok'
                     AND m.expires_at IS NOT NULL AND m.expires_at<=now()
                     AND m.deleted_at IS NULL
                     AND EXISTS (
                         SELECT 1 FROM collector_ingest_page_videos v
                         JOIN collector_ingest_pages p
                           ON p.id=v.page_id AND p.project_id=v.project_id
                         WHERE v.source_metadata_id=m.id
                           AND v.video_id=m.video_id AND p.project_id=%s
                     )
                   ORDER BY m.id LIMIT %s FOR UPDATE OF m""",
                (pid,size),
            ).fetchall()
            scrubbed=[]
            for (metadata_id,) in candidates:
                # Recheck expiry inside UPDATE to avoid stale select and
                # to preserve all FK-linked audit identities.
                record=conn.execute(
                    """UPDATE source_video_metadata
                       SET title=NULL,description=NULL,creator_name=NULL,
                           is_current=FALSE,deleted_at=now()
                       WHERE id=%s AND source_id='dtk_tiktok'
                         AND expires_at IS NOT NULL AND expires_at<=now()
                         AND deleted_at IS NULL
                       RETURNING id""",(metadata_id,),
                ).fetchone()
                if record is not None:
                    scrubbed.append(record[0])
    return {"schema_version":"collector.retention_scrub/v1",
            "project_id":pid,"scrubbed_count":len(scrubbed),
            "metadata_ids":scrubbed,"deleted_audit_rows":0,
            "provider_calls":0}
