"""Transactional, versioned per-project Taxonomy Builder pipeline settings.

Never calls an LLM, never estimates cost, never starts a paid job.
Only the local owner's current, explicitly confirmed questionnaire is eligible.
"""
from __future__ import annotations
from uuid import UUID
import re
from intelligence import config
from .pipeline_contracts import PipelineError,validate_pipeline,sha256,canonical
from .catalog import list_models,latest_verified

PROJECT_ID=re.compile(r"[0-9a-f]{32}\Z")

def _pid(project_id):
    if not isinstance(project_id,str) or not PROJECT_ID.fullmatch(project_id):
        raise PipelineError("Project not found",404)
    return project_id

def _connect():
    if not config.enabled() or not config.dsn():
        raise PipelineError("PostgreSQL must be enabled before Pipeline setup",503)
    import psycopg
    try:
        return psycopg.connect(config.dsn(),connect_timeout=config.connect_timeout())
    except psycopg.Error as exc:
        raise PipelineError("PostgreSQL unavailable",503) from exc

def _confirmed(conn, project_id: str):
    """Read current DB state with row lock. Local profile is checked outside too."""
    row=conn.execute("""SELECT profile_revision,profile_hash
      FROM project_registry WHERE id=%s FOR UPDATE""",(project_id,)).fetchone()
    if row is None:
        raise PipelineError("Project has not been synced to PostgreSQL",409)
    exists=conn.execute("""SELECT 1 FROM tb_intake_confirmations
      WHERE project_id=%s AND profile_revision=%s AND profile_hash=%s""",
      (project_id,row[0],row[1])).fetchone()
    if not exists:
        raise PipelineError("Confirm the current questionnaire first",409)
    return row[0],row[1]

def _local_current(project_id):
    from intelligence.taxonomy_builder.intake import get_intake_view
    status=get_intake_view(project_id)
    if not status["confirmed"] or not status["pipeline_studio_available"]:
        raise PipelineError("The current project questionnaire needs confirmation",409)
    return status["profile_revision"],status["profile_hash"]

def _db_error(exc):
    if isinstance(exc,PipelineError):
        raise exc
    raise PipelineError("Database error while saving Pipeline; check migrations",503) from exc

def _read_configured(conn,project_id):
    row=conn.execute("""SELECT profile_revision,profile_hash,draft_revision,config,
                              changed_by,updated_at
      FROM tb_pipeline_drafts WHERE project_id=%s""",(project_id,)).fetchone()
    if not row:
        return None
    return {"profile_revision":row[0],"profile_hash":row[1],
            "draft_revision":row[2],"config":row[3],
            "changed_by":row[4],"updated_at":row[5].isoformat()}

def get_pipeline_state(project_id: str) -> dict:
    project_id=_pid(project_id)
    rev,digest=_local_current(project_id)
    try:
        with _connect() as conn:
            current=conn.execute("""SELECT profile_revision,profile_hash
               FROM project_registry WHERE id=%s""",(project_id,)).fetchone()
            if current!=(rev,digest):
                raise PipelineError("Local profile and PostgreSQL are out of sync",409)
            draft=_read_configured(conn,project_id)
            if draft and (draft["profile_revision"],draft["profile_hash"])!=(rev,digest):
                draft=None  # Old profile draft must not be presented as current.
            rows=conn.execute("""SELECT v.id,v.pipeline_version,v.source_draft_revision,
                     v.config,v.config_sha256,v.created_at,a.approved_at
                     FROM tb_pipeline_versions v
                     JOIN tb_pipeline_approvals a ON a.pipeline_id=v.id
                     WHERE v.project_id=%s AND v.profile_revision=%s
                       AND v.profile_hash=%s
                     ORDER BY v.pipeline_version DESC""",
                     (project_id,rev,digest)).fetchall()
            history=[{"pipeline_id":str(r[0]),"pipeline_version":r[1],
                    "source_draft_revision":r[2],"config":r[3],"config_sha256":r[4],
                    "created_at":r[5].isoformat(),"approved_at":r[6].isoformat()}
                    for r in rows]
            approved_current=bool(draft and history and
                                  draft["draft_revision"]==history[0]["source_draft_revision"])
            return {"project_id":project_id,"profile_revision":rev,"profile_hash":digest,
                    "draft":draft,"approved_versions":history,
                    "status":"pipeline_approved" if approved_current else
                             "pipeline_review" if draft else "pipeline_setup",
                    "next_step":"cost_estimate" if approved_current else "pipeline_studio",
                    "model_editor_available":True}  # Block 4 UI is available.
    except PipelineError:
        raise
    except Exception as exc:
        _db_error(exc)

def get_project_model_catalog(project_id: str) -> dict:
    _pid(project_id)
    _local_current(project_id)
    try:
        with _connect() as conn:
            return {"models":list_models(conn),"source":"openrouter",
                    "cost_estimate_available":False}
    except Exception as exc:
        _db_error(exc)

def _load_entries(conn,config):
    stages=config.get("stages",[]) if isinstance(config,dict) else []
    ids=set()
    names=set()
    for row in stages:
        if not isinstance(row,dict):
            raise PipelineError("Invalid stage")
        if row.get("enabled",True):
            cid=row.get("catalog_version_id")
            if type(cid) is not int or cid<=0:
                raise PipelineError("Choose a verified catalog_version_id for each active stage")
            ids.add(cid)
            if isinstance(row.get("model_id"),str):
                names.add(row["model_id"])
    if not ids:
        raise PipelineError("At least two active stages are required")
    verified=latest_verified(conn,names)
    by_id={}
    for row in verified.values():
        by_id[row["catalog_version_id"]]={
           "model_id":row["model_id"],"capabilities":row["capabilities"],
           "pricing":row["pricing"],"verified_at":row["verified_at"],
           "catalog_sha256":row["catalog_sha256"]}
    if ids != set(by_id):
        raise PipelineError("Selected model catalog revision is stale; choose the latest",409)
    return by_id

