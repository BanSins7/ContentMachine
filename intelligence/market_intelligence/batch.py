"""Durable, bounded Marketing batch intake and media acquisition orchestration.

All video identities come from canonical D1 discovery or registered project
sources. No branch imports, rights elevation, synthetic results or silent
equivalence between a partial analysis and the full A/B/C pipeline.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
import json
import threading
import time

from intelligence.discovery import service as d1
from intelligence.project_sources.repository import RegistryProblem, _project, create_source
from intelligence.project_sources.video_jobs import enqueue as enqueue_video
from intelligence.project_sources.video_adapters import AcquisitionProblem
from .contracts import MarketError, digest

JOB_TYPE = "market.batch100.v1"
MAX_BATCH = 100
_lock = threading.Lock()
_thread = None


def balanced_selection(rows, limit=MAX_BATCH):
    """Fair deterministic rotation over platforms and known author IDs."""
    if type(limit) is not int or not 1 <= limit <= MAX_BATCH:
        raise MarketError("invalid_batch_limit", "Размер пакета от 1 до 100.")
    unique = {}
    for row in rows:
        vid, url, platform, native, author = row
        if type(vid) is not int or not url or platform not in ("tiktok","instagram","youtube"):
            continue
        unique.setdefault(vid, (vid, url, platform, native, author))
    buckets = defaultdict(deque)
    for row in sorted(unique.values(), key=lambda r: (r[2],r[0])):
        buckets[(row[2],str(row[4] or row[3]))].append(row)
    selected = []
    keys = sorted(buckets)
    while keys and len(selected) < limit:
        remaining = []
        for key in keys:
            if len(selected) >= limit:
                break
            selected.append(buckets[key].popleft())
            if buckets[key]:
                remaining.append(key)
        keys = remaining
    return selected


CANDIDATES_SQL = """
SELECT v.id,v.canonical_url,v.platform,v.platform_video_id,
       COALESCE(c.platform_creator_id,ap.profile_url)
