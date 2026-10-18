"""D1 offline ingestion: PostgreSQL journal, durable chunked jobs, explicit local-source rights.

No scraping or network fetches. The only network connection is local PostgreSQL.
"""
from __future__ import annotations
import hashlib
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from .importers import parse_content
from .normalize import validate_candidate
from intelligence import config, migrate

SOURCE_ID = "manual_local_d1"
WORKER_ID = "discovery-" + uuid.uuid4().hex[:12]
CHUNK_SIZE = 50
_worker_thread = None
_start_lock = threading.Lock()

class DiscoveryError(ValueError):
    pass

def connect():
    if not config.enabled() or not config.dsn():
        raise DiscoveryError("PostgreSQL не настроен.")
    import psycopg
    return psycopg.connect(config.dsn(), connect_timeout=4, autocommit=True)

def ensure_local_source(conn):
    """Explicit user-initiated import is consent for local metadata, never for metric history."""
    conn.execute("""INSERT INTO sources
      (id,provider,source_type,status,can_search,can_store_metadata,
       can_store_metrics,can_derive_scores,can_analyze_media)
      VALUES (%s,'local_user_import','manual','enabled',FALSE,TRUE,FALSE,FALSE,FALSE)
      ON CONFLICT (id) DO NOTHING""", (SOURCE_ID,))
    conn.execute("""INSERT INTO source_policy_versions
      (source_id,version,is_active,capabilities,permissions,retention_policy)
      VALUES (%s,1,TRUE,'{"manual_import":true}'::jsonb,
              '{"can_store_metadata":true,"can_store_metrics":false}'::jsonb,
              '{"retention_days":null}'::jsonb)
      ON CONFLICT (source_id,version) DO NOTHING""",(SOURCE_ID,))

