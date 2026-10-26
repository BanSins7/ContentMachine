"""Cancellation, crash marking, and durable outbox read/ack for Stage C.

Reconciliation of real OpenRouter invoices is NOT implemented here; no
provider API is called. Unknown costs remain reserved until evidenced review.
"""
from __future__ import annotations

from datetime import datetime, timezone

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, canonical
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid
from intelligence.search_planner.contracts import publication_uuid
from .repository import mark_uncertain_attempt


def request_cancel(project_id, generation_id, *, connect=None):
    """Stop new claims; release only pre-dispatch reservations atomically.

    Already dispatched attempts are left untouched for later billing
    reconciliation. No completed outbox is published for a cancelled run.
    """
    pid, gid = _pid(project_id), publication_uuid(generation_id)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            gen = conn.execute(
                """SELECT status,cancel_requested,consent_id
                   FROM sqg_generations WHERE id=%s::uuid AND project_id=%s FOR UPDATE""",
                (gid, pid),
            ).fetchone()
            if gen is None:
                raise PipelineError("Search generation not found", 404)
            if gen[0] == "completed":
                raise PipelineError("Completed handoff cannot be cancelled", 409)
            if gen[0] == "cancelled":
                return {"generation_id": gid, "status": "cancelled", "reused": True}
            if gen[2]:
                conn.execute(
                    """SELECT id FROM sqg_consents WHERE id=%s::uuid AND project_id=%s
                       FOR UPDATE""", (str(gen[2]), pid),
                )
            conn.execute(
                """UPDATE sqg_generations SET cancel_requested=TRUE,updated_at=now()
                   WHERE id=%s::uuid AND project_id=%s""", (gid, pid),
            )
            batches = conn.execute(
                """SELECT id,status FROM sqg_batches WHERE generation_id=%s::uuid
                   ORDER BY batch_index FOR UPDATE""", (gid,),
            ).fetchall()
            active = False
            for bid, state in batches:
                if state == "pending":
                    conn.execute(
                        """UPDATE sqg_batches SET status='cancelled',updated_at=now()
                           WHERE id=%s::uuid""", (str(bid),),
                    )
                elif state == "reserved":
                    r = conn.execute(
                        """SELECT id,reservation_usd FROM sqg_attempts
                           WHERE batch_id=%s::uuid AND status='reserved' FOR UPDATE""",
                        (str(bid),),
                    ).fetchone()
                    if r is None:
                        raise PipelineError("Reserved batch has no reserved attempt", 409)
                    conn.execute(
                        """UPDATE sqg_attempts
                           SET status='abandoned_before_dispatch',ended_at=now()
                           WHERE id=%s::uuid""", (str(r[0]),),
                    )
                    conn.execute(
                        """UPDATE sqg_consents
                           SET reserved_usd=reserved_usd-%s WHERE id=%s::uuid AND project_id=%s""",
                        (str(r[1]), str(gen[2]), pid),
                    )
                    conn.execute(
                        """UPDATE sqg_batches SET status='cancelled',updated_at=now()
                           WHERE id=%s::uuid""", (str(bid),),
                    )
                elif state in ("dispatching", "uncertain"):
                    active = True
            new_state = "uncertain" if active else "cancelled"
            conn.execute(
                """UPDATE sqg_generations SET status=%s,updated_at=now()
                   WHERE id=%s::uuid AND project_id=%s""",
                (new_state, gid, pid),
            )
            if not gen[1]:
                conn.execute(
                    """INSERT INTO sqg_events(project_id,generation_id,event_kind,payload)
                       VALUES (%s,%s::uuid,'cancel_requested',%s::jsonb)""",
                    (pid, gid, canonical({"active_attempts": active})),
                )
            return {"generation_id": gid, "status": new_state, "reused": bool(gen[1])}


def mark_stale_dispatches(project_id, generation_id, *, older_than_seconds=900,
                          connect=None):
    """One iteration of bounded recovery; never automatically retries POST."""
    pid, gid = _pid(project_id), publication_uuid(generation_id)
    if type(older_than_seconds) is not int or not 1 <= older_than_seconds <= 86400:
        raise PipelineError("Invalid stale attempt age", 422)
    factory = connect or _connect
    with factory() as conn:
        rows = conn.execute(
            """SELECT a.id FROM sqg_attempts a JOIN sqg_generations g
                 ON g.id=a.generation_id
               WHERE g.project_id=%s AND g.id=%s::uuid
                 AND a.status='dispatch_intent'
                 AND a.dispatch_intent_at < NOW() - (%s * INTERVAL '1 second')
               ORDER BY a.dispatch_intent_at LIMIT 100""",
            (pid, gid, older_than_seconds),
        ).fetchall()
    items = []
    for row in rows:
        try:
            items.append(mark_uncertain_attempt(pid, str(row[0]), connect=connect))
        except PipelineError as err:
            if err.http_status != 409:
                raise
            # Race with normal response settlement: already settled is fine.
    return {"generation_id": gid, "marked_uncertain": len(items),
            "paid_retries": 0}


def pending_outbox(project_id, *, limit=50, connect=None):
    """Read-only safe event pointers. Consumer still absent in Stage C."""
    pid = _pid(project_id)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise PipelineError("Invalid outbox page size", 422)
    factory = connect or _connect
    with factory() as conn:
        rows = conn.execute(
            """SELECT o.id,o.generation_id,o.event_type,o.payload_pointer
               FROM sqg_outbox o
               JOIN sqg_generations g ON g.id=o.generation_id
               WHERE g.project_id=%s AND g.status='completed'
                 AND o.delivered_at IS NULL AND o.next_delivery_at<=NOW()
               ORDER BY o.created_at,o.id LIMIT %s""",
            (pid, limit),
        ).fetchall()
    return [{
        "outbox_id": str(r[0]), "generation_id": str(r[1]),
        "event_type": r[2], "payload_pointer": r[3],
    } for r in rows]


def ack_outbox(project_id, outbox_id, *, connect=None):
    """Consumer ACK only; caller must ensure durable downstream ingestion.

    Never called by the Stage C worker. Exactly-once logical ingestion is
    the future Query Validation consumer's responsibility.
    """
    pid, oid = _pid(project_id), publication_uuid(outbox_id)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            row = conn.execute(
                """SELECT o.id,o.delivered_at FROM sqg_outbox o
                   JOIN sqg_generations g ON g.id=o.generation_id
                   WHERE o.id=%s::uuid AND g.project_id=%s AND g.status='completed'
                   FOR UPDATE OF o""",
                (oid, pid),
            ).fetchone()
            if row is None:
                raise PipelineError("Completed handoff event not found", 404)
            if row[1] is None:
                conn.execute(
                    """UPDATE sqg_outbox SET delivered_at=now(),delivery_attempts=delivery_attempts+1
                       WHERE id=%s::uuid""", (oid,),
                )
            return {"outbox_id": oid, "acknowledged": True, "reused": row[1] is not None}
