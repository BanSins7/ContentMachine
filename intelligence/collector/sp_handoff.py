"""Real, server-only SP12 -> Video Collector V1 durable intake.

The authoritative source is the already ACKed immutable Search Planner inbox,
not a client-provided query set. Owner-approved HELD plans are required.
Collector QuerySets are durable, but this endpoint never creates search jobs,
releases workers or authorizes provider spend.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, canonical, sha256
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid
from intelligence.search_planner.contracts import publication_uuid
from intelligence.search_planner.task_planner.repository import load_verified_planning_input
from intelligence.search_planner.handoff.projection import project_verified_query_handoff
from intelligence.search_planner.handoff.verification import verify_handoff_projection
from intelligence.search_planner.handoff.collector_adapter import (
    RECEIPT_SCHEMA, EXPECTED_COLLECTOR_SCHEMA, verify_collector_receipt,
)
from .contracts import CollectorError
from .query_intake import parse_validated_query_set


def _fail(reason):
    raise CollectorError("sp_intake_untrusted", reason, 409)


def _expected_key(event_id, index):
    return "sp12-" + event_id + "-" + str(index)


def _source(conn, pid, eid, index, key, *, requires_attempt):
    row = conn.execute(
        """SELECT e.qv_run_id,e.qv_outbox_id,e.projection,e.status,
                  e.selected_count,e.chunk_count,c.chunk,c.chunk_sha256,
                  o.delivered_at, g.id,g.taxonomy_sha256,g.taxonomy_version,
                  COALESCE(g.publication_id,g.rtb_v2_publication_id),
                  g.rtb_v2_publication_id
           FROM sp_handoff_events e
           JOIN sp_handoff_chunks c ON c.event_id=e.id
            AND c.chunk_index=%s
           JOIN qv_outbox o ON o.id=e.qv_outbox_id AND o.run_id=e.qv_run_id
           JOIN qv_runs q ON q.id=e.qv_run_id AND q.project_id=e.project_id
           JOIN sqg_generations g ON g.id=q.generation_id AND g.project_id=e.project_id
           WHERE e.id=%s::uuid AND e.project_id=%s""",
        (index,eid,pid),
    ).fetchone()
    if row is None:
        _fail("Project-scoped SP inbox chunk not found")
    (qvid,oid,projection,state,total,count,chunk,chunk_sha,acked,gid,
     tax_sha,version,pub,v2pub)=row
    if (state not in ("awaiting_collector","delivering","needs_attention","collector_accepted")
        or acked is None or type(projection) is not dict
        or total<1 or type(chunk) is not dict or count<1
        or index>=count or key!=_expected_key(eid,index)
        or chunk.get("chunk_sha256")!=chunk_sha
        or sha256({k:v for k,v in chunk.items() if k!="chunk_sha256"})!=chunk_sha):
        _fail("SP QV ACK, chunk integrity or delivery identity invalid")
    plan=conn.execute(
        """SELECT a.plan_id FROM sp_held_plan_approvals a
           JOIN sp_held_task_plans p ON p.id=a.plan_id
           WHERE p.project_id=%s AND p.validation_run_id=%s::uuid
             AND p.snapshot->>'external_dispatch_authorized'='false'
             AND jsonb_array_length(p.snapshot->'tasks')>0
           ORDER BY a.approved_at DESC LIMIT 1""",
        (pid,str(qvid)),
    ).fetchone()
    if plan is None:
        _fail("Owner-approved HELD Search Planner source plan missing")
    if requires_attempt:
        attempted=conn.execute(
            """SELECT id FROM sp_delivery_attempts
               WHERE event_id=%s::uuid AND chunk_index=%s
                 AND idempotency_key=%s AND chunk_sha256=%s
                 AND status IN ('uncertain','accepted')""",
            (eid,index,key,chunk_sha),
        ).fetchone()
        if attempted is None:
            _fail("SP must durably reserve an exact chunk before Collector intake")
    return {
        "qvid":str(qvid),"oid":str(oid),"projection":projection,
        "chunk":chunk,"chunk_sha":chunk_sha,
        "gid":str(gid),"tax_sha":tax_sha,"version":version,
        "pub":str(pub),"v2":v2pub is not None,
    }


def _verify_trusted_sp_source(pid,eid,index,source):
    trusted=load_verified_planning_input(pid,source["qvid"])
    envelope=project_verified_query_handoff(
        trusted,chunk_size=source["projection"]["chunk_capacity"],
    )
    verify_handoff_projection(envelope,trusted_source=trusted)
    if (source["projection"]!=envelope
        or source["oid"]!=envelope["qv_outbox_id"]
        or source["gid"]!=envelope["generation_id"]
        or source["pub"]!=envelope["taxonomy_publication_id"]
        or source["tax_sha"]!=envelope["taxonomy_sha256"]
        or source["version"]!=envelope["taxonomy_version"]
        or source["chunk"]!=envelope["chunks"][index]):
        _fail("SP immutable QV lineage does not replay to this Collector chunk")


def _readback(conn,pid,key):
    row=conn.execute(
        """SELECT r.sp_event_id,r.chunk_index,r.chunk_sha256,
                  r.sp_query_set_sha256,r.collector_query_set_id,r.receipt,
                  r.receipt_sha256,q.query_set,q.query_set_sha256,
                  q.generation_id,COALESCE(q.publication_id,q.rtb_v2_publication_id),
                  q.validation_origin
           FROM collector_sp_intake_receipts r
           JOIN collector_query_sets q ON q.id=r.collector_query_set_id
             AND q.project_id=r.project_id
           WHERE r.project_id=%s AND r.idempotency_key=%s""",
        (pid,key),
    ).fetchone()
    if row is None:
        return None
    eid,index,chunksha,setsha,qid,receipt,digest,raw,stored,gen,pub,origin=row
    parsed=parse_validated_query_set(raw,expected_project_id=pid)
    if (type(receipt) is not dict or
        sha256({k:v for k,v in receipt.items() if k!="receipt_sha256"})!=digest
        or receipt.get("receipt_sha256")!=digest
        or receipt.get("chunk_sha256")!=chunksha
        or receipt.get("collector_query_set_sha256")!=setsha
        or receipt.get("external_query_set_id")!=str(qid)
        or receipt.get("event_id")!=str(eid)
        or receipt.get("chunk_index")!=index
        or receipt.get("project_id")!=pid
        or receipt.get("idempotency_key")!=key
        or receipt.get("durable") is not True
        or receipt.get("provider_execution_authorized") is not False
        or receipt.get("external_task_ids")!=[]
        or origin!="trusted_query_validation/v1"
        or parsed.fingerprint!=stored
        or sha256(raw)!=setsha
        or parsed.generation_id!=str(gen)
        or parsed.taxonomy_publication_id!=str(pub)):
        _fail("Collector receipt and independently stored query set disagree")
    verify_collector_receipt(
        receipt,project_id=pid,event_id=str(eid),
        chunk={"chunk_index":index,"chunk_sha256":chunksha,
               "collector_query_set_sha256":setsha},
        idempotency_key=key,
    )
    return deepcopy(receipt)


def accept_sp_handoff(project_id, handoff, idempotency_key, *, connect=None):
    pid=_pid(project_id)
    if type(handoff) is not dict or type(idempotency_key) is not str:
        _fail("Unversioned SP handoff")
    eid=publication_uuid(handoff.get("event_id"))
    index=handoff.get("chunk_index")
    if type(index) is not int or not 0<=index<=479:
        _fail("Invalid SP chunk index")
    factory=connect or _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SELECT id FROM project_registry WHERE id=%s FOR UPDATE",(pid,))
            source=_source(conn,pid,eid,index,idempotency_key,requires_attempt=True)
            if {k:v for k,v in handoff.items() if k!="event_id"}!=source["chunk"]:
                _fail("Browser or caller supplied content differs from immutable SP inbox")
            previous=_readback(conn,pid,idempotency_key)
            if previous is not None:
                if previous["event_id"]!=eid or previous["chunk_sha256"]!=source["chunk_sha"]:
                    _fail("Reused idempotency key for a different SP chunk")
                return previous
            _verify_trusted_sp_source(pid,eid,index,source)
            raw=deepcopy(source["chunk"]["collector_query_set"])
            parsed=parse_validated_query_set(raw,expected_project_id=pid,
                expected_publication_id=source["pub"])
            if (parsed.generation_id!=source["gid"]
                or parsed.taxonomy_version!=source["version"]
                or parsed.taxonomy_sha256!=source["tax_sha"]
                or source["chunk"]["collector_query_set_sha256"]!=sha256(raw)):
                _fail("SP source and Collector QuerySet identities disagree")
            # Collector's canonical parser sorts UUIDs. Preserve the incoming
            # SP source bytes/hash independently of the canonical DB fingerprint.
            row=conn.execute(
                """INSERT INTO collector_query_sets
                   (project_id,generation_id,publication_id,rtb_v2_publication_id,
                    validation_origin,query_set,query_set_sha256)
                   VALUES (%s,%s::uuid,%s::uuid,%s::uuid,
                           'trusted_query_validation/v1',%s::jsonb,%s)
                   RETURNING id""",
                (pid,source["gid"],None if source["v2"] else source["pub"],
                 source["pub"] if source["v2"] else None,
                 canonical(raw),parsed.fingerprint),
            ).fetchone()
            qid=str(row[0])
            stamp=conn.execute("SELECT clock_timestamp()").fetchone()[0]
            receipt={
                "schema_version":RECEIPT_SCHEMA,
                "collector_contract":EXPECTED_COLLECTOR_SCHEMA,
                "project_id":pid,"event_id":eid,
                "chunk_index":index,
                "chunk_sha256":source["chunk_sha"],
                "collector_query_set_sha256":sha256(raw),
                "idempotency_key":idempotency_key,
                "external_query_set_id":qid,
                "external_plan_id":None,"external_task_ids":[],
                "accepted_at":stamp.isoformat(),
                "durable":True,"provider_execution_authorized":False,
            }
            receipt["receipt_sha256"]=sha256(receipt)
            conn.execute(
                """INSERT INTO collector_sp_intake_receipts
                   (project_id,sp_event_id,chunk_index,idempotency_key,
                    chunk_sha256,sp_query_set_sha256,collector_query_set_id,
                    receipt,receipt_sha256,accepted_at)
                   VALUES (%s,%s::uuid,%s,%s,%s,%s,%s::uuid,%s::jsonb,%s,%s)""",
                (pid,eid,index,idempotency_key,source["chunk_sha"],
                 sha256(raw),qid,canonical(receipt),receipt["receipt_sha256"],stamp),
            )
    # Separate, durable read-after-commit, as required by SP dispatcher.
    confirmed=get_sp_receipt(pid,idempotency_key,connect=connect)
    if confirmed!=receipt:
        _fail("Collector durable read-after-commit failed")
    return confirmed


def get_sp_receipt(project_id,idempotency_key,*,connect=None):
    pid=_pid(project_id)
    if type(idempotency_key) is not str or not idempotency_key.startswith("sp12-"):
        _fail("Invalid SP receipt lookup key")
    factory=connect or _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return _readback(conn,pid,idempotency_key)


def list_sp_query_sets(project_id,*,limit=50,connect=None):
    pid=_pid(project_id)
    if type(limit) is not int or not 1<=limit<=50:
        _fail("Invalid SP QuerySet page size")
    factory=connect or _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            rows=conn.execute(
                """SELECT r.id,r.sp_event_id,r.chunk_index,r.idempotency_key,
                          q.id,q.generation_id,
                          COALESCE(q.publication_id,q.rtb_v2_publication_id),
                          jsonb_array_length(q.query_set->'queries'),
                          q.rtb_v2_publication_id
                   FROM collector_sp_intake_receipts r
                   JOIN collector_query_sets q ON q.id=r.collector_query_set_id
                    AND q.project_id=r.project_id
                   WHERE r.project_id=%s ORDER BY r.accepted_at DESC LIMIT %s""",
                (pid,limit),
            ).fetchall()
            for row in rows:
                if _readback(conn,pid,row[3]) is None:
                    _fail("Missing durable SP QuerySet receipt")
    return {
        "schema_version":"collector.sp_query_sets/v1","project_id":pid,
        "query_sets":[{
            "receipt_id":str(r[0]),"event_id":str(r[1]),
            "chunk_index":r[2],"query_set_id":str(r[4]),
            "generation_id":str(r[5]),"publication_id":str(r[6]),
            "query_count":r[7],"source":"rtb_deep_v2" if r[8] else "deep_c9_v1",
        } for r in rows],
    }
