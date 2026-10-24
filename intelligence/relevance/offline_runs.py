"""S4 PostgreSQL-backed OFFLINE run engine; zero provider calls, zero paid retries.

A trusted internal caller must authenticate project ownership before invoking.
Use real D1 S3 content versions and S2 approved RTB, never synthetic taxonomy
claims or unscoped global videos. All fake results are marked offline_fixture.
"""
from __future__ import annotations

import json
from uuid import uuid4

from .hypothesis_context import build_classifier_contexts
from .intake import read_content
from .offline_fake import FAKE_MODEL, evaluate_fake
from .taxonomy_adapter import read_authorized_taxonomy
from .v1_contracts import (
    CONTRACT_VERSIONS, SREContractError, canonical_uuid, fingerprint,
    nonempty, positive, project_id, reject, validate_transition,
)
from .v1_runtime import validate_run_plan
from .contracts import validate_pipeline


def _lock_run(conn, project, run):
    rid = canonical_uuid(run, "run_id")
    row = conn.execute(
        """SELECT r.run_id::text,r.status,r.cancel_requested,
                  c.taxonomy_version_id,c.pipeline_id
           FROM sre_offline_runs r
           JOIN sre_classification_runs c ON c.id=r.run_id
             AND c.project_id=r.project_id
           WHERE r.project_id=%s AND r.run_id=%s::uuid
           FOR UPDATE OF r""", (project, rid),
    ).fetchone()
    if row is None:
        reject("run_id", "offline run does not belong to project", "rights_denied")
    return row


def _refresh(conn, project, run):
    """Requires locked offline run row (or sole owner in initial creation)."""
    state = conn.execute(
        """SELECT state,count(*) FROM sre_offline_items
           WHERE project_id=%s AND run_id=%s::uuid
           GROUP BY state""", (project, run),
    ).fetchall()
    counts = dict(state)
    queued = counts.get("queued", 0)
    running = counts.get("running", 0)
    completed = counts.get("completed", 0)
    failures = counts.get("failed", 0) + counts.get("blocked_rights", 0)
    cancelled = counts.get("cancelled", 0)
    if cancelled:
        status = "cancelled"
    elif queued or running:
        status = "running" if running or completed or failures else "queued"
    elif failures:
        status = "partial" if completed else "failed"
    else:
        status = "completed"
    conn.execute(
        """UPDATE sre_offline_runs SET status=%s,updated_at=now(),
           finished_at=CASE WHEN %s IN ('completed','partial','failed','cancelled')
                        THEN now() ELSE NULL END
           WHERE project_id=%s AND run_id=%s::uuid""",
        (status,status,project,run),
    )
    conn.execute(
        """UPDATE sre_classification_runs SET status=%s,
           finished_at=CASE WHEN %s IN ('completed','partial','failed','cancelled')
                         THEN now() ELSE NULL END
           WHERE project_id=%s AND id=%s::uuid""",
        (status,status,project,run),
    )
    return status


def _check_pure_offline_pipeline(config):
    validated = validate_pipeline(config)
    stages = [s for s in validated["stages"] if s.get("enabled", True)]
    if len(stages) != 1 or stages[0]["type"] != "llm_classifier" or (
        stages[0].get("run_when","always") != "always"
    ):
        reject("pipeline_id", "S4 supports only single-stage offline strong_only",
               "unsupported_pipeline")
    return validated, stages[0]["model_ref"]


