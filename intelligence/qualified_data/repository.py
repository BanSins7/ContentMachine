"""PostgreSQL implementation of SRE -> Qualified Set -> Enrichment -> A/B/C preparation.

Never calls an LLM, downloads media, queries a social platform or starts paid work.
All external evidence is read from the existing canonical D1/SRE stores.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from psycopg.rows import dict_row

from intelligence.config import dsn
from intelligence.jobs import enqueue
from .contracts import (QDPError, analysis_readiness, freshness_reason,
                        idempotency_key, options, positive_id, project_id,
                        qualify_sre_rows, sha256)

JOB_TYPE = "qdp.prepare_video"
MAX_ITEMS = 10_000
WORKER_LEASE_SECONDS = 120


def _connect():
    if not dsn():
        raise QDPError("database_disabled", "PostgreSQL is not configured", 503)
    import psycopg
    return psycopg.connect(dsn(), connect_timeout=5, autocommit=True,
                           row_factory=dict_row)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _uuid(value, label):
    try:
        result = str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise QDPError("invalid_id", "Invalid " + label) from exc
    if str(value) != result:
        raise QDPError("invalid_id", "Noncanonical " + label)
    return result


def _sre_snapshot(conn, pid, sre_run_id, *, only_video_id=None):
    row = conn.execute(
        """SELECT r.id,r.taxonomy_version_id,r.status,t.status AS taxonomy_status
           FROM sre_classification_runs r
           JOIN sre_taxonomy_versions t ON t.id=r.taxonomy_version_id
                                      AND t.project_id=r.project_id
           WHERE r.project_id=%s AND r.id=%s::uuid""",
        (pid, sre_run_id)).fetchone()
    if not row:
        raise QDPError("sre_not_found", "SRE run not found in this project", 404)
    if row["status"] != "completed" or row["taxonomy_status"] != "approved":
        raise QDPError("sre_not_approved", "Requires completed SRE and approved taxonomy", 409)
    rows = conn.execute(
        """SELECT r.id AS result_id,r.video_id,r.category_key::text AS category_key,
                  COALESCE(h.label,r.decision) AS effective_decision,
                  (cv.id IS NOT NULL) AS content_readable
           FROM sre_classification_results r
           JOIN sre_taxonomy_categories c
             ON c.project_id=r.project_id
            AND c.taxonomy_version_id=r.taxonomy_version_id
            AND c.category_key=r.category_key AND c.review_status='approved'
           LEFT JOIN sre_readable_content_versions cv
             ON cv.id=r.content_version_id AND cv.project_id=r.project_id
            AND cv.video_id=r.video_id
           LEFT JOIN LATERAL (
              SELECT label FROM sre_human_labels h
              WHERE h.project_id=r.project_id AND h.video_id=r.video_id
                AND h.taxonomy_version_id=r.taxonomy_version_id
                AND h.category_key=r.category_key AND h.superseded_at IS NULL
              ORDER BY h.created_at DESC,h.id DESC LIMIT 1
           ) h ON TRUE
           WHERE r.run_id=%s::uuid AND r.project_id=%s
             AND r.taxonomy_version_id=%s
             AND (%s::bigint IS NULL OR r.video_id=%s::bigint)
           ORDER BY r.video_id,r.category_key""",
        (sre_run_id, pid, row["taxonomy_version_id"], only_video_id, only_video_id)).fetchall()
    if not rows:
        raise QDPError("sre_empty", "Completed SRE run has no approved category results", 409)
    return row, qualify_sre_rows(rows)


def preview(project, sre_run_id, *, connect=None):
    """Read-only preview. Always rechecks SRE status and current rights."""
    pid = project_id(project)
    rid = _uuid(sre_run_id, "SRE run ID")
    with (connect or _connect)() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            row, qualified = _sre_snapshot(conn, pid, rid)
    return {"ok": True, "sre_run_id": rid,
            "taxonomy_version_id": row["taxonomy_version_id"],
            "totals": qualified["totals"],
            "qualified_video_ids": [x["video_id"] for x in qualified["qualified"][:30]],
            "sample_truncated": len(qualified["qualified"]) > 30,
            "paid_calls": 0, "media_downloads": 0,
            "note": "Qualification only; enrichment rights are rechecked by worker"}


def create_run(project, sre_run_id, key, config=None, *, connect=None):
    pid, rid = project_id(project), _uuid(sre_run_id, "SRE run ID")
    key, cfg = idempotency_key(key), options(config)
    fingerprint = sha256({"project_id": pid, "sre_run_id": rid,
                          "config": cfg, "version": "qdp/v1"})
    with (connect or _connect)() as conn:
        with conn.transaction():
            # A project-scoped mutex also serializes idempotency and snapshots.
            exists = conn.execute(
                "SELECT id FROM project_registry WHERE id=%s FOR UPDATE", (pid,)
            ).fetchone()
            if not exists:
                raise QDPError("project_not_found", "Project not registered", 404)
            previous = conn.execute(
                """SELECT id,input_sha256 FROM qdp_runs
                   WHERE project_id=%s AND idempotency_key=%s""", (pid, key)).fetchone()
            if previous:
                if previous["input_sha256"] != fingerprint:
                    raise QDPError("idempotency_conflict", "Key reused with different inputs", 409)
                return {"ok": True, "run_id": str(previous["id"]), "replayed": True}
            source, selection = _sre_snapshot(conn, pid, rid)
            if len(selection["qualified"]) > MAX_ITEMS:
                raise QDPError("too_many_items", "Run exceeds 10,000 qualified videos", 422)
            result = conn.execute(
                """INSERT INTO qdp_runs
                   (project_id,sre_run_id,taxonomy_version_id,idempotency_key,
                    input_sha256,config,total_qualified,total_review,total_excluded,status)
                   VALUES (%s,%s::uuid,%s,%s,%s,%s::jsonb,%s,%s,%s,%s) RETURNING id""",
                (pid, rid, source["taxonomy_version_id"], key, fingerprint, _json(cfg),
                 selection["totals"]["qualified"], selection["totals"]["needs_review"],
                 selection["totals"]["excluded"],
                 "queued" if selection["qualified"] else "completed")).fetchone()
            run_id = str(result["id"])
            for item in selection["qualified"]:
                conn.execute(
                    """INSERT INTO qdp_items
                       (run_id,project_id,video_id,sre_category_keys,sre_result_ids)
                       VALUES (%s::uuid,%s,%s,%s::jsonb,%s::jsonb)""",
                    (run_id, pid, item["video_id"], _json(item["category_keys"]),
                     _json(item["result_ids"])))
                enqueue(conn, JOB_TYPE, "qdp:" + run_id + ":" + str(item["video_id"]),
                        {"project_id": pid, "run_id": run_id, "video_id": item["video_id"]},
                        max_attempts=4)
            conn.execute(
                """INSERT INTO qdp_events(run_id,project_id,event_code,details)
                   VALUES (%s::uuid,%s,'qualified_set_created',%s::jsonb)""",
                (run_id,pid,_json(selection["totals"])))
            if not selection["qualified"]:
                conn.execute("UPDATE qdp_runs SET finished_at=now() WHERE id=%s::uuid",
                             (run_id,))
            return {"ok": True, "run_id": run_id, "replayed": False,
                    "totals": selection["totals"], "status": "queued" if selection["qualified"] else "completed"}


def _authorized_metadata(conn, video_id):
    return conn.execute(
        """SELECT m.id AS metadata_id,m.video_id,m.source_id,
                  m.source_policy_version_id,m.title,m.description,
                  m.creator_name,m.published_at,m.duration_seconds,m.fetched_at,
                  m.expires_at, v.platform,v.platform_video_id,v.canonical_url,
                  v.content_kind, s.can_store_metrics,s.can_derive_scores,
                  s.can_analyze_media,s.retention_days,p.permissions
           FROM source_video_metadata m
           JOIN videos v ON v.id=m.video_id
           JOIN sources s ON s.id=m.source_id
           JOIN source_policy_versions p
             ON p.id=m.source_policy_version_id AND p.source_id=s.id
           WHERE m.video_id=%s AND m.deleted_at IS NULL AND s.status='enabled'
             AND s.can_store_metadata AND p.is_active
             AND p.permissions->>'can_store_metadata'='true'
             AND (m.expires_at IS NULL OR m.expires_at>now())
             AND (s.retention_days IS NULL OR
                  m.fetched_at+s.retention_days*interval '1 day'>now())
           ORDER BY m.fetched_at DESC,m.id DESC LIMIT 1""",
        (video_id,)).fetchone()


def _authorized_metrics(conn, metadata):
    if not (metadata["can_store_metrics"]
            and metadata["permissions"].get("can_store_metrics") is True):
        return []
    return conn.execute(
        """SELECT id,observed_at,views,likes,comments,shares,saves,followers,
                  precision,source_metric_definition,source_metric_version
           FROM metric_snapshots
           WHERE video_id=%s AND source_id=%s AND source_policy_version_id=%s
             AND observation_time_assumed=FALSE AND data_quality_status='ok'
             AND observed_at<=now() AND (expires_at IS NULL OR expires_at>now())
           ORDER BY observed_at DESC,id DESC LIMIT 20""",
        (metadata["video_id"],metadata["source_id"],
         metadata["source_policy_version_id"])).fetchall()


def _authorized_media(conn, metadata):
    if not (metadata["can_analyze_media"]
            and metadata["permissions"].get("can_analyze_media") is True):
        return None
    row = conn.execute(
        """SELECT a.id,a.sha256 FROM media_assets a
           WHERE a.video_id=%s AND a.source_id=%s
             AND a.permission<>'metadata_only'
             AND (a.expires_at IS NULL OR a.expires_at>now())
           ORDER BY a.created_at DESC,a.id DESC LIMIT 1""",
        (metadata["video_id"],metadata["source_id"])).fetchone()
    return row


def _score_config(conn):
    return conn.execute(
        """SELECT id FROM metric_calculation_configs
           WHERE is_active AND alpha IS NOT NULL AND bayesian_k IS NOT NULL
           ORDER BY id DESC LIMIT 1""").fetchone()


def _effective_expiry(metadata):
    expiry = metadata["expires_at"]
    days = metadata["retention_days"]
    if days is not None:
        from datetime import timedelta
        limited = metadata["fetched_at"] + timedelta(days=days)
        expiry = min(expiry, limited) if expiry else limited
    return expiry


def _update_run_state(conn, run_id):
    counts = conn.execute(
        """SELECT count(*) FILTER (WHERE status='queued') AS pending,
                  count(*) FILTER (WHERE status='ready') AS ready,
                  count(*) FILTER (WHERE status='partial') AS partial,
                  count(*) FILTER (WHERE status='blocked') AS blocked,
                  count(*) FILTER (WHERE status='cancelled') AS cancelled
           FROM qdp_items WHERE run_id=%s::uuid""", (run_id,)).fetchone()
    run = conn.execute(
        "SELECT cancel_requested FROM qdp_runs WHERE id=%s::uuid", (run_id,)
    ).fetchone()
    if run["cancel_requested"]:
        state = "cancelled" if not counts["pending"] else "running"
    elif counts["pending"]:
        state = "running"
    elif counts["partial"] or counts["blocked"] or counts["cancelled"]:
        state = "partial"
    else:
        state = "completed"
    conn.execute(
        """UPDATE qdp_runs SET status=%s,updated_at=now(),
           finished_at=CASE WHEN %s IN ('completed','partial','cancelled')
                              THEN now() ELSE NULL END WHERE id=%s::uuid""",
        (state,state,run_id))


def _process_locked(conn, job, worker_id):
    job_id, generation, payload = job["id"], job["lease_generation"], job["payload"]
    run_id = _uuid(payload["run_id"], "QDP run")
    pid = project_id(payload["project_id"])
    vid = positive_id(payload["video_id"], "video")
    lease = conn.execute(
        """SELECT id FROM jobs
           WHERE id=%s AND job_type=%s AND state='running'
             AND locked_by=%s AND lease_generation=%s AND lease_until>now()
           FOR UPDATE""", (job_id, JOB_TYPE, worker_id, generation)).fetchone()
    if not lease:
        return {"state": "lost_lease"}
    run = conn.execute(
        "SELECT * FROM qdp_runs WHERE id=%s::uuid AND project_id=%s FOR UPDATE",
        (run_id, pid)).fetchone()
    if not run:
        raise QDPError("run_not_found", "QDP run missing", 404)
    item = conn.execute(
        """SELECT status FROM qdp_items
           WHERE run_id=%s::uuid AND project_id=%s AND video_id=%s FOR UPDATE""",
        (run_id,pid,vid)).fetchone()
    if not item:
        raise QDPError("item_not_found", "QDP item missing", 404)
    if run["cancel_requested"]:
        status, reason = "cancelled", "run_cancelled"
    elif item["status"] != "queued":
        status, reason = item["status"], "already_processed"
    else:
        # Revalidate that THIS SRE run, approval and evidence are still readable.
        source, selection = _sre_snapshot(conn, pid, str(run["sre_run_id"]), only_video_id=vid)
        still = next((x for x in selection["qualified"] if x["video_id"] == vid), None)
        if source["taxonomy_version_id"] != run["taxonomy_version_id"] or still is None:
            status, reason = "blocked", "sre_evidence_revoked_or_changed"
        else:
            metadata = _authorized_metadata(conn, vid)
            if metadata is None:
                status, reason = "blocked", "metadata_rights_or_source_missing"
            else:
                reason = freshness_reason(metadata["published_at"], run["config"])
                if reason:
                    status = "blocked"
                else:
                    expiry = _effective_expiry(metadata)
                    # An expiring source must remain valid until the snapshot is saved.
                    if expiry is not None and expiry <= datetime.now(timezone.utc):
                        status, reason = "blocked", "metadata_expired"
                    else:
                        metrics = _authorized_metrics(conn, metadata)
                        asset = _authorized_media(conn, metadata)
                        config = _score_config(conn)
                        metric_ids = [m["id"] for m in metrics]
                        definitions = [m["source_metric_definition"] for m in metrics]
                        decisions = analysis_readiness(
                            media_asset_id=asset["id"] if asset else None,
                            media_allowed=asset is not None,
                            metric_snapshot_ids=metric_ids,
                            metric_definitions=definitions,
                            scoring_allowed=(metadata["can_derive_scores"] and
                               metadata["permissions"].get("can_derive_scores") is True and
                               metadata["can_store_metrics"] and
                               metadata["permissions"].get("can_store_metrics") is True),
                            active_score_config_id=config["id"] if config else None)
                        meta = {k:metadata[k] for k in (
                            "platform","platform_video_id","canonical_url",
                            "title","description","creator_name","published_at",
                            "duration_seconds","fetched_at","content_kind")}
                        metric_data = {
                            "available_count": len(metrics),
                            "observations": [{k:m[k] for k in (
                                "id","observed_at","views","likes","comments","shares",
                                "saves","followers","precision","source_metric_definition",
                                "source_metric_version")} for m in metrics],
                        }
                        conn.execute(
                            """INSERT INTO qdp_enrichments
                              (run_id,project_id,video_id,source_id,
                               source_policy_version_id,metadata_id,media_asset_id,
                               metadata,metrics,metric_snapshot_ids,expires_at)
                              VALUES (%s::uuid,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,
                                      %s::jsonb,%s)""",
                            (run_id,pid,vid,metadata["source_id"],
                             metadata["source_policy_version_id"],metadata["metadata_id"],
                             asset["id"] if asset else None,_json(meta),_json(metric_data),
                             _json(metric_ids),expiry))
                        for group, result in decisions.items():
                            inputs = {"enrichment_source_id": metadata["source_id"],
                                      "source_policy_version_id": metadata["source_policy_version_id"],
                                      "metadata_id": metadata["metadata_id"],
                                      "media_asset_id": asset["id"] if group in ("A","B") and asset else None,
                                      "metric_snapshot_ids": metric_ids if group=="C" else [],
                                      "score_config_id": config["id"] if group=="C" and config else None,
                                      "result_schema": "qdp-preparation/v1"}
                            conn.execute(
                                """INSERT INTO qdp_preparations
                                   (run_id,project_id,video_id,analysis_group,state,reason_code,
                                    inputs,input_sha256)
                                   VALUES (%s::uuid,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                                (run_id,pid,vid,group,result["state"],result["reason_code"],
                                 _json(inputs),sha256(inputs)))
                        ready = sum(x["state"]=="ready" for x in decisions.values())
                        status = "ready" if ready==3 else "partial" if ready else "blocked"
                        reason = None if ready==3 else "downstream_groups_blocked"
    if item["status"] == "queued":
        conn.execute(
            """UPDATE qdp_items SET status=%s,reason_code=%s,updated_at=now()
               WHERE run_id=%s::uuid AND video_id=%s""", (status,reason,run_id,vid))
        conn.execute(
            """INSERT INTO qdp_events(run_id,project_id,video_id,event_code,details)
               VALUES (%s::uuid,%s,%s,%s,%s::jsonb)""",
            (run_id,pid,vid,"item_" + status,_json({"reason":reason})))
    conn.execute(
        """UPDATE jobs SET state='completed',result=%s::jsonb,locked_by=NULL,
           lease_until=NULL,updated_at=now()
           WHERE id=%s AND locked_by=%s AND lease_generation=%s""",
        (_json({"status":status,"reason":reason}),job_id,worker_id,generation))
    _update_run_state(conn,run_id)
    return {"state":status,"run_id":run_id,"video_id":vid,"reason":reason}


