"""SP-12 explicit, receipt-verified Collector intake. Never run source providers.

The durable delivery attempt is committed as "uncertain" BEFORE the external
intake call. If a response is lost, a subsequent dispatch NEVER blindly
retries it. Explicit reconciliation may call get_receipt (read-only), but
never invokes accept again for that same attempt.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, canonical
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid
from intelligence.search_planner.contracts import publication_uuid
from intelligence.search_planner.generation_workflow import _guard
from .collector_adapter import (
    CAPABILITIES_SCHEMA, EmptyCollectorAdapter,
    EXPECTED_COLLECTOR_SCHEMA, verify_collector_receipt,
)

OWNER_ACTION = "search_planner.handoff.deliver"


def _port(port):
    p=port if port is not None else EmptyCollectorAdapter()
    for name in ("available","capabilities","accept","get_receipt"):
        if not callable(getattr(p,name,None)):
            raise PipelineError("Invalid server-owned durable Collector handoff port",422)
    available=p.available()
    if type(available) is not bool:
        raise PipelineError("Collector port readiness must be explicit",409)
    # The adapter returns an operator-verified local manifest; no speculative
    # network probe and no assumption that a declaration enables a source.
    info=p.capabilities()
    if (type(info) is not dict or set(info)!={
            "schema_version","accepted_contracts","max_queries_per_set",
            "durable_receipt_readback","provider_execution_separate",
        } or info["schema_version"]!=CAPABILITIES_SCHEMA):
        raise PipelineError("Unversioned Collector adapter capability manifest",409)
    if available and (
            type(info["accepted_contracts"]) is not list or
            info["accepted_contracts"] != [EXPECTED_COLLECTOR_SCHEMA] or
            type(info["max_queries_per_set"]) is not int or
            info["max_queries_per_set"] != 50 or
            info["durable_receipt_readback"] is not True or
            info["provider_execution_separate"] is not True):
        raise PipelineError("Collector adapter cannot safely accept V1 durable chunks",409)
    return p,available


def _checked_event(conn,pid,eid,*,lock):
    sql="""SELECT e.id,e.qv_run_id,e.status,e.chunk_count,e.projection,
                  o.delivered_at,e.project_id
             FROM sp_handoff_events e
             JOIN qv_outbox o ON o.id=e.qv_outbox_id
            WHERE e.id=%s::uuid AND e.project_id=%s"""
    if lock:
        sql+=" FOR UPDATE OF e"
    row=conn.execute(sql,(eid,pid)).fetchone()
    if row is None:
        raise PipelineError("Project-scoped SP handoff not found",404)
    if row[5] is None or row[6]!=pid:
        raise PipelineError("SP inbox event missing completed QV ACK",409)
    if row[2] in ("no_work","cancelled","incompatible"):
        return row
    if (type(row[4]) is not dict or
            row[4].get("qv_outbox_acknowledged_by_this_projection") is not False):
        raise PipelineError("SP durable source envelope untrusted",409)
    return row


def _require_approved_plan(conn,pid,qvid):
    row=conn.execute(
        """SELECT p.id
             FROM sp_held_plan_approvals a
             JOIN sp_held_task_plans p ON p.id=a.plan_id
            WHERE p.project_id=%s AND p.validation_run_id=%s::uuid
              AND p.snapshot->>'external_dispatch_authorized'='false'
              AND jsonb_array_length(p.snapshot->'tasks')>0
            ORDER BY a.approved_at DESC,p.id DESC LIMIT 1""",
        (pid,qvid),
    ).fetchone()
    if row is None:
        raise PipelineError("Owner-approved HELD SP source plan is required before Collector intake",403)


def _next_chunk(conn,eid):
    rows=conn.execute(
        """SELECT c.chunk_index,c.chunk_sha256,c.chunk,
                  a.id,a.status,a.idempotency_key,r.id
             FROM sp_handoff_chunks c
             LEFT JOIN sp_delivery_attempts a
                    ON a.event_id=c.event_id AND a.chunk_index=c.chunk_index
             LEFT JOIN sp_delivery_receipts r
                    ON r.event_id=c.event_id AND r.chunk_index=c.chunk_index
            WHERE c.event_id=%s::uuid
            ORDER BY c.chunk_index""",
        (eid,),
    ).fetchall()
    if any(r[6] is not None and r[4]!="accepted" for r in rows):
        raise PipelineError("External receipt without accepted SP delivery record",409)
    pending=[r for r in rows if r[6] is None]
    return rows,(pending[0] if pending else None)


def _state(conn,eid,status):
    conn.execute(
        """UPDATE sp_handoff_events SET status=%s,updated_at=now()
           WHERE id=%s::uuid""",
        (status,eid),
    )


def _reserve(pid,eid,owner_action_guard,conn):
    with conn.transaction():
        event=_checked_event(conn,pid,eid,lock=True)
        _,qvid,state,count,source,_,_=event
        if state in ("no_work","cancelled","incompatible"):
            return {"kind":"terminal","status":state}
        _require_approved_plan(conn,pid,str(qvid))
        rows,nxt=_next_chunk(conn,eid)
        if len(rows)!=count:
            raise PipelineError("SP durable handoff chunk inventory corrupted",409)
        if nxt is None:
            _state(conn,eid,"collector_accepted")
            return {"kind":"terminal","status":"collector_accepted"}
        idx,chunk_sha,chunk,aid,attempt_state,key,receipt=nxt
        if aid is not None:
            if attempt_state=="uncertain":
                return {"kind":"unknown","chunk_index":idx,"idempotency_key":key}
            raise PipelineError("Delivery was previously resolved without a matching receipt",409)
        if state in ("needs_attention","collector_accepted"):
            raise PipelineError("SP handoff needs explicit resolution before new delivery",409)
        key="sp12-"+eid+"-"+str(idx)
        inserted=conn.execute(
            """INSERT INTO sp_delivery_attempts
                (event_id,chunk_index,idempotency_key,chunk_sha256,status)
               VALUES (%s::uuid,%s,%s,%s,'uncertain') RETURNING id""",
            (eid,idx,key,chunk_sha),
        ).fetchone()
        if inserted is None:
            raise PipelineError("Could not durably reserve Collector delivery",409)
        _state(conn,eid,"delivering")
        return {
            "kind":"reserved","chunk_index":idx,"chunk":chunk,
            "idempotency_key":key,"attempt_id":str(inserted[0]),
        }


def _finalize(pid,eid,reserved,receipt,conn):
    chunk=reserved["chunk"]
    idx=reserved["chunk_index"]
    key=reserved["idempotency_key"]
    checked=verify_collector_receipt(
        receipt, project_id=pid,event_id=eid,
        chunk=chunk,idempotency_key=key,
    )
    with conn.transaction():
        event=_checked_event(conn,pid,eid,lock=True)
        if event[2] in ("cancelled","no_work","incompatible"):
            raise PipelineError("Collector receipt for cancelled/no-work handoff",409)
        row=conn.execute(
            """SELECT id,status,chunk_sha256 FROM sp_delivery_attempts
               WHERE event_id=%s::uuid AND chunk_index=%s
               FOR UPDATE""",
            (eid,idx),
        ).fetchone()
        if (row is None or str(row[0])!=reserved["attempt_id"] or
                row[2]!=chunk["chunk_sha256"] or row[1]!="uncertain"):
            raise PipelineError("Delivery attempt not in uncertain state",409)
        already=conn.execute(
            """SELECT id FROM sp_delivery_receipts
               WHERE event_id=%s::uuid AND chunk_index=%s""",
            (eid,idx),
        ).fetchone()
        if already is not None:
            raise PipelineError("Collector receipt already finalized",409)
        timestamp=datetime.fromisoformat(checked["accepted_at"].replace("Z","+00:00"))
        conn.execute(
            """INSERT INTO sp_delivery_receipts
                 (attempt_id,event_id,chunk_index,external_query_set_id,
                  external_plan_id,collector_contract,
                  collector_receipt_sha256,receipt,accepted_at)
               VALUES (%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
            (reserved["attempt_id"],eid,idx,checked["external_query_set_id"],
             checked["external_plan_id"],EXPECTED_COLLECTOR_SCHEMA,
             checked["receipt_sha256"],canonical(checked),timestamp),
        )
        conn.execute(
            """UPDATE sp_delivery_attempts SET status='accepted',
                      resolved_at=now() WHERE id=%s::uuid""",
            (reserved["attempt_id"],),
        )
        all_count=conn.execute(
            """SELECT COUNT(*) FROM sp_delivery_receipts
               WHERE event_id=%s::uuid""",(eid,),
        ).fetchone()[0]
        if not 0<=all_count<=event[3]:
            raise PipelineError("Collector receipt count violates SP chunk coverage",409)
        complete=all_count==event[3]
        _state(conn,eid,"collector_accepted" if complete else "awaiting_collector")
        return {
            "schema_version":"search-planner-collector-delivery-status/v1",
            "event_id":eid,"project_id":pid,
            "status":"collector_accepted" if complete else "awaiting_collector",
            "accepted_chunks":all_count,"chunk_count":event[3],
            "chunk_index":idx,"external_query_set_id":checked["external_query_set_id"],
            "receipt_sha256":checked["receipt_sha256"],
            "provider_execution_authorized":False,
            "external_source_calls":0,
        }


