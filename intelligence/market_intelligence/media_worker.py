"""Standalone MI media analyzer, no imports from Group A/B/C.

Uses canonical project-acquired, hashed local media with explicit project
analyze rights. Reads optional SHA-verified SRT from the SAME acquisition
version. Never downloads media or invents vision/speech labels.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from intelligence.discovery import service as d1
from .contracts import MarketError, digest
from .media_guard import verified_media, authorized_media
from .replica_media import analyze_media
from .creative import analyze_captions, parse_srt

JOB_TYPE="market.media_analyze.v1"
METHOD="mi-abc-b8-native-media/v4"
_lock=threading.Lock()
_thread=None
_workers=[]

ASSETS_SQL="""
SELECT v.id AS video_id, pvr.source_id,pvr.result_source_version_id,
       pvr.video_asset_id, pvr.video_sha256, pa.storage_ref
FROM project_video_acquisition_runs pvr
JOIN source_extraction_jobs j ON j.project_id=pvr.project_id
     AND j.id=pvr.job_id AND j.state IN ('completed','partial')
JOIN project_sources ps ON ps.project_id=pvr.project_id AND ps.id=pvr.source_id
     AND ps.tombstoned_at IS NULL AND ps.can_analyze AND ps.can_store
JOIN project_media_assets pa ON pa.project_id=pvr.project_id
     AND pa.id=pvr.video_asset_id AND pa.asset_kind='video'
     AND pa.content_sha256=pvr.video_sha256
JOIN videos v ON v.platform=pvr.platform AND v.platform_video_id=pvr.native_id
WHERE pvr.project_id=%s AND pvr.video_asset_id IS NOT NULL
  AND EXISTS (SELECT 1 FROM project_sources src WHERE src.project_id=pvr.project_id
       AND src.id=ps.id AND src.can_fetch AND src.can_store AND src.can_analyze)
  AND (
    EXISTS (SELECT 1 FROM project_sources ref
      WHERE ref.project_id=pvr.project_id AND ref.tombstoned_at IS NULL
        AND ref.source_type='user_video_reference' AND ref.platform=v.platform
        AND ref.platform_native_id=v.platform_video_id)
    OR EXISTS (SELECT 1 FROM mi_author_publications publication
      JOIN project_sources ref ON ref.project_id=publication.project_id
        AND ref.canonical_url=publication.profile_url
        AND ref.link_kind='account' AND ref.tombstoned_at IS NULL
      WHERE publication.project_id=pvr.project_id AND publication.video_id=v.id)
  )
  AND (%s::bigint[] IS NULL OR v.id = ANY(%s::bigint[]))