def work_once(*, connect=None, worker_id=None):
    """Exactly one free, local-only preparation job; lease fenced across workers."""
    worker_id = worker_id or "qdp-" + uuid.uuid4().hex[:12]
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute(
                """UPDATE jobs SET state='failed',locked_by=NULL,lease_until=NULL,
                   last_error='QDP lease exhausted',updated_at=now()
                   WHERE job_type=%s AND state='running' AND lease_until<now()
                     AND attempts>=max_attempts""", (JOB_TYPE,))
            selected = conn.execute(
                """SELECT id FROM jobs WHERE job_type=%s
                   AND ((state='queued' AND run_after<=now())
                     OR (state='running' AND lease_until<now() AND attempts<max_attempts))
                   ORDER BY priority DESC,created_at,id
                   FOR UPDATE SKIP LOCKED LIMIT 1""", (JOB_TYPE,)).fetchone()
            if not selected:
                return None
            job = conn.execute(
                """UPDATE jobs SET state='running',locked_by=%s,
                   lease_until=now()+(%s*interval '1 second'),
                   attempts=attempts+1,lease_generation=lease_generation+1,
                   updated_at=now() WHERE id=%s
                   RETURNING id,payload,lease_generation""",
                (worker_id, WORKER_LEASE_SECONDS,selected["id"])).fetchone()
        try:
            with conn.transaction():
                return _process_locked(conn, job, worker_id)
        except Exception as exc:
            # Nonpaid local jobs are retriable. Preserve sanitized error only.
            with conn.transaction():
                conn.execute(
                    """UPDATE jobs SET
                       state=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'queued' END,
                       run_after=now()+interval '30 seconds',
                       last_error=%s,locked_by=NULL,lease_until=NULL,updated_at=now()
                       WHERE id=%s AND state='running' AND locked_by=%s
                         AND lease_generation=%s""",
                    (type(exc).__name__,job["id"],worker_id,job["lease_generation"]))
                rid = job["payload"].get("run_id")
                if rid:
                    conn.execute(
                        """UPDATE qdp_runs SET status='failed',updated_at=now()
                           WHERE id=%s::uuid AND NOT EXISTS
                             (SELECT 1 FROM jobs WHERE job_type=%s
                                AND payload->>'run_id'=%s
                                AND state IN ('queued','running'))""",
                        (rid,JOB_TYPE,rid))
            return {"state": "retry_or_failed", "job_id": job["id"],
                    "error_code": type(exc).__name__}


