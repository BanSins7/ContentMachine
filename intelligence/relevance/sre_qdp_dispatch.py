"""Explicit trusted SRE M36 -> QDP durable intake + fenced producer ACK.

No self-HTTP, no paid request and no invented model output. The caller is a
trusted operator with project-scoped authorization; neither a browser token
nor a transport SHA grants access to SRE source events.
"""
from __future__ import annotations

from intelligence.relevance.live_result_outbox import (
    claim_sre_result_event,acknowledge_sre_result_event,
)
from intelligence.relevance.v1_contracts import project_id, reject
from intelligence.qualified_data.sre_live_bridge import (
    receive_sre_live_item,get_sre_live_receipt,
)


def dispatch_one_sre_qdp(project, *, sre_connect, actor_id, actor_authorizer,
                         qdp_connect=None):
    """Exactly one leased item. An uncertain ACK is NOT silently retried.

    On crash after QDP commit but before SRE ACK, a later SRE lease may
    replay the *same* item UUID and SHA; QDP receipt is idempotent.
    """
    pid=project_id(project)
    if not callable(sre_connect) or not callable(actor_authorizer):
        reject("sre_qdp","trusted SRE DB and host actor authorizer required",
               "actor_not_authorized")
    with sre_connect() as conn:
        claimed=claim_sre_result_event(
            conn,pid,actor_id=actor_id,actor_authorizer=actor_authorizer,
        )
    if claimed is None:
        return {"schema_version":"sre-qdp-dispatch/v1",
                "project_id":pid,"state":"no_work","provider_calls":0}
    event=claimed["event"]
    # Destination verifies source row and immutable payload in its own
    # transaction. Do not catch+swallow partial or uncertain delivery.
    receipt=receive_sre_live_item(event,connect=qdp_connect)
    if (receipt["event_id"]!=event["event_id"]
        or receipt["project_id"]!=pid
        or receipt["event_sha256"]!=event["event_sha256"]
        or receipt["persisted"] is not True
        or receipt["qualification_created"] is not False):
        reject("sre_qdp","QDP receipt differs from leased SRE source",
               "snapshot_mismatch")
    readback=get_sre_live_receipt(
        pid,event["event_id"],connect=qdp_connect,
    )
    if readback!=receipt:
        reject("sre_qdp","Independent QDP durable receipt readback failed",
               "snapshot_mismatch")
    with sre_connect() as conn:
        ack=acknowledge_sre_result_event(
            conn,pid,event["event_id"],claimed["lease_token"],
            claimed["lease_generation"],event["event_sha256"],
            actor_id=actor_id,actor_authorizer=actor_authorizer,
        )
    if ack.get("acknowledged") is not True or ack.get("event_id")!=event["event_id"]:
        reject("sre_qdp","SRE lease ACK was not committed",
               "invalid_transition")
    return {
        "schema_version":"sre-qdp-dispatch/v1",
        "project_id":pid,"event_id":event["event_id"],
        "event_type":event["schema_version"],
        "state":"acknowledged","qdp_receipt":receipt,
        "qualification_created":False,"provider_calls":0,
    }
