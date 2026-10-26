"""VC-MOD-12: project-scoped, read-only Collector run catalogue and D1 trace.

Only canonical database facts are returned. No DTK calls, raw provider JSON,
media download, model/analytics processing or synthesized SRE relevance.
The host HTTP handler remains responsible for same-origin authorization.
"""
from __future__ import annotations

from .contracts import CollectorError, PROJECT_RE, require_id, require_int
from .repository import _connection, _run, get_run


def _project(project_id):
    return require_id(project_id, "project_id", PROJECT_RE)


def _run_id(run_id):
    return require_int(run_id, "run_id", 1, 10**18 - 1)


def _limit(limit):
    return require_int(limit, "limit", 1, 50)


def _iso(value):
    return value.isoformat() if value is not None else None


def list_runs(project_id: str, *, limit=20, before_run_id=None, connect=None):
    """Latest real Collector runs; keyset pagination, not global search_runs."""
    pid=_project(project_id)
    size=_limit(limit)
    before=(_run_id(before_run_id) if before_run_id is not None else None)
    with _connection(connect) as conn:
        rows=conn.execute(
            """SELECT c.run_id,r.state,c.stage,r.created_at,r.finished_at,
                      c.plan_id,c.query_set_id,p.source_id,
                      m.id,m.status,m.validation_run_id,
                      COALESCE((SELECT COUNT(*) FROM collector_provider_tasks t
                                WHERE t.run_id=c.run_id),0),
                      COALESCE((SELECT COUNT(*) FROM collector_ingest_pages ip
                                WHERE ip.project_id=c.project_id
                                  AND ip.run_id=c.run_id),0),
                      COALESCE((SELECT SUM(ip.saved_count)
                                FROM collector_ingest_pages ip
                                WHERE ip.project_id=c.project_id
                                  AND ip.run_id=c.run_id),0),
                      COALESCE((SELECT COUNT(*) FROM collector_sre_outbox e
                                JOIN collector_ingest_pages ip ON ip.id=e.page_id
                                WHERE ip.project_id=c.project_id
                                  AND ip.run_id=c.run_id
                                  AND e.project_id=c.project_id),0)
               FROM collector_runs c
               JOIN search_runs r ON r.id=c.run_id
               JOIN collector_plans p ON p.id=c.plan_id AND p.project_id=c.project_id
               LEFT JOIN collector_query_shards sh ON sh.query_set_id=c.query_set_id
                    AND sh.project_id=c.project_id
               LEFT JOIN collector_qv_manifests m ON m.id=sh.manifest_id
                    AND m.project_id=sh.project_id
               WHERE c.project_id=%s AND (%s::bigint IS NULL OR c.run_id<%s)
               ORDER BY c.run_id DESC LIMIT %s""",
            (pid,before,before,size+1),
        ).fetchall()
    more=len(rows)>size
    rows=rows[:size]
    runs=[{"run_id":row[0],"state":row[1],"stage":row[2],
           "created_at":_iso(row[3]),"finished_at":_iso(row[4]),
           "plan_id":str(row[5]),"query_set_id":str(row[6]),
           "source_id":row[7],
           "manifest_id":str(row[8]) if row[8] is not None else None,
           "manifest_status":row[9],
           "validation_run_id":str(row[10]) if row[10] is not None else None,
           "task_count":row[11],"d1_persisted_pages":row[12],
           "d1_saved_videos":row[13],"sre_ready_events":row[14]}
          for row in rows]
    return {"schema_version":"collector.run_list/v1","project_id":pid,
            "runs":runs,"has_more":more,
            "next_before_run_id":runs[-1]["run_id"] if more else None,
            "external_calls":0}


def _cursor(after):
    if after is None:
        return (0,0)
    if type(after) is not str or len(after)>50 or after.count(".")!=1:
        raise CollectorError("invalid_cursor","Expected page_id.video_id cursor",400)
    left,right=after.split(".")
    if not (left.isdigit() and right.isdigit()) or (
        left.startswith("0") or right.startswith("0")
    ):
        raise CollectorError("invalid_cursor","Cursor requires positive canonical IDs",400)
    return (_run_id(int(left)),_run_id(int(right)))


