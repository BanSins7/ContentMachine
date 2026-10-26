"""FIX-12 QDP durable outbox delivery + signed-by-evidence consumer receipt.

Claims, timeouts and ACKs touch ONLY the addressed consumer delivery row.
No Group A/B/C jobs are fabricated here: the injected receiver must commit
its OWN inbox/job transaction and return the same receipt on replay.
Delivery ACK = durable acceptance, never completed paid analysis.

The adapter must be explicitly provided by FIX-13/14 integrations; missing
receiver fails closed, not a local placeholder success. No network/provider
calls inside this module.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Mapping

from .contracts import GROUPS, QDPError, sha256
from .integrations.contracts import QDPHandoffPointer
from .worker_hardening import CONSUMER, retry_delay_seconds

DELIVERY_LEASE_SECONDS = 90
_CANONICAL_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_RECEIPT_KEY = re.compile(r"[A-Za-z0-9._:/-]{8,160}\Z")
CONSUMER_GROUP = {value: key for key, value in CONSUMER.items()}


def _uuid(value, label):
    if not isinstance(value, str) or not _CANONICAL_UUID.fullmatch(value):
        raise QDPError("invalid_delivery_id", "Invalid " + label, 422)
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError("noncanonical")
    except (TypeError, ValueError) as exc:
        raise QDPError("invalid_delivery_id", "Invalid " + label, 422) from exc
    return value


def _consumer(value):
    if value not in CONSUMER_GROUP:
        raise QDPError("invalid_delivery_consumer", "Unknown QDP delivery consumer", 422)
    return value


def verify_delivery_event(row: Mapping, consumer: str) -> QDPHandoffPointer:
    """Validate original committed event identity and payload, no shortcuts."""
    _consumer(consumer)
    event = row.get("payload")
    if not isinstance(event, Mapping):
        raise QDPError("delivery_payload_invalid", "Outbox payload is not an object", 409)
    if row.get("event_type") != "preparation_ready" or (
            row.get("analysis_group") != CONSUMER_GROUP[consumer]):
        raise QDPError("delivery_type_mismatch", "Mismatched Group handoff", 409)
    if event.get("schema_version") != "qdp-event/v1" or (
            event.get("event_type") != row.get("event_type")):
        raise QDPError("delivery_schema_mismatch", "Unsupported event schema", 409)
    if sha256(event) != row.get("payload_sha256"):
        raise QDPError("delivery_payload_modified", "Outbox payload integrity failed", 409)
    expected = {
        "project_id":row.get("project_id"), "run_id":str(row.get("run_id")),
        "video_id":row.get("video_id"), "analysis_group":row.get("analysis_group"),
        "state":"ready",
    }
    if any(event.get(k) != value for k,value in expected.items()):
        raise QDPError("delivery_identity_mismatch", "Outbox identity differs from row", 409)
    digest = event.get("input_sha256")
    if type(digest) is not str or _HEX64.fullmatch(digest) is None or (
            event.get("reason_code") is not None):
        raise QDPError("delivery_input_invalid", "Ready event has no valid input SHA", 409)
    try:
        return QDPHandoffPointer(
            event_id=str(row["event_id"]), project_id=expected["project_id"],
            qdp_run_id=expected["run_id"], video_id=expected["video_id"],
            analysis_group=expected["analysis_group"], input_sha256=digest)
    except (ValueError, TypeError, KeyError) as exc:
        raise QDPError("delivery_identity_invalid", "Invalid QDP handoff pointer", 409) from exc


def verify_receipt(receipt: Mapping, pointer: QDPHandoffPointer,
                   consumer: str, payload_sha256: str) -> str:
    """Receipt proves the trusted adapter COMMITTED a durable consumer inbox.

    Do not accept mere 'queued' or 'scheduled' statuses: the adapter must
    commit its own work before returning state='committed'. The receiver is
    a trusted, operator-configured port, not an arbitrary untrusted caller.
    """
    if not isinstance(receipt, Mapping) or receipt.get("state") != "committed" or (
            receipt.get("event_id") != pointer.event_id or
            receipt.get("consumer") != consumer or
            receipt.get("payload_sha256") != payload_sha256):
        raise QDPError("consumer_receipt_invalid", "No committed consumer receipt", 409)
    key=receipt.get("receipt_key")
    if type(key) is not str or _RECEIPT_KEY.fullmatch(key) is None:
        raise QDPError("consumer_receipt_invalid", "Invalid consumer receipt key", 409)
    return key


def _connection(factory):
    if factory is not None:
        return factory
    from .repository import _connect
    return _connect


def claim_delivery(consumer: str, *, connect=None, worker_id: str | None = None):
    """Lease one pending or expired delivery with SKIP LOCKED and generation."""
    consumer=_consumer(consumer)
    worker_id=worker_id or "qdp-outbox-" + uuid.uuid4().hex[:12]
    if type(worker_id) is not str or not 1 <= len(worker_id) <= 128:
        raise QDPError("invalid_worker", "Invalid delivery worker identity", 422)
    with _connection(connect)() as conn:
        with conn.transaction():
            # Reap ONLY exhausted rows for this consumer. Other consumers are
            # untouched, even when they share the exact same outbox event.
            conn.execute(
                """WITH expired AS (
                     SELECT d.event_id,d.consumer
                       FROM qdp_outbox_deliveries d
                      WHERE d.consumer=%s AND d.attempts>=d.max_attempts
                        AND (d.state='pending'
                          OR (d.state='leased' AND d.lease_until<=now()))
                      FOR UPDATE SKIP LOCKED LIMIT 100
                   )
                   UPDATE qdp_outbox_deliveries d
                      SET state='dead_letter',reason_code='attempts_exhausted',
                          locked_by=NULL,lease_until=NULL,updated_at=now()
                     FROM expired x
                    WHERE d.event_id=x.event_id AND d.consumer=x.consumer""",
                (consumer,))
            selected=conn.execute(
                """SELECT d.event_id
                     FROM qdp_outbox_deliveries d
                     JOIN qdp_outbox o ON o.event_id=d.event_id
                    WHERE d.consumer=%s AND d.attempts<d.max_attempts
                      AND o.event_type='preparation_ready'
                      AND o.analysis_group=%s
                      AND ((d.state='pending' AND d.run_after<=now())
                        OR (d.state='leased' AND d.lease_until<=now()))
                    ORDER BY o.created_at,d.event_id
                    FOR UPDATE OF d SKIP LOCKED LIMIT 1""",
                (consumer,CONSUMER_GROUP[consumer])).fetchone()
            if selected is None:
                return None
            leased=conn.execute(
                """UPDATE qdp_outbox_deliveries SET state='leased',
                      locked_by=%s,lease_until=now()+(%s * interval '1 second'),
                      attempts=attempts+1,lease_generation=lease_generation+1,
                      reason_code=NULL,updated_at=now()
                   WHERE event_id=%s::uuid AND consumer=%s
                   RETURNING event_id,consumer,attempts,lease_generation""",
                (worker_id,DELIVERY_LEASE_SECONDS,str(selected["event_id"]),consumer)
            ).fetchone()
            if not leased:
                raise QDPError("delivery_claim_lost", "Delivery claim was lost", 409)
            return dict(leased,worker_id=worker_id)


def _fenced(conn, lease, *, lock=False):
    sql="""SELECT d.event_id,d.consumer,d.attempts,d.max_attempts,
                  d.lease_generation,d.state,d.locked_by,d.lease_until,
                  o.project_id,o.run_id,o.video_id,o.analysis_group,
                  o.event_type,o.payload,o.payload_sha256
             FROM qdp_outbox_deliveries d
             JOIN qdp_outbox o ON o.event_id=d.event_id
            WHERE d.event_id=%s::uuid AND d.consumer=%s
              AND d.state='leased' AND d.locked_by=%s
              AND d.lease_generation=%s AND d.lease_until>now()"""
    if lock:
        sql+=" FOR UPDATE OF d"
    return conn.execute(sql,(str(lease["event_id"]),lease["consumer"],
                             lease["worker_id"],lease["lease_generation"])).fetchone()


def settle_delivery(lease: Mapping, *, receipt=None, error_code=None,
                    permanent=False, connect=None):
    """ACK, retry or dead-letter only while holding this generation's lease."""
    if receipt is None and error_code is None:
        raise QDPError("invalid_settlement", "Receipt or reason required", 422)
    with _connection(connect)() as conn:
        with conn.transaction():
            row=_fenced(conn,lease,lock=True)
            if row is None:
                return {"state":"lost_lease","event_id":str(lease["event_id"]),
                        "consumer":lease["consumer"]}
            try:
                pointer=verify_delivery_event(row,lease["consumer"])
            except QDPError as exc:
                receipt=None
                error_code=exc.code
                permanent=True
                pointer=None
            if receipt is not None:
                key=verify_receipt(receipt,pointer,lease["consumer"],row["payload_sha256"])
                # Receipt state and delivery ACK are committed ATOMICALLY in
                # the QDP DB. Duplicate ack is impossible by PK(event,consumer).
                inserted=conn.execute(
                    """INSERT INTO qdp_delivery_receipts
                       (event_id,consumer,receipt_key,payload_sha256)
                       VALUES (%s::uuid,%s,%s,%s)
                       ON CONFLICT (event_id,consumer) DO NOTHING
                       RETURNING receipt_key""",
                    (str(lease["event_id"]),lease["consumer"],key,row["payload_sha256"])
                ).fetchone()
                if inserted is None:
                    existing=conn.execute(
                        """SELECT receipt_key,payload_sha256 FROM qdp_delivery_receipts
                           WHERE event_id=%s::uuid AND consumer=%s""",
                        (str(lease["event_id"]),lease["consumer"])).fetchone()
                    if not existing or existing["receipt_key"]!=key or (
                            existing["payload_sha256"]!=row["payload_sha256"]):
                        raise QDPError("receipt_conflict","Conflicting committed receipt",409)
                conn.execute(
                    """UPDATE qdp_outbox_deliveries SET state='acknowledged',
                       locked_by=NULL,lease_until=NULL,reason_code=NULL,
                       updated_at=now()
                       WHERE event_id=%s::uuid AND consumer=%s""",
                    (str(lease["event_id"]),lease["consumer"]))
                return {"state":"acknowledged","event_id":str(lease["event_id"]),
                        "consumer":lease["consumer"],"receipt_key":key}
            reason=error_code if type(error_code) is str and error_code else "consumer_unavailable"
            if len(reason)>120 or not re.fullmatch(r"[a-zA-Z0-9_:-]+",reason):
                reason="delivery_failed"
            exhausted=permanent or row["attempts"]>=row["max_attempts"]
            delay=retry_delay_seconds(row["attempts"])
            conn.execute(
                """UPDATE qdp_outbox_deliveries SET
                     state=%s,reason_code=%s,locked_by=NULL,lease_until=NULL,
                     run_after=now()+(%s * interval '1 second'),updated_at=now()
                   WHERE event_id=%s::uuid AND consumer=%s""",
                ("dead_letter" if exhausted else "pending",reason,delay,
                 str(lease["event_id"]),lease["consumer"]))
            return {"state":"dead_letter" if exhausted else "pending",
                    "event_id":str(lease["event_id"]),"consumer":lease["consumer"],
                    "reason_code":reason,"retry_after_seconds":None if exhausted else delay}


