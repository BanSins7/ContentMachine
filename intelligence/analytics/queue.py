"""Durable zero-cost Analytics queue, lease recovery and retention housekeeping.

No provider/network dispatch: a replay only recomputes deterministic local DB
analysis with one immutable request key. A live source/extractor must have
already persisted lawful observations separately.
"""
from __future__ import annotations
import re
from .contracts import AnalyticsError, validate_request
from .service import _connect, _source, _project_video, _one, _all, run_qualification

def enqueue_batch(project_id, source_id, video_ids, batch_key, *, connect=None):
    if type(video_ids) is not list or not 1<=len(video_ids)<=50:
        raise AnalyticsError("invalid_batch","Batch must contain 1..50 canonical video IDs")
    if len(set(str(v) for v in video_ids)) != len(video_ids):
        raise AnalyticsError("duplicate_video_id","Batch contains duplicate video IDs")
    if (type(batch_key) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,130}",batch_key)):
        raise AnalyticsError("invalid_batch_key","Batch key must be 8..130 safe characters")
    for video_id in video_ids:
        validate_request(project_id,video_id,batch_key+":"+str(video_id))
    factory=connect or _connect
    with factory() as conn, conn.transaction():
        _source(conn,source_id)
        result=[]
        # All-or-none enqueue. Never enqueue an unlinked video into another project.
        for video_id in video_ids:
            _project_video(conn,project_id,video_id)
        for video_id in video_ids:
            key=batch_key+":"+str(video_id)
            row=_one(conn, """
              INSERT INTO analytics_qualification_jobs(project_id,video_id,source_id,request_key)
              VALUES(%s,%s,%s,%s)
              ON CONFLICT(project_id,request_key) DO UPDATE
                SET request_key=analytics_qualification_jobs.request_key
              RETURNING id,video_id,source_id,request_key,state,attempts
            """, (project_id,video_id,source_id,key))
            if row["video_id"]!=video_id or row["source_id"]!=source_id:
                raise AnalyticsError("idempotency_conflict","Batch key reused with different source/video",409)
            result.append(row)
        return result

def process_one(worker_id="local-analytics", *, connect=None) -> dict | None:
    if type(worker_id) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}",worker_id):
        raise AnalyticsError("invalid_worker","Invalid worker id")
    factory=connect or _connect
    with factory() as conn, conn.transaction():
        row=_one(conn, """
            SELECT * FROM analytics_qualification_jobs
            WHERE attempts<max_attempts AND (
              (state='queued' AND run_after<=now()) OR
              (state='leased' AND lease_until<=now()))
            ORDER BY run_after,id FOR UPDATE SKIP LOCKED LIMIT 1
        """,())
        if not row:
            return None
        leased=_one(conn, """
            UPDATE analytics_qualification_jobs SET state='leased',worker_id=%s,
                   lease_until=now()+INTERVAL '120 seconds',attempts=attempts+1,
                   updated_at=now() WHERE id=%s
            RETURNING *
        """,(worker_id,row["id"]))
    try:
        outcome=run_qualification(leased["project_id"],leased["video_id"],
                                  leased["source_id"],leased["request_key"],connect=factory)
        status=outcome["status"]
        snapshot_id=outcome["id"]
        error=None
    except AnalyticsError as exc:
        snapshot_id=None
        error=exc.code
        status=("blocked" if exc.http_status in (403,404)
                else "failed" if leased["attempts"]>=leased["max_attempts"]
                else "queued")
    except Exception:
        snapshot_id=None
        error="unexpected_database_failure"
        status="failed" if leased["attempts"]>=leased["max_attempts"] else "queued"
    with factory() as conn, conn.transaction():
        # Guard against a second worker completing after a lost lease.
        row=_one(conn,"""
          UPDATE analytics_qualification_jobs SET state=%s,snapshot_id=%s,
             last_error_code=%s,lease_until=NULL,worker_id=NULL,
             run_after=CASE WHEN %s='queued' THEN now()+INTERVAL '60 seconds'
                            ELSE run_after END,updated_at=now()
          WHERE id=%s AND state='leased' AND worker_id=%s
          RETURNING id,project_id,video_id,source_id,request_key,state,attempts,snapshot_id,last_error_code
        """,(status,snapshot_id,error,status,leased["id"],worker_id))
        return row or {"id":leased["id"],"state":"lease_lost"}

def list_jobs(project_id, *, limit=50, connect=None):
    if type(project_id) is not str or not re.fullmatch(r"[0-9a-f]{32}",project_id):
        raise AnalyticsError("invalid_project","Invalid project id")
    if type(limit) is not int or not 1<=limit<=100:
        raise AnalyticsError("invalid_limit","Limit must be between 1 and 100")
    factory=connect or _connect
    with factory() as conn:
        return _all(conn, """
            SELECT id,video_id,source_id,request_key,state,attempts,snapshot_id,
                   last_error_code,created_at,updated_at
            FROM analytics_qualification_jobs WHERE project_id=%s
            ORDER BY id DESC LIMIT %s
        """,(project_id,limit))

def purge_unreadable(*, connect=None):
    """Operator-triggered deletion of cached unified payloads after rights/TTL loss.

    Raw facts live in their own source systems and follow those retention policies.
    """
    factory=connect or _connect
    with factory() as conn, conn.transaction():
        deleted=_one(conn, """
          WITH d AS (
            DELETE FROM analytics_unified_snapshots u
            WHERE NOT EXISTS (SELECT 1 FROM analytics_visible_snapshots v WHERE v.id=u.id)
            RETURNING id
          ) SELECT count(*) AS count FROM d
        """,())
        # Queued jobs contain only IDs and cannot run if source is revoked.
        cancelled=_one(conn, """
          WITH d AS (
            DELETE FROM analytics_qualification_jobs j
            WHERE NOT EXISTS (
             SELECT 1 FROM sources s JOIN source_policy_versions p
               ON p.source_id=s.id AND p.is_active
             WHERE s.id=j.source_id AND s.status='enabled'
               AND s.can_store_metadata AND s.can_analyze_media
               AND s.can_store_metrics AND s.can_derive_scores
               AND p.reviewed_at IS NOT NULL
               AND p.permissions->>'can_store_metadata'='true'
               AND p.permissions->>'can_analyze_media'='true'
               AND p.permissions->>'can_store_metrics'='true'
               AND p.permissions->>'can_derive_scores'='true')
            RETURNING id
          ) SELECT count(*) AS count FROM d
        """,())
        return {"deleted_unified_snapshots":deleted["count"],
                "deleted_revoked_jobs":cancelled["count"]}