def get_run(project, run_id, *, connect=None, limit=100):
    pid,rid=project_id(project),_uuid(run_id,"QDP run ID")
    if type(limit) is not int or not 1<=limit<=200:
        raise QDPError("invalid_limit","limit must be 1..200")
    with (connect or _connect)() as conn:
        run=conn.execute(
            """SELECT id,sre_run_id,taxonomy_version_id,status,config,
                      total_qualified,total_review,total_excluded,created_at,finished_at
               FROM qdp_runs WHERE id=%s::uuid AND project_id=%s""",(rid,pid)).fetchone()
        if not run:
            raise QDPError("run_not_found","QDP run not found",404)
        rows=conn.execute(
            """SELECT i.video_id,i.status,i.reason_code,i.sre_category_keys,
                      (e.video_id IS NOT NULL) AS enrichment_readable,
                      e.source_id,e.metadata_id,e.metadata,
                      e.metrics,e.media_asset_id
               FROM qdp_items i
               LEFT JOIN qdp_readable_enrichments e
                  ON e.run_id=i.run_id AND e.video_id=i.video_id
               WHERE i.run_id=%s::uuid AND i.project_id=%s
               ORDER BY i.video_id LIMIT %s""",(rid,pid,limit)).fetchall()
        preparation=conn.execute(
            """SELECT p.video_id,p.analysis_group,p.state,p.reason_code
               FROM qdp_preparations p
               JOIN qdp_readable_enrichments e
                 ON e.run_id=p.run_id AND e.video_id=p.video_id
               WHERE p.run_id=%s::uuid AND p.project_id=%s
                 AND p.video_id IN
                   (SELECT video_id FROM qdp_items WHERE run_id=%s::uuid
                    ORDER BY video_id LIMIT %s)""",
            (rid,pid,rid,limit)).fetchall()
        states={}
        for p in preparation:
            states.setdefault(p["video_id"],{})[p["analysis_group"]]={
                "state":p["state"],"reason_code":p["reason_code"]}
        items=[]
        for item in rows:
            readable=item["enrichment_readable"]
            items.append({
                "video_id":item["video_id"],
                "status":item["status"] if readable or item["status"]=="queued" else "blocked",
                "reason_code":item["reason_code"] if readable or item["status"]=="queued" else
                               "source_policy_inactive_or_expired",
                "category_keys":item["sre_category_keys"],
                "source_id":item["source_id"] if readable else None,
                "metadata":item["metadata"] if readable else None,
                "metrics":item["metrics"] if readable else None,
                "media_asset_id":item["media_asset_id"] if readable else None,
                "groups":states.get(item["video_id"],{}),
            })
        return {"ok":True,"run":{k:str(v) if isinstance(v,(uuid.UUID,datetime)) else v
                                  for k,v in run.items()},
                "items":items,"limit":limit}


