"""Independent source-checked Marketing carousel acquisition + A/B jobs.

Only source-approved public post URLs. gallery-dl images stay in allowed roots;
each original slide and the full ordered bundle have immutable SHA evidence.
No QDP, no fake MP4, no video timestamps, no unverified watch metrics.
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import threading
import time

from intelligence import config
from intelligence.discovery import service as d1
from intelligence.project_sources.video_jobs import enqueue as enqueue_video
from .contracts import MarketError,digest
from .carousel_media import (
    CarouselError,download_carousel,analyze_carousel,carousel_candidate)

JOB_TYPE="market.carousel_analyze.v1"
METHOD="mi-photo-carousel/v1"
_lock=threading.Lock()
_thread=None


def _rights(conn,project_id,platform,native):
    """A profile's account permission never authorizes its child post."""
    return conn.execute("""
      SELECT ps.id,ps.active_version_id,ps.can_fetch,ps.can_store,
             ps.can_analyze,ps.can_send_to_llm
      FROM project_sources ps
      WHERE ps.project_id=%s AND ps.platform=%s AND ps.platform_native_id=%s
        AND ps.link_kind='video' AND ps.source_type='user_video_reference'
        AND ps.tombstoned_at IS NULL
      ORDER BY ps.created_at LIMIT 1
    """,(project_id,platform,native)).fetchone()


def queue_carousel(project_id,video,*,start=True):
    """Idempotent bounded queue for one exact photo publication."""
    url=video["url"]
    if not carousel_candidate(url):
        raise MarketError("not_photo_carousel_candidate",
                          "Не является поддерживаемой ссылкой на фото.")
    platform=video["platform"]
    native=video["native_id"]
    with d1.connect() as conn:
        with conn.transaction():
            source=_rights(conn,project_id,platform,native)
            if (source is None or not source[1] or
                not all(source[i] is True for i in (2,3,4))):
                raise MarketError("carousel_source_rights_required",
                                  "Права на конкретный пост не подтверждены.",409)
            info=conn.execute("""
              SELECT v.id FROM videos v WHERE v.id=%s AND v.platform=%s
                AND v.platform_video_id=%s AND v.canonical_url=%s
            """,(video["video_id"],platform,native,url)).fetchone()
            if info is None:
                raise MarketError("carousel_video_identity_mismatch",
                                  "Нет подтверждённого ID публикации.",409)
            payload={
                "project_id":project_id,
                "video_id":video["video_id"],
                "url":url,"platform":platform,"native_id":native,
                "source_id":str(source[0]),
                "source_version_id":str(source[1]),
            }
            key="mi.carousel."+digest([project_id,video["video_id"],url,METHOD])[:48]
            found=conn.execute("""
                INSERT INTO jobs(job_type,idempotency_key,payload,max_attempts)
                VALUES(%s,%s,%s::jsonb,1)
                ON CONFLICT(idempotency_key) DO NOTHING RETURNING id
            """,(JOB_TYPE,key,json.dumps(payload,ensure_ascii=False))).fetchone()
    if found is not None and start:
        start_worker()
    return {"queued":bool(found),"job_id":found[0] if found else None}


def _claim():
    with d1.connect() as conn:
        with conn.transaction():
            conn.execute("""
                UPDATE jobs SET state='failed',last_error='Carousel worker lease expired',
                    locked_by=NULL,lease_until=NULL,updated_at=now()
                WHERE job_type=%s AND state='running' AND lease_until<now()
            """,(JOB_TYPE,))
            row=conn.execute("""
                SELECT id FROM jobs
                WHERE job_type=%s AND state='queued' AND run_after<=now()
                ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1
            """,(JOB_TYPE,)).fetchone()
            if not row:return None
            return conn.execute("""
                UPDATE jobs SET state='running',locked_by='mi-carousel',
                    lease_until=now()+interval '20 minutes',attempts=attempts+1,
                    lease_generation=lease_generation+1,updated_at=now()
                WHERE id=%s RETURNING id,payload,lease_generation
            """,(row[0],)).fetchone()


def _root():
    locations=[p for p in config.setting("CM_MARKET_MEDIA_ROOTS","").split(os.pathsep) if p]
    if not locations:
        raise CarouselError("carousel_storage_root_not_configured")
    first=Path(locations[0])
    if not first.is_absolute() or not first.exists() or first.is_symlink():
        raise CarouselError("carousel_storage_root_not_trusted")
    return first.resolve(strict=True)


