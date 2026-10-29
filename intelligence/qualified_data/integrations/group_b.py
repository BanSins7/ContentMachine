"""Live QDP qdp-event/v1 + qdp-preparation/v2 → Group B adapter.

The QDP outbox event hash is NOT the qdp-handoff/v1 pointer hash.
The Group B transaction re-reads the actual QDP event and versioned
current preparation before committing its inbox + initial B draft/receipt.
The QDP dispatcher acknowledges only after this method returns.
"""
from __future__ import annotations

from dataclasses import asdict
from .contracts import QDPHandoffPointer, IntegrationCapability, CapabilityState
from ..contracts import QDPError

QDP_EVENT="qdp-event/v1"
QDP_PREPARATION="qdp-preparation/v2"
GROUP_B_COMPAT="qdp-v2-receiver"


def group_b_capability(*,peer_event_contract=None,peer_preparation_contract=None):
    if peer_event_contract is None or peer_preparation_contract is None:
        return IntegrationCapability(
            "group_b",CapabilityState.UNAVAILABLE,
            reason_code="peer_contract_not_negotiated")
    if peer_event_contract!=QDP_EVENT or peer_preparation_contract!=QDP_PREPARATION:
        return IntegrationCapability(
            "group_b",CapabilityState.INCOMPATIBLE,
            observed_schema=str(peer_event_contract)+"/"+str(peer_preparation_contract),
            reason_code="qdp_v2_reader_version_mismatch")
    return IntegrationCapability(
        "group_b",CapabilityState.AVAILABLE,
        observed_schema=QDP_EVENT+"/"+QDP_PREPARATION,
        reason_code="installed_v2_adapter_matched")


class _InstalledGroupBReceiver:
    """The B native inbox owns its *own* commit before QDP receives an ACK."""
    def __init__(self,connect):
        if not callable(connect):
            raise QDPError("group_b_unavailable","B database factory required",503)
        self._connect=connect

    def receive(self,pointer:QDPHandoffPointer,payload_sha256:str) -> dict:
        from intelligence.group_b.intake import receive_qdp_delivery
        from intelligence.group_bc.qdp_b_v2 import read_qdp_v2
        if not isinstance(pointer,QDPHandoffPointer) or pointer.analysis_group!="B":
            raise QDPError("group_b_wrong_target","Only B accepted",409)
        # B native qdp-handoff/v1 is a transport pointer. Its original QDP
        # outbox payload is qdp-event/v1; hash must remain unchanged.
        wire=asdict(pointer)
        receipt=receive_qdp_delivery(
            self._connect,wire,payload_sha256,
            preparation_reader=read_qdp_v2,
        )
        if (receipt.event_id!=pointer.event_id or
            receipt.project_id!=pointer.project_id or
            type(receipt.receipt_key) is not str or
            len(receipt.receipt_key)<8):
            raise QDPError("group_b_receipt_invalid","B durable receipt mismatch",409)
        return {
            "state":"committed","event_id":pointer.event_id,
            "consumer":"group_b","payload_sha256":payload_sha256,
            "receipt_key":receipt.receipt_key,
        }


class GroupBConsumerAdapter:
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
                           "Observed B QDP version is incompatible",503)
        if not callable(getattr(self._receiver,"receive",None)):
            raise QDPError("group_b_unavailable","B durable receiver missing",503)
        return self._receiver.receive(pointer,payload_sha256)


def from_installed_group_b(connect=None,*,enabled=False):
    """Opt-in concrete B receiver; never self-install from untrusted HTTP."""
    if enabled is not True or not callable(connect):
        raise QDPError("group_b_unavailable","Group B opt-in disabled or no connection",503)
    return GroupBConsumerAdapter(
        _InstalledGroupBReceiver(connect),
        peer_event_contract=QDP_EVENT,
        peer_preparation_contract=QDP_PREPARATION,
    )
