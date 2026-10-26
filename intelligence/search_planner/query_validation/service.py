"""Atomic Generation Outbox -> Query Validation -> Task Planner Outbox.

The embedding/scoring stage runs outside DB locks. A second project-scoped
transaction locks the original outbox, persists ALL decisions/coverage and
acks the upstream event atomically. On crash, input is still unacknowledged.
No video collector calls, model downloads, paid LLM calls or auto polling.
"""
from __future__ import annotations

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256, canonical
from intelligence.taxonomy_builder.pipeline_repository import _pid, _connect
from intelligence.search_planner.contracts import publication_uuid
from intelligence.search_planner.generation_engine.output_manager import get_candidates_bundle
from .contracts import validate_policy, policy_fingerprint
from .selection import select_queries


def _source(conn, pid, oid, *, lock=False):
    sql = """SELECT o.generation_id,o.delivered_at,o.payload_pointer,g.status
        FROM sqg_outbox o JOIN sqg_generations g ON g.id=o.generation_id
        WHERE o.id=%s::uuid AND g.project_id=%s"""
    if lock:
        sql += " FOR UPDATE OF o"
    row=conn.execute(sql,(oid,pid)).fetchone()
    if row is None:
        raise PipelineError("Source outbox not found in project",404)
    gid,delivered,pointer,status=row
    if status!="completed" or pointer.get("generation_id")!=str(gid) or (
        pointer.get("schema_version")!="query_candidates/v1"
    ):
        raise PipelineError("Untrusted or incomplete generation outbox",409)
    return str(gid),delivered,pointer


def _contexts(conn,gid):
    rows=conn.execute(
        """SELECT hypothesis_id,context,context_sha256 FROM sqg_contexts
           WHERE generation_id=%s::uuid ORDER BY hypothesis_id""",(gid,)
    ).fetchall()
    cs=[]
    for hypid,context,expected in rows:
        if context["hypothesis"]["id"]!=hypid or sha256(context)!=expected:
            raise PipelineError("RTB context checksum mismatch",409)
        cs.append(context)
    if not cs:
        raise PipelineError("No source RTB contexts",409)
    return cs