ORDER BY v.id LIMIT 251
"""

def queue_available(project_id: str, video_ids: list[int] | None = None) -> dict:
    """Queue only approved project assets, optionally scoped to a pinned batch."""
    if video_ids is not None and (type(video_ids) is not list or
        not 1 <= len(video_ids) <= 100 or any(type(i) is not int or i <= 0 for i in video_ids)):
        raise MarketError("invalid_media_scope", "Ожидается список из 1–100 ID видео.")
    queued=[];already=0
    with d1.connect() as conn:
        with conn.transaction():
            records=conn.execute(ASSETS_SQL,(project_id,video_ids,video_ids)).fetchall()
            truncated=len(records)>250
            for video_id,sid,version,asset_id,sha,local_path in records[:250]:
                key="mi.media."+digest([project_id,video_id,sha,METHOD])[:52]
                payload={"project_id":project_id,"video_id":video_id,
                         "source_id":str(sid),"source_version_id":str(version),
                         "asset_id":str(asset_id),"sha256":sha}
                result=conn.execute("""
                   INSERT INTO jobs(job_type,idempotency_key,payload,max_attempts)
                   VALUES (%s,%s,%s::jsonb,1)
                   ON CONFLICT(idempotency_key) DO NOTHING RETURNING id
                """,(JOB_TYPE,key,json.dumps(payload))).fetchone()
                if result: queued.append(result[0])
                else: already+=1
    if queued:start_worker()
    return {"queued":len(queued),"already_queued":already,
            "truncated":truncated, "requires_approved_local_media":True}

def _claim():
    with d1.connect() as conn:
        with conn.transaction():
            row=conn.execute("""
                SELECT id FROM jobs
                WHERE job_type=%s AND
                  ((state='queued' AND run_after<=now())
                    OR (state='running' AND lease_until<now() AND attempts<max_attempts))
                ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1
            """,(JOB_TYPE,)).fetchone()
            if not row:
                conn.execute("""
                   UPDATE jobs SET state='failed',last_error='Media job lease expired',
                   lease_until=NULL,locked_by=NULL,updated_at=now()
                   WHERE job_type=%s AND state='running' AND
                     lease_until<now() AND attempts>=max_attempts
                """,(JOB_TYPE,))
                return None
            return conn.execute("""
                UPDATE jobs SET state='running',locked_by='mi-media',
                   lease_until=now()+interval '90 minutes',attempts=attempts+1,
                   lease_generation=lease_generation+1,updated_at=now()
                WHERE id=%s RETURNING id,payload,lease_generation
            """,(row[0],)).fetchone()

def _read_current(conn,payload):
    return conn.execute("""
       SELECT pa.storage_ref,pa.content_sha256,ps.can_send_to_llm,
           srt.storage_ref AS subtitle_path,srt.content_sha256 AS subtitle_sha
       FROM project_media_assets pa
       JOIN project_sources ps ON ps.project_id=pa.project_id
         AND ps.id=pa.source_id AND ps.tombstoned_at IS NULL
         AND ps.can_fetch AND ps.can_store AND ps.can_analyze
       JOIN project_video_acquisition_runs pvr ON pvr.project_id=pa.project_id
         AND pvr.video_asset_id=pa.id AND pvr.video_sha256=pa.content_sha256
       JOIN source_extraction_jobs j ON j.project_id=pvr.project_id
         AND j.id=pvr.job_id AND j.state IN ('completed','partial')
       LEFT JOIN LATERAL(
          SELECT storage_ref,content_sha256 FROM project_media_assets att
          WHERE att.project_id=pa.project_id AND att.source_id=pa.source_id
            AND att.source_version_id=pa.source_version_id
            AND att.asset_kind='subtitle' AND att.media_type='application/x-subrip'
          ORDER BY att.created_at DESC LIMIT 1
       ) srt ON TRUE
       WHERE pa.project_id=%s AND pa.id=%s AND pa.source_id=%s
         AND pa.source_version_id=%s AND pa.content_sha256=%s
         AND pa.asset_kind='video' AND pvr.platform=(SELECT platform FROM videos WHERE id=%s)
         AND pvr.native_id=(SELECT platform_video_id FROM videos WHERE id=%s)
    """,(payload["project_id"],payload["asset_id"],payload["source_id"],
         payload["source_version_id"],payload["sha256"],payload["video_id"],payload["video_id"])).fetchone()

def _srt(path,sha,roots,*,include_raw=False):
    if not path or not sha:
        return ([],None) if include_raw else []
    from hashlib import sha256
    file=authorized_media_sidecar(path,roots)
    raw=file.read_bytes()
    if len(raw)>500_000 or sha256(raw).hexdigest()!=sha:
        raise ValueError("subtitle_hash_mismatch")
    parsed=parse_srt(raw.decode("utf-8-sig"))
    return (parsed,raw) if include_raw else parsed

def authorized_media_sidecar(path, roots):
    from .media_guard import authorized_media
    # Use the same strict root restrictions as the video, without accepting
    # arbitrary media kinds. Explicitly require .srt; no symlinks or URLs.
    import os
    p=Path(path)
    if not p.is_absolute() or p.suffix.lower()!=".srt":raise ValueError("invalid_subtitle_path")
    resolved=p.resolve(strict=True)
    if not any(resolved.is_relative_to(Path(root).resolve(strict=True)) for root in roots):
        raise ValueError("subtitle_path_outside_roots")
    if not resolved.is_file() or resolved.stat().st_size>500_000:
        raise ValueError("invalid_subtitle")
    return resolved

def process(job, *, analyzer=None):
    job_id,payload,generation=job
    with d1.connect() as conn:
        record=_read_current(conn,payload)
    if record is None:
        raise MarketError("media_rights_unavailable","Нет разрешения анализировать медиа.",409)
    path,sha,send_to_llm,subpath,subsha=record
    import os
    from intelligence import config
    roots=[r for r in config.setting("CM_MARKET_MEDIA_ROOTS","").split(os.pathsep) if r]
    local=verified_media(path,sha,roots=roots)
    text,srt_raw=_srt(subpath,subsha,roots,include_raw=True)
    tested=analyzer(local,strict=True,roots=roots) if analyzer is not None else None
    measured,visual=analyze_media(local,sha,roots=roots,
         project_id=payload["project_id"],srt_rows=text,technical=tested,
         allow_provider=bool(send_to_llm),srt_raw=srt_raw,srt_sha=subsha)
    semantic_timed_rows=visual.get("asr_segments") or text
    creative=analyze_captions(semantic_timed_rows,
           duration=float(measured.measurement.duration_sec))
    from .group_a_report import verify_group_a_report
    group_a=visual.get("group_a_report")
    if not verify_group_a_report(group_a,sha):
        raise MarketError("group_a_report_invalid","Ошибка проверки отчёта Group A.",409)
    creative["group_a"]=group_a

    inferred=visual.get("model_result") or {}
    if inferred.get("state")=="model_observed_partial":
        for key in ("format_family","hook_mechanisms","hook_text",
                    "hook_visual_description","story_roles"):
            if inferred.get(key) is not None:
                creative[key]=inferred[key]
        creative["state"]="model_observed_partial"
        creative["cta_type"]=inferred.get("cta_type")
        creative["cta_text"]=inferred.get("cta_text")
        creative["visual_subjects"]=inferred.get("visual_subjects",[])
        creative["frame_evidence_refs"]=inferred.get("evidence_frame_refs",[])
        creative["model_id"]=inferred.get("model")
        creative["provider_response_id"]=inferred.get("provider_response_id")
    from .extended_b import assemble_b1_b8
    creative["group_b"]=assemble_b1_b8(
        visual=visual,inferred=inferred,measurements=measured,
        transcript=semantic_timed_rows,extras=visual.get("b_extra_result"))
    from .group_b_report import build_group_b_report,verify_group_b_report
    b_report=build_group_b_report(
        source_sha256=sha,group_a=group_a,visual=visual,
        group_b=creative["group_b"],
        duration_sec=measured.measurement.duration_sec)
    if not verify_group_b_report(b_report,sha,group_a["report_sha256"]):
        raise MarketError("group_b_report_invalid","Не прошла проверка отчёта Group B.",409)
    creative["group_b"]["native_report"]=b_report
    from .native_evidence import make_receipt,verify_receipt
    receipt=make_receipt(
        project_id=payload["project_id"],video_id=payload["video_id"],
        source_id=payload["source_id"],source_version_id=payload["source_version_id"],
        media_asset_id=payload["asset_id"],media_sha256=sha,
        duration_sec=measured.measurement.duration_sec,visual=visual,
        group_b=creative["group_b"],method=METHOD)
    if not verify_receipt(receipt,media_sha256=sha):
        raise MarketError("evidence_integrity","Не прошла проверка источников аналитики.",409)
    creative["native_evidence"]=receipt
    # No QDP, synthetic Group B receipt or external A/B/C service is involved.
    creative["visual_evidence"]={k:v for k,v in visual.items()
        if k not in ("model_result","b_extra_result")}
    creative["limitations"]=sorted(set(creative.get("limitations",[])+
       inferred.get("limitations",[]) +
       ([] if inferred.get("state")=="model_observed_partial"
        else ["visual_model_not_verified"])))
    # Defensive repeat of both rights and byte proof after FFmpeg decoding.
    verified_media(path,sha,roots=roots)
    with d1.connect() as conn:
        with conn.transaction():
            current=conn.execute("SELECT state,lease_generation FROM jobs WHERE id=%s FOR UPDATE",(job_id,)).fetchone()
            if not current or current!=("running",generation):
                raise MarketError("media_lease_lost","Задача устарела.",409)
            if _read_current(conn,payload) is None:
                raise MarketError("media_rights_revoked","Права на медиа отозваны.",409)
            row=conn.execute("""
                INSERT INTO mi_video_analyses
                (project_id,video_id,source_id,source_version_id,media_asset_id,
                 media_sha256,method_version,technical,creative)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                ON CONFLICT(project_id,video_id,media_sha256,method_version)
                DO NOTHING RETURNING id
            """,(payload["project_id"],payload["video_id"],payload["source_id"],
                 payload["source_version_id"],payload["asset_id"],sha,METHOD,
                 json.dumps(measured.to_dict()),json.dumps(creative))).fetchone()
            conn.execute("""
                UPDATE jobs SET state='completed',result=%s::jsonb,locked_by=NULL,
                   lease_until=NULL,updated_at=now()
                WHERE id=%s AND lease_generation=%s
            """,(json.dumps({"analyzed":bool(row),"creative_state":creative["state"]}),job_id,generation))
    return {"video_id":payload["video_id"],"creative_state":creative["state"]}

def run_once(*, processor=process):
    item=_claim()
    if not item:return False
    try:processor(item)
    except Exception as exc:
        job_id,payload,generation=item
        with d1.connect() as conn:
            with conn.transaction():
                conn.execute("""
                  UPDATE jobs SET state='failed',last_error=%s,lease_until=NULL,
                    locked_by=NULL,updated_at=now()
                  WHERE id=%s AND state='running' AND lease_generation=%s
                """,(str(exc)[:320],job_id,generation))
    return True

def _loop():
    while True:
        try:
            if not run_once():time.sleep(3)
        except Exception:
            time.sleep(5)

def start_worker():
    """Bound concurrent FFmpeg/LLM tasks, claim each job with PostgreSQL fencing."""
    from intelligence import config
    count=config.setting("CM_MARKET_MEDIA_WORKERS","2")
    try:
        count=max(1,min(3,int(count)))
    except (ValueError,TypeError):
        count=2
    with _lock:
        for i in range(count):
            if i>=len(_workers) or not _workers[i].is_alive():
                worker=threading.Thread(target=_loop,daemon=True,
                          name="mi-media-analytics-"+str(i))
                if i>=len(_workers):
                    _workers.append(worker)
                else:
                    _workers[i]=worker
                worker.start()

def get_status(project_id):
    with d1.connect() as conn:
        counts=conn.execute("""
            SELECT state,count(*) FROM jobs WHERE job_type=%s
              AND payload->>'project_id'=%s GROUP BY state
        """,(JOB_TYPE,project_id)).fetchall()
        measured=conn.execute("""
          SELECT count(DISTINCT video_id) AS total,
             count(DISTINCT video_id) FILTER(WHERE creative->>'state'='model_observed_partial') AS model_visual,
             count(DISTINCT video_id) FILTER(WHERE creative->'visual_evidence'->>'state'='sampled') AS sampled,
             count(DISTINCT video_id) FILTER(WHERE creative->'visual_evidence'->>'ocr_state'='partial') AS ocr
          FROM mi_video_analyses WHERE project_id=%s
        """,(project_id,)).fetchone()
    return {"ok":True,"jobs":dict(counts),"media_analyses":measured[0],
            "frame_sampled_analyses":measured[2],
            "visual_model_analyses":measured[1],
            "ocr_partial_analyses":measured[3],
            "media_processing":"only_authorized_locally_acquired_assets"}