def process(job,*,downloader=download_carousel,analyzer=analyze_carousel):
    ident,payload,generation=job
    source_id=payload["source_id"]
    with d1.connect() as conn:
        source=_rights(conn,payload["project_id"],payload["platform"],payload["native_id"])
    if (source is None or str(source[0])!=source_id or
        str(source[1])!=payload["source_version_id"] or
        not all(source[i] is True for i in (2,3,4))):
        raise CarouselError("carousel_rights_changed_before_acquisition")
    # Separate per-project directory, never reuse an arbitrary input path.
    root=_root()
    project_dir=root/"mi-carousels"/payload["project_id"]
    if project_dir.is_symlink():
        raise CarouselError("unsafe_carousel_project_dir")
    project_dir.mkdir(parents=True,exist_ok=True)
    manifest=downloader(payload["url"],project_dir)
    from intelligence.discovery.normalize import canonical_video_url
    plat,native,url=canonical_video_url(payload["url"])
    if (manifest.get("canonical_post_url")!=url or
        manifest.get("platform")!=plat or manifest.get("post_native_id")!=native):
        raise CarouselError("carousel_post_identity_mismatch")
    folder=project_dir/("carousel-"+plat+"-"+native)
    result=analyzer(manifest,folder,allow_llm=bool(source[5]))
    a=result["group_a"]
    b=result["group_b"]
    if (a["source_manifest_sha256"]!=manifest["manifest_sha256"] or
        b["source_manifest_sha256"]!=manifest["manifest_sha256"] or
        b["group_a_report_sha256"]!=a["report_sha256"]):
        raise CarouselError("carousel_analysis_lineage_mismatch")
    technical={"content_kind":"photo_carousel",
               "source_manifest_sha256":manifest["manifest_sha256"],
               "slide_count":len(manifest["slides"]),
               "media_kind":"ordered_images_not_video",
               "no_video_duration":True,
               "no_audio_or_motion_claim":True}
    creative={"content_kind":"photo_carousel",
              "state":"slide_analysis_partial",
              "group_a":a,
              "group_b":b,
              "visual_model_state":result["visual_model"],
              "original_full_abc_certified":False}
    with d1.connect() as conn:
        with conn.transaction():
            current=conn.execute("""
              SELECT state,lease_generation FROM jobs WHERE id=%s FOR UPDATE
            """,(ident,)).fetchone()
            if current!=("running",generation):
                raise CarouselError("carousel_job_lease_lost")
            verified=_rights(conn,payload["project_id"],plat,native)
            if (verified is None or str(verified[0])!=source_id or
                str(verified[1])!=payload["source_version_id"] or
                not all(verified[i] is True for i in (2,3,4)) or
                (result["visual_model"]=="model_observed_partial" and
                 verified[5] is not True)):
                raise CarouselError("carousel_rights_revoked")
            inserted=conn.execute("""
              INSERT INTO mi_carousel_analyses(
                project_id,video_id,source_id,source_version_id,
                platform,native_id,canonical_post_url,content_kind,
                method_version,source_manifest_sha256,local_storage_ref,
                manifest,technical,creative)
              VALUES(%s,%s,%s,%s,%s,%s,%s,'photo_carousel',%s,%s,%s,
                     %s::jsonb,%s::jsonb,%s::jsonb)
              ON CONFLICT(project_id,video_id,source_manifest_sha256,method_version)
              DO NOTHING RETURNING id
            """,(payload["project_id"],payload["video_id"],source_id,
                 payload["source_version_id"],plat,native,url,METHOD,
                 manifest["manifest_sha256"],str(folder),
                 json.dumps(manifest,ensure_ascii=False),
                 json.dumps(technical,ensure_ascii=False),
                 json.dumps(creative,ensure_ascii=False))).fetchone()
            conn.execute("""
              UPDATE jobs SET state='completed',result=%s::jsonb,
                  locked_by=NULL,lease_until=NULL,updated_at=now()
              WHERE id=%s AND lease_generation=%s
            """,(json.dumps({"kind":"photo_carousel",
                 "slide_count":len(manifest["slides"]),
                 "analysis_stored":bool(inserted),
                 "manifest_sha256":manifest["manifest_sha256"]}),ident,generation))
    return {"video_id":payload["video_id"],"slide_count":len(manifest["slides"])}


def run_once(*,processor=process):
    item=_claim()
    if item is None:
        return False
    try:
        processor(item)
    except Exception as exc:
        jid,payload,generation=item
        code=getattr(exc,"code",None)
        if not code or not isinstance(code,str):
            code="carousel_analysis_unavailable"
        # Instagram /p/ can also contain a conventional video. If no image
        # bundle exists, queue the existing exact-ID MP4 downloader; do not
        # claim that its photo analysis succeeded.
        if (payload.get("platform")=="instagram" and
            code in ("carousel_slide_count_unavailable","carousel_gallery_dl_failed")):
            try:
                with d1.connect() as conn:
                    original=_rights(conn,payload["project_id"],"instagram",payload["native_id"])
                if original and all(original[i] is True for i in (2,3,4)):
                    enqueue_video(payload["project_id"],payload["source_id"],{"refresh":False})
            except Exception:
                pass
        with d1.connect() as conn:
            with conn.transaction():
                conn.execute("""
                   UPDATE jobs SET state='failed',last_error=%s,
                     locked_by=NULL,lease_until=NULL,updated_at=now()
                   WHERE id=%s AND state='running' AND lease_generation=%s
                """,(code[:120],jid,generation))
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
            _thread=threading.Thread(target=_loop,daemon=True,name="mi-carousel")
            _thread.start()
