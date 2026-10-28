"""SP-14 read-only project-scoped UI discovery of existing immutable SP records.

Only original PostgreSQL tables are read; this is NOT an alternative
configuration/QV/source registry. All tables are optional EMPTY, not faked.
"""
from __future__ import annotations
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.pipeline_repository import _pid,_connect

def read_sp_ui_overview(project_id, *, limit=20, connect=None):
    pid=_pid(project_id)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise PipelineError("Invalid SP owner dashboard limit",422)
    factory=connect if connect is not None else _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            rows=conn.execute(
                """SELECT c.id,c.revision,c.config_sha256,c.config->'model',
                          c.approved_at
                     FROM sqg_config_versions c
                    WHERE c.project_id=%s
                    ORDER BY c.revision DESC LIMIT %s""",(pid,limit)
            ).fetchall()
            generations=conn.execute(
                """SELECT g.id,g.publication_id,g.config_id,g.status,
                          g.created_at,o.id
                     FROM sqg_generations g
                     LEFT JOIN sqg_outbox o ON o.generation_id=g.id
                    WHERE g.project_id=%s
                    ORDER BY g.created_at DESC,g.id DESC LIMIT %s""",(pid,limit)
            ).fetchall()
            validations=conn.execute(
                """SELECT r.id,r.generation_id,r.selected_count,
                          r.source_candidate_count,r.embedding_mode,
                          r.created_at,o.id
                     FROM qv_runs r
                     LEFT JOIN qv_outbox o ON o.run_id=r.id
                    WHERE r.project_id=%s
                    ORDER BY r.created_at DESC,r.id DESC LIMIT %s""",(pid,limit)
            ).fetchall()
            inbox=conn.execute(
                """SELECT e.id,e.qv_run_id,e.selected_count,e.chunk_count,
                          e.status,e.created_at
                     FROM sp_handoff_events e
                    WHERE e.project_id=%s
                    ORDER BY e.created_at DESC,e.id DESC LIMIT %s""",(pid,limit)
            ).fetchall()
            paid=conn.execute(
                """SELECT a.generation_id,a.idempotency_key,a.state,
                          a.created_at,a.finished_at
                     FROM sp_http_paid_actions a
                    WHERE a.project_id=%s
                    ORDER BY a.created_at DESC,a.id DESC LIMIT %s""",(pid,limit)
            ).fetchall()
    return {
        "schema_version":"search-planner-owner-overview/v1",
        "project_id":pid,
        "configs":[{
            "config_id":str(r[0]),"revision":r[1],
            "config_sha256":r[2],
            "model":r[3],"approved_at":r[4].isoformat(),
        } for r in rows],
        "generations":[{
            "generation_id":str(r[0]),"publication_id":str(r[1]),
            "config_id":str(r[2]),"status":r[3],
            "created_at":r[4].isoformat(),
            "source_outbox_id":str(r[5]) if r[5] is not None else None,
        } for r in generations],
        "validations":[{
            "validation_run_id":str(r[0]),"generation_id":str(r[1]),
            "selected_count":r[2],"candidate_count":r[3],
            "embedding_mode":r[4],"created_at":r[5].isoformat(),
            "qv_outbox_id":str(r[6]) if r[6] is not None else None,
        } for r in validations],
        "inbox":[{
            "event_id":str(r[0]),"validation_run_id":str(r[1]),
            "selected_count":r[2],"chunk_count":r[3],
            "status":r[4],"created_at":r[5].isoformat(),
        } for r in inbox],
        "paid_http_requests":[{
            "generation_id":str(r[0]),"idempotency_key":r[1],
            "status":r[2],"created_at":r[3].isoformat(),
            "finished_at":r[4].isoformat() if r[4] is not None else None,
        } for r in paid],
        "provider_requests_in_this_read":0,
    }



def read_sp_pinned_model_catalog(project_id, *, connect=None):
    """Read only latest provider-verified model snapshots and exact Stage D pins.

    This is an existing Taxonomy Builder cache. It does NOT refresh OpenRouter
    or download models, estimate prices or authorize a paid completion.
    """
    pid=_pid(project_id)
    from intelligence.taxonomy_builder.catalog import list_models
    from .ai_query.prompts import stage_d_fingerprints
    factory=connect if connect is not None else _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            known=conn.execute("SELECT 1 FROM project_registry WHERE id=%s",
                               (pid,)).fetchone()
            if known is None:
                raise PipelineError("Search Planner project has not been synced",404)
            records=list_models(conn)
    return {
        "schema_version":"search-planner-model-picker/v1",
        "project_id":pid,
        "models":records,
        "prompt_pins":stage_d_fingerprints(),
        "available_count":sum(1 for record in records if record["selectable"]),
        "provider_requests_in_this_read":0,
        "model_downloads_in_this_read":0,
        "spending_authorized":False,
    }