def dispatch_once(consumer: str, *, receiver, connect=None, worker_id=None):
    """Delivery orchestrator. The trusted receiver must commit a peer job first.

    A missing receiver is NOT a positive ACK and does not consume a lease.
    QDP only sends an immutable QDPHandoffPointer, not user data/media bytes.
    """
    _consumer(consumer)
    if receiver is None or not callable(getattr(receiver,"receive",None)):
        raise QDPError("consumer_unavailable","A durable consumer adapter is required",503)
    factory=_connection(connect)
    lease=claim_delivery(consumer,connect=factory,worker_id=worker_id)
    if lease is None:
        return None
    row=None
    try:
        with factory() as conn:
            with conn.transaction():
                row=_fenced(conn,lease)
        if row is None:
            return {"state":"lost_lease","consumer":consumer,
                    "event_id":str(lease["event_id"])}
        pointer=verify_delivery_event(row,consumer)
        # No event may escape after source/score/media/SRE revocation.
        from .preparation import read_current_preparation
        current=read_current_preparation(pointer.project_id,pointer.qdp_run_id,
                                         pointer.video_id,pointer.analysis_group,
                                         connect=factory)
        if current["state"]!="ready" or current.get("input_sha256")!=pointer.input_sha256:
            reason=current.get("reason_code") or "preparation_hash_mismatch"
            return settle_delivery(lease,error_code=reason,permanent=True,
                                   connect=factory)
        # Receiver must be a trusted concrete peer adapter. Its receive()
        # transaction commits the consumer's own inbox AND idempotent job,
        # returning the same durable receipt on repeated (event_id, hash).
        receipt=receiver.receive(pointer,row["payload_sha256"])
        return settle_delivery(lease,receipt=receipt,connect=factory)
    except Exception as exc:
        # No exception/message, URL, token, or provider error is persisted.
        # If consumer committed before this exception, replay MUST be idempotent.
        return settle_delivery(lease,error_code=(
            exc.code if isinstance(exc,QDPError) else type(exc).__name__),
            permanent=isinstance(exc,QDPError) and exc.code in {
                "delivery_schema_mismatch","delivery_identity_mismatch",
                "delivery_payload_modified","delivery_type_mismatch",
                "delivery_input_invalid","receipt_conflict"},
            connect=factory)