def validate_generation_outbox(project_id,outbox_id,*,embedding_provider,
                                policy,allow_test_embeddings=False,connect=None):
    """Consume exactly one completed source event, safely idempotent.

    The caller must explicitly provision a semantic local embedding model.
    Test lexical hashing is accepted ONLY by explicit test opt-in and its
    downstream event cannot be consumed as a production Task Planner job.
    """
    pid=_pid(project_id);oid=publication_uuid(outbox_id)
    cfg=validate_policy(policy)
    mode=getattr(embedding_provider,"mode",None)
    model_key=getattr(embedding_provider,"model_key",None)
    if mode not in ("semantic_local","test_fixture") or (
        type(model_key) is not str or not 1<=len(model_key)<=240
    ) or (mode=="test_fixture" and allow_test_embeddings is not True):
        raise PipelineError("Production semantic embedding model required",403)
    fingerprint=policy_fingerprint(cfg,model_key,mode)
    factory=connect or _connect
    with factory() as conn:
        gid,delivered,pointer=_source(conn,pid,oid)
        existing=conn.execute(
            """SELECT id,config_sha256,source_bundle_sha256,embedding_model_key,
                      embedding_mode,selected_count
               FROM qv_runs WHERE source_outbox_id=%s::uuid""",(oid,)
        ).fetchone()
        if existing:
            if existing[1]!=fingerprint or existing[3]!=model_key or existing[4]!=mode:
                raise PipelineError("Already validated with a different immutable config/model",409)
            return {"validation_run_id":str(existing[0]),"generation_id":gid,
                    "selected_count":existing[5],"status":"completed","reused":True}
        if delivered is not None:
            raise PipelineError("Outbox already acked without durable validation",409)
        contexts=_contexts(conn,gid)
    # No locks across local embedding inference. Stage C source records
    # and contexts are immutable; recheck lineage/fingerprint under commit.
    bundle=get_candidates_bundle(pid,gid,connect=factory)
    if bundle["input_sha256"]!=pointer.get("input_sha256"):
        raise PipelineError("Source outbox input SHA mismatch",409)
    bundle_sha=sha256(bundle)
    evaluation=select_queries(bundle,contexts,cfg,embedding_provider)
    if evaluation["source_candidate_count"]!=len(bundle["candidates"]):
        raise AssertionError("Validation source coverage mismatch")
    with factory() as conn:
        with conn.transaction():
            locked_gid,delivered,pinned=_source(conn,pid,oid,lock=True)
            if locked_gid!=gid or pinned!=pointer:
                raise PipelineError("Outbox changed during validation",409)
            prior=conn.execute(
                """SELECT id,config_sha256,source_bundle_sha256,selected_count
                   FROM qv_runs WHERE source_outbox_id=%s::uuid""",(oid,)
            ).fetchone()
            if prior:
                if prior[1]!=fingerprint or prior[2]!=bundle_sha:
                    raise PipelineError("Concurrent validation with conflicting policy/input",409)
                return {"validation_run_id":str(prior[0]),"generation_id":gid,
                        "selected_count":prior[3],"status":"completed","reused":True}
            if delivered is not None:
                raise PipelineError("Source was ACKed without committed result",409)
            source_n=conn.execute(
                "SELECT COUNT(*) FROM sqg_candidates WHERE generation_id=%s::uuid",(gid,)
            ).fetchone()[0]
            if source_n!=len(bundle["candidates"]) or len(_contexts(conn,gid))!=len(contexts):
                raise PipelineError("Source changed during validation",409)
            run=conn.execute(
                """INSERT INTO qv_runs(project_id,generation_id,source_outbox_id,
                     config,config_sha256,source_bundle_sha256,embedding_model_key,
                     embedding_mode,source_candidate_count,selected_count)
                   VALUES (%s,%s::uuid,%s::uuid,%s::jsonb,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (pid,gid,oid,canonical(cfg),fingerprint,bundle_sha,model_key,mode,
                 source_n,evaluation["selected_count"]),
            ).fetchone()
            qvid=str(run[0])
            for d in evaluation["decisions"]:
                conn.execute(
                    """INSERT INTO qv_decisions
                      (run_id,generation_id,candidate_id,hypothesis_id,decision,reason_code,
                       positive_similarity,negative_similarity,score_margin,mmr_score,
                       selected_rank,duplicate_of)
                       VALUES (%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s::uuid)""",
                    (qvid,gid,d["candidate_id"],d["hypothesis_id"],d["decision"],
                     d["reason_code"],d["positive_similarity"],d["negative_similarity"],
                     d["score_margin"],d["mmr_score"],d["selected_rank"],d["duplicate_of"]),
                )
            for c in evaluation["coverage"]:
                conn.execute(
                    """INSERT INTO qv_coverage
                         (run_id,generation_id,hypothesis_id,source_count,selected_count,
                          outcome,reason)
                       VALUES (%s::uuid,%s::uuid,%s,%s,%s,%s,%s)""",
                    (qvid,gid,c["hypothesis_id"],c["source_count"],c["selected_count"],
                     c["outcome"],c["reason"]),
                )
            conn.execute(
                """INSERT INTO qv_outbox(run_id,event_type,payload_pointer)
                  VALUES (%s::uuid,'validated_queries_ready/v1',%s::jsonb)""",
                (qvid,canonical({"validation_run_id":qvid,"generation_id":gid,
                                  "schema_version":"validated_queries/v1",
                                  "embedding_mode":mode,"selected_count":evaluation["selected_count"]})),
            )
            # ACK source only after downstream decisions+coverage+outbox exist
            # in SAME PostgreSQL transaction.
            n=conn.execute(
                """UPDATE sqg_outbox
                   SET delivered_at=now(),delivery_attempts=delivery_attempts+1
                   WHERE id=%s::uuid AND delivered_at IS NULL RETURNING id""",
                (oid,),
            ).fetchone()
            if n is None:
                raise PipelineError("Lost upstream outbox lease",409)
            return {"validation_run_id":qvid,"generation_id":gid,
                    "selected_count":evaluation["selected_count"],"status":"completed",
                    "reused":False,"embedding_mode":mode,
                    "reason_counts":evaluation["reason_counts"]}


def get_validated_bundle(project_id,validation_run_id,*,include_test=False,connect=None):
    pid=_pid(project_id);rid=publication_uuid(validation_run_id)
    factory=connect or _connect
    with factory() as conn:
        run=conn.execute(
            """SELECT id,generation_id,embedding_model_key,embedding_mode,
                      config_sha256,source_bundle_sha256,selected_count
               FROM qv_runs WHERE id=%s::uuid AND project_id=%s""",
            (rid,pid),
        ).fetchone()
        if run is None:
            raise PipelineError("Validated query run not found",404)
        if run[3]!="semantic_local" and include_test is not True:
            raise PipelineError("Test embeddings cannot be released as production queries",409)
        selected=conn.execute(
            """SELECT c.id,c.hypothesis_id,c.direction_id,c.branch_id,
                     c.source_fact_ids,c.text,c.query_mode,c.angle,c.language,
                     c.research_stance,c.rationale,d.positive_similarity,
                     d.negative_similarity,d.mmr_score,d.selected_rank
               FROM qv_decisions d JOIN sqg_candidates c ON c.id=d.candidate_id
               WHERE d.run_id=%s::uuid AND d.decision='selected'
               ORDER BY c.hypothesis_id,d.selected_rank,c.id""",(rid,)
        ).fetchall()
        coverage=conn.execute(
            """SELECT hypothesis_id,source_count,selected_count,outcome,reason
               FROM qv_coverage WHERE run_id=%s::uuid ORDER BY hypothesis_id""",(rid,)
        ).fetchall()
        decisions=conn.execute(
            """SELECT candidate_id,decision,reason_code,duplicate_of,positive_similarity,
                      negative_similarity,score_margin,mmr_score
               FROM qv_decisions WHERE run_id=%s::uuid ORDER BY candidate_id""",(rid,)
        ).fetchall()
        return {
            "schema_version":"validated_queries/v1","validation_run_id":rid,
            "generation_id":str(run[1]),"project_id":pid,
            "embedding_model_key":run[2],"embedding_mode":run[3],
            "config_sha256":run[4],"source_bundle_sha256":run[5],
            "status":"completed",
            "queries":[{
                "candidate_id":str(r[0]),"hypothesis_id":r[1],"direction_id":r[2],
                "branch_id":r[3],"source_fact_ids":r[4],"text":r[5],
                "query_mode":r[6],"angle":r[7],"language":r[8],
                "research_stance":r[9],"rationale":r[10],
                "relevance_score":r[11],"negative_score":r[12],
                "mmr_score":r[13],"rank_in_hypothesis":r[14],
            } for r in selected],
            "coverage":[{
                "hypothesis_id":r[0],"source_count":r[1],"selected_count":r[2],
                "outcome":r[3],"reason":r[4],
            } for r in coverage],
            "decisions":[{
                "candidate_id":str(r[0]),"decision":r[1],"reason_code":r[2],
                "duplicate_of":str(r[3]) if r[3] else None,
                "positive_similarity":r[4],"negative_similarity":r[5],
                "score_margin":r[6],"mmr_score":r[7],
            } for r in decisions],
        }


def pending_validated_outbox(project_id,*,include_test=False,limit=50,connect=None):
    pid=_pid(project_id)
    if type(limit) is not int or not 1<=limit<=100:
        raise PipelineError("Invalid validated outbox limit",422)
    factory=connect or _connect
    with factory() as conn:
        rows=conn.execute(
            """SELECT o.id,o.run_id,o.event_type,o.payload_pointer
               FROM qv_outbox o JOIN qv_runs r ON r.id=o.run_id
               WHERE r.project_id=%s AND o.delivered_at IS NULL
                  AND o.next_delivery_at<=now()
                  AND (r.embedding_mode='semantic_local' OR %s)
               ORDER BY o.created_at,o.id LIMIT %s""",(pid,include_test,limit)
        ).fetchall()
        return [{"outbox_id":str(x[0]),"validation_run_id":str(x[1]),
                 "event_type":x[2],"payload_pointer":x[3]} for x in rows]


def ack_validated_outbox(project_id,outbox_id,*,connect=None):
    """Acknowledges delivery only AFTER Task Planner durably ingested event.

    Query Validation itself never invokes this. Allows only production
    semantic events, not fixture embeddings, to be acknowledged for routing.
    """
    pid=_pid(project_id);oid=publication_uuid(outbox_id)
    factory=connect or _connect
    with factory() as conn:
        with conn.transaction():
            row=conn.execute(
                """SELECT o.id,o.delivered_at
                   FROM qv_outbox o JOIN qv_runs r ON r.id=o.run_id
                   WHERE o.id=%s::uuid AND r.project_id=%s
                     AND r.embedding_mode='semantic_local'
                   FOR UPDATE OF o""",(oid,pid)
            ).fetchone()
            if row is None:
                raise PipelineError("Production validated outbox not found",404)
            if row[1] is None:
                conn.execute(
                    """UPDATE qv_outbox SET delivered_at=now(),
                              delivery_attempts=delivery_attempts+1
                       WHERE id=%s::uuid""",(oid,)
                )
            return {"outbox_id":oid,"acknowledged":True,"reused":row[1] is not None}
