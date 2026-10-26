"""FIX-15 separate durable compensating-revocation dispatch to Group A/B/C.

Peer implementations must opt in with revoke(pointer,event_hash,notice), commit
a durable idempotent invalidation inbox, and return FIX-12 committed receipt.
Existing pinned Group A/C peers do not yet implement revoke; unavailable !=
acknowledged. No provider calls, Group work or made-up revocation responses.
"""
from __future__ import annotations

from collections.abc import Mapping
import re

from .contracts import QDPError, sha256
from .integrations.contracts import QDPHandoffPointer
from .outbox_delivery import (_consumer,CONSUMER_GROUP,_connection,
                              claim_delivery,_fenced,settle_delivery)

_SHA = re.compile(r"[0-9a-f]{64}\Z")


def verify_invalidation_event(row: Mapping, consumer: str) -> QDPHandoffPointer:
    _consumer(consumer)
    payload=row.get("payload")
    if (not isinstance(payload,Mapping)
            or row.get("event_type")!="preparation_invalidated"
            or row.get("analysis_group")!=CONSUMER_GROUP[consumer]
            or payload.get("schema_version")!="qdp-event/v1"
            or payload.get("event_type")!="preparation_invalidated"
            or sha256(payload)!=row.get("payload_sha256")
            or payload.get("project_id")!=row.get("project_id")
            or payload.get("run_id")!=str(row.get("run_id"))
            or payload.get("video_id")!=row.get("video_id")
            or payload.get("analysis_group")!=row.get("analysis_group")
            or type(payload.get("invalidation_generation")) is not int
            or payload["invalidation_generation"]<=0
            or type(payload.get("provenance_sha256")) is not str
            or not _SHA.fullmatch(payload["provenance_sha256"])
            or type(payload.get("input_sha256")) is not str
            or not _SHA.fullmatch(payload["input_sha256"])
            or payload.get("origin") not in (
                "sre","human_review","d1_source","retention",
                "configuration","operator")):
        raise QDPError("invalid_revocation","Unverifiable immutable revocation event",409)
    try:
        return QDPHandoffPointer(
            event_id=str(row["event_id"]),
            project_id=payload["project_id"],
            qdp_run_id=payload["run_id"],
            video_id=payload["video_id"],
            analysis_group=payload["analysis_group"],
            input_sha256=payload["input_sha256"])
    except (ValueError,TypeError,KeyError) as exc:
        raise QDPError("invalid_revocation","Invalid revocation pointer",409) from exc


def dispatch_invalidation_once(consumer, *, receiver, connect=None, worker_id=None):
    """Claim a signed compensation event and ACK ONLY after real peer commit."""
    _consumer(consumer)
    if receiver is None or not callable(getattr(receiver,"revoke",None)):
        raise QDPError("revocation_receiver_unavailable",
                       "No durable peer revocation inbox connected",503)
    factory=_connection(connect)
    lease=claim_delivery(consumer,connect=factory,worker_id=worker_id,
                         event_type="preparation_invalidated")
    if lease is None:
        return None
    try:
        with factory() as conn:
            with conn.transaction():
                event=_fenced(conn,lease)
        if event is None:
            return {"state":"lost_lease","event_id":str(lease["event_id"])}
        pointer=verify_invalidation_event(event,consumer)
        result=receiver.revoke(pointer,event["payload_sha256"],dict(event["payload"]))
        return settle_delivery(lease,receipt=result,connect=factory)
    except Exception as exc:
        return settle_delivery(lease,error_code=(
            exc.code if isinstance(exc,QDPError) else type(exc).__name__),
            permanent=isinstance(exc,QDPError) and exc.code=="invalid_revocation",
            connect=factory)
