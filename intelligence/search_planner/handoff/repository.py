"""SP-12 transactional QV -> SP inbox. No Collector import or network calls.

SP inbox ACK and all chunk/query lineage insertions share ONE transaction.
QV is row-locked first, so 10 workers replay into one authoritative event.
The QV ACK deliberately means "durably saved in SP" and NEVER means
"accepted by Collector".
"""
from __future__ import annotations

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, canonical, sha256
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid
from intelligence.search_planner.contracts import publication_uuid
from intelligence.search_planner.query_validation.integrity import verify_outbox_event
from .projection import VerifiedHandoffProjector
from .verification import verify_handoff_projection

EVENT_STATES = frozenset((
    "pending_planning", "awaiting_owner_commit", "committed_held",
    "awaiting_collector", "delivering", "collector_accepted",
    "incompatible", "needs_attention", "no_work", "cancelled",
))


def _locked_qv_outbox(conn, pid, oid):
    row = conn.execute(
        """SELECT o.id,o.run_id,o.delivered_at,o.event_type,o.payload_pointer,
                  r.generation_id,r.embedding_mode,r.selected_count
             FROM qv_outbox o JOIN qv_runs r ON r.id=o.run_id
            WHERE o.id=%s::uuid AND r.project_id=%s
              AND r.embedding_mode='semantic_local'
            FOR UPDATE OF o""",
        (oid,pid),
    ).fetchone()
    if row is None:
        raise PipelineError("Production QV outbox missing or foreign",404)
    event = {
        "outbox_id":str(row[0]),"validation_run_id":str(row[1]),
        "event_type":row[3],"payload_pointer":row[4],
    }
    verify_outbox_event(event,expected_run_id=str(row[1]))
    pointer=event["payload_pointer"]
    if (pointer["generation_id"]!=str(row[5]) or
            pointer["embedding_mode"]!=row[6] or
            pointer["selected_count"]!=row[7]):
        raise PipelineError("QV source pointer differs from immutable run",409)
    return row


def _check_inserted_inbox(conn,pid,oid,envelope):
    """Read back every saved chunk and all original selected link hashes."""
    event=conn.execute(
        """SELECT id,qv_run_id,projection_sha256,projection,selected_count,
                  chunk_count,status
             FROM sp_handoff_events
            WHERE project_id=%s AND qv_outbox_id=%s::uuid FOR UPDATE""",
        (pid,oid),
    ).fetchone()
    if event is None:
        raise PipelineError("SP inbox event missing after durable insert",409)
    eid=str(event[0])
    if (str(event[1])!=envelope["validation_run_id"] or
            event[2]!=envelope["projection_sha256"] or
            event[3]!=envelope or event[4]!=envelope["selected_query_count"] or
            event[5]!=envelope["chunk_count"] or
            event[6] not in EVENT_STATES):
        raise PipelineError("SP inbox immutable source content conflict",409)
    chunks=conn.execute(
        """SELECT chunk_index,chunk_sha256,selected_count,chunk
             FROM sp_handoff_chunks WHERE event_id=%s::uuid
            ORDER BY chunk_index""",
        (eid,),
    ).fetchall()
    if len(chunks)!=envelope["chunk_count"]:
        raise PipelineError("SP inbox chunk insertion incomplete",409)
    for row, ch in zip(chunks,envelope["chunks"]):
        if (row[0]!=ch["chunk_index"] or row[1]!=ch["chunk_sha256"] or
                row[2]!=ch["chunk_size"] or row[3]!=ch):
            raise PipelineError("SP inbox chunk SHA/replay conflict",409)
    links=conn.execute(
        """SELECT chunk_index,candidate_id,qv_hypothesis_id,provenance,
                  provenance_sha256
             FROM sp_handoff_query_links WHERE event_id=%s::uuid
            ORDER BY chunk_index,candidate_id""",
        (eid,),
    ).fetchall()
    expected = sorted([
        (ch["chunk_index"],q["query_id"],p["selected_candidate"]["hypothesis_id"],
         p,sha256(p))
        for ch in envelope["chunks"] for q,p in
        zip(ch["collector_query_set"]["queries"],ch["source_provenance"])
    ],key=lambda r:(r[0],r[1]))
    if len(links)!=len(expected):
        raise PipelineError("SP inbox query lineage incomplete",409)
    for row,exp in zip(links,expected):
        if (row[0]!=exp[0] or str(row[1])!=exp[1] or row[2]!=exp[2] or
                row[3]!=exp[3] or row[4]!=exp[4]):
            raise PipelineError("SP inbox source fact/UUID SHA mismatch",409)
    return eid,event[6]


