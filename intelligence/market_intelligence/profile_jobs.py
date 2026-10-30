"""Durable, metadata-only author-publication discovery using existing jobs and D1.

A user explicitly supplies a public profile URL. Results are bounded and
written to canonical videos/discoveries, with project/profile provenance.
This worker does NOT fetch actual MP4s, metrics or submit paid provider calls.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

from intelligence.discovery import service as d1
from .contracts import MarketError, digest
from .profile_discovery import enumerate_profile, canonical_profile, ListingUnavailable

SOURCE_ID = "mi_public_profile_listing_v1"
JOB_TYPE = "market.profile_discover.v1"
MAX_POSTS = 100
_lock = threading.Lock()
_thread = None


def queue_profiles(project_id: str, profiles: list[dict]) -> list[dict]:
    """Idempotent per project/profile/day, without repeated external calls."""
    if not profiles:
        return []
    day = datetime.now(timezone.utc).date().isoformat()
    queued = []
    with d1.connect() as conn:
        with conn.transaction():
            for row in profiles:
                platform, canonical = canonical_profile(row["url"])
                # Project registration was committed before this call.
                exists = conn.execute("""
                    SELECT 1 FROM project_sources
                     WHERE project_id=%s AND canonical_url=%s AND tombstoned_at IS NULL
                       AND link_kind='account' AND source_type IN ('competitor_account','promoter_account')
                """, (project_id, canonical)).fetchone()
                if not exists:
                    raise MarketError("profile_seed_missing", "Сначала зарегистрируй профиль в проекте.", 409)
                key = f"mi.profile.{project_id}.{digest([canonical, day])[:32]}"
                payload = {"project_id": project_id, "platform": platform,
                           "profile_url": canonical, "limit": MAX_POSTS}
                record = conn.execute("""
                    INSERT INTO jobs(job_type,idempotency_key,payload,max_attempts)
                    VALUES (%s,%s,%s::jsonb,1)
                    ON CONFLICT(idempotency_key) DO NOTHING RETURNING id,state
                """,(JOB_TYPE,key,json.dumps(payload,ensure_ascii=False))).fetchone()
                if record is None:
                    record = conn.execute("""
                        SELECT id,state FROM jobs WHERE idempotency_key=%s AND job_type=%s
                    """,(key,JOB_TYPE)).fetchone()
                if record is None:
                    raise MarketError("job_register_failed", "Не удалось зарегистрировать задачу.", 409)
                queued.append({"job_id":record[0], "platform":platform,
                               "profile_url":canonical,"state":record[1]})
    start_worker()
    return queued


def _claim():
    with d1.connect() as conn:
        with conn.transaction():
            row=conn.execute("""
                SELECT id FROM jobs
                WHERE job_type=%s
                  AND ((state='queued' AND run_after<=now())
                   OR (state='running' AND lease_until<now() AND attempts<max_attempts))
                ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1
            """,(JOB_TYPE,)).fetchone()
            if row is None:
                # Expired final attempt must not appear to be running forever.
                conn.execute("""
                    UPDATE jobs SET state='failed', locked_by=NULL, lease_until=NULL,
                      last_error='Lease expired; profile discovery cannot be replayed automatically',
                      updated_at=now()
                    WHERE job_type=%s AND state='running'
                      AND lease_until<now() AND attempts>=max_attempts
                """,(JOB_TYPE,))
                return None
            return conn.execute("""
                UPDATE jobs SET state='running', locked_by='mi-profile-local',
                  lease_until=now()+interval '15 minutes', attempts=attempts+1,
                  lease_generation=lease_generation+1,updated_at=now()
                WHERE id=%s RETURNING id,payload,lease_generation
            """,(row[0],)).fetchone()


def _ensure_source(conn):
    conn.execute("""
      INSERT INTO sources(id,provider,source_type,status,can_search,
                          can_store_metadata,can_store_metrics,can_derive_scores,can_analyze_media)
      VALUES (%s,'public_profile_user_seed','manual','enabled',
              FALSE,TRUE,FALSE,FALSE,FALSE)
      ON CONFLICT(id) DO NOTHING
    """,(SOURCE_ID,))
    conn.execute("""
      INSERT INTO source_policy_versions
       (source_id,version,is_active,capabilities,permissions,retention_policy)
      VALUES (%s,1,TRUE,
        '{"public_profile_listing":true,"provider_policy_verified":false}'::jsonb,
        '{"can_store_metadata":true,"can_store_metrics":false,"can_analyze_media":false,"can_derive_scores":false}'::jsonb,
        '{"retention_days":null}'::jsonb)
      ON CONFLICT(source_id,version) DO NOTHING
    """,(SOURCE_ID,))


def _save_posts(job_id: int, generation: int, payload: dict, output: dict) -> dict:
    project = payload["project_id"]
    platform = output["platform"]
    profile_url = output["profile_url"]
    unique = output["urls"]
    with d1.connect() as conn:
        with conn.transaction():
            current=conn.execute("""
                SELECT state,lease_generation,payload FROM jobs WHERE id=%s FOR UPDATE
            """,(job_id,)).fetchone()
            if not current or current[0]!='running' or current[1]!=generation or current[2]!=payload:
                raise MarketError("job_lease_expired", "Задача устарела или отозвана.", 409)
            active=conn.execute("""
                SELECT 1 FROM project_sources WHERE project_id=%s AND canonical_url=%s
                  AND tombstoned_at IS NULL AND source_type IN ('competitor_account','promoter_account')
            """,(project, profile_url)).fetchone()
            if not active:
                raise MarketError("profile_archived", "Профиль удалён из проекта.", 409)
            _ensure_source(conn)
            allowed=conn.execute("""
                SELECT 1 FROM sources s
                JOIN source_policy_versions p ON p.source_id=s.id AND p.is_active
                WHERE s.id=%s AND s.status='enabled' AND s.can_store_metadata
                   AND (p.permissions->>'can_store_metadata')='true'
            """,(SOURCE_ID,)).fetchone()
            if not allowed:
                raise MarketError("metadata_rights_revoked", "Хранение ссылок источника недоступно.", 409)
            new_posts=0
            for url in unique:
                from intelligence.discovery.normalize import canonical_video_url
                plat, native, canonical = canonical_video_url(url)
                if plat!=platform:
                    raise MarketError("platform_mismatch", "Источник вернул неподходящую платформу.")
                record=conn.execute("""
                    INSERT INTO videos(platform,platform_video_id,canonical_url)
                    VALUES(%s,%s,%s)
                    ON CONFLICT(platform,platform_video_id) DO NOTHING RETURNING id
                """,(plat,native,canonical)).fetchone()
                video_id=record[0] if record else conn.execute("""
                    SELECT id FROM videos WHERE platform=%s AND platform_video_id=%s
                """,(plat,native)).fetchone()[0]
                inserted=conn.execute("""
                    INSERT INTO mi_author_publications(
                       project_id,profile_url,platform,platform_video_id,video_id,discovery_job_id)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(project_id,profile_url,platform,platform_video_id)
                    DO NOTHING RETURNING video_id
                """,(project,profile_url,platform,native,video_id,job_id)).fetchone()
                if inserted:
                    new_posts+=1
                    conn.execute("""
                        INSERT INTO discoveries(video_id,source_id,method,run_id,discovery_key)
                        VALUES(%s,%s,'market_public_profile',NULL,%s)
                        ON CONFLICT(discovery_key) WHERE discovery_key IS NOT NULL DO NOTHING
                    """,(video_id,SOURCE_ID,
                         "mi:"+digest([project,profile_url,plat,native])[:58]))
            conn.execute("""
                UPDATE jobs SET state='completed',result=%s::jsonb,
                  lease_until=NULL,locked_by=NULL, updated_at=now()
                WHERE id=%s AND lease_generation=%s
            """,(json.dumps({"found":len(unique),"new_to_project":new_posts,
                  "capped":output["capped"],"completeness":output["completeness"]}),job_id,generation))
    return {"found":len(unique),"new_to_project":new_posts}


def _fail(job_id: int, generation: int, err: str):
    with d1.connect() as conn:
        with conn.transaction():
            conn.execute("""
                UPDATE jobs SET state='failed',last_error=%s,locked_by=NULL,
                   lease_until=NULL,updated_at=now()
                WHERE id=%s AND state='running' AND lease_generation=%s
            """,(err[:350],job_id,generation))


def run_once(*, enumerator=enumerate_profile) -> bool:
    claimed=_claim()
    if not claimed:
        return False
    job_id,payload,generation=claimed
    try:
        info=enumerator(payload["profile_url"],limit=payload["limit"])
        _save_posts(job_id,generation,payload,info)
        from .brightdata_jobs import queue_metrics
        queue_metrics(payload['project_id'])
    except Exception as exc:
        # Platform errors are not silently swallowed; never fabricate URLs.
        _fail(job_id,generation, str(exc))
    return True


def _loop():
    while True:
        try:
            if not run_once():
                time.sleep(3)
        except Exception:
            time.sleep(5)


def start_worker():
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop,daemon=True,name="mi-profile-discovery")
            _thread.start()


def list_jobs(project_id: str) -> dict:
    with d1.connect() as conn:
        records=conn.execute("""
            SELECT id,state,payload,result,last_error,created_at,updated_at
            FROM jobs WHERE job_type=%s AND payload->>'project_id'=%s
            ORDER BY created_at DESC,id DESC LIMIT 100
        """,(JOB_TYPE,project_id)).fetchall()
        stats=conn.execute("""
            SELECT profile_url,platform,count(*)
            FROM mi_author_publications WHERE project_id=%s
            GROUP BY profile_url,platform ORDER BY platform,profile_url
        """,(project_id,)).fetchall()
    jobs=[]
    for ident,state,payload,result,error,created,updated in records:
        jobs.append({"job_id":ident, "state":state, "profile_url":payload["profile_url"],
                     "platform":payload["platform"],"result":result,
                     "error":error, "created_at":created.isoformat(),
                     "updated_at":updated.isoformat()})
    return {"ok":True,"jobs":jobs,"profiles":[
        {"profile_url":url,"platform":platform,"discovered_videos":n}
        for url,platform,n in stats],
        "total_discovered_video_links":sum(x[2] for x in stats)}
