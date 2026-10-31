"""Durable Bright Data metric jobs: trigger once, poll, store canonical D1.

No Group A/B/C calls or duplicated metrics store. No secret persisted in jobs.
Each billable request is de-duplicated by project/platform/day/video batch.
A crash after an ambiguous trigger is fail-closed: never re-charge automatically.
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from datetime import datetime,timezone

from intelligence.discovery import service as d1
from .contracts import MarketError, digest
from . import brightdata_provider as bd

JOB_TYPE="market.brightdata_metrics.v1"
SOURCE_PREFIX="mi_brightdata_"
MAX_JOBS_PER_ENQUEUE=40
_lock=threading.Lock()
_thread=None

SQL_TARGETS="""
SELECT v.id,v.platform,v.canonical_url,
       v.published_at,MAX(ms.observed_at) AS last_collected
FROM videos v
LEFT JOIN metric_snapshots ms ON ms.video_id=v.id AND
     ms.source_id=%s
WHERE (
 EXISTS (SELECT 1 FROM project_sources ps WHERE ps.project_id=%s
     AND ps.tombstoned_at IS NULL AND ps.source_type='user_video_reference'
     AND ps.link_kind='video' AND ps.platform=v.platform
     AND ps.platform_native_id=v.platform_video_id)
 OR EXISTS (SELECT 1 FROM mi_author_publications mp
   JOIN project_sources ps ON ps.project_id=mp.project_id
        AND ps.canonical_url=mp.profile_url AND ps.tombstoned_at IS NULL
        AND ps.link_kind='account'
   WHERE mp.project_id=%s AND mp.video_id=v.id)
)
AND v.platform=%s
GROUP BY v.id,v.platform,v.canonical_url,v.published_at
ORDER BY v.id LIMIT 2001
"""

def _interval(published,now):
    if published is None:return 86400
    age=(now-published).total_seconds()
    if age<0:return 86400
    if age<=48*3600:return 6*3600
    if age<=14*86400:return 86400
    return 3*86400

def _source_id(platform):
    return SOURCE_PREFIX+platform+"_v1"

def queue_metrics(project_id: str) -> dict:
    """Register only due videos: no provider call on the HTTP request thread."""
    if not bd.readiness()["ready"]:
        return {"enabled":False,"queued":0,"reason":"brightdata_not_configured"}
    from datetime import datetime,timezone
    now=datetime.now(timezone.utc)
    queued=[]
    totals={}
    with d1.connect() as conn:
        with conn.transaction():
            pending=conn.execute("""
                SELECT payload->'urls' FROM jobs
                WHERE job_type=%s AND payload->>'project_id'=%s
                  AND state IN ('queued','running')
            """,(JOB_TYPE,project_id)).fetchall()
            pending_urls={url for (entries,) in pending
                          for url in (entries or []) if isinstance(url,str)}
            for platform in ("tiktok","instagram","youtube"):
                rows=conn.execute(SQL_TARGETS,(_source_id(platform),project_id,project_id,platform)).fetchall()
                due=[]
                for video_id,pl,url,published,last in rows[:2000]:
                    if (url not in pending_urls and
                        (last is None or (now-last).total_seconds()>=_interval(published,now))):
                        due.append(url)
                totals[platform]={"known":len(rows),"due":len(due)}
                for i in range(0,len(due),bd.MAX_INPUTS):
                    if len(queued)>=MAX_JOBS_PER_ENQUEUE:break
                    chunk=due[i:i+bd.MAX_INPUTS]
                    # Six-hour collection window enables multiple real snapshots
                    # of early posts, but prevents duplicate billable triggers.
                    time_bucket=now.strftime("%Y-%m-%d")+":"+str(now.hour//6)
                    key="mi.bd."+digest([project_id,platform,time_bucket,chunk])[:50]
                    payload={"project_id":project_id,"platform":platform,"urls":chunk,
                             "dataset_id":bd.dataset_id(platform)}
                    row=conn.execute("""
                      INSERT INTO jobs(job_type,idempotency_key,payload,max_attempts)
                      VALUES (%s,%s,%s::jsonb,1)
                      ON CONFLICT(idempotency_key) DO NOTHING RETURNING id
                    """,(JOB_TYPE,key,json.dumps(payload))).fetchone()
                    if row:queued.append(row[0])
    if queued:start_worker()
    return {"enabled":True,"queued":len(queued),"videos":totals,
            "billable_when_provider_succeeds":True,
            "max_new_provider_batches":MAX_JOBS_PER_ENQUEUE}

def _claim():
    with d1.connect() as conn:
        with conn.transaction():
            conn.execute("""
              UPDATE jobs SET state='failed',locked_by=NULL,lease_until=NULL,
                last_error='Uncertain provider trigger; no automatic retry',
                updated_at=now()
              WHERE job_type=%s AND state='running' AND lease_until<now()
                AND result IS NULL
            """,(JOB_TYPE,))
            conn.execute("""
              UPDATE jobs SET state='queued',locked_by=NULL,lease_until=NULL,
                run_after=now(),updated_at=now()
              WHERE job_type=%s AND state='running' AND lease_until<now()
                AND result ? 'snapshot_id'
            """,(JOB_TYPE,))
            row=conn.execute("""
              SELECT id FROM jobs
              WHERE job_type=%s AND state='queued' AND run_after<=now()
              ORDER BY priority DESC,created_at,id
              FOR UPDATE SKIP LOCKED LIMIT 1
            """,(JOB_TYPE,)).fetchone()
            if not row:return None
            return conn.execute("""
              UPDATE jobs SET state='running', locked_by='mi-brightdata',
                 lease_until=now()+interval '3 minutes',updated_at=now(),
                 attempts=attempts+CASE WHEN result IS NULL THEN 1 ELSE 0 END,
                 lease_generation=lease_generation+1
              WHERE id=%s RETURNING id,payload,result,lease_generation
            """,(row[0],)).fetchone()

def _save_stage(job_id,generation,state,result,*,delay_seconds=0,error=None):
    with d1.connect() as conn:
        with conn.transaction():
            changed=conn.execute("""
               UPDATE jobs SET state=%s,result=%s::jsonb,locked_by=NULL,
                  lease_until=NULL,run_after=now()+(%s*interval '1 second'),
                  last_error=%s,updated_at=now()
               WHERE id=%s AND state='running' AND lease_generation=%s
               RETURNING id
            """,(state,json.dumps(result),delay_seconds,error,job_id,generation)).fetchone()
            if not changed:
                raise MarketError("brightdata_stale_job","Устаревшее задание Bright Data.",409)

def _ensure_source(conn,platform):
    """Source rights are enabled only after explicit admin-side verification."""
    if not bd.readiness()["rights_verified"]:
        raise MarketError("brightdata_rights","Не подтверждены условия использования метрик.",409)
    ident=_source_id(platform)
    conn.execute("""
       INSERT INTO sources(id,provider,source_type,status,can_search,
          can_store_metadata,can_store_metrics,can_derive_scores,can_analyze_media,
          retention_days)
       VALUES(%s,'brightdata','commercial','enabled',FALSE,TRUE,TRUE,TRUE,FALSE,90)
       ON CONFLICT(id) DO NOTHING
    """,(ident,))
    conn.execute("""
       INSERT INTO source_policy_versions(source_id,version,is_active,capabilities,
          permissions,retention_policy,reference_urls)
       VALUES(%s,1,TRUE,'{"vendor":"brightdata","on_demand":true}'::jsonb,
         '{"can_store_metadata":true,"can_store_metrics":true,
            "can_derive_scores":true,"can_analyze_media":false}'::jsonb,
         '{"retention_days":90}'::jsonb,
         '["https://brightdata.com/products/web-scraper"]'::jsonb)
       ON CONFLICT(source_id,version) DO NOTHING
    """,(ident,))
    row=conn.execute("""
        SELECT policy.id FROM source_policy_versions policy
        JOIN sources s ON s.id=policy.source_id
        WHERE policy.source_id=%s AND policy.is_active
          AND s.status='enabled' AND s.can_store_metrics AND s.can_derive_scores
          AND (policy.permissions->>'can_store_metrics')='true'
          AND (policy.permissions->>'can_derive_scores')='true'
        ORDER BY policy.version DESC LIMIT 1
    """,(ident,)).fetchone()
    if row is None:
        raise MarketError("brightdata_source_disabled","Источник метрик отключён.",409)
    return ident,row[0]

def _ingest(job_id,generation,payload,snapshot_id,records):
    platform=payload["platform"]
    allowed={}
    for url in payload["urls"]:
        from intelligence.discovery.normalize import canonical_video_url
        p,n,canonical=canonical_video_url(url)
        if p!=platform:raise MarketError("brightdata_input_scope","Несовпадение платформ.")
        allowed[n]=canonical
    accepted={}
    for raw in records:
        row=bd.normalize_record(platform,raw)
        if row and row["native_id"] in allowed:
            accepted[row["native_id"]]=row
    if not accepted:
        raise MarketError("brightdata_no_metrics",
            "В ответе нет метрик запрошенных публикаций.",502)
    imported=0;views_count=0
    with d1.connect() as conn:
        with conn.transaction():
            job=conn.execute("""
              SELECT state,lease_generation FROM jobs WHERE id=%s FOR UPDATE
            """,(job_id,)).fetchone()
            if not job or job[0]!="running" or job[1]!=generation:
                raise MarketError("brightdata_lease_lost","Задание Bright Data устарело.",409)
            active=conn.execute("""
                SELECT 1 FROM project_registry WHERE id=%s
            """,(payload["project_id"],)).fetchone()
            if not active:raise MarketError("project_missing","Проект отсутствует.",404)
            source,policy=_ensure_source(conn,platform)
            for native,record in accepted.items():
                # Never insert unrelated videos returned by a provider.
                video=conn.execute("""
                  SELECT v.id,v.creator_id FROM videos v WHERE v.platform=%s
                      AND v.platform_video_id=%s AND (
                    EXISTS(SELECT 1 FROM project_sources ps
                       WHERE ps.project_id=%s AND ps.tombstoned_at IS NULL
                         AND ps.source_type='user_video_reference'
                         AND ps.platform=v.platform AND ps.platform_native_id=v.platform_video_id)
                    OR EXISTS(SELECT 1 FROM mi_author_publications ap
                       JOIN project_sources ps ON ps.project_id=ap.project_id
                         AND ps.canonical_url=ap.profile_url
                         AND ps.tombstoned_at IS NULL
                       WHERE ap.project_id=%s AND ap.video_id=v.id))
                """,(platform,native,payload["project_id"],payload["project_id"])).fetchone()
                if not video:continue
                video_id,old_author=video
                # Creator identity is determined by the registered profile
                # provenance, never inferred from a creator display name.
                profiles=conn.execute("""
                   SELECT ap.profile_url FROM mi_author_publications ap
                   JOIN project_sources ps ON ps.project_id=ap.project_id
                     AND ps.canonical_url=ap.profile_url AND ps.tombstoned_at IS NULL
                   WHERE ap.project_id=%s AND ap.video_id=%s
                   ORDER BY ap.profile_url LIMIT 1
                """,(payload["project_id"],video_id)).fetchone()
                if profiles and old_author is None:
                    profile=profiles[0]
                    handle=profile.rstrip("/").rsplit("/",1)[-1].lstrip("@")
                    if handle:
                        creator=conn.execute("""
                          INSERT INTO creators(platform,platform_creator_id,username,canonical_url)
                          VALUES(%s,%s,%s,%s)
                          ON CONFLICT(platform,platform_creator_id)
                          DO UPDATE SET canonical_url=EXCLUDED.canonical_url
                          RETURNING id
                        """,(platform,profile,handle,profile)).fetchone()
                        conn.execute("""
                          UPDATE videos SET creator_id=%s WHERE id=%s AND creator_id IS NULL
                        """,(creator[0],video_id))
                if record["published_at"] is not None:
                    conn.execute("""
                      UPDATE videos SET published_at=COALESCE(published_at,%s::timestamptz)
                      WHERE id=%s
                    """,(record["published_at"],video_id))
                snapkey="mi.bd."+digest([snapshot_id,platform,native])[:58]
                inserted=conn.execute("""
                  INSERT INTO metric_snapshots(video_id,source_id,source_policy_version_id,
                    observed_at,views,likes,comments,shares,saves,precision,
                    expires_at,data_quality_status,observation_time_assumed,
                    source_metric_definition,source_metric_version,
                    snapshot_key,ingest_job_id,raw_payload_ref)
                  VALUES(%s,%s,%s,now(),%s,%s,%s,%s,%s,'exact',
                    now()+interval '90 days','ok',FALSE,
                    %s,%s,%s,%s,%s)
                  ON CONFLICT DO NOTHING RETURNING id
                """,(video_id,source,policy,
                     record["views"],record["likes"],record["comments"],
                     record["shares"],record["saves"],
                     platform+"_public_views","brightdata_on_demand_v1",
                     snapkey,job_id,"brightdata:snapshot:"+snapshot_id)).fetchone()
                if inserted:
                    # Preserve numeric fields returned by the provider.
                    # No comment bodies, usernames or entire post JSON.
                    conn.execute("""
                      INSERT INTO mi_brightdata_numeric_fields(
                        metric_snapshot_id,project_id,video_id,platform,source_id,
                        fields,observed_at,expires_at)
                      VALUES(%s,%s,%s,%s,%s,%s::jsonb,now(),now()+interval '90 days')
                    """,(inserted[0],payload["project_id"],video_id,platform,source,
                         json.dumps(record["numeric_fields"],ensure_ascii=False)))
                    imported+=1
                    if record["views"] is not None:views_count+=1
            conn.execute("""
              UPDATE jobs SET state='completed',result=%s::jsonb,locked_by=NULL,
                  lease_until=NULL,updated_at=now()
              WHERE id=%s AND lease_generation=%s
            """,(json.dumps({"snapshot_id":snapshot_id,"phase":"completed",
                  "imported":imported,"with_views":views_count,
                  "returned_records":len(records)}),job_id,generation))
    return imported

def run_once():
    row=_claim()
    if row is None:return False
    job_id,payload,result,generation=row
    phase=result or {}
    try:
        if not phase.get("snapshot_id"):
            # This step can incur provider charges. A failed ambiguous trigger
            # is NEVER retried automatically.
            sid=bd.trigger(payload["platform"],payload["urls"])
            phase={"snapshot_id":sid,"phase":"poll","polls":0}
            _save_stage(job_id,generation,"queued",phase,delay_seconds=10)
            return True
        sid=phase["snapshot_id"]
        state=bd.progress(sid)
        if state=="failed":
            _save_stage(job_id,generation,"failed",phase,error="Bright Data collection failed")
        elif state in ("starting","running"):
            polls=int(phase.get("polls",0))+1
            phase["polls"]=polls
            if polls>90:
                _save_stage(job_id,generation,"failed",phase,
                      error="Collection not ready; snapshot retained for manual investigation")
            else:
                _save_stage(job_id,generation,"queued",phase,delay_seconds=20)
        else:
            records=bd.download(sid)
            _ingest(job_id,generation,payload,sid,records)
    except Exception as exc:
        # Resume only if the paid trigger is already persisted.
        if phase.get("snapshot_id") and int(phase.get("polls",0))<90:
            phase["polls"]=int(phase.get("polls",0))+1
            _save_stage(job_id,generation,"queued",phase,delay_seconds=60,
                error="Bright Data temporarily unavailable")
        else:
            _save_stage(job_id,generation,"failed",phase,
                error="Bright Data processing failed; no automatic paid retry")
    return True

def _loop():
    while True:
        try:
            if not run_once():time.sleep(3)
        except Exception:time.sleep(5)

def start_worker():
    global _thread
    if not bd.readiness()["ready"]:return
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread=threading.Thread(target=_loop,daemon=True,name="mi-brightdata")
            _thread.start()

def status(project_id:str) -> dict:
    with d1.connect() as conn:
        rows=conn.execute("""
          SELECT id,state,payload->>'platform',result,last_error,created_at
          FROM jobs WHERE job_type=%s AND payload->>'project_id'=%s
          ORDER BY created_at DESC,id DESC LIMIT 100
        """,(JOB_TYPE,project_id)).fetchall()
    return {"ok":True,"provider":bd.readiness(),
        "jobs":[{"id":rid,"state":state,"platform":platform,
                  "found":(result or {}).get("imported",0),
                  "error_code":"collection_failed" if err else None,
                  "created_at":created.isoformat()}
                for rid,state,platform,result,err,created in rows],
        "completed_batches":sum(state=="completed" for _,state,*_ in rows)}
