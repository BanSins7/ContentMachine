"""PostgreSQL implementation of SRE -> Qualified Set -> Enrichment -> A/B/C preparation.

Never calls an LLM, downloads media, queries a social platform or starts paid work.
All external evidence is read from the existing canonical D1/SRE stores.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from intelligence.config import dsn
from .contracts import (QDPError, analysis_readiness, freshness_reason,
                        idempotency_key, options, positive_id, project_id,
                        sha256)
from .qualification import resolve_qualified_set
from .run_manifest import build_run_manifest, read_pinned_video

JOB_TYPE = "qdp.prepare_video"
MAX_ITEMS = 10_000
WORKER_LEASE_SECONDS = 120


def _connect():
    if not dsn():
        raise QDPError("database_disabled", "PostgreSQL is not configured", 503)
    import psycopg
    from psycopg.rows import dict_row
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
    """LIVE-only SRE DB intake; never accept legacy, offline or browser labels."""
    from .intake import read_live_sre_run, get_live_result_rows, verify_live_coverage
    row = read_live_sre_run(conn, pid, sre_run_id)
    rows = get_live_result_rows(conn, pid, sre_run_id,
                                only_video_id=only_video_id, verified_run=row)
    verification = verify_live_coverage(conn, row, rows, only_video_id=only_video_id)
    row["manifest_digest"] = verification["manifest_digest"]
    return row, resolve_qualified_set(rows, strict=True)

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
    with (connect or _connect)() as conn:
        with conn.transaction():
            # A project-scoped mutex also serializes idempotency and snapshots.
            exists = conn.execute(
                "SELECT id FROM project_registry WHERE id=%s FOR UPDATE", (pid,)
            ).fetchone()
            if not exists:
                raise QDPError("project_not_found", "Project not registered", 404)
            # Independent QDP schema must be present before any run is created.
            from .schema import require_qdp_schema
            require_qdp_schema(conn)
            source, selection = _sre_snapshot(conn, pid, rid)
            pinned = build_run_manifest(pid, rid, source, selection, cfg)
            fingerprint = sha256({
                "project_id": pid, "sre_run_id": rid, "config": cfg,
                "taxonomy_version_id": source["taxonomy_version_id"],
                "sre_manifest_sha256": source["manifest_digest"],
                "selection_sha256": selection["selection_sha256"],
                "review_watermark": selection["review_watermark"],
                "qdp_run_manifest_sha256": pinned["manifest_sha256"],
                "version": "qdp/v2-selection"
            })
            previous = conn.execute(
                """SELECT id,input_sha256 FROM qdp_runs
                   WHERE project_id=%s AND idempotency_key=%s""", (pid, key)).fetchone()
            if previous:
                if previous["input_sha256"] != fingerprint:
                    raise QDPError("idempotency_conflict", "Key reused with different inputs", 409)
                # The fingerprint was recomputed from CURRENT SRE+human state
                # before replay. Stale human changes cannot revive this run.
                return {"ok": True, "run_id": str(previous["id"]), "replayed": True}
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
            conn.execute(
                """INSERT INTO qdp_run_manifests
                   (run_id,project_id,taxonomy_sha256,manifest_sha256,
                    selected_sha256,expected_pairs,expected_videos,
                    review_watermark,manifest)
                   VALUES (%s::uuid,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)""",
                (run_id,pid,pinned["taxonomy_sha256"],pinned["manifest_sha256"],
                 pinned["selected_sha256"],pinned["expected_pairs"],
                 pinned["expected_videos"],_json(pinned["review_watermark"]),
                 _json(pinned["manifest"])))
            # All SRE verdicts including excluded/review remain auditable.
            for row in pinned["lineage"]:
                conn.execute(
                    """INSERT INTO qdp_item_lineage
                       (run_id,project_id,video_id,category_key,sre_result_id,
                        content_version_id,content_sha256,evidence_sha256,
                        model_decision,effective_decision,human_label_id)
                       VALUES (%s::uuid,%s,%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s)""",
                    (run_id,pid,row["video_id"],row["category_key"],
                     row["sre_result_id"],row["content_version_id"],
                     row["content_sha256"],row["evidence_sha256"],
                     row["model_decision"],row["effective_decision"],
                     row["human_label_id"]))
            for item in selection["qualified"]:
                conn.execute(
                    """INSERT INTO qdp_items
                       (run_id,project_id,video_id,sre_category_keys,sre_result_ids)
                       VALUES (%s::uuid,%s,%s,%s::jsonb,%s::jsonb)""",
                    (run_id, pid, item["video_id"], _json(item["category_keys"]),
                     _json(item["result_ids"])))
                conn.execute(
                    """INSERT INTO jobs(job_type,idempotency_key,payload,max_attempts)
                       VALUES (%s,%s,%s::jsonb,4)""",
                    (JOB_TYPE,"qdp:" + run_id + ":" + str(item["video_id"]),
                     _json({"project_id":pid,"run_id":run_id,"video_id":item["video_id"]})))
            # All jobs for this run are already committed in this transaction
            # or none are, including an explicit zero-qualified no-work run.
            count = conn.execute(
                """SELECT count(*) AS n FROM jobs
                   WHERE job_type=%s AND payload->>'run_id'=%s""",
                (JOB_TYPE,run_id)).fetchone()["n"]
            if count != selection["totals"]["qualified"]:
                raise QDPError("qdp_job_count_mismatch",
                               "Atomic QDP preparation job count mismatch", 409)
            conn.execute(
                """INSERT INTO qdp_events(run_id,project_id,event_code,details)
                   VALUES (%s::uuid,%s,'qualified_set_created',%s::jsonb)""",
                (run_id,pid,_json(selection["totals"])))
            from .worker_hardening import append_event
            append_event(conn,project=pid,run_id=run_id,
                         event_type="qualified_set_created",
                         state="queued" if selection["qualified"] else "completed")
            if not selection["qualified"]:
                conn.execute("UPDATE qdp_runs SET finished_at=now() WHERE id=%s::uuid",
                             (run_id,))
                append_event(conn,project=pid,run_id=run_id,
                             event_type="run_completed",state="completed")
            return {"ok": True, "run_id": run_id, "replayed": False,
                    "totals": selection["totals"], "status": "queued" if selection["qualified"] else "completed"}


def _authorized_metadata(conn, project, video_id, pinned):
    """Exact pinned SRE S3 observation only; never latest global D1 metadata."""
    from .provenance import read_exact_metadata
    return read_exact_metadata(conn, project, video_id, pinned)


def _authorized_metrics(conn, metadata):
    """Existing internal port: read only validated, bounded metric history."""
    from .metric_history import read_metric_history
    return read_metric_history(conn, metadata)


def _authorized_media(conn, metadata):
    """FIX-09 currently authorized local file proof, split A/B readiness."""
    from .media_readiness import read_verified_media
    return read_verified_media(conn, metadata)


def _score_config(conn):
    return conn.execute(
        """SELECT id FROM metric_calculation_configs
           WHERE is_active AND alpha IS NOT NULL AND bayesian_k IS NOT NULL
           ORDER BY id DESC LIMIT 1""").fetchone()


def _effective_expiry(metadata):
    from datetime import timedelta
    expiry = metadata["expires_at"]
    policy_days = metadata["retention_policy"].get("retention_days")
    if policy_days is not None and (type(policy_days) is not int or policy_days < 1):
        raise QDPError("invalid_retention_policy", "Unverifiable source retention policy", 409)
    for days in (metadata["retention_days"], policy_days):
        if days is not None:
            limited = metadata["fetched_at"] + timedelta(days=days)
            expiry = min(expiry, limited) if expiry else limited
    return expiry


def _update_run_state(conn, run_id):
    """Run terminal state reflects BOTH prepared items and exhausted jobs."""
    counts = conn.execute(
        """SELECT count(*) FILTER (WHERE status='queued') AS pending,
                  count(*) FILTER (WHERE status='ready') AS ready,
                  count(*) FILTER (WHERE status='partial') AS partial,
                  count(*) FILTER (WHERE status='blocked') AS blocked,
                  count(*) FILTER (WHERE status='cancelled') AS cancelled
           FROM qdp_items WHERE run_id=%s::uuid""", (run_id,)).fetchone()
    run=conn.execute(
        "SELECT project_id,cancel_requested,status FROM qdp_runs WHERE id=%s::uuid FOR UPDATE",
        (run_id,)).fetchone()
    if not run:
        raise QDPError("run_not_found", "QDP run does not exist", 404)
    job_counts=conn.execute(
        """SELECT count(*) FILTER (WHERE state IN ('queued','running')) AS active,
                  count(*) FILTER (WHERE state='failed') AS exhausted
           FROM jobs WHERE job_type=%s AND payload->>'run_id'=%s""",
        (JOB_TYPE,run_id)).fetchone()
    if run["cancel_requested"]:
        state="cancelled"
    elif counts["pending"] or job_counts["active"]:
        state="running"
    elif job_counts["exhausted"]:
        state="partial" if (counts["ready"] or counts["partial"]) else "failed"
    elif counts["partial"] or counts["blocked"] or counts["cancelled"]:
        state="partial"
    else:
        state="completed"
    before=run["status"]
    conn.execute(
        """UPDATE qdp_runs SET status=%s,updated_at=now(),
           finished_at=CASE WHEN %s IN ('completed','partial','failed','cancelled')
                              THEN COALESCE(finished_at,now()) ELSE NULL END
           WHERE id=%s::uuid""",(state,state,run_id))
    if state in ("completed","partial") and before not in ("completed","partial"):
        from .worker_hardening import append_event
        append_event(conn,project=run["project_id"],run_id=run_id,
                     event_type="run_completed",state=state)
    return state

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
        """SELECT status,sre_category_keys,sre_result_ids FROM qdp_items
           WHERE run_id=%s::uuid AND project_id=%s AND video_id=%s FOR UPDATE""",
        (run_id,pid,vid)).fetchone()
    if not item:
        raise QDPError("item_not_found", "QDP item missing", 404)
    if run["cancel_requested"]:
        status, reason = "cancelled", "run_cancelled"
    elif conn.execute(
        """SELECT 1 FROM qdp_invalidations
           WHERE project_id=%s AND run_id=%s::uuid
             AND (video_id IS NULL OR video_id=%s)
           LIMIT 1""",(pid,run_id,vid)).fetchone():
        # A past run may never resurrect after a trusted revocation.
        status, reason = "blocked", "preparation_invalidated"
    elif item["status"] != "queued":
        status, reason = item["status"], "already_processed"
    else:
        # Historical preparation cannot silently change its selected labels,
        # SRE content/source hash or effective human-review snapshot.
        original = read_pinned_video(conn, pid, run_id, vid)
        source, selection = _sre_snapshot(conn, pid, str(run["sre_run_id"]), only_video_id=vid)
        current_details = selection["details"][0] if selection["details"] else None
        still = next((x for x in selection["qualified"] if x["video_id"] == vid), None)
        if (source["taxonomy_version_id"] != run["taxonomy_version_id"] or
                still is None or
                still["category_keys"] != list(item["sre_category_keys"]) or
                still["result_ids"] != list(item["sre_result_ids"]) or
                original["selection_sha256"] != (current_details or {}).get("selection_sha256")):
            # A human veto of H1 while H2 stays positive is still qualified,
            # but the historical input selection is no longer the same run.
            status, reason = "blocked", "sre_evidence_revoked_or_changed"
        else:
            metadata, metadata_reason = _authorized_metadata(conn, pid, vid, original)
            if metadata is None:
                status, reason = "blocked", metadata_reason
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
                        metric_data = _authorized_metrics(conn, metadata)
                        media = _authorized_media(conn, metadata)
                        asset = media["asset"]
                        config = _score_config(conn)
                        metric_ids = metric_data["metric_snapshot_ids"]
                        c_ids = metric_data["growth_pair_ids"]
                        growth_def = metric_data["growth_definition"]
                        definitions = ([growth_def["name"] + "@" + growth_def["version"]] * 2
                                       if growth_def is not None else [])
                        from .preparation import build_preparations
                        preparations = build_preparations(
                            metadata, original, media=media, metrics=metric_data,
                            score_config_id=config["id"] if config else None,
                            scoring_allowed=(
                                metadata["can_derive_scores"] and
                                metadata["permissions"].get("can_derive_scores") is True and
                                metadata["can_store_metrics"] and
                                metadata["permissions"].get("can_store_metrics") is True))
                        decisions = {g:{"state":p["state"],"reason_code":p["reason_code"]}
                                     for g,p in preparations.items()}
                        meta = {k:metadata[k] for k in (
                            "platform","platform_video_id","canonical_url",
                            "title","description","creator_name","published_at",
                            "duration_seconds","fetched_at","content_kind",
                            "sre_content_version_id","sre_content_sha256",
                            "metadata_observation_id","discovery_id",
                            "provenance_schema")}
                        meta["verified_media"] = (
                            {key:asset[key] for key in ("id","sha256","media_kind","size_bytes")}
                            if asset else None)
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
                        from .worker_hardening import append_event
                        for group, prepared in preparations.items():
                            inputs=prepared["inputs"]
                            conn.execute(
                                """INSERT INTO qdp_preparations
                                   (run_id,project_id,video_id,analysis_group,state,reason_code,
                                    inputs,input_sha256)
                                   VALUES (%s::uuid,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                                (run_id,pid,vid,group,prepared["state"],
                                 prepared["reason_code"],_json(inputs),
                                 prepared["input_sha256"]))
                            append_event(
                                conn,project=pid,run_id=run_id,video_id=vid,
                                group=group,
                                event_type="preparation_ready"
                                  if prepared["state"]=="ready"
                                  else "preparation_blocked",
                                reason=prepared["reason_code"],
                                inputs_hash=prepared["input_sha256"],
                                state=prepared["state"])
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
    completed=conn.execute(
        """UPDATE jobs SET state='completed',result=%s::jsonb,locked_by=NULL,
           lease_until=NULL,updated_at=now()
           WHERE id=%s AND job_type=%s AND state='running'
             AND locked_by=%s AND lease_generation=%s
             AND lease_until>now() RETURNING id""",
        (_json({"status":status,"reason":reason}),job_id,JOB_TYPE,worker_id,generation)
    ).fetchone()
    if not completed:
        raise QDPError("lease_fence_lost", "Worker lease was lost; rollback all facts",409)
    _update_run_state(conn,run_id)
    return {"state":status,"run_id":run_id,"video_id":vid,"reason":reason}


