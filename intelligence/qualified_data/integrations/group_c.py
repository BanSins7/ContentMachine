"""FIX-14 Group C real durable acceptance adapter.

QDP never scores a video; receipt records Group C inbox + queued score job,
not a scored metric. The Group C package is injected explicitly, never
required at QDP import time. Missing peer remains unavailable (503).
"""
from __future__ import annotations

import re
from dataclasses import asdict
from .contracts import QDPHandoffPointer
from ..contracts import QDPError

_HEX = re.compile(r"[0-9a-f]{64}\Z")


def _notice(pointer: QDPHandoffPointer):
    if not isinstance(pointer,QDPHandoffPointer) or pointer.analysis_group!="C":
        raise QDPError("group_c_wrong_target","Only Group C handoffs accepted",409)
    return asdict(pointer)


def translate_c_receipt(raw, pointer, payload_sha256):
    """Require an actually committed C inbox + score job (not computed score)."""
    if not isinstance(raw,dict) or raw.get("ack") is not True or (
            raw.get("event_id")!=pointer.event_id):
        raise QDPError("group_c_receipt_invalid","Group C did not commit durable intake",409)
    try:
        import uuid
        run_id=raw["run_id"]
        if type(run_id) is not str or str(uuid.UUID(run_id))!=run_id:
            raise ValueError("run")
        job_id=raw["job_id"]
        if type(job_id) is not int or job_id<1:
            raise ValueError("job")
        key=raw["receipt_key"]
        if key!=run_id:
            raise ValueError("key")
        if type(payload_sha256) is not str or not _HEX.fullmatch(payload_sha256):
            raise ValueError("sha")
    except (KeyError,TypeError,ValueError,AttributeError) as exc:
        raise QDPError("group_c_receipt_invalid","Missing Group C durable run/job",409) from exc
    return {
        "state":"committed","event_id":pointer.event_id,
        "consumer":"group_c","payload_sha256":payload_sha256,
        "receipt_key":"gc:"+run_id+":"+str(job_id),
    }


class GroupCConsumerAdapter:
    """FIX-12 receiver interface; C owns transaction and job creation.

    Connection factory must return an autocommit connection so C's own
    transaction context can commit before producer QDP ACK is written.
    """
    def __init__(self,consumer,notice_factory,connect):
        if (not callable(getattr(consumer,"receive",None))
                or not callable(notice_factory) or not callable(connect)):
            raise QDPError("group_c_unavailable","Concrete C inbox unavailable",503)
        self._consumer=consumer
        self._notice_factory=notice_factory
        self._connect=connect

    def receive(self,pointer:QDPHandoffPointer,payload_sha256:str)->dict:
        notice=self._notice_factory(_notice(pointer))
        with self._connect() as conn:
            result=self._consumer.receive(conn,notice,payload_sha256)
        return translate_c_receipt(result,pointer,payload_sha256)


def from_installed_group_c(connect,*,enabled=False):
    if enabled is not True or not callable(connect):
        raise QDPError("group_c_unavailable","Group C opt-in disabled",503)
    try:
        from intelligence.group_c.qdp_intake import QDPGroupCConsumer
        from intelligence.group_c.ports import QDPHandoffNotice
    except ImportError as exc:
        raise QDPError("group_c_unavailable","Real Group C package absent",503) from exc
    return GroupCConsumerAdapter(QDPGroupCConsumer(),
                                 QDPHandoffNotice.from_mapping,connect)
