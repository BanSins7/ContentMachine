"""FIX-14 Group B exact compatibility gate, no phantom durable ACK.

Pinned GB-MOD-04 reader compares persisted QDP outbox.payload to *pointer*
and requires qdp-preparation/v1. Current QDP produces qdp-event/v1 envelope
and qdp-preparation/v2; the peer's SQL reader cannot be repaired by changing
a transport argument. Until Group B installs a version-negotiated reader, DO
NOT call receive_qdp_delivery or claim a real Group B job/ACK.
"""
from __future__ import annotations
from .contracts import QDPHandoffPointer, IntegrationCapability, CapabilityState
from ..contracts import QDPError

QDP_EVENT = "qdp-event/v1"
QDP_PREPARATION = "qdp-preparation/v2"
OBSERVED_GROUP_B_EVENT_EQ = "qdp-handoff/v1(pointer-as-event)"
OBSERVED_GROUP_B_PREPARATION = "qdp-preparation/v1"
GROUP_B_COMPAT = "incompatible"


def group_b_capability(*, peer_event_contract=None,peer_preparation_contract=None):
    """Return AVAILABLE only for observed, exact peer acceptance semantics."""
    if peer_event_contract is None or peer_preparation_contract is None:
        return IntegrationCapability(
            "group_b",CapabilityState.UNAVAILABLE,
            reason_code="peer_contract_not_negotiated")
    if (peer_event_contract!=QDP_EVENT or
            peer_preparation_contract!=QDP_PREPARATION):
        return IntegrationCapability(
            "group_b",CapabilityState.INCOMPATIBLE,
            observed_schema=str(peer_event_contract)+"/"+str(peer_preparation_contract),
            reason_code="peer_reader_requires_pointer_payload_and_preparation_v1")
    return IntegrationCapability(
        "group_b",CapabilityState.AVAILABLE,
        observed_schema=QDP_EVENT+"/"+QDP_PREPARATION,
        reason_code="matched")


class GroupBConsumerAdapter:
    """Adapter requiring a real peer that explicitly supports QDP's versions."""
    def __init__(self,receiver,*,peer_event_contract=None,
                 peer_preparation_contract=None):
        self._receiver=receiver
        self._cap=group_b_capability(
            peer_event_contract=peer_event_contract,
            peer_preparation_contract=peer_preparation_contract)

    def receive(self,pointer:QDPHandoffPointer,payload_sha256:str):
        if not isinstance(pointer,QDPHandoffPointer) or pointer.analysis_group!="B":
            raise QDPError("group_b_wrong_target","Not Group B",409)
        if self._cap.state is not CapabilityState.AVAILABLE:
            raise QDPError("group_b_incompatible",
                           "Group B peer SQL expects old pointer/v1 instead of event/v2",503)
        if not callable(getattr(self._receiver,"receive",None)):
            raise QDPError("group_b_unavailable","Group B durable inbox adapter missing",503)
        # Explicitly negotiated future peer adapter; no fake receipt.
        return self._receiver.receive(pointer,payload_sha256)


def from_installed_group_b(*,enabled=False):
    """Current pinned GB-MOD-04 CANNOT consume QDP v2; fail before claiming."""
    if enabled is not True:
        raise QDPError("group_b_unavailable","Group B integration not enabled",503)
    raise QDPError("group_b_incompatible",
       "GB-MOD-04 QDP reader requires pointer-as-event and qdp-preparation/v1",503)