def _mark_uncertain(pid,eid,conn):
    with conn.transaction():
        event=_checked_event(conn,pid,eid,lock=True)
        if event[2] not in ("no_work","cancelled","collector_accepted"):
            _state(conn,eid,"needs_attention")


def deliver_next_held_chunk(project_id,event_id,*,collector_port=None,
                            owner_action_guard=None,connect=None):
    """One explicit owner-approved intake request, no background/polling/retry.

    Returns awaiting_collector with no IO if the optional adapter is absent.
    A transport timeout/invalid/missing receipt leaves the durable attempt
    uncertain; future dispatch requires explicit read-only reconciliation.
    """
    pid,eid=_pid(project_id),publication_uuid(event_id)
    port,available=_port(collector_port)
    factory=connect if connect is not None else _connect
    with factory() as conn:
        row=_checked_event(conn,pid,eid,lock=False)
    if row[2] in ("no_work","cancelled","collector_accepted"):
        return {
            "schema_version":"search-planner-collector-delivery-status/v1",
            "event_id":eid,"project_id":pid,"status":row[2],
            "external_source_calls":0,"provider_execution_authorized":False,
        }
    if not available:
        return {
            "schema_version":"search-planner-collector-delivery-status/v1",
            "event_id":eid,"project_id":pid,"status":row[2],
            "collector_available":False,"external_source_calls":0,
            "provider_execution_authorized":False,
        }
    _guard(owner_action_guard,OWNER_ACTION,pid,eid)
    with factory() as conn:
        reservation=_reserve(pid,eid,owner_action_guard,conn)
    if reservation["kind"]=="terminal":
        return {"event_id":eid,"project_id":pid,"status":reservation["status"],
                "external_source_calls":0}
    if reservation["kind"]=="unknown":
        return {"event_id":eid,"project_id":pid,"status":"needs_attention",
                "reason":"previous_submit_result_unknown_no_auto_retry",
                "chunk_index":reservation["chunk_index"],"external_source_calls":0}
    chunk=reservation["chunk"]
    adapter_chunk={**chunk,"event_id":eid}
    key=reservation["idempotency_key"]
    # At this point a durable uncertain reservation has already COMMITTED.
    # We must not auto retry even if the provider appears to timeout.
    try:
        proposed=port.accept(project_id=pid,handoff=adapter_chunk,idempotency_key=key)
        received=port.get_receipt(project_id=pid,idempotency_key=key)
        if received is None or proposed!=received:
            raise PipelineError("Collector intake not independently readable/durable",409)
        with factory() as conn:
            return _finalize(pid,eid,reservation,received,conn)
    except Exception:
        with factory() as conn:
            _mark_uncertain(pid,eid,conn)
        return {
            "schema_version":"search-planner-collector-delivery-status/v1",
            "event_id":eid,"project_id":pid,"status":"needs_attention",
            "reason":"unknown_collector_intake_result_reconcile_only",
            "chunk_index":reservation["chunk_index"],
            "idempotency_key":key,"external_source_calls":0,
            "provider_execution_authorized":False,
        }


