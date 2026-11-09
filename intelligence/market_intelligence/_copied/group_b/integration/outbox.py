"""GB-MOD-24 project-scoped idempotent outbox handoff boundary.

No network send. A transport worker may read pending events but may only ACK
one after an independently verified durable consumer receipt for the exact
event and payload SHA. Concurrent ACK is serialized under FOR UPDATE.
"""
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from ..contracts import canonical_hash
from .freshness import InvalidationError
from .invalidation import (
    INVALIDATED,REFRESH_REQUESTED,REFRESH_DRAFT_CREATED,_deny,_row,
)


def _scope(project_id,event_id=None):
    if type(project_id) is not str or not project_id:
        _deny("outbox_project_scope_invalid")
    if event_id is not None:
        try:
            if type(event_id) is not str or str(UUID(event_id))!=event_id:
                _deny("outbox_event_id_invalid")
        except ValueError:
            _deny("outbox_event_id_invalid")


@dataclass(frozen=True,slots=True)
class PendingOutboxEvent:
    id:str
    run_id:str
    event_type:str
    event_key:str
    payload_sha256:str
    payload:dict


def _validated(row):
    if (row is None or
        row["event_type"] not in (
            INVALIDATED,REFRESH_REQUESTED,REFRESH_DRAFT_CREATED
        ) or
        type(row["payload"]) is not dict or
        row["payload"].get("schema_version")!="group-b-invalidation-event/v1" or
        row["payload"].get("event_type")!=row["event_type"] or
        row["payload"].get("project_id")!=row["project_id"] or
        row["payload"].get("run_id")!=row["run_id"] or
        row["payload"].get("qualified_for_downstream") is not False or
        row["payload_sha256"]!=canonical_hash(row["payload"])):
        _deny("outbox_lineage_digest_or_safe_event_invalid")
    return PendingOutboxEvent(
        row["id"],row["run_id"],row["event_type"],
        row["event_key"],row["payload_sha256"],row["payload"],
    )


def read_pending_outbox(conn,*,project_id,limit=50,verifier):
    """Read only this project, require authenticated transport worker."""
    _scope(project_id)
    if type(limit) is not int or not 1<=limit<=200:
        _deny("outbox_read_limit_invalid")
    verify=getattr(verifier,"verify_outbox_reader",None)
    if not callable(verify):
        _deny("outbox_reader_authorization_missing")
    try:
        granted=verify(project_id,conn) is True
    except Exception:
        granted=False
    if not granted:
        _deny("outbox_reader_not_authorized")
    rows=conn.execute(
        """SELECT id::text AS id,project_id,run_id::text AS run_id,
                  event_type,event_key,payload_sha256,payload
           FROM group_b_outbox
           WHERE project_id=%s AND status IN ('queued','failed')
             AND event_type IN (%s,%s,%s)
           ORDER BY created_at,id LIMIT %s""",
        (project_id,INVALIDATED,REFRESH_REQUESTED,REFRESH_DRAFT_CREATED,limit),
    ).fetchall()
    return tuple(_validated(dict(zip((
        "id","project_id","run_id","event_type","event_key",
        "payload_sha256","payload"),row))) for row in rows)


def acknowledge_outbox_delivery(conn,*,project_id,event_id,
                                consumer_receipt:str,verifier):
    """One-shot durable ACK. No non-verified output may be marked delivered.

    Callback independently verifies consumer receipt for the exact event
    payload SHA and project/run. Caller owns surrounding transaction.
    """
    _scope(project_id,event_id)
    if type(consumer_receipt) is not str or not consumer_receipt or len(consumer_receipt)>200:
        _deny("outbox_consumer_receipt_invalid")
    check=getattr(verifier,"verify_consumer_delivery",None)
    if not callable(check) or not callable(getattr(conn,"transaction",None)):
        _deny("outbox_consumer_receipt_verifier_missing")
    with conn.transaction():
        row=_row(conn.execute(
            """SELECT id::text AS id,project_id,run_id::text AS run_id,
                      event_type,event_key,payload_sha256,payload,status
               FROM group_b_outbox
               WHERE project_id=%s AND id=%s::uuid FOR UPDATE""",
            (project_id,event_id),
        ))
        event=_validated(row)
        try:
            verified=check(project_id,event,consumer_receipt,conn) is True
        except Exception:
            verified=False
        if not verified:
            _deny("outbox_consumer_delivery_not_attested")
        if row["status"]=="delivered":
            return False
        if row["status"] not in ("queued","failed"):
            _deny("outbox_delivery_state_invalid")
        updated=conn.execute(
            """UPDATE group_b_outbox
               SET status='delivered',delivered_at=now()
               WHERE project_id=%s AND id=%s::uuid
                 AND status IN ('queued','failed')""",
            (project_id,event_id),
        )
        if updated.rowcount!=1:
            _deny("outbox_ack_concurrent_update")
        return True
