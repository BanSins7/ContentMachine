"""Group A stage A1: explicit, QDP-scoped local execution and PostgreSQL facts.

Safe-by-default boundaries:
- only QDP Group A = ready, same project + video + media source
- approved current provider policy and unexpired, non-metadata-only media
- no external HTTP/provider/LLM or auto-download
- PostgreSQL job fencing; no DB transaction held while FFmpeg decodes
- repeat live-rights check immediately before writing the feature schema
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal
from .contracts import (
    GROUP, JOB_TYPE, METHOD, GroupAError, TechnicalMeasurement, canonical_hash,
    parse_config, positive_int
)
from .extractor import authorized_file, measure
from .capabilities import require_live_handoff


def _connect():
    from intelligence.config import dsn, enabled
    if not enabled() or not dsn():
        raise GroupAError("db_unavailable","PostgreSQL is not configured",503)
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(dsn(),autocommit=True,connect_timeout=5,
                           row_factory=dict_row)


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,default=str)

def _wire(value):
    """Convert PostgreSQL NUMERIC/date/UUID into JSON-serializable exact values."""
    if isinstance(value,dict):
        return {key:_wire(item) for key,item in value.items()}
    if isinstance(value,(list,tuple)):
        return [_wire(item) for item in value]
    if isinstance(value,Decimal):
        return str(value)
    if isinstance(value,(datetime,uuid.UUID)):
        return str(value)
    return value


def _uuid(raw):
    try:
        value=str(uuid.UUID(str(raw)))
        if value != str(raw):
            raise ValueError()
        return value
    except (ValueError,TypeError,AttributeError) as exc:
        raise GroupAError("invalid_id","Invalid canonical UUID") from exc


def _project(value):
    import re
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{32}",value) is None:
        raise GroupAError("invalid_project","Invalid project ID")
    return value


def _eligible(conn,project,run,video):
    """Authoritative current rights and exact prepared QDP lineage.

    Never rely on a browser's 'ready', on metadata-only permission, on a stored
    Group A status after revocation, or on a media_asset from another video.
    """
    return conn.execute(
        """SELECT q.source_id,q.source_policy_version_id,q.media_asset_id,
                  p.input_sha256 AS qdp_hash,t.local_path,
                  t.media_kind,t.expires_at,v.content_kind,s.status AS source_status
           FROM qdp_items i
           JOIN qdp_readable_enrichments q
             ON q.run_id=i.run_id AND q.project_id=i.project_id
            AND q.video_id=i.video_id
           JOIN qdp_preparations p
             ON p.run_id=i.run_id AND p.project_id=i.project_id
            AND p.video_id=i.video_id AND p.analysis_group='A' AND p.state='ready'
           JOIN videos v ON v.id=i.video_id
           JOIN media_assets t
             ON t.id=q.media_asset_id AND t.video_id=i.video_id
            AND t.source_id=q.source_id AND t.permission<>'metadata_only'
            AND t.media_kind='video'
            AND (t.expires_at IS NULL OR t.expires_at>now())
           JOIN sources s ON s.id=q.source_id AND s.status='enabled'
            AND s.can_analyze_media
           JOIN source_policy_versions sp
             ON sp.id=q.source_policy_version_id AND sp.source_id=s.id
            AND sp.is_active AND sp.permissions->>'can_analyze_media'='true'
           WHERE i.project_id=%s AND i.run_id=%s::uuid AND i.video_id=%s
             AND i.status IN ('ready','partial')
             AND v.content_kind IN ('video','slideshow_video')""",
        (project,run,video)).fetchone()


def create_task(project, qdp_run_id, video_id, config=None, *, connect=None):
    require_live_handoff()
    pid,rid,vid=_project(project),_uuid(qdp_run_id),positive_int(video_id,"video ID")
    cfg=parse_config(config)
    with (connect or _connect)() as conn:
        with conn.transaction():
            row=_eligible(conn,pid,rid,vid)
            if not row:
                raise GroupAError("not_ready","Group A requires current authorized QDP readiness",409)
            fingerprint=canonical_hash({
                "method":METHOD,"config":cfg,"project_id":pid,"run_id":rid,
                "video_id":vid,"source_id":row["source_id"],
                "source_policy_version_id":row["source_policy_version_id"],
                "media_asset_id":row["media_asset_id"],"qdp_input":row["qdp_hash"]
            })
            prev=conn.execute(
                """SELECT id,status,job_id FROM group_a_tasks
                   WHERE qdp_run_id=%s::uuid AND video_id=%s AND input_sha256=%s
                   FOR UPDATE""",(rid,vid,fingerprint)).fetchone()
            if prev:
                return {"ok":True,"task_id":str(prev["id"]),
                        "status":prev["status"],"replayed":True}
            task=conn.execute(
                """INSERT INTO group_a_tasks
                   (project_id,qdp_run_id,video_id,media_asset_id,source_id,
                    source_policy_version_id,qdp_input_sha256,input_sha256,config)
                   VALUES (%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s::jsonb)
                   RETURNING id""",
                (pid,rid,vid,row["media_asset_id"],row["source_id"],
                 row["source_policy_version_id"],row["qdp_hash"],fingerprint,
                 _json(cfg))).fetchone()
            task_id=str(task["id"])
            job=conn.execute(
                """INSERT INTO jobs(job_type,idempotency_key,payload,max_attempts)
                   VALUES (%s,%s,%s::jsonb,3) RETURNING id""",
                (JOB_TYPE,"group-a:"+task_id,
                 _json({"task_id":task_id,"project_id":pid}))).fetchone()
            conn.execute(
                "UPDATE group_a_tasks SET job_id=%s WHERE id=%s::uuid",
                (job["id"],task_id))
            return {"ok":True,"task_id":task_id,"status":"queued","replayed":False}


def _task(conn,task_id,pid,*,lock=False):
    suffix=" FOR UPDATE" if lock else ""
    return conn.execute(
        """SELECT * FROM group_a_tasks WHERE id=%s::uuid AND project_id=%s""" + suffix,
        (task_id,pid)).fetchone()


def _claim(conn,worker_id):
    with conn.transaction():
        # Expired final leases cannot silently remain running forever.
        for job in conn.execute(
            """UPDATE jobs SET state='failed',locked_by=NULL,lease_until=NULL,
               last_error='Group A lease exhausted',updated_at=now()
               WHERE job_type=%s AND state='running' AND lease_until<now()
                 AND attempts>=max_attempts RETURNING payload""",
            (JOB_TYPE,)).fetchall():
            value=job["payload"].get("task_id")
            if value:
                conn.execute(
                    """UPDATE group_a_tasks SET status='failed',
                       error_code='lease_exhausted',finished_at=now(),updated_at=now()
                       WHERE id=%s::uuid AND status IN ('queued','running')""",
                    (value,))
        candidate=conn.execute(
            """SELECT id FROM jobs WHERE job_type=%s
               AND ((state='queued' AND run_after<=now())
                OR (state='running' AND lease_until<now() AND attempts<max_attempts))
               ORDER BY priority DESC,created_at,id
               FOR UPDATE SKIP LOCKED LIMIT 1""",(JOB_TYPE,)).fetchone()
        if not candidate:
            return None
        return conn.execute(
            """UPDATE jobs SET state='running',locked_by=%s,
               attempts=attempts+1,lease_generation=lease_generation+1,
               lease_until=now()+interval '300 seconds',updated_at=now()
               WHERE id=%s RETURNING id,payload,lease_generation,attempts,max_attempts""",
            (worker_id,candidate["id"])).fetchone()


def _live_lease(conn,job,worker_id):
    return conn.execute(
        """SELECT id FROM jobs WHERE id=%s AND state='running' AND locked_by=%s
           AND lease_generation=%s AND lease_until>now() FOR UPDATE""",
        (job["id"],worker_id,job["lease_generation"])).fetchone()


def _finish_job(conn,job,worker_id,status,reason=None):
    updated=conn.execute(
        """UPDATE jobs SET state='completed',locked_by=NULL,lease_until=NULL,
           result=%s::jsonb,updated_at=now()
           WHERE id=%s AND state='running' AND locked_by=%s AND lease_generation=%s
           RETURNING id""",
        (_json({"status":status,"reason":reason}),
         job["id"],worker_id,job["lease_generation"])).fetchone()
    if not updated:
        raise GroupAError("lease_lost","Job lease lost",409)


def _finalize(conn,job,worker_id,measurement=None,error=None):
    task_id=_uuid(job["payload"]["task_id"])
    pid=_project(job["payload"]["project_id"])
    with conn.transaction():
        if not _live_lease(conn,job,worker_id):
            return {"status":"lost_lease","task_id":task_id}
        task=_task(conn,task_id,pid,lock=True)
        if not task:
            _finish_job(conn,job,worker_id,"cancelled","task_missing")
            return {"status":"cancelled","task_id":task_id}
        if task["status"] in ("completed","blocked","failed","cancelled"):
            _finish_job(conn,job,worker_id,task["status"],task["error_code"])
            return {"status":task["status"],"task_id":task_id}
        live=_eligible(conn,pid,str(task["qdp_run_id"]),task["video_id"])
        if (not live or live["source_id"]!=task["source_id"]
            or live["source_policy_version_id"]!=task["source_policy_version_id"]
            or live["media_asset_id"]!=task["media_asset_id"]
            or live["qdp_hash"]!=task["qdp_input_sha256"]):
            status,reason="blocked","source_policy_or_qdp_revoked"
        elif error is not None:
            status,reason="blocked",error.code
        elif measurement is None:
            status,reason="failed","observation_missing"
        else:
            # Exact measured data, never synthetic 'completed' 14-feature coverage.
            analysis_key="group-a:"+task_id
            info={"version":METHOD,"scene_detector":"ffmpeg select(scene)",
                  "threshold":task["config"]["scene_threshold"],
                  "min_gap_sec":task["config"]["min_scene_gap_sec"],
                  "geometry":"ffprobe_display_geometry","external_requests":0}
            absent={"speech":"not_run_in_A1","screen_text":"not_run_in_A1",
                    "audio":"not_run_in_A1","subtitles":"not_measured_in_A1",
                    "cta":"requires_evidence_from_other_stage"}
            row=conn.execute(
                """INSERT INTO video_feature_a_analyses
                   (video_id,source_id,source_policy_version_id,media_asset_id,
                    analysis_key,analysis_version,method_name,method_version,
                    state,scene_status,speech_status,screen_text_status,audio_status,
                    measured_at,duration_sec,aspect_ratio,measurement_methods,unavailable_reasons)
                   VALUES (%s,%s,%s,%s,%s,'A1',%s,%s,'partial','complete',
                    'not_run','not_run','not_run',now(),%s,%s,%s::jsonb,%s::jsonb)
                   ON CONFLICT(analysis_key) DO NOTHING RETURNING id""",
                (task["video_id"],task["source_id"],task["source_policy_version_id"],
                 task["media_asset_id"],analysis_key,METHOD,METHOD,
                 measurement.duration_sec,measurement.aspect_ratio,
                 _json(info),_json(absent))).fetchone()
            if row is None:
                row=conn.execute(
                    "SELECT id FROM video_feature_a_analyses WHERE analysis_key=%s",
                    (analysis_key,)).fetchone()
            analysis_id=row["id"]
            if measurement.scenes and row:
                # A unique analysis_key makes replay safe: scene rows are never changed.
                existing=conn.execute(
                    "SELECT count(*) AS n FROM video_feature_a_scenes WHERE analysis_id=%s",
                    (analysis_id,)).fetchone()["n"]
                if existing not in (0,len(measurement.scenes)):
                    raise GroupAError("inconsistent_analysis","Existing technical analysis is incomplete",409)
                if existing==0:
                    for scene in measurement.scenes:
                        conn.execute(
                            """INSERT INTO video_feature_a_scenes
                               (analysis_id,scene_index,start_sec,end_sec,measurement_method)
                               VALUES (%s,%s,%s,%s,'ffmpeg_scene_select')""",
                            (analysis_id,scene.index,scene.start_sec,scene.end_sec))
            conn.execute(
                """UPDATE group_a_tasks SET status='completed',analysis_id=%s,
                   error_code=NULL,finished_at=now(),updated_at=now()
                   WHERE id=%s::uuid""",(analysis_id,task_id))
            _finish_job(conn,job,worker_id,"completed")
            return {"status":"completed","task_id":task_id,
                    "analysis_id":analysis_id,"scene_count":len(measurement.scenes),
                    "analysis_state":"partial"}
        conn.execute(
            """UPDATE group_a_tasks SET status=%s,error_code=%s,
               finished_at=now(),updated_at=now() WHERE id=%s::uuid""",
            (status,reason,task_id))
        _finish_job(conn,job,worker_id,status,reason)
        return {"status":status,"reason":reason,"task_id":task_id}


def _retry(conn,job,worker_id):
    task_id=_uuid(job["payload"]["task_id"])
    pid=_project(job["payload"]["project_id"])
    with conn.transaction():
        if not _live_lease(conn,job,worker_id):
            return {"status":"lost_lease","task_id":task_id}
        exhausted=job["attempts"]>=job["max_attempts"]
        conn.execute(
            """UPDATE jobs SET state=%s,locked_by=NULL,lease_until=NULL,
               run_after=now()+interval '45 seconds',last_error='extractor_unavailable',
               updated_at=now() WHERE id=%s AND locked_by=%s AND lease_generation=%s""",
            ("failed" if exhausted else "queued",job["id"],worker_id,
             job["lease_generation"]))
        if exhausted:
            conn.execute(
                """UPDATE group_a_tasks SET status='failed',error_code='extractor_unavailable',
                   finished_at=now(),updated_at=now()
                   WHERE id=%s::uuid AND project_id=%s AND status IN ('queued','running')""",
                (task_id,pid))
        return {"status":"failed" if exhausted else "retry","task_id":task_id}


def work_once(*,connect=None,worker_id=None,extractor=measure,roots=None):
    """One job: external subprocess *outside* DB transactions, then fenced insert."""
    require_live_handoff()
    worker_id=worker_id or "group-a-"+uuid.uuid4().hex[:12]
    factory=connect or _connect
    with factory() as conn:
        job=_claim(conn,worker_id)
        if job is None:
            return None
        task_id=_uuid(job["payload"]["task_id"])
        pid=_project(job["payload"]["project_id"])
        try:
            task=_task(conn,task_id,pid)
            if not task:
                return _finalize(conn,job,worker_id,
                                 error=GroupAError("task_missing","Task no longer exists",409))
            live=_eligible(conn,pid,str(task["qdp_run_id"]),task["video_id"])
            if live is None or live["media_asset_id"]!=task["media_asset_id"]:
                return _finalize(conn,job,worker_id,
                                 error=GroupAError("rights_revoked","Media access revoked",409))
            path=authorized_file(live["local_path"],roots=roots)
            measured=extractor(path,task["config"])
            if not isinstance(measured,TechnicalMeasurement):
                raise GroupAError("invalid_measurement","Extractor returned an invalid contract")
            return _finalize(conn,job,worker_id,measurement=measured)
        except GroupAError as exc:
            if exc.code in ("extractor_unavailable","tool_not_installed"):
                return _retry(conn,job,worker_id)
            return _finalize(conn,job,worker_id,error=exc)
        except Exception:
            # No media paths/credentials or provider errors in durable job logs.
            return _retry(conn,job,worker_id)


def task_details(project,task_id,*,connect=None):
    require_live_handoff()
    pid,ident=_project(project),_uuid(task_id)
    with (connect or _connect)() as conn:
        task=_task(conn,ident,pid)
        if not task:
            raise GroupAError("not_found","Group A task not found",404)
        result=conn.execute(
            "SELECT * FROM group_a_readable_results WHERE task_id=%s::uuid AND project_id=%s",
            (ident,pid)).fetchone()
        scenes=[]
        if result is not None:
            scenes=conn.execute(
                """SELECT scene_index,start_sec,end_sec FROM video_feature_a_scenes
                   WHERE analysis_id=%s ORDER BY scene_index""",
                (result["analysis_id"],)).fetchall()
        revoked=(task["status"]=="completed" and result is None)
        return {"ok":True,"task_id":ident,
                "status":"blocked" if revoked else task["status"],
                "error_code":"rights_revoked_or_snapshot_expired" if revoked else task["error_code"],
                "project_id":pid,"qdp_run_id":str(task["qdp_run_id"]),
                "video_id":task["video_id"],"analysis":_wire(dict(result)) if result else None,
                "scenes":_wire(scenes)}


def list_tasks(project,run_id,*,connect=None,limit=100):
    require_live_handoff()
    pid,rid=_project(project),_uuid(run_id)
    if type(limit) is not int or not 1<=limit<=200:
        raise GroupAError("invalid_limit","Limit must be 1..200")
    with (connect or _connect)() as conn:
        rows=conn.execute(
            """SELECT t.id,t.video_id,t.status,t.error_code,t.created_at,
                  (r.task_id IS NOT NULL) AS readable
               FROM group_a_tasks t LEFT JOIN group_a_readable_results r
                 ON r.task_id=t.id
               WHERE t.project_id=%s AND t.qdp_run_id=%s::uuid
               ORDER BY t.created_at DESC,t.id DESC LIMIT %s""",
            (pid,rid,limit)).fetchall()
        return {"ok":True,"tasks":[
            {"task_id":str(r["id"]),"video_id":r["video_id"],
             "status":"blocked" if r["status"]=="completed" and not r["readable"]
                      else r["status"],
             "error_code":"rights_revoked_or_snapshot_expired"
               if r["status"]=="completed" and not r["readable"] else r["error_code"],
             "created_at":str(r["created_at"])} for r in rows]}