def list_run_results(project_id: str, run_id: int, *, limit=20,
                     after=None, connect=None):
    """Only actually persisted D1 page-video rows, never provider responses."""
    pid=_project(project_id)
    rid=_run_id(run_id)
    size=_limit(limit)
    page_after,video_after=_cursor(after)
    with _connection(connect) as conn:
        _run(conn,pid,rid)  # 404 on wrong project, before ANY result read
        rows=conn.execute(
            """SELECT ip.id,ip.page_ordinal,ip.persisted_at,ip.page_sha256,
                      ip.source_request_id,ip.provider_task_id,
                      pv.video_id,pv.quality,v.platform_video_id,
                      v.canonical_url,v.title,v.content_kind,v.published_at,
                      v.duration_seconds,
                      pv.discovery_id,d.method,pv.source_metadata_id,m.fetched_at,
                      e.id,e.state,e.attempts,e.last_error_code,e.delivered_at,
                      ARRAY(SELECT q.candidate_id
                            FROM collector_page_video_queries q
                            WHERE q.page_id=ip.id AND q.project_id=ip.project_id
                              AND q.video_id=pv.video_id
                            ORDER BY q.candidate_id),
                      ARRAY(SELECT DISTINCT t.hypothesis_id
                            FROM collector_page_video_queries q
                            JOIN collector_task_queries t
                              ON t.source_request_id=q.source_request_id
                             AND t.query_id=q.candidate_id
                            WHERE q.page_id=ip.id AND q.project_id=ip.project_id
                              AND q.video_id=pv.video_id
                            ORDER BY t.hypothesis_id)
               FROM collector_ingest_pages ip
               JOIN collector_ingest_page_videos pv
                    ON pv.page_id=ip.id AND pv.project_id=ip.project_id
               JOIN videos v ON v.id=pv.video_id
               JOIN discoveries d ON d.id=pv.discovery_id AND d.video_id=pv.video_id
               JOIN source_video_metadata m ON m.id=pv.source_metadata_id
                   AND m.video_id=pv.video_id
               LEFT JOIN collector_sre_outbox e
                    ON e.page_id=ip.id AND e.project_id=ip.project_id
                   AND e.video_id=pv.video_id
                   AND e.discovery_id=pv.discovery_id
                   AND e.source_metadata_id=pv.source_metadata_id
               WHERE ip.project_id=%s AND ip.run_id=%s
                  AND (ip.id>%s OR (ip.id=%s AND pv.video_id>%s))
               ORDER BY ip.id,pv.video_id LIMIT %s""",
            (pid,rid,page_after,page_after,video_after,size+1),
        ).fetchall()
    more=len(rows)>size
    rows=rows[:size]
    videos=[]
    for row in rows:
        (page_id,ordinal,persisted_at,page_sha,request_id,provider_task_id,
         video_id,quality,platform_id,url,title,content_kind,published_at,
         duration,discovery_id,method,metadata_id,fetched_at,
         event_id,event_state,attempts,error_code,delivered_at,
         queries,hypotheses)=row
        videos.append({
            "page_id":page_id,"page_ordinal":ordinal,
            "page_sha256":page_sha,"d1_persisted_at":_iso(persisted_at),
            "source_request_id":request_id,"provider_task_id":provider_task_id,
            "video_id":video_id,"platform_video_id":platform_id,
            "canonical_url":url,"title":title,"content_kind":content_kind,
            "published_at":_iso(published_at),
            "duration_seconds":str(duration) if duration is not None else None,
            "quality":quality,"discovery_id":discovery_id,
            "discovery_method":method,"source_metadata_id":metadata_id,
            "source_observed_at":_iso(fetched_at),
            "candidate_ids":list(queries or []),
            "hypothesis_ids":list(hypotheses or []),
            "sre":{"event_id":str(event_id) if event_id is not None else None,
                   "delivery_state":event_state or "missing",
                   "attempts":attempts or 0,"last_error_code":error_code,
                   "delivered_at":_iso(delivered_at),
                   "relevance_state":"not_evaluated",
                   "classification_verified":False},
        })
    last=videos[-1] if videos else None
    return {"schema_version":"collector.run_results/v1",
            "project_id":pid,"run_id":rid,"videos":videos,
            "has_more":more,
            "next_after":f'{last["page_id"]}.{last["video_id"]}' if more else None,
            "d1_only":True,"external_calls":0}