def reconcile_uncertain_chunk(project_id,event_id,chunk_index,*,
                              collector_port=None,owner_action_guard=None,
                              connect=None):
    """Read existing durable external receipt WITHOUT invoking accept()."""
    pid,eid=_pid(project_id),publication_uuid(event_id)
    if type(chunk_index) is not int or not 0<=chunk_index<480:
        raise PipelineError("Invalid handoff reconciliation chunk",422)
    _guard(owner_action_guard,OWNER_ACTION,pid,eid)
    port,available=_port(collector_port)
    if not available:
        return {"event_id":eid,"status":"awaiting_collector",
                "external_source_calls":0}
    factory=connect if connect is not None else _connect
    with factory() as conn:
        event=_checked_event(conn,pid,eid,lock=False)
        row=conn.execute(
            """SELECT a.id,a.idempotency_key,a.status,c.chunk
                 FROM sp_delivery_attempts a
                 JOIN sp_handoff_chunks c
                   ON c.event_id=a.event_id AND c.chunk_index=a.chunk_index
                WHERE a.event_id=%s::uuid AND a.chunk_index=%s""",
            (eid,chunk_index),
        ).fetchone()
        if row is None:
            raise PipelineError("No submitted attempt to reconcile",404)
        if row[2]!="uncertain":
            raise PipelineError("Only uncertain delivery can be reconciled",409)
        reservation={
            "attempt_id":str(row[0]),"idempotency_key":row[1],
            "chunk_index":chunk_index,"chunk":row[3],
        }
    receipt=port.get_receipt(project_id=pid,idempotency_key=reservation["idempotency_key"])
    if receipt is None:
        return {"event_id":eid,"status":"needs_attention",
                "reason":"durable_external_receipt_not_found_no_auto_retry",
                "chunk_index":chunk_index,"external_source_calls":0}
    with factory() as conn:
        return _finalize(pid,eid,reservation,receipt,conn)