def create_offline_run(conn, project, publication, content_version_ids,
                       pipeline_id, idempotency_key):
    """Pin approved RTB, actual D1 snapshots and an explicitly offline pipeline.

    A project row lock serializes a competing identical idempotency request and
    ensures the whole creation commits together or not at all. No money/consent.
    """
    pid = project_id(project)
    pub = canonical_uuid(publication, "publication_id")
    conf_id = positive(pipeline_id, "pipeline_id")
    key = nonempty(idempotency_key, "idempotency_key", limit=120)
    if not all(c.isascii() and (c.isalnum() or c in "._-") for c in key):
        reject("idempotency_key", "invalid ASCII idempotency key")
    if (type(content_version_ids) is not list or not 1 <= len(content_version_ids) <= 1000
        or any(type(x) is not int or x<=0 for x in content_version_ids)
        or len(set(content_version_ids)) != len(content_version_ids)):
        reject("content_version_ids", "expected unique positive content IDs (1..1000)")
    with conn.transaction():
        if conn.execute(
            "SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR UPDATE",
            (pid,),
        ).fetchone() is None:
            reject("project_id", "no SRE project workspace", "rights_denied")
        snapshot = read_authorized_taxonomy(conn, pid, pub)
        contexts = build_classifier_contexts(snapshot)
        if not contexts or len(contexts) > 48:
            reject("taxonomy", "no bounded approved hypotheses")
        p = conn.execute(
            "SELECT config FROM sre_pipeline_configs WHERE project_id=%s AND id=%s",
            (pid,conf_id),
        ).fetchone()
        if p is None:
            reject("pipeline_id", "pipeline not in project", "rights_denied")
        config, ref = _check_pure_offline_pipeline(p[0])
        pinned_pipeline_sha = fingerprint(config)
        items = []
        for cid in content_version_ids:
            content = read_content(conn,pid,cid,external_llm=False)
            items.append({
                "video_id":content["video_id"],"content_version_id":cid,
                "content_sha256":content["content_sha256"],
                "hypothesis_keys":[c["category_key"] for c in contexts],
            })
        items.sort(key=lambda v:v["video_id"])
        rid = str(uuid4())
        plan = validate_run_plan({
            "schema_version": CONTRACT_VERSIONS["run"],
            "project_id":pid,"run_id":rid,"publication_id":pub,
            "taxonomy_version_id":snapshot["sre_taxonomy_version_id"],
            "taxonomy_sha256":snapshot["tree_sha256"],
            "pipeline_config_id":conf_id,"pipeline_sha256":pinned_pipeline_sha,
            "idempotency_key":key,"state":"awaiting_authorization","items":items,
        })
        req_digest = fingerprint({
            "project_id":pid,"publication_id":pub,
            "taxonomy_sha256":snapshot["tree_sha256"],
            "taxonomy_version_id":snapshot["sre_taxonomy_version_id"],
            "pipeline_id":conf_id,"pipeline_sha256":pinned_pipeline_sha,
            "items":items,"contexts_sha256":fingerprint(contexts),
            "mode":"offline_fixture_s4",
        })
        existing = conn.execute(
            """SELECT run_id::text,request_sha256 FROM sre_offline_runs
               WHERE project_id=%s AND idempotency_key=%s""", (pid,key),
        ).fetchone()
        if existing:
            if existing[1] != req_digest:
                reject("idempotency_key", "run key already used for different inputs",
                       "idempotency_conflict")
            return {"run_id":existing[0],"replayed":True,"status":
                    _lock_run(conn,pid,existing[0])[1],"items":len(items)}
        model = {ref:dict(FAKE_MODEL)}
        base = {"mode":"offline_fixture_s4","publication_id":pub,
                "taxonomy_sha256":snapshot["tree_sha256"],
                "pipeline_sha256":pinned_pipeline_sha,
                "request_sha256":req_digest,
                "content_versions":items}
        conn.execute(
            """INSERT INTO sre_classification_runs
               (id,project_id,taxonomy_version_id,pipeline_id,resolved_models,
                dataset_snapshot,status,cost_usd)
               VALUES(%s::uuid,%s,%s,%s,%s::jsonb,%s::jsonb,'queued',0)""",
            (rid,pid,snapshot["sre_taxonomy_version_id"],conf_id,
             json.dumps(model),json.dumps(base)),
        )
        conn.execute(
            """INSERT INTO sre_offline_runs
               (run_id,project_id,publication_id,taxonomy_sha256,
                pipeline_sha256,request_sha256,idempotency_key)
               VALUES (%s::uuid,%s,%s::uuid,%s,%s,%s,%s)""",
            (rid,pid,pub,snapshot["tree_sha256"],pinned_pipeline_sha,req_digest,key),
        )
        ctx_json=json.dumps(contexts,ensure_ascii=False)
        ctx_hash=fingerprint(contexts)
        for item in plan["items"]:
            conn.execute(
                """INSERT INTO sre_offline_items
                   (run_id,project_id,video_id,content_version_id,
                    content_sha256,contexts,contexts_sha256)
                   VALUES (%s::uuid,%s,%s,%s,%s,%s::jsonb,%s)""",
                (rid,pid,item["video_id"],item["content_version_id"],
                 item["content_sha256"],ctx_json,ctx_hash),
            )
        return {"run_id":rid,"replayed":False,"status":"queued","items":len(items)}


def get_offline_run(conn, project, run_id):
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    row=conn.execute(
        """SELECT r.status,r.cancel_requested,r.publication_id::text,
                  c.taxonomy_version_id,c.pipeline_id,r.request_sha256
           FROM sre_offline_runs r JOIN sre_classification_runs c
             ON c.id=r.run_id AND c.project_id=r.project_id
           WHERE r.project_id=%s AND r.run_id=%s::uuid""", (pid,rid),
    ).fetchone()
    if row is None:
        reject("run_id","run not in project","rights_denied")
    items=conn.execute(
        """SELECT video_id,content_version_id,state,attempts,error_code
           FROM sre_offline_items WHERE project_id=%s AND run_id=%s::uuid
           ORDER BY video_id""",(pid,rid),
    ).fetchall()
    return {
        "run_id":rid,"project_id":pid,"status":row[0],"cancel_requested":row[1],
        "publication_id":row[2],"taxonomy_version_id":row[3],
        "pipeline_id":row[4],"request_sha256":row[5],"mode":"offline_fixture_s4",
        "items":[{"video_id":v,"content_version_id":c,"state":s,
                  "attempts":a,"error_code":e}
                 for v,c,s,a,e in items],
    }


def cancel_offline_run(conn, project, run_id):
    pid=project_id(project)
    with conn.transaction():
        run = _lock_run(conn,pid,run_id)
        if run[1] in ("completed","partial","failed","cancelled"):
            return run[1]
        conn.execute(
            """UPDATE sre_offline_runs SET cancel_requested=TRUE
               WHERE project_id=%s AND run_id=%s::uuid""", (pid,run[0]),
        )
        conn.execute(
            """UPDATE sre_offline_items SET state='cancelled',lease_until=NULL,
               worker_token=NULL,updated_at=now(),finished_at=now(),
               error_code='cancel_requested'
               WHERE project_id=%s AND run_id=%s::uuid
               AND state IN ('queued','running')""", (pid,run[0]),
        )
        return _refresh(conn,pid,run[0])