def ingest_qv_to_sp_inbox(project_id,validation_run_id,*,projector=None,
                          connect=None,failpoint=None):
    """Ingest one immutable production QV and ACK only after fully durable SP rows.

    failpoint is a trusted test-only injection before transaction commit,
    never accepted from HTTP. Rollback covers SP rows and QV delivered_at.
    """
    pid,vid=_pid(project_id),publication_uuid(validation_run_id)
    if failpoint is not None and not callable(failpoint):
        raise PipelineError("Invalid trusted crash test hook",422)
    loader=projector if projector is not None else VerifiedHandoffProjector()
    if not isinstance(loader,VerifiedHandoffProjector):
        raise PipelineError("Invalid trusted SP11 source loader",422)
    # No lock across full SELECT/DB verification. QV/C9 are immutable and
    # generation/outbox hashes rechecked UNDER locked QV before saving.
    envelope=loader.preview(pid,vid)
    verify_handoff_projection(envelope)
    oid=publication_uuid(envelope["qv_outbox_id"])
    factory=connect if connect is not None else _connect
    with factory() as conn:
        with conn.transaction():
            qv=_locked_qv_outbox(conn,pid,oid)
            if (str(qv[1])!=vid or
                    str(qv[5])!=envelope["generation_id"] or
                    qv[7]!=envelope["selected_query_count"]):
                raise PipelineError("QV changed before inbox commit",409)
            existing=conn.execute(
                """SELECT id FROM sp_handoff_events
                   WHERE project_id=%s AND qv_outbox_id=%s::uuid""",
                (pid,oid),
            ).fetchone()
            was_new=existing is None
            if was_new:
                state="no_work" if envelope["selected_query_count"]==0 else "awaiting_collector"
                inserted=conn.execute(
                    """INSERT INTO sp_handoff_events
                          (project_id,qv_run_id,qv_outbox_id,projection_sha256,
                           projection,selected_count,chunk_count,status)
                       VALUES (%s,%s::uuid,%s::uuid,%s,%s::jsonb,%s,%s,%s)
                       RETURNING id""",
                    (pid,vid,oid,envelope["projection_sha256"],canonical(envelope),
                     envelope["selected_query_count"],envelope["chunk_count"],state),
                ).fetchone()
                eid=str(inserted[0])
                for chunk in envelope["chunks"]:
                    index=chunk["chunk_index"]
                    conn.execute(
                        """INSERT INTO sp_handoff_chunks
                              (event_id,chunk_index,chunk_sha256,selected_count,chunk)
                           VALUES (%s::uuid,%s,%s,%s,%s::jsonb)""",
                        (eid,index,chunk["chunk_sha256"],chunk["chunk_size"],
                         canonical(chunk)),
                    )
                    for source in chunk["source_provenance"]:
                        candidate=source["selected_candidate"]
                        conn.execute(
                            """INSERT INTO sp_handoff_query_links
                                  (event_id,chunk_index,candidate_id,
                                   qv_hypothesis_id,provenance,provenance_sha256)
                               VALUES (%s::uuid,%s,%s::uuid,%s,%s::jsonb,%s)""",
                            (eid,index,source["query_id"],
                             candidate["hypothesis_id"],canonical(source),
                             sha256(source)),
                        )
            eid,state=_check_inserted_inbox(conn,pid,oid,envelope)
            if failpoint is not None:
                failpoint("after_inbox_before_qv_ack")
            if qv[2] is None:
                done=conn.execute(
                    """UPDATE qv_outbox SET delivered_at=now(),
                              delivery_attempts=delivery_attempts+1
                       WHERE id=%s::uuid AND delivered_at IS NULL RETURNING id""",
                    (oid,),
                ).fetchone()
                if done is None:
                    raise PipelineError("Lost QV outbox lock before SP ACK",409)
            elif was_new:
                raise PipelineError("QV ACK exists without previously durable inbox",409)
            if failpoint is not None:
                failpoint("after_qv_ack_before_commit")
            return {
                "schema_version":"search-planner-inbox-receipt/v1",
                "event_id":eid,"project_id":pid,"validation_run_id":vid,
                "qv_outbox_id":oid,"projection_sha256":envelope["projection_sha256"],
                "selected_count":envelope["selected_query_count"],
                "chunk_count":envelope["chunk_count"],
                "status":state,"reused":not was_new,
                "qv_acknowledged_in_same_transaction":True,
                "collector_accepted":state=="collector_accepted",
                "external_search_executed":False,
            }


def get_sp_handoff_status(project_id,event_id,*,connect=None):
    """Zero-spend project-scoped immutable inbox and separate receipt metadata."""
    pid,eid=_pid(project_id),publication_uuid(event_id)
    factory=connect if connect is not None else _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row=conn.execute(
                """SELECT e.qv_run_id,e.qv_outbox_id,e.projection_sha256,
                          e.selected_count,e.chunk_count,e.status
                     FROM sp_handoff_events e
                    WHERE e.id=%s::uuid AND e.project_id=%s""",
                (eid,pid),
            ).fetchone()
            if row is None:
                raise PipelineError("SP inbox event not found",404)
            receipts=conn.execute(
                """SELECT r.chunk_index,r.external_query_set_id,
                          r.external_plan_id,r.collector_contract,
                          r.collector_receipt_sha256,r.accepted_at
                     FROM sp_delivery_receipts r
                     JOIN sp_handoff_events e ON e.id=r.event_id
                    WHERE r.event_id=%s::uuid AND e.project_id=%s
                    ORDER BY r.chunk_index""",
                (eid,pid),
            ).fetchall()
            attempts=conn.execute(
                """SELECT a.chunk_index,a.status,a.attempted_at
                     FROM sp_delivery_attempts a
                     JOIN sp_handoff_events e ON e.id=a.event_id
                    WHERE a.event_id=%s::uuid AND e.project_id=%s
                    ORDER BY a.chunk_index""",
                (eid,pid),
            ).fetchall()
    return {
        "schema_version":"search-planner-inbox-status/v1",
        "project_id":pid,"event_id":eid,
        "validation_run_id":str(row[0]),"qv_outbox_id":str(row[1]),
        "projection_sha256":row[2],
        "selected_count":row[3],"chunk_count":row[4],
        "status":row[5],
        "collector_accepted_chunks":[{
            "chunk_index":r[0],"external_query_set_id":r[1],
            "external_plan_id":r[2],"collector_contract":r[3],
            "receipt_sha256":r[4],"accepted_at":r[5].isoformat()
        } for r in receipts],
        "delivery_attempts":[{
            "chunk_index":a[0],"status":a[1],"attempted_at":a[2].isoformat()
        } for a in attempts],
        "external_search_executed":False,
    }