def save_draft(project_id: str,config: dict,expected_draft_revision: int,
               changed_by: str = "local_owner") -> dict:
    project_id=_pid(project_id)
    if type(expected_draft_revision) is not int or expected_draft_revision<0:
        raise PipelineError("expected_draft_revision must be a nonnegative integer")
    if not isinstance(changed_by,str) or not changed_by.strip():
        raise PipelineError("Pipeline editor is required")
    # Same lock as the existing local questionnaire write path.
    from projects import _PROFILE_WRITE_LOCK
    with _PROFILE_WRITE_LOCK:
        rev,digest=_local_current(project_id)
        try:
            with _connect() as conn:
                with conn.transaction():
                    if _confirmed(conn,project_id)!=(rev,digest):
                        raise PipelineError("Questionnaire changed; reconfirm it",409)
                    row=conn.execute("""SELECT profile_revision,profile_hash,draft_revision
                         FROM tb_pipeline_drafts WHERE project_id=%s FOR UPDATE""",
                         (project_id,)).fetchone()
                    same_profile=row and (row[0],row[1])==(rev,digest)
                    current=row[2] if same_profile else 0
                    if current!=expected_draft_revision:
                        raise PipelineError("Draft changed elsewhere; reload before editing",409)
                    normalized,_=validate_pipeline(config,_load_entries(conn,config))
                    next_rev=current+1
                    conn.execute("""INSERT INTO tb_pipeline_drafts
                      (project_id,profile_revision,profile_hash,draft_revision,config,changed_by)
                      VALUES (%s,%s,%s,%s,%s::jsonb,%s)
                      ON CONFLICT(project_id) DO UPDATE SET
                      profile_revision=EXCLUDED.profile_revision,
                      profile_hash=EXCLUDED.profile_hash,
                      draft_revision=EXCLUDED.draft_revision,
                      config=EXCLUDED.config,
                      changed_by=EXCLUDED.changed_by,
                      updated_at=now()""",
                      (project_id,rev,digest,next_rev,canonical(normalized),changed_by.strip()))
                    return {"project_id":project_id,"profile_revision":rev,
                            "draft_revision":next_rev,"status":"pipeline_review",
                            "config":normalized}
        except PipelineError:
            raise
        except Exception as exc:
            _db_error(exc)

def approve_draft(project_id: str,expected_draft_revision: int,
                  approved_by: str = "local_owner") -> dict:
    project_id=_pid(project_id)
    if type(expected_draft_revision) is not int or expected_draft_revision<1:
        raise PipelineError("Expected saved draft revision is required")
    if not isinstance(approved_by,str) or not approved_by.strip():
        raise PipelineError("Approving actor is required")
    from projects import _PROFILE_WRITE_LOCK
    with _PROFILE_WRITE_LOCK:
        rev,digest=_local_current(project_id)
        try:
            with _connect() as conn:
                with conn.transaction():
                    if _confirmed(conn,project_id)!=(rev,digest):
                        raise PipelineError("Questionnaire changed",409)
                    row=conn.execute("""SELECT draft_revision,config FROM tb_pipeline_drafts
                      WHERE project_id=%s AND profile_revision=%s AND profile_hash=%s
                      FOR UPDATE""",(project_id,rev,digest)).fetchone()
                    if not row or row[0]!=expected_draft_revision:
                        raise PipelineError("Draft changed; reload before approval",409)
                    existing=conn.execute("""SELECT v.id,v.pipeline_version,v.config_sha256
                          FROM tb_pipeline_versions v
                          JOIN tb_pipeline_approvals a ON a.pipeline_id=v.id
                          WHERE v.project_id=%s AND v.profile_revision=%s
                            AND v.source_draft_revision=%s""",
                          (project_id,rev,row[0])).fetchone()
                    if existing:
                        return {"pipeline_id":str(existing[0]),
                                "pipeline_version":existing[1],
                                "config_sha256":existing[2],"status":"approved",
                                "next_step":"cost_estimate"}
                    config,snapshots=validate_pipeline(row[1],_load_entries(conn,row[1]))
                    payload={"config":config,"model_snapshots":snapshots}
                    checksum=sha256(payload)
                    num=conn.execute("""SELECT COALESCE(MAX(pipeline_version),0)+1
                         FROM tb_pipeline_versions WHERE project_id=%s
                           AND profile_revision=%s""",(project_id,rev)).fetchone()[0]
                    pipeline_id=conn.execute("""INSERT INTO tb_pipeline_versions
                         (project_id,profile_revision,profile_hash,pipeline_version,
                          source_draft_revision,config,model_snapshots,
                          config_sha256,created_by)
                         VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)
                         RETURNING id""",
                         (project_id,rev,digest,num,row[0],canonical(config),
                          canonical(snapshots),checksum,approved_by.strip())).fetchone()[0]
                    conn.execute("""INSERT INTO tb_pipeline_approvals
                       (pipeline_id,project_id,approved_by)
                       VALUES (%s,%s,%s)""",(pipeline_id,project_id,approved_by.strip()))
                    return {"pipeline_id":str(pipeline_id),
                            "pipeline_version":num,"config_sha256":checksum,
                            "profile_revision":rev,"status":"approved",
                            "next_step":"cost_estimate"}
        except PipelineError:
            raise
        except Exception as exc:
            _db_error(exc)