FROM videos v
LEFT JOIN creators c ON c.id=v.creator_id
LEFT JOIN LATERAL(
  SELECT p.profile_url FROM mi_author_publications p
  JOIN project_sources ps ON ps.project_id=p.project_id
     AND ps.canonical_url=p.profile_url
     AND ps.link_kind='account' AND ps.tombstoned_at IS NULL
  WHERE p.project_id=%s AND p.video_id=v.id
  ORDER BY p.profile_url LIMIT 1
) ap ON TRUE
WHERE EXISTS(
  SELECT 1 FROM project_sources ps
  WHERE ps.project_id=%s AND ps.link_kind='video'
    AND ps.source_type='user_video_reference'
    AND ps.tombstoned_at IS NULL
    AND ps.platform=v.platform AND ps.platform_native_id=v.platform_video_id
) OR ap.profile_url IS NOT NULL
ORDER BY v.platform,v.id LIMIT 2001
"""


def start_batch(project_id: str, limit: int=MAX_BATCH) -> dict:
    if type(limit) is not int or not 1 <= limit <= MAX_BATCH:
        raise MarketError("invalid_batch_limit", "Размер пакета от 1 до 100.")
    try:
        _project(project_id)
    except RegistryProblem as exc:
        raise MarketError("project_unavailable", str(exc), exc.http_status) from exc
    with d1.connect() as conn:
        rows = conn.execute(CANDIDATES_SQL, (project_id, project_id)).fetchall()
    chosen = balanced_selection(rows, limit)
    if not chosen:
        return {"ok":False, "selected":0,"requested":limit,
                "reason":"no_discovered_video_ids",
                "next_step":"Сначала зарегистрируй URL роликов или дождись поиска публикаций профилей."}
    # This is a pinned selection: never silently replace failed videos.
    payload = {"schema_version":"market-batch100/v1","project_id":project_id,
               "requested":limit,
               "videos":[{"video_id":r[0],"url":r[1],"platform":r[2],
                          "native_id":r[3]} for r in chosen]}
    key = "mi.batch100." + digest(payload)[:48]
    with d1.connect() as conn:
        with conn.transaction():
            inserted = conn.execute("""
              INSERT INTO jobs(job_type,idempotency_key,payload,max_attempts)
              VALUES (%s,%s,%s::jsonb,1)
              ON CONFLICT(idempotency_key) DO NOTHING RETURNING id
            """,(JOB_TYPE,key,json.dumps(payload,ensure_ascii=False))).fetchone()
            row = inserted or conn.execute(
                "SELECT id FROM jobs WHERE idempotency_key=%s AND job_type=%s",
                (key,JOB_TYPE)).fetchone()
            if not row:
                raise MarketError("batch_persist_failed", "Не удалось сохранить пакет.", 503)
            batch_id = row[0]
    start_worker()
    return {"ok":True,"batch_id":batch_id,"requested":limit,"selected":len(chosen),
            "source":"canonical_d1_and_registered_authors",
            "batch_immutable":True,"acquisition_started":True,
            "full_abc_certified":False}


def _source_for_video(conn, project_id, video):
    return conn.execute("""
      SELECT id,can_fetch,can_store,can_analyze,can_send_to_llm
      FROM project_sources
      WHERE project_id=%s AND platform=%s AND platform_native_id=%s
        AND link_kind='video' AND tombstoned_at IS NULL
        AND source_type='user_video_reference'
      ORDER BY created_at LIMIT 1
    """,(project_id,video["platform"],video["native_id"])).fetchone()


def _drive_video(project_id, video):
    with d1.connect() as conn:
        row = _source_for_video(conn,project_id,video)
    if row is None:
        # Register the exact discovered URL, with deny-by-default rights.
        # Never inherit permissions from an account or auto-enable downloads.
        try:
            create_source(project_id,{"url":video["url"],"source_type":"user_video_reference"})
        except RegistryProblem as exc:
            if exc.http_status != 409:
                raise
        with d1.connect() as conn:
            row = _source_for_video(conn,project_id,video)
    if row is None or not all(row[1:4]):
        return "rights_required"
    # Downloader itself checks exact-ID and verified MP4 provenance.
    enqueue_video(project_id,str(row[0]),{"refresh":False})
    return "download_queued"


def drive_once(*, max_new_downloads=8):
    """Reconcile persisted batches and queue only authorized source videos."""
    if type(max_new_downloads) is not int or not 1 <= max_new_downloads <= 20:
        raise ValueError("invalid_download_budget")
    with d1.connect() as conn:
        batches=conn.execute("""
          SELECT id,payload FROM jobs
          WHERE job_type=%s ORDER BY created_at DESC,id DESC LIMIT 8
        """,(JOB_TYPE,)).fetchall()
    queued=0
    for batch_id,payload in batches:
        project=payload["project_id"]
        for video in payload["videos"]:
            if queued >= max_new_downloads:
                break
            with d1.connect() as conn:
                s=_source_for_video(conn,project,video)
                existing=(conn.execute("""
                  SELECT j.state FROM project_video_acquisition_runs r
                  JOIN source_extraction_jobs j ON j.id=r.job_id
                  WHERE r.project_id=%s AND r.source_id=%s
                  ORDER BY r.created_at DESC LIMIT 1
                """,(project,s[0])).fetchone() if s else None)
            if existing:
                continue
            try:
                state=_drive_video(project,video)
                if state=="download_queued":
                    queued+=1
            except (RegistryProblem,AcquisitionProblem,MarketError):
                # Preserve the video in the pinned batch. Status exposes failure.
                continue
        # Existing licensed and verified media can be analyzed immediately.
        from .media_worker import queue_available
        queue_available(project, video_ids=[v["video_id"] for v in payload["videos"]])
    return queued


def status(project_id: str, batch_id: int | None=None):
    try:
        _project(project_id)
    except RegistryProblem as exc:
        raise MarketError("project_unavailable", str(exc), exc.http_status) from exc
    with d1.connect() as conn:
        if batch_id is None:
            row=conn.execute("""
              SELECT id,payload FROM jobs
              WHERE job_type=%s AND payload->>'project_id'=%s
              ORDER BY created_at DESC,id DESC LIMIT 1
            """,(JOB_TYPE,project_id)).fetchone()
        else:
            if type(batch_id) is not int or batch_id<=0:
                raise MarketError("invalid_batch_id","Неверный ID пакета.")
            row=conn.execute("""
              SELECT id,payload FROM jobs
              WHERE id=%s AND job_type=%s AND payload->>'project_id'=%s
            """,(batch_id,JOB_TYPE,project_id)).fetchone()
        if row is None:
            return {"ok":True,"batch_id":None,"videos":[],"counts":{}}
        batch_id,payload=row
        video_results=[]
        for v in payload["videos"]:
            source=_source_for_video(conn,project_id,v)
            acquired=None
            if source:
                acquired=conn.execute("""
                  SELECT j.state,r.phase,r.unavailable_reason,r.video_sha256
                  FROM project_video_acquisition_runs r
                  JOIN source_extraction_jobs j ON j.id=r.job_id
                  WHERE r.project_id=%s AND r.source_id=%s
                  ORDER BY r.created_at DESC LIMIT 1
                """,(project_id,source[0])).fetchone()
            analysis=conn.execute("""
              SELECT creative->>'state',method_version
              FROM mi_video_analyses
              WHERE project_id=%s AND video_id=%s
              ORDER BY created_at DESC,id DESC LIMIT 1
            """,(project_id,v["video_id"])).fetchone()
            if analysis:
                state="analysis_partial"  # NEVER claim B1-B8/C complete
            elif source is None or not all(source[1:4]):
                state="rights_required"
            elif acquired is None:
                state="awaiting_download"
            elif acquired[0] in ("unavailable","failed","cancelled"):
                state=acquired[0]
            elif acquired[0] in ("completed","partial"):
                state="media_awaiting_analysis"
            else:
                state="downloading"
            video_results.append({**v,"state":state,
                "acquisition_state":acquired[0] if acquired else None,
                "acquisition_reason":acquired[2] if acquired else None,
                "creative_state":analysis[0] if analysis else None,
                "analysis_method":analysis[1] if analysis else None})
    counts=Counter(x["state"] for x in video_results)
    return {"ok":True,"batch_id":batch_id,"requested":payload["requested"],
            "selected":len(video_results),"counts":dict(counts),"videos":video_results,
            "full_abc_certified":False}


def _loop():
    while True:
        try:
            drive_once()
        except Exception:
            pass
        time.sleep(20)


def start_worker():
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread=threading.Thread(target=_loop,daemon=True,name="mi-batch100")
            _thread.start()
