"""Project-scoped persistence for experiments, human labels and SRE classifications.

No remote calls. A future HTTP layer must authenticate the local project owner
before invoking these operations. IDs and evidence are always checked against
the project's stored, immutable snapshots; no LLM gets direct database access.
"""
from __future__ import annotations
import json
from uuid import UUID
from .contracts import ContractError, validate_evidence
from .repository import _project_id, _missing

VERDICTS=frozenset(("match","no_match","uncertain","not_evaluated"))

def attach_video(conn, project_id: str, video_id: int) -> None:
    project_id=_project_id(project_id)
    if type(video_id) is not int or video_id<=0:
        raise ContractError("Invalid video_id")
    with conn.transaction():
        conn.execute("SELECT 1 FROM sre_workspaces WHERE project_id=%s",
                     (project_id,)).fetchone() or _missing()
        conn.execute("SELECT 1 FROM videos WHERE id=%s",(video_id,)).fetchone() or _missing()
        conn.execute("""INSERT INTO sre_project_videos(project_id,video_id)
                        VALUES (%s,%s) ON CONFLICT DO NOTHING""",(project_id,video_id))

def create_run(conn,project_id: str,taxonomy_version_id: int,pipeline_id: int,
               resolved_models: dict,dataset_snapshot: dict) -> str:
    project_id=_project_id(project_id)
    if not isinstance(resolved_models,dict) or not resolved_models:
        raise ContractError("Exact resolved model snapshots are required")
    for alias,model in resolved_models.items():
        if not isinstance(alias,str) or not alias.strip() or not isinstance(model,dict) or not all(
            isinstance(model.get(k),str) and model[k].strip()
            for k in ("provider","model_id","model_version","prompt_version")):
            raise ContractError("Each model must pin provider/id/version/prompt_version")
    if not isinstance(dataset_snapshot,dict):
        raise ContractError("Dataset snapshot must be an object")
    with conn.transaction():
        conn.execute("""SELECT 1 FROM sre_taxonomy_versions
           WHERE id=%s AND project_id=%s AND status='approved'""",
           (taxonomy_version_id,project_id)).fetchone() or _missing()
        conn.execute("""SELECT 1 FROM sre_pipeline_configs
           WHERE id=%s AND project_id=%s""",
           (pipeline_id,project_id)).fetchone() or _missing()
        value=conn.execute("""INSERT INTO sre_classification_runs
            (project_id,taxonomy_version_id,pipeline_id,resolved_models,dataset_snapshot)
            VALUES(%s,%s,%s,%s::jsonb,%s::jsonb) RETURNING id""",
            (project_id,taxonomy_version_id,pipeline_id,
             json.dumps(resolved_models),json.dumps(dataset_snapshot))).fetchone()[0]
        return str(value)

def record_result(conn,project_id: str,run_id: str,video_id: int,
                  content_version_id: int | None,category_key: str,
                  decision: str,reason: str,evidence: list,stage_trace: list) -> int:
    """No match can be stored without source content and exact-quote evidence."""
    project_id=_project_id(project_id)
    if decision not in VERDICTS:
        raise ContractError("Unknown classification decision")
    if not isinstance(stage_trace,list):
        raise ContractError("Stage trace must be a list")
    if not isinstance(reason,str) or not reason.strip():
        raise ContractError("Reason is mandatory")
    with conn.transaction():
        row=conn.execute("""SELECT taxonomy_version_id,status FROM sre_classification_runs
                  WHERE id=%s AND project_id=%s FOR UPDATE""",
                 (run_id,project_id)).fetchone()
        if not row: _missing()
        if row[1] in ("cancelled","failed","completed","partial"):
            raise ContractError("Classification run is closed")
        taxonomy_id=row[0]
        conn.execute("""SELECT 1 FROM sre_taxonomy_categories
              WHERE taxonomy_version_id=%s AND project_id=%s AND category_key=%s
              AND review_status='approved'""",
              (taxonomy_id,project_id,category_key)).fetchone() or _missing()
        content={}
        if content_version_id is not None:
            fetched=conn.execute("""SELECT content FROM sre_readable_content_versions
                  WHERE id=%s AND project_id=%s AND video_id=%s""",
                  (content_version_id,project_id,video_id)).fetchone()
            if not fetched: _missing()
            content=fetched[0]
        if decision in ("match","no_match") and content_version_id is None:
            raise ContractError("Definitive classification requires a content snapshot")
        validated=validate_evidence(content,evidence)
        if decision=="match" and not validated:
            raise ContractError("Positive label requires source-backed evidence")
        if decision=="not_evaluated" and validated:
            raise ContractError("A skipped category must not contain model evidence")
        ident=conn.execute("""INSERT INTO sre_classification_results
               (run_id,project_id,video_id,content_version_id,taxonomy_version_id,
                category_key,decision,reason,evidence,stage_trace)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb) RETURNING id""",
               (run_id,project_id,video_id,content_version_id,taxonomy_id,category_key,
                decision,reason.strip(),json.dumps(validated,ensure_ascii=False),
                json.dumps(stage_trace,ensure_ascii=False))
               ).fetchone()[0]
        return ident

def record_human_label(conn,project_id: str,video_id: int,taxonomy_version_id: int,
                       category_key: str,label: str,reviewer: str,reason: str = "") -> int:
    """Append a human correction and supersede the previous current label."""
    project_id=_project_id(project_id)
    if label not in ("match","no_match","uncertain") or not isinstance(reviewer,str) or not reviewer.strip():
        raise ContractError("Invalid human label/reviewer")
    if not isinstance(reason,str):
        raise ContractError("Reason must be text")
    try: category_key=str(UUID(str(category_key)))
    except ValueError as exc: raise ContractError("Invalid category UUID") from exc
    with conn.transaction():
        conn.execute("""SELECT 1 FROM sre_project_videos WHERE project_id=%s AND video_id=%s
                        FOR UPDATE""",(project_id,video_id)).fetchone() or _missing()
        conn.execute("""SELECT 1 FROM sre_taxonomy_versions v
            JOIN sre_taxonomy_categories c ON c.taxonomy_version_id=v.id
            AND c.project_id=v.project_id
            WHERE v.project_id=%s AND v.id=%s AND v.status='approved'
              AND c.category_key=%s AND c.review_status='approved'""",
            (project_id,taxonomy_version_id,category_key)).fetchone() or _missing()
        conn.execute("""UPDATE sre_human_labels SET superseded_at=now()
                 WHERE project_id=%s AND video_id=%s AND taxonomy_version_id=%s
                 AND category_key=%s AND superseded_at IS NULL""",
                 (project_id,video_id,taxonomy_version_id,category_key))
        ident=conn.execute("""INSERT INTO sre_human_labels
            (project_id,video_id,taxonomy_version_id,category_key,label,reviewer,reason)
            VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (project_id,video_id,taxonomy_version_id,category_key,label,
             reviewer.strip(),reason)).fetchone()[0]
        conn.execute("""INSERT INTO sre_review_events
                 (project_id,target_type,target_id,action,after_value,reviewed_by)
                 VALUES (%s,'label',%s,'correct',%s::jsonb,%s)""",
                 (project_id,str(ident),json.dumps({
                     "video_id":video_id,"category_key":category_key,
                     "taxonomy_version_id":taxonomy_version_id,
                     "label":label,"reason":reason},ensure_ascii=False),reviewer.strip()))
        return ident