def list_runs(project, *, connect=None, limit=30):
    pid=project_id(project)
    if type(limit) is not int or not 1<=limit<=100:
        raise QDPError("invalid_limit","limit must be 1..100")
    with (connect or _connect)() as conn:
        rows=conn.execute(
            """SELECT id,sre_run_id,status,total_qualified,total_review,total_excluded,
                      created_at,finished_at FROM qdp_runs
               WHERE project_id=%s ORDER BY created_at DESC,id DESC LIMIT %s""",
            (pid,limit)).fetchall()
    return {"ok":True,"runs":[{k:str(v) if isinstance(v,(uuid.UUID,datetime)) else v
                                for k,v in row.items()} for row in rows]}


def cancel_run(project, run_id, *, connect=None):
    pid,rid=project_id(project),_uuid(run_id,"QDP run ID")
    with (connect or _connect)() as conn:
        with conn.transaction():
            row=conn.execute(
                "SELECT status FROM qdp_runs WHERE id=%s::uuid AND project_id=%s FOR UPDATE",
                (rid,pid)).fetchone()
            if not row:
                raise QDPError("run_not_found","QDP run not found",404)
            if row["status"] in ("completed","partial","cancelled"):
                raise QDPError("run_closed","QDP run already finished",409)
            conn.execute(
                """UPDATE qdp_runs SET cancel_requested=TRUE,status='cancelled',
                   finished_at=now(),updated_at=now()
                   WHERE id=%s::uuid""",(rid,))
            conn.execute(
                """UPDATE jobs SET state='cancelled',locked_by=NULL,lease_until=NULL,
                   updated_at=now() WHERE job_type=%s AND payload->>'run_id'=%s
                   AND state='queued'""",(JOB_TYPE,rid))
            conn.execute(
                """UPDATE qdp_items SET status='cancelled',reason_code='run_cancelled',
                   updated_at=now() WHERE run_id=%s::uuid AND status='queued'""",(rid,))
            return {"ok":True,"run_id":rid,"status":"cancelled"}


def purge_revoked(*, connect=None):
    """Remove materialized data that can no longer be accessed under live rights."""
    with (connect or _connect)() as conn:
        with conn.transaction():
            inaccessible=conn.execute(
                """SELECT e.run_id,e.video_id FROM qdp_enrichments e
                   WHERE NOT EXISTS (SELECT 1 FROM qdp_readable_enrichments v
                                     WHERE v.run_id=e.run_id AND v.video_id=e.video_id)
                   FOR UPDATE""").fetchall()
            for row in inaccessible:
                conn.execute(
                    "DELETE FROM qdp_preparations WHERE run_id=%s AND video_id=%s",
                    (row["run_id"],row["video_id"]))
                conn.execute(
                    "DELETE FROM qdp_enrichments WHERE run_id=%s AND video_id=%s",
                    (row["run_id"],row["video_id"]))
                conn.execute(
                    """UPDATE qdp_items SET status='blocked',
                       reason_code='source_policy_inactive_or_expired',updated_at=now()
                       WHERE run_id=%s AND video_id=%s""",
                    (row["run_id"],row["video_id"]))
            return len(inaccessible)
