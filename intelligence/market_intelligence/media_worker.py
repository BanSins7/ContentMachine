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
from .technical import analyze_technical
from .creative import analyze_captions, parse_srt

JOB_TYPE="market.media_analyze.v1"
METHOD="mi-independent-media/v1"
_lock=threading.Lock()
_thread=None

ASSETS_SQL="""
SELECT v.id AS video_id, pvr.source_id,pvr.result_source_version_id,
       pvr.video_asset_id, pvr.video_sha256, pa.storage_ref
FROM project_video_acquisition_runs pvr
JOIN source_extraction_jobs j ON j.project_id=pvr.project_id
     AND j.id=pvr.job_id AND j.state='completed'
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
ORDER BY v.id LIMIT 251
"""

def queue_available(project_id: str) -> dict:
    """Queue ONLY already-acquired, rights-approved project video assets."""
    queued=[];already=0
    with d1.connect() as conn:
        with conn.transaction():
            records=conn.execute(ASSETS_SQL,(project_id,)).fetchall()
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
                   lease_until=now()+interval '15 minutes',attempts=attempts+1,
                   lease_generation=lease_generation+1,updated_at=now()
                WHERE id=%s RETURNING id,payload,lease_generation
            """,(row[0],)).fetchone()

def _read_current(conn,payload):
    return conn.execute("""
       SELECT pa.storage_ref,pa.content_sha256,
           srt.storage_ref AS subtitle_path,srt.content_sha256 AS subtitle_sha
       FROM project_media_assets pa
       JOIN project_sources ps ON ps.project_id=pa.project_id
         AND ps.id=pa.source_id AND ps.tombstoned_at IS NULL
         AND ps.can_fetch AND ps.can_store AND ps.can_analyze
       JOIN project_video_acquisition_runs pvr ON pvr.project_id=pa.project_id
         AND pvr.video_asset_id=pa.id AND pvr.video_sha256=pa.content_sha256
       JOIN source_extraction_jobs j ON j.project_id=pvr.project_id
         AND j.id=pvr.job_id AND j.state='completed'
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

def _srt(path,sha,roots):
    if not path or not sha:return []
    from hashlib import sha256
    file=authorized_media_sidecar(path,roots)
    raw=file.read_bytes()
    if len(raw)>500_000 or sha256(raw).hexdigest()!=sha:
        raise ValueError("subtitle_hash_mismatch")
    return parse_srt(raw.decode("utf-8-sig"))

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

def process(job, *, analyzer=analyze_technical):
    job_id,payload,generation=job
    with d1.connect() as conn:
        record=_read_current(conn,payload)
    if record is None:
        raise MarketError("media_rights_unavailable","Нет разрешения анализировать медиа.",409)
    path,sha,subpath,subsha=record
    import os
    roots=[r for r in os.environ.get("CM_MARKET_MEDIA_ROOTS","").split(os.pathsep) if r]
    local=verified_media(path,sha,roots=roots)
    measured=analyzer(local,strict=True,roots=roots)
    text=_srt(subpath,subsha,roots)
    creative=analyze_captions(text,duration=float(measured.measurement.duration_sec))
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
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread=threading.Thread(target=_loop,daemon=True,name="mi-media-analytics")
            _thread.start()

def get_status(project_id):
    with d1.connect() as conn:
        counts=conn.execute("""
            SELECT state,count(*) FROM jobs WHERE job_type=%s
              AND payload->>'project_id'=%s GROUP BY state
        """,(JOB_TYPE,project_id)).fetchall()
        total=conn.execute("SELECT count(*) FROM mi_video_analyses WHERE project_id=%s",(project_id,)).fetchone()[0]
    return {"ok":True,"jobs":dict(counts),"media_analyses":total,
            "media_processing":"only_authorized_locally_acquired_assets"}