def create_import(text: str, fmt: str = "urls", topic: str = "", key: str | None = None) -> dict:
    rows = parse_content(text, fmt)
    if not isinstance(topic, str) or len(topic) > 120 or len(topic.strip()) < 0:
        raise DiscoveryError("Некорректная тема.")
    if key is None:
        key = uuid.uuid4().hex
    if not isinstance(key, str) or not 1 <= len(key) <= 120 or not all(c.isalnum() or c in "._-" for c in key):
        raise DiscoveryError("Некорректный ключ повторного запроса.")
    params = {"format": fmt, "topic": topic.strip(), "rows": len(rows),
              "payload_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    with connect() as conn:
        with conn.transaction():
            ensure_local_source(conn)
            found = conn.execute(
                """INSERT INTO search_runs(idempotency_key,config,planner_version,state)
                   VALUES (%s,%s::jsonb,'d1-offline-v1','queued')
                   ON CONFLICT(idempotency_key) DO NOTHING RETURNING id""",
                (key,json.dumps(params,ensure_ascii=False)),
            ).fetchone()
            if found is None:
                other = conn.execute(
                    "SELECT id,config FROM search_runs WHERE idempotency_key=%s",(key,)
                ).fetchone()
                if other is None or other[1] != params:
                    raise DiscoveryError("Ключ повтора уже использован с другими данными.")
                return {"ok":True,"run_id":other[0],"queued_rows":len(rows),"replayed":True}
            run_id = found[0]
            if topic.strip():
                conn.execute("INSERT INTO topics(name) VALUES (%s) ON CONFLICT(name) DO NOTHING",(topic.strip(),))
            for offset in range(0,len(rows),CHUNK_SIZE):
                chunk = rows[offset:offset+CHUNK_SIZE]
                payload = {"run_id":run_id,"offset":offset,"rows":chunk,"topic":topic.strip()}
                conn.execute(
                    """INSERT INTO jobs(job_type,idempotency_key,payload,max_attempts)
                       VALUES ('discovery.import',%s,%s::jsonb,3)""",
                    (f"d1:{run_id}:{offset}",json.dumps(payload,ensure_ascii=False)),
                )
            return {"ok":True,"run_id":run_id,"queued_rows":len(rows),"replayed":False}

def _claim(conn):
    with conn.transaction():
        row = conn.execute("""SELECT id FROM jobs WHERE job_type='discovery.import'
             AND ((state='queued' AND run_after<=now())
                  OR (state='running' AND lease_until<now() AND attempts<max_attempts))
             ORDER BY priority DESC, created_at, id
             FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        if not row:
            return None
        return conn.execute("""UPDATE jobs SET state='running',locked_by=%s,
                lease_until=now()+interval '120 seconds',
                attempts=attempts+1,lease_generation=lease_generation+1,
                updated_at=now() WHERE id=%s RETURNING id,payload,lease_generation""",
                (WORKER_ID,row[0])).fetchone()

def _run_state(conn, run_id):
    counts = conn.execute("""SELECT state,count(*) FROM jobs
        WHERE job_type='discovery.import' AND payload->>'run_id'=%s
        GROUP BY state""",(str(run_id),)).fetchall()
    states = dict(counts)
    if states.get("queued",0) or states.get("running",0):
        state = "running"
    elif states.get("failed",0):
        state = "partial" if states.get("completed",0) else "failed"
    else:
        state = "completed"
    conn.execute("""UPDATE search_runs SET state=%s,
        started_at=COALESCE(started_at,now()),
        finished_at=CASE WHEN %s IN ('completed','partial','failed') THEN now()
                         ELSE NULL END WHERE id=%s""",(state,state,run_id))
    return state

def _ingest_one(conn, run_id, source_policy_id, row, topic_id):
    check = validate_candidate(row)
    if check.state == "rejected":
        conn.execute("""INSERT INTO data_quality_flags(run_id,source_id,code,details)
            VALUES (%s,%s,%s,'{}'::jsonb)""",(run_id,SOURCE_ID,check.reasons[0]))
        return "rejected"
    c = check.candidate
    new = conn.execute("""INSERT INTO videos(platform,platform_video_id,canonical_url)
        VALUES (%s,%s,%s) ON CONFLICT(platform,platform_video_id)
        DO NOTHING RETURNING id""",(c.platform,c.platform_video_id,c.canonical_url)).fetchone()
    if new:
        video_id=new[0]
    else:
        video_id=conn.execute("SELECT id FROM videos WHERE platform=%s AND platform_video_id=%s",
                              (c.platform,c.platform_video_id)).fetchone()[0]
    stamp = conn.execute("SELECT created_at FROM search_runs WHERE id=%s",(run_id,)).fetchone()[0]
    conn.execute("""INSERT INTO source_video_metadata
       (video_id,source_id,source_policy_version_id,fetched_at,title,published_at,duration_seconds)
       VALUES (%s,%s,%s,%s,%s,%s,%s)
       ON CONFLICT(video_id,source_id,fetched_at) DO NOTHING""",
       (video_id,SOURCE_ID,source_policy_id,stamp,c.title,c.published_at,c.duration_seconds))
    occurrence_key = hashlib.sha256(
        f"{run_id}|{SOURCE_ID}|{c.platform}|{c.platform_video_id}".encode("utf-8")).hexdigest()
    conn.execute("""INSERT INTO discoveries(video_id,source_id,method,run_id,discovery_key)
       VALUES (%s,%s,'manual_import',%s,%s)
       ON CONFLICT(discovery_key) WHERE discovery_key IS NOT NULL DO NOTHING""",
       (video_id,SOURCE_ID,run_id,occurrence_key))
    if topic_id:
        conn.execute("""INSERT INTO video_topics(video_id,topic_id,relevance_state)
         VALUES (%s,%s,'unknown')
         ON CONFLICT(video_id,topic_id) DO NOTHING""",(video_id,topic_id))
    for reason in check.reasons:
        # A partial row is saved and explained, not silently called complete.
        flag_key = f"d1:{run_id}:{video_id}:{reason}"
        existing = conn.execute("""SELECT id FROM data_quality_flags
            WHERE video_id=%s AND run_id=%s AND code=%s LIMIT 1""",
            (video_id,run_id,reason)).fetchone()
        if not existing:
            conn.execute("""INSERT INTO data_quality_flags
               (video_id,source_id,run_id,code,details)
               VALUES (%s,%s,%s,%s,'{}'::jsonb)""",
               (video_id,SOURCE_ID,run_id,reason))
    # Manual imports cannot provide metric history; source can_store_metrics=False.
    return "new" if new else "duplicate"

def work_once():
    with connect() as conn:
        claimed = _claim(conn)
        if not claimed:
            return False
        job_id,payload,generation=claimed
        run_id=payload["run_id"]
        try:
            with conn.transaction():
                policy=conn.execute("""SELECT spv.id FROM sources s JOIN source_policy_versions spv
                    ON spv.source_id=s.id AND spv.is_active
                    WHERE s.id=%s AND s.can_store_metadata
                      AND spv.permissions->>'can_store_metadata'='true'""",(SOURCE_ID,)).fetchone()
                if not policy:
                    raise DiscoveryError("Источник не разрешает хранить метаданные.")
                topic_id=None
                if payload["topic"]:
                    topic_id=conn.execute("SELECT id FROM topics WHERE name=%s",
                                           (payload["topic"],)).fetchone()[0]
                stats={"new":0,"duplicate":0,"rejected":0}
                for row in payload["rows"]:
                    state=_ingest_one(conn,run_id,policy[0],row,topic_id)
                    stats[state]+=1
                completed=conn.execute("""UPDATE jobs SET state='completed',
                    result=%s::jsonb,payload='{}'::jsonb,
                    locked_by=NULL,lease_until=NULL,updated_at=now()
                    WHERE id=%s AND state='running'
                      AND locked_by=%s AND lease_generation=%s RETURNING id""",
                    (json.dumps({**stats,"run_id":run_id}),job_id,WORKER_ID,generation)).fetchone()
                if not completed:
                    raise RuntimeError("Истёкшая аренда задания.")
                _run_state(conn,run_id)
        except Exception as exc:
            # A batch transaction rolled back in full. Never publish partial success.
            with conn.transaction():
                conn.execute("""UPDATE jobs SET
                    state=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'queued' END,
                    run_after=now()+interval '5 seconds',last_error=%s,
                    locked_by=NULL,lease_until=NULL,updated_at=now()
                    WHERE id=%s AND state='running'
                    AND locked_by=%s AND lease_generation=%s""",
                    (type(exc).__name__[:60],job_id,WORKER_ID,generation))
                _run_state(conn,run_id)
        return True

def _worker_loop():
    while True:
        try:
            if not work_once():
                time.sleep(0.7)
        except Exception:
            # PostgreSQL may be offline, retry without taking the main UI down.
            time.sleep(3.0)

def start_worker():
    global _worker_thread
    if not config.enabled():
        return
    with _start_lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread=threading.Thread(target=_worker_loop,daemon=True,name="D1-import-worker")
            _worker_thread.start()

def run_detail(run_id: int) -> dict:
    with connect() as conn:
        base=conn.execute("""SELECT id,state,config,created_at,started_at,finished_at
            FROM search_runs WHERE id=%s""",(run_id,)).fetchone()
        if not base:
            raise DiscoveryError("Запуск не найден.")
        parts=conn.execute("""SELECT state,result FROM jobs
           WHERE job_type='discovery.import'
             AND (payload->>'run_id'=%s OR result->>'run_id'=%s)""",
           (str(run_id),str(run_id))).fetchall()
        counts={"new":0,"duplicate":0,"rejected":0,"queued":0,"running":0,"failed":0,"completed":0}
        for state,result in parts:
            counts[state]=counts.get(state,0)+1
            if result:
                for key in ("new","duplicate","rejected"):
                    counts[key]+=result.get(key,0)
        # Counts returned to UI are actual committed results only.
        flags=conn.execute("""SELECT code,count(*) FROM data_quality_flags
          WHERE run_id=%s GROUP BY code ORDER BY count(*) DESC LIMIT 10""",(run_id,)).fetchall()
        return {"ok":True,"run_id":base[0],"state":base[1],"config":base[2],
                "created_at":base[3].isoformat(),
                "started_at":base[4].isoformat() if base[4] else None,
                "finished_at":base[5].isoformat() if base[5] else None,
                "counts":counts,"flags":dict(flags)}

def list_runs(limit=25):
    with connect() as conn:
        rows=conn.execute("""SELECT id,state,config,created_at FROM search_runs
            ORDER BY id DESC LIMIT %s""",(min(100,max(1,limit)),)).fetchall()
    return {"ok":True,"runs":[{"id":r[0],"state":r[1],"config":r[2],
                                 "created_at":r[3].isoformat()} for r in rows]}

def list_videos(limit=50, before=None):
    with connect() as conn:
        rows=conn.execute("""SELECT v.id,v.platform,v.platform_video_id,v.canonical_url,
           v.first_seen_at,m.title,m.published_at,d.source_id
           FROM videos v JOIN discoveries d ON d.video_id=v.id AND d.source_id=%s
           LEFT JOIN LATERAL (
               SELECT title,published_at FROM source_video_metadata
               WHERE video_id=v.id AND source_id=%s
                  AND deleted_at IS NULL
                  AND (expires_at IS NULL OR expires_at>now())
               ORDER BY fetched_at DESC LIMIT 1
           ) m ON TRUE
           WHERE (%s::bigint IS NULL OR v.id<%s::bigint)
           GROUP BY v.id,v.platform,v.platform_video_id,v.canonical_url,
                    v.first_seen_at,m.title,m.published_at,d.source_id
           ORDER BY v.id DESC LIMIT %s""",
          (SOURCE_ID,SOURCE_ID,before,before,min(100,max(1,limit)))).fetchall()
    return {"ok":True,"videos":[{"id":r[0],"platform":r[1],"platform_video_id":r[2],
               "url":r[3],"first_seen_at":r[4].isoformat(),
               "title":r[5],"published_at":r[6].isoformat() if r[6] else None,
               "source_id":r[7]} for r in rows]}

def video_trace(video_id: int):
    with connect() as conn:
        base=conn.execute("SELECT platform,platform_video_id,canonical_url FROM videos WHERE id=%s",(video_id,)).fetchone()
        if not base:
            raise DiscoveryError("Видео не найдено.")
        sources=conn.execute("""SELECT d.source_id,d.method,d.run_id,d.found_at,
                   d.source_request_id FROM discoveries d WHERE d.video_id=%s
                   ORDER BY d.found_at DESC LIMIT 100""",(video_id,)).fetchall()
        flags=conn.execute("""SELECT code,created_at FROM data_quality_flags
                  WHERE video_id=%s ORDER BY created_at DESC LIMIT 50""",(video_id,)).fetchall()
        return {"ok":True,"id":video_id,"platform":base[0],"platform_video_id":base[1],
                "url":base[2],"discoveries":[{"source":r[0],"method":r[1],"run_id":r[2],
                "found_at":r[3].isoformat(),"request_id":r[4]} for r in sources],
                "quality_flags":[{"code":r[0],"at":r[1].isoformat()} for r in flags]}

def diagnostics():
    from intelligence.health import status
    return {"ok":True,"health":status(),"live_collection":False,"paid_collectors":False,
            "manual_import":True,"monthly_target":10000}
