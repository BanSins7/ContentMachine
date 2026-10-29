"""FIX-17 QDP project-local, read-only observability.

All counts derive from QDP committed database facts and CURRENT QDP views.
A durable Group receipt means inbox acceptance only. There is no guess of
completed Group A/B/C analysis, no provider calls and no cross-project data.
"""
from __future__ import annotations

from datetime import datetime
from .contracts import QDPError,project_id
from .repository import _connect, _uuid

MAX_SAMPLES=50


def _check_limit(value):
    if type(value) is not int or not 1 <= value <= MAX_SAMPLES:
        raise QDPError("invalid_limit","Observability sample limit must be 1..50",422)
    return value


def snapshot(project, *, run_id=None, limit=20, connect=None):
    """Bounded counters + oldest unresolved delivery by group; all read-only.

    These counts are snapshots, not guaranteed atomic across multiple API
    requests. The single transaction ensures internal snapshot consistency.
    SQL is grouped/limited: never loads full videos or outbox payload.
    """
    pid=project_id(project)
    rid=_uuid(run_id,"QDP run") if run_id is not None else None
    size=_check_limit(limit)
    with (connect or _connect)() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            if rid is not None:
                found=conn.execute(
                    """SELECT 1 FROM qdp_runs WHERE project_id=%s AND id=%s::uuid""",
                    (pid,rid)).fetchone()
                if found is None:
                    raise QDPError("run_not_found","QDP run outside this project",404)
            totals=conn.execute(
                """SELECT count(*) AS runs,
                     COALESCE(sum(total_qualified),0) AS qualified_snapshots,
                     COALESCE(sum(total_review),0) AS review_snapshots,
                     COALESCE(sum(total_excluded),0) AS excluded_snapshots,
                     count(*) FILTER (WHERE status IN ('queued','running')) AS active_runs,
                     count(*) FILTER (WHERE cancel_requested) AS cancelled_runs
                   FROM qdp_runs
                   WHERE project_id=%s
                     AND (%s::uuid IS NULL OR id=%s::uuid)""",
                (pid,rid,rid)).fetchone()
            stage=conn.execute(
                """SELECT i.status,count(*) AS n
                     FROM qdp_items i
                    WHERE i.project_id=%s AND (%s::uuid IS NULL OR i.run_id=%s::uuid)
                    GROUP BY i.status ORDER BY i.status""",
                (pid,rid,rid)).fetchall()
            current=conn.execute(
                """SELECT p.analysis_group,
                          count(*) AS historical_ready,
                          count(live.run_id) AS currently_ready
                     FROM qdp_preparations p
                     LEFT JOIN qdp_group_current_preparations live
                       ON live.project_id=p.project_id AND live.run_id=p.run_id
                      AND live.video_id=p.video_id
                      AND live.analysis_group=p.analysis_group
                    WHERE p.project_id=%s
                      AND (%s::uuid IS NULL OR p.run_id=%s::uuid)
                      AND p.state='ready'
                    GROUP BY p.analysis_group ORDER BY p.analysis_group""",
                (pid,rid,rid)).fetchall()
            delivery=conn.execute(
                """SELECT d.consumer,o.event_type,d.state,count(*) AS n,
                          count(*) FILTER(WHERE d.state='leased'
                              AND d.lease_until<=now()) AS expired_leases,
                          count(*) FILTER(WHERE d.state='pending'
                              AND d.run_after<=now()) AS due_now,
                          min(o.created_at) FILTER
                              (WHERE d.state IN ('pending','leased')) AS oldest_unresolved_at
                     FROM qdp_outbox o JOIN qdp_outbox_deliveries d
                       ON d.event_id=o.event_id
                    WHERE o.project_id=%s
                      AND (%s::uuid IS NULL OR o.run_id=%s::uuid)
                    GROUP BY d.consumer,o.event_type,d.state
                    ORDER BY d.consumer,o.event_type,d.state""",
                (pid,rid,rid)).fetchall()
            # jobs.payload is only read through QDP's own run_id, and never
            # emitted to the client. Do not count Discovery/Collector jobs.
            jobs=conn.execute(
                """SELECT j.state,count(*) AS n,
                          count(*) FILTER(WHERE j.state='running'
                            AND j.lease_until<=now()) AS expired_leases,
                          count(*) FILTER(WHERE j.state='queued'
                            AND j.run_after<=now()) AS due_now
                     FROM jobs j JOIN qdp_runs r
                       ON j.payload->>'run_id'=r.id::text
                    WHERE j.job_type='qdp.prepare_video'
                      AND r.project_id=%s
                      AND (%s::uuid IS NULL OR r.id=%s::uuid)
                    GROUP BY j.state ORDER BY j.state""",
                (pid,rid,rid)).fetchall()
            revoked=conn.execute(
                """SELECT count(*) AS n FROM qdp_invalidations
                   WHERE project_id=%s
                     AND (%s::uuid IS NULL OR run_id=%s::uuid)""",
                (pid,rid,rid)).fetchone()["n"]
            # Bounded metadata only, never raw media, metrics, provider errors.
            oldest=conn.execute(
                """SELECT o.event_id,o.run_id,o.analysis_group,o.event_type,
                          d.consumer,d.state,d.attempts,o.created_at
                     FROM qdp_outbox o JOIN qdp_outbox_deliveries d
                       ON d.event_id=o.event_id
                    WHERE o.project_id=%s
                      AND (%s::uuid IS NULL OR o.run_id=%s::uuid)
                      AND d.state IN ('pending','leased')
                    ORDER BY o.created_at,o.event_id
                    LIMIT %s""",(pid,rid,rid,size)).fetchall()
    def jsonrow(row):
        return {k:(str(v) if isinstance(v,datetime) or hasattr(v,"hex") and not isinstance(v,(str,int)) else v)
                for k,v in dict(row).items()}
    return {
        "ok":True,"scope":{"project_id":pid,"run_id":rid},
        "runs":jsonrow(totals),
        "items_by_historical_status":[jsonrow(x) for x in stage],
        "readiness_by_group":[jsonrow(x) for x in current],
        "delivery_by_state":[jsonrow(x) for x in delivery],
        "worker_jobs_by_state":[jsonrow(x) for x in jobs],
        "revocations":revoked,"oldest_unresolved":[jsonrow(x) for x in oldest],
        "oldest_limit":size,
        "analysis_completed":None,
        "analysis_completed_status":"unavailable_without_verified_peer_results",
        "receipt_meaning":"durable_consumer_inbox_only",
        "paid_calls":0,
    }
