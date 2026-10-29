"""FIX-11 QDP worker durability, exact job-type scoping and transactional events.

QDP prepares existing evidence only. No paid calls and no peer execution.
FIX-12 will own outbox delivery/consumer ACK; this module persists events and
initial pending receipts IN THE SAME transaction as immutable group snapshots.
"""
from __future__ import annotations

from .contracts import GROUPS, QDPError, sha256
from .repository import JOB_TYPE, _json, _update_run_state

CONSUMER = {"A": "group_a", "B": "group_b", "C": "group_c"}
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 1800


def retry_delay_seconds(attempts: int) -> int:
    """Bounded exponential delay. 1→30, 2→60, 3→120, cap 1800 sec."""
    if type(attempts) is not int or attempts < 1:
        raise QDPError("invalid_attempts", "Worker attempt count must be positive")
    return min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * 2**min(10, attempts-1))


def append_event(conn, *, project: str, run_id: str, video_id=None,
                 group=None, event_type: str, reason=None, inputs_hash=None,
                 state=None) -> str:
    """Atomically persist one QDP-outbox fact and optional pending consumer job.

    Event ID is a durable UUID only created on commit; never a claim that the
    downstream analysis already happened. Retry after rollback creates no
    outbox orphan. Idempotency relies on fenced qdp_items.status transition.
    """
    if event_type in ("preparation_ready", "preparation_blocked"):
        if group not in GROUPS or type(video_id) is not int or video_id < 1:
            raise QDPError("invalid_outbox", "Group event requires pinned video", 409)
        if type(inputs_hash) is not str or len(inputs_hash) != 64:
            raise QDPError("invalid_outbox", "Group event requires input SHA", 409)
    elif event_type not in ("qualified_set_created", "run_completed"):
        raise QDPError("invalid_outbox", "Unsupported event type", 409)
    payload = {"schema_version":"qdp-event/v1",
               "event_type":event_type, "project_id":project, "run_id":run_id,
               "video_id":video_id, "analysis_group":group,
               "state":state, "reason_code":reason, "input_sha256":inputs_hash}
    digest=sha256(payload)
    saved=conn.execute(
        """INSERT INTO qdp_outbox
           (project_id,run_id,video_id,analysis_group,event_type,
            payload,payload_sha256)
           VALUES (%s,%s::uuid,%s,%s,%s,%s::jsonb,%s) RETURNING event_id""",
        (project,run_id,video_id,group,event_type,_json(payload),digest)
    ).fetchone()
    if not saved:
        raise QDPError("outbox_write_failed", "Unable to persist QDP event", 503)
    event_id=str(saved["event_id"])
    if event_type=="preparation_ready":
        conn.execute(
            """INSERT INTO qdp_outbox_deliveries(event_id,consumer)
               VALUES (%s::uuid,%s)""",
            (event_id,CONSUMER[group]))
    return event_id


def reap_exhausted(conn) -> list[dict]:
    """Bounded reaper; process only exhausted QDP jobs, never another module.

    Runs inside the same transaction as the owning worker's claim. A row is
    updated only when its lease expired (or terminal queued attempt count was
    reached). Mark corresponding QDP item blocked and reconcile run status.
    All updates + audit facts roll back together on crash.
    """
    expired=conn.execute(
        """WITH victims AS (
             SELECT id FROM jobs WHERE job_type=%s
               AND attempts>=max_attempts
               AND ((state='running' AND lease_until<now())
                  OR state='queued')
             ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 100
           )
           UPDATE jobs j SET state='failed',locked_by=NULL,lease_until=NULL,
             last_error='QDP attempts exhausted',updated_at=now()
           FROM victims v WHERE j.id=v.id
           RETURNING j.id,j.payload""",(JOB_TYPE,)).fetchall()
    affected=set()
    for row in expired:
        payload=row["payload"]
        if not isinstance(payload, dict):
            continue
        run_id, project, video=payload.get("run_id"),payload.get("project_id"),payload.get("video_id")
        if type(run_id) is not str or type(project) is not str or type(video) is not int:
            continue
        updated=conn.execute(
            """UPDATE qdp_items SET status='blocked',
                   reason_code='worker_attempts_exhausted',updated_at=now()
               WHERE run_id=%s::uuid AND project_id=%s AND video_id=%s
                 AND status='queued' RETURNING video_id""",
            (run_id,project,video)).fetchone()
        if updated:
            conn.execute(
                """INSERT INTO qdp_events
                   (run_id,project_id,video_id,event_code,details)
                   VALUES (%s::uuid,%s,%s,'item_blocked',%s::jsonb)""",
                (run_id,project,video,
                 _json({"reason":"worker_attempts_exhausted"})))
            affected.add(run_id)
    for run_id in sorted(affected):
        _update_run_state(conn,run_id)
    return expired