def get_run_trace(project_id: str, run_id: int, *, connect=None):
    """A grounded C9/SQG/QV→DTK→D1→SRE provenance summary per task."""
    pid=_project(project_id)
    rid=_run_id(run_id)
    with _connection(connect) as conn:
        _run(conn,pid,rid)  # exactly this owner
        ancestry=conn.execute(
            """SELECT c.query_set_id,q.generation_id,q.publication_id,
                      g.taxonomy_version,g.taxonomy_sha256,
                      p.source_id,p.plan_sha256,m.id,m.validation_run_id,
                      m.status,m.selected_count,sh.shard_ordinal,
                      sh.query_count,m.manifest_sha256
               FROM collector_runs c
               JOIN collector_query_sets q ON q.id=c.query_set_id
                    AND q.project_id=c.project_id
               JOIN sqg_generations g ON g.id=q.generation_id
                    AND g.project_id=q.project_id
               JOIN collector_plans p ON p.id=c.plan_id
                    AND p.project_id=c.project_id
               LEFT JOIN collector_query_shards sh ON sh.query_set_id=q.id
                    AND sh.project_id=c.project_id
               LEFT JOIN collector_qv_manifests m ON m.id=sh.manifest_id
                    AND m.project_id=c.project_id
               WHERE c.project_id=%s AND c.run_id=%s""",
            (pid,rid),
        ).fetchone()
        if ancestry is None:
            raise CollectorError("trace_not_found","Collector lineage missing",404)
        rows=conn.execute(
            """SELECT t.id,t.state,t.provider_task_id,t.last_error_code,
                      sr.id,sr.state,j.state,
                      (SELECT COUNT(*) FROM collector_ingest_pages ip
                       WHERE ip.project_id=%s AND ip.run_id=t.run_id
                         AND ip.provider_task_row_id=t.id),
                      (SELECT COALESCE(SUM(ip.saved_count),0)
                       FROM collector_ingest_pages ip
                       WHERE ip.project_id=%s AND ip.run_id=t.run_id
                         AND ip.provider_task_row_id=t.id),
                      (SELECT COALESCE(SUM(ip.rejected_count),0)
                       FROM collector_ingest_pages ip
                       WHERE ip.project_id=%s AND ip.run_id=t.run_id
                         AND ip.provider_task_row_id=t.id),
                      (SELECT COALESCE(SUM(ip.inserted_count),0)
                       FROM collector_ingest_pages ip
                       WHERE ip.project_id=%s AND ip.run_id=t.run_id
                         AND ip.provider_task_row_id=t.id),
                      (SELECT COALESCE(SUM(ip.duplicate_count),0)
                       FROM collector_ingest_pages ip
                       WHERE ip.project_id=%s AND ip.run_id=t.run_id
                         AND ip.provider_task_row_id=t.id),
                      (SELECT COUNT(*) FROM collector_sre_outbox e
                       JOIN collector_ingest_pages ip ON ip.id=e.page_id
                       WHERE ip.project_id=%s AND ip.run_id=t.run_id
                         AND ip.provider_task_row_id=t.id AND e.project_id=%s),
                      (SELECT COUNT(*) FROM collector_sre_outbox e
                       JOIN collector_ingest_pages ip ON ip.id=e.page_id
                       WHERE ip.project_id=%s AND ip.run_id=t.run_id
                         AND ip.provider_task_row_id=t.id AND e.project_id=%s
                         AND e.state='delivered'),
                      (SELECT COUNT(*) FROM collector_sre_outbox e
                       JOIN collector_ingest_pages ip ON ip.id=e.page_id
                       WHERE ip.project_id=%s AND ip.run_id=t.run_id
                         AND ip.provider_task_row_id=t.id AND e.project_id=%s
                         AND e.state='blocked'),
                      (SELECT COUNT(*) FROM collector_sre_outbox e
                       JOIN collector_ingest_pages ip ON ip.id=e.page_id
                       WHERE ip.project_id=%s AND ip.run_id=t.run_id
                         AND ip.provider_task_row_id=t.id AND e.project_id=%s
                         AND e.state='pending'),
                      (SELECT COUNT(*) FROM collector_sre_outbox e
                       JOIN collector_ingest_pages ip ON ip.id=e.page_id
                       WHERE ip.project_id=%s AND ip.run_id=t.run_id
                         AND ip.provider_task_row_id=t.id AND e.project_id=%s
                         AND e.state='leased')
               FROM collector_provider_tasks t
               JOIN source_requests sr ON sr.id=t.source_request_id
               JOIN jobs j ON j.id=t.job_id
               WHERE t.run_id=%s ORDER BY t.id""",
            (pid,pid,pid,pid,pid,
             pid,pid,pid,pid,pid,pid,pid,pid,pid,pid,rid),
        ).fetchall()
        queryrows=conn.execute(
            """SELECT t.id,l.query_id,l.hypothesis_id,l.direction_id
               FROM collector_provider_tasks t
               JOIN collector_task_queries l ON l.source_request_id=t.source_request_id
               WHERE t.run_id=%s ORDER BY t.id,l.query_id""",
            (rid,),
        ).fetchall()
    by_task={}
    for task_id,qid,hyp,direction in queryrows:
        entry=by_task.setdefault(task_id,{"candidate_ids":[],"hypothesis_ids":set(),
                                           "direction_ids":set()})
        entry["candidate_ids"].append(qid)
        entry["hypothesis_ids"].add(hyp)
        entry["direction_ids"].add(direction)
    tasks=[]
    for row in rows:
        (task_id,state,provider_task_id,error_code,request_id,request_state,job_state,
         pages,saved,rejected,inserted,duplicate,sre_total,delivered,blocked,
         pending,leased)=row
        q=by_task.get(task_id,{"candidate_ids":[],"hypothesis_ids":set(),
                               "direction_ids":set()})
        tasks.append({
            "task_id":task_id,"provider_task_id":provider_task_id,
            "task_state":state,"request_id":request_id,
            "source_request_state":request_state,"job_state":job_state,
            "last_error_code":error_code,
            "candidate_ids":sorted(q["candidate_ids"]),
            "hypothesis_ids":sorted(q["hypothesis_ids"]),
            "direction_ids":sorted(q["direction_ids"]),
            "d1_pages":pages,"d1_saved_videos":saved,
            "d1_rejected_examined":rejected,"d1_new_videos":inserted,
            "d1_duplicate_videos":duplicate,
            "sre_events":sre_total,"sre_pending":pending,
            "sre_leased":leased,"sre_delivered":delivered,
            "sre_blocked":blocked,
            "d1_checkpoint_present":pages>0,
            "completion_receipt_rechecked_here":False,
        })
    (qid,gid,pub,taxversion,taxsha,source,plan_sha,manifest_id,validation_id,
     manifest_status,selected_count,shard_ordinal,shard_count,manifest_sha)=ancestry
    return {
        "schema_version":"collector.run_trace/v1",
        "project_id":pid,"run_id":rid,
        "lineage":{
            "c9_sp_snapshot_verified_here":False,
            "generation_id":str(gid),"query_set_id":str(qid),
            "taxonomy_publication_id":str(pub),
            "taxonomy_version":taxversion,"taxonomy_sha256":taxsha,
            "manifest_id":str(manifest_id) if manifest_id is not None else None,
            "manifest_sha256":manifest_sha,
            "validation_run_id":str(validation_id) if validation_id is not None else None,
            "qv_status":manifest_status or "not_linked",
            "qv_selected_count":selected_count,
            "shard_ordinal":shard_ordinal,"shard_query_count":shard_count,
            "source_id":source,"plan_sha256":plan_sha,
        },
        "tasks":tasks,
        "summary":{"task_count":len(tasks),
                   "d1_persisted_pages":sum(x["d1_pages"] for x in tasks),
                   "d1_saved_videos":sum(x["d1_saved_videos"] for x in tasks),
                   "sre_ready_events":sum(x["sre_events"] for x in tasks),
                   "sre_confirmed_delivered":sum(x["sre_delivered"] for x in tasks)},
        "sre_relevance_verified":False,
        "external_calls":0,
    }