def work_once(*, connect=None, worker_id=None):
    """One QDP-only leased job. Claim/prepare/ACK are separate DB txns.

    Never touches another module's job. Prepare and all group/outbox writes
    commit atomically; retry after interruption cannot publish half a video.
    """
    worker_id = worker_id or "qdp-" + uuid.uuid4().hex[:12]
    factory=connect or _connect
    with factory() as conn:
        with conn.transaction():
            from .worker_hardening import reap_exhausted
            reap_exhausted(conn)
            selected=conn.execute(
                """SELECT id FROM jobs WHERE job_type=%s
                     AND attempts<max_attempts
                     AND ((state='queued' AND run_after<=now())
                      OR (state='running' AND lease_until<now()))
                   ORDER BY priority DESC,created_at,id
                   FOR UPDATE SKIP LOCKED LIMIT 1""",(JOB_TYPE,)).fetchone()
            if not selected:
                return None
            job=conn.execute(
                """UPDATE jobs SET state='running',locked_by=%s,
                   lease_until=now()+(%s*interval '1 second'),
                   attempts=attempts+1,lease_generation=lease_generation+1,
                   updated_at=now() WHERE id=%s AND job_type=%s
                   RETURNING id,payload,attempts,max_attempts,lease_generation""",
                (worker_id,WORKER_LEASE_SECONDS,selected["id"],JOB_TYPE)).fetchone()
        try:
            with conn.transaction():
                return _process_locked(conn,job,worker_id)
        except Exception as exc:
            from .worker_hardening import retry_delay_seconds
            with conn.transaction():
                # A worker holding stale generation cannot requeue another
                # worker's lease, write a result, or change its run state.
                retry_seconds=retry_delay_seconds(job["attempts"])
                updated=conn.execute(
                    """UPDATE jobs SET
                       state=CASE WHEN attempts>=max_attempts THEN 'failed'
                                  ELSE 'queued' END,
                       run_after=now()+(%s*interval '1 second'),
                       last_error=%s,locked_by=NULL,lease_until=NULL,updated_at=now()
                       WHERE id=%s AND job_type=%s AND state='running'
                         AND locked_by=%s AND lease_generation=%s
                       RETURNING state,payload""",
                    (retry_seconds,type(exc).__name__,
                     job["id"],JOB_TYPE,worker_id,job["lease_generation"])
                ).fetchone()
                if not updated:
                    return {"state":"lost_lease","job_id":job["id"]}
                payload=updated["payload"]
                rid,pid,vid=(payload.get("run_id"),payload.get("project_id"),
                             payload.get("video_id"))
                if updated["state"]=="failed" and rid and pid and type(vid) is int:
                    state=conn.execute(
                        """UPDATE qdp_items SET status='blocked',
                           reason_code='worker_attempts_exhausted',
                           updated_at=now()
                           WHERE run_id=%s::uuid AND project_id=%s
                             AND video_id=%s AND status='queued'
                           RETURNING video_id""",(rid,pid,vid)).fetchone()
                    if state:
                        conn.execute(
                            """INSERT INTO qdp_events
                               (run_id,project_id,video_id,event_code,details)
                               VALUES (%s::uuid,%s,%s,'item_blocked',%s::jsonb)""",
                            (rid,pid,vid,
                             _json({"reason":"worker_attempts_exhausted"})))
                if rid:
                    _update_run_state(conn,rid)
            return {"state":"retry_or_failed","job_id":job["id"],
                    "error_code":type(exc).__name__,
                    "retry_delay_seconds":retry_seconds}


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
                      EXISTS(SELECT 1 FROM qdp_invalidations inv
                             WHERE inv.run_id=i.run_id AND inv.project_id=i.project_id
                               AND (inv.video_id IS NULL OR inv.video_id=i.video_id))
                         AS explicitly_invalidated,
                      EXISTS(SELECT 1 FROM qdp_enrichments raw
                             WHERE raw.run_id=i.run_id AND raw.video_id=i.video_id)
                        AS snapshot_exists,
                      e.source_id,e.metadata_id,e.metadata,
                      e.metrics,e.media_asset_id
               FROM qdp_items i
               LEFT JOIN qdp_current_base_enrichments e
                  ON e.run_id=i.run_id AND e.project_id=i.project_id
                 AND e.video_id=i.video_id
               WHERE i.run_id=%s::uuid AND i.project_id=%s
               ORDER BY i.video_id LIMIT %s""",(rid,pid,limit)).fetchall()
        preparation=conn.execute(
            """SELECT p.video_id,p.analysis_group,p.state,p.reason_code,
                      (live.run_id IS NOT NULL) AS currently_readable
               FROM qdp_preparations p
               LEFT JOIN qdp_group_current_preparations live
                 ON live.run_id=p.run_id AND live.project_id=p.project_id
                AND live.video_id=p.video_id
                AND live.analysis_group=p.analysis_group
               WHERE p.run_id=%s::uuid AND p.project_id=%s
                 AND p.video_id IN
                   (SELECT video_id FROM qdp_items WHERE run_id=%s::uuid
                    ORDER BY video_id LIMIT %s)""",
            (rid,pid,rid,limit)).fetchall()
        states={}
        for p in preparation:
            current = (p["state"]=="ready" and p["currently_readable"])
            states.setdefault(p["video_id"],{})[p["analysis_group"]]={
                "state":"ready" if current else "blocked",
                "reason_code":None if current else (
                    p["reason_code"] if p["state"]=="blocked"
                    else "preparation_current_rights_revoked")}
        items=[]
        for item in rows:
            readable=item["enrichment_readable"]
            revoked=item["snapshot_exists"] and not readable
            invalidated=item["explicitly_invalidated"]
            items.append({
                "video_id":item["video_id"],
                "status":"blocked" if revoked or invalidated else item["status"],
                "reason_code":("preparation_invalidated" if invalidated else
                               "source_policy_inactive_or_expired" if revoked else
                               item["reason_code"]),
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
