"""VC-MOD-09: atomic, project-scoped, lease-fenced canonical D1 ingest.

Only a dedicated Collector worker should call ingest_page() with its current
claimed task and an ephemeral InspectedPage. No provider/network request,
new video catalog, SRE classifier, raw TikTok blob, or metric-history write.
The entire checkpoint/videos/metadata/lineage/SRE outbox unit is one PG TX.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from intelligence.taxonomy_builder.pipeline_contracts import canonical, sha256
from .contracts import CollectorError, PROJECT_RE, require_id, require_int, require_uuid
from .inspector_contract import serialize_inspected_page, parse_inspected_page
from .provider_inspector import InspectedPage
from .repository import _connection

SOURCE_ID = "dtk_tiktok"
SCHEMA_VERSION = "collector.ingest_receipt/v1"


def _conflict(code: str, detail: str) -> None:
    raise CollectorError(code, detail, 409)


def _task_scope(ctx: dict, page: dict) -> dict:
    if type(ctx) is not dict:
        _conflict("ingest_worker_only", "Dedicated Worker context is required")
    try:
        keys = ("id", "run_id", "job_id", "request_id", "worker_id",
                "generation", "provider_task_id", "state")
        values = {key: ctx[key] for key in keys}
    except KeyError:
        _conflict("ingest_worker_only", "Incomplete Collector lease context")
    for key in ("id", "run_id", "job_id", "request_id", "generation"):
        require_int(values[key], key, 1, 2**63 - 1)
    if (values["state"] != "submitted"
        or type(values["worker_id"]) is not str
        or not 1 <= len(values["worker_id"]) <= 100
        or values["provider_task_id"] != page["provider_task_id"]):
        _conflict("ingest_worker_only", "Submitted task and verified provider ID required")
    return values


def _current_scope(conn, ctx: dict, page: dict) -> tuple[str, int, str]:
    """After search_run → collector_run locks, pin project/source/lease/policy."""
    run = conn.execute(
        "SELECT id,cancel_requested FROM search_runs WHERE id=%s FOR UPDATE",
        (ctx["run_id"],),
    ).fetchone()
    if run is None or run[1]:
        _conflict("ingest_cancelled", "Search run no longer permits ingest")
    owner = conn.execute(
        """SELECT project_id,stage,policy_version_id
           FROM collector_runs WHERE run_id=%s FOR UPDATE""",
        (ctx["run_id"],),
    ).fetchone()
    if owner is None or owner[0] != page["project_id"] or owner[1] != "released":
        _conflict("ingest_project_scope", "Published project is not released")
    pid = require_id(owner[0], "project_id", PROJECT_RE)
    task = conn.execute(
        """SELECT t.provider_task_id,t.state,j.state,j.locked_by,
                  j.lease_generation,j.lease_until,
                  g.policy_version_id,g.source_id,c.plan_id,
                  q.generation_id,q.publication_id,p.plan_sha256,
                  s.id,s.can_store_metadata,s.status,
                  v.is_active,v.reviewed_at,v.permissions
           FROM collector_provider_tasks t
           JOIN jobs j ON j.id=t.job_id
           JOIN collector_runs c ON c.run_id=t.run_id
           JOIN collector_plans p ON p.id=c.plan_id AND p.project_id=c.project_id
           JOIN collector_query_sets q ON q.id=c.query_set_id AND q.project_id=c.project_id
           JOIN collector_execution_grants g ON g.run_id=c.run_id
           JOIN sources s ON s.id=g.source_id
           JOIN source_policy_versions v ON v.id=g.policy_version_id
           WHERE t.id=%s AND t.run_id=%s AND t.source_request_id=%s
             AND t.job_id=%s AND c.project_id=%s
           FOR UPDATE OF t,j""",
        (ctx["id"], ctx["run_id"], ctx["request_id"],ctx["job_id"],pid),
    ).fetchone()
    if task is None:
        _conflict("ingest_task_not_found", "Worker task not in project")
    (provider_id,state,job_state,locked_by,lease_gen,lease_until,
     policy_id,source_id,_plan_id,gen_id,pub_id,plan_sha,
     _sid,can_store,source_state,active,reviewed,permissions) = task
    # The database clock, not the worker local clock, determines expiry.
    fenced=conn.execute(
        """SELECT 1 FROM jobs WHERE id=%s AND state='running'
             AND locked_by=%s AND lease_generation=%s AND lease_until>now()""",
        (ctx["job_id"],ctx["worker_id"],ctx["generation"]),
    ).fetchone()
    if (not fenced or job_state != "running"
        or locked_by != ctx["worker_id"] or lease_gen != ctx["generation"]
        or state != "submitted" or provider_id != ctx["provider_task_id"]
        or source_id != SOURCE_ID or policy_id != owner[2]
        or source_state != "enabled" or can_store is not True
        or active is not True or reviewed is None
        or type(permissions) is not dict
        or permissions.get("can_store_metadata") is not True
        or permissions.get("automated_collection_authorized") is not True
        or permissions.get("upstream_archive_disabled_confirmed") is not True):
        _conflict("ingest_lease_or_policy", "Worker lease or storage rights revoked")
    if (str(gen_id) != page["generation_id"]
        or str(pub_id) != page["taxonomy_publication_id"]):
        _conflict("ingest_provenance_mismatch", "QV generation/publication differs")
    return pid,policy_id,plan_sha


def _retention_deadline(conn, policy_id: int, fetched_at: datetime) -> datetime:
    """Re-read reviewed source retention; NULL or invalid policy blocks ingest.

    The shorter enforceable source/policy term wins. New metadata, immutable
    checkpoint and SRE outbox carry an explicit due-date; they never imply
    audit rows can be deleted under the 057 FK/immutable triggers.
    """
    row=conn.execute(
        """SELECT s.retention_days,p.retention_policy,now()
           FROM source_policy_versions p
           JOIN sources s ON s.id=p.source_id
           WHERE p.id=%s AND p.is_active=TRUE AND p.reviewed_at IS NOT NULL
             AND s.id='dtk_tiktok' AND s.status='enabled'""",
        (policy_id,),
    ).fetchone()
    if row is None or type(row[0]) is not int or not isinstance(row[1],dict):
        _conflict("ingest_retention_policy","Reviewed retention policy unavailable")
    source_days=row[0]
    policy_days=row[1].get("retention_days")
    if (type(policy_days) is not int or source_days<1 or policy_days<1
        or source_days>36500 or policy_days>36500):
        _conflict("ingest_retention_policy","Invalid retention duration")
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        _conflict("ingest_retention_policy","Observed time must have timezone")
    deadline=fetched_at+timedelta(days=min(source_days,policy_days))
    if (len(row)<3 or not isinstance(row[2],datetime)
        or row[2].tzinfo is None or row[2].utcoffset() is None):
        _conflict("ingest_retention_policy","Database time unavailable")
    if deadline<=row[2]:
        _conflict("ingest_retention_expired",
                  "Provider observation is older than approved retention")
    return deadline


def _verified_query_links(conn, ctx: dict, page: dict) -> dict[str, tuple[str,str]]:
    rows=conn.execute(
        """SELECT query_id,hypothesis_id,direction_id
           FROM collector_task_queries WHERE source_request_id=%s
           ORDER BY query_id""",
        (ctx["request_id"],),
    ).fetchall()
    observed={}
    for q,h,d in rows:
        if q in observed or type(q) is not str:
            _conflict("ingest_query_lineage", "Repeated invalid verified query")
        observed[q]=(h,d)
    if not observed or set(observed) != set(page["query_ids"]):
        _conflict("ingest_query_lineage", "Unverified source request QV identifiers")
    if set(h for h,_ in observed.values()) != set(page["hypothesis_ids"]):
        _conflict("ingest_query_lineage", "Hypothesis provenance mismatch")
    return observed


def _replay_receipt(conn, ctx: dict, page: dict, page_ordinal: int,
                    digest: str, snapshot_sha: str) -> dict | None:
    prior=conn.execute(
        """SELECT id,project_id,source_request_id,provider_task_id,
                  page_sha256,snapshot_sha256,lease_generation,
                  accepted_count,rejected_count,inserted_count,
                  duplicate_count,saved_count
           FROM collector_ingest_pages
           WHERE provider_task_row_id=%s AND page_ordinal=%s""",
        (ctx["id"],page_ordinal),
    ).fetchone()
    if prior is None:
        return None
    (id_,pid,req,provider,hash_,snapshot,lease,accepted,rejected,
     inserted,duplicate,saved)=prior
    examined_rejected=page["rejected_count"]-page["overflow_count"]
    if (pid != page["project_id"] or req != ctx["request_id"]
        or provider != page["provider_task_id"] or hash_ != digest
        or snapshot != snapshot_sha or accepted != page["accepted_count"]
        or rejected != examined_rejected or saved != accepted
        or duplicate + inserted != saved):
        _conflict("ingest_checkpoint_conflict", "Existing page receipt has a different SHA or scope")
    count=conn.execute(
        """SELECT COUNT(*) FROM collector_ingest_page_videos
           WHERE page_id=%s AND project_id=%s""",(id_,pid),
    ).fetchone()
    links=conn.execute(
        """SELECT COUNT(*) FROM collector_page_video_queries
           WHERE page_id=%s AND project_id=%s""",(id_,pid),
    ).fetchone()
    events=conn.execute(
        """SELECT COUNT(*) FROM collector_sre_outbox
           WHERE page_id=%s AND project_id=%s""",(id_,pid),
    ).fetchone()
    if (count is None or count[0] != saved
        or links is None or links[0] != sum(
            len(item["query_ids"]) for item in page["items"]
        )
        or events is None or events[0] != saved):
        _conflict("ingest_checkpoint_incomplete", "Page receipt lacks its canonical D1 associations")
    return {"schema_version":SCHEMA_VERSION,"page_id":id_,"project_id":pid,
            "run_id":ctx["run_id"],"source_request_id":req,
            "page_sha256":hash_,"accepted_count":accepted,
            "saved_count":saved,"inserted_count":inserted,
            "duplicate_count":duplicate,"rejected_count":rejected,
            "overflow_count":page["overflow_count"],"sre_events":saved,"metrics_persisted":False,
            "provider_completed":False,"d1_persisted":True,"replayed":True}


def _upsert_video(conn, item: dict) -> tuple[int,bool]:
    """Upsert canonical ID inside the same page transaction, before receipt."""
    video=conn.execute(
        """INSERT INTO videos(platform,platform_video_id,canonical_url,
                  published_at,duration_seconds,title,content_kind)
           VALUES ('tiktok',%s,%s,%s,%s,%s,%s)
           ON CONFLICT(platform,platform_video_id) DO NOTHING
           RETURNING id""",
        (item["platform_video_id"],item["canonical_url"],
         item["published_at"],item["duration_seconds"],item["title"],
         item["content_kind"]),
    ).fetchone()
    inserted=video is not None
    if video is None:
        video=conn.execute(
            """SELECT id FROM videos WHERE platform='tiktok'
                 AND platform_video_id=%s""",(item["platform_video_id"],),
        ).fetchone()
    if video is None:
        _conflict("ingest_video_missing", "D1 video upsert did not return an identity")
    return video[0], inserted


def _persist_candidate(conn, ctx: dict, page_id: int, pid: str,
                       policy_id: int, page_sha: str, video_id: int,
                       item: dict, rank: int, expires_at: datetime,
                       query_links: dict[str,tuple[str,str]]) -> None:
    metadata=conn.execute(
        """INSERT INTO source_video_metadata
             (video_id,source_id,source_policy_version_id,fetched_at,
              title,published_at,duration_seconds,expires_at)
           VALUES (%s,'dtk_tiktok',%s,%s,%s,%s,%s,%s)
           ON CONFLICT(video_id,source_id,fetched_at) DO NOTHING
           RETURNING id""",
        (video_id,policy_id,item["fetched_at"],item["title"],
         item["published_at"],item["duration_seconds"],expires_at),
    ).fetchone()
    if metadata is None:
        metadata=conn.execute(
            """SELECT id FROM source_video_metadata
               WHERE video_id=%s AND source_id='dtk_tiktok' AND fetched_at=%s
                 AND deleted_at IS NULL""",(video_id,item["fetched_at"]),
        ).fetchone()
    if metadata is None:
        _conflict("ingest_metadata_conflict", "Canonical metadata observation unavailable")
    meta_id=metadata[0]
    key=sha256({"schema_version":"collector.discovery_key/v1",
                "source_request_id":ctx["request_id"],
                "platform_video_id":item["platform_video_id"],
                "source_id":SOURCE_ID})
    found=conn.execute(
        """INSERT INTO discoveries
             (video_id,source_id,method,source_rank,provider_request_id,
              run_id,source_request_id,discovery_key)
           VALUES (%s,'dtk_tiktok','collector.keyword',%s,NULL,%s,%s,%s)
           ON CONFLICT(discovery_key) WHERE discovery_key IS NOT NULL DO NOTHING
           RETURNING id""",
        (video_id,rank,ctx["run_id"],ctx["request_id"],key),
    ).fetchone()
    if found is None:
        found=conn.execute(
            """SELECT id,video_id FROM discoveries
               WHERE discovery_key=%s AND source_id='dtk_tiktok'
                 AND run_id=%s AND source_request_id=%s""",
            (key,ctx["run_id"],ctx["request_id"]),
        ).fetchone()
        if found is None or found[1]!=video_id:
            _conflict("ingest_discovery_conflict", "Duplicate occurrence does not match canonical D1")
    discovery_id=found[0]
    conn.execute(
        """INSERT INTO collector_ingest_page_videos
             (page_id,project_id,source_request_id,video_id,discovery_id,
              source_metadata_id,quality)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (page_id,pid,ctx["request_id"],video_id,discovery_id,meta_id,item["quality"]),
    )
    for query_id in item["query_ids"]:
        hypothesis,direction=query_links[query_id]
        conn.execute(
            """INSERT INTO collector_video_hypotheses
                 (source_request_id,video_id,query_id,hypothesis_id,discovery_id)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT(source_request_id,video_id,query_id) DO NOTHING""",
            (ctx["request_id"],video_id,query_id,hypothesis,discovery_id),
        )
        conn.execute(
            """INSERT INTO collector_page_video_queries
                 (page_id,project_id,source_request_id,video_id,candidate_id)
               VALUES (%s,%s,%s,%s,%s)""",
            (page_id,pid,ctx["request_id"],video_id,query_id),
        )
    delivery_key=sha256({"schema_version":"collector.sre_ready/v1",
                         "project_id":pid,"page_id":page_id,
                         "video_id":video_id,"discovery_id":discovery_id})
    evt={
        "schema_version":"collector.sre_ready/v1",
        "project_id":pid,"delivery_key":delivery_key,
        "evidence_scope":"metadata_only","relevance_state":"not_evaluated",
        "source_id":SOURCE_ID,"source_request_id":str(ctx["request_id"]),
        "run_id":str(ctx["run_id"]), "page_id":str(page_id),
        "page_sha256":page_sha,
        "video":{"video_id":str(video_id),"discovery_id":str(discovery_id),
                 "source_metadata_id":str(meta_id)},
        "candidate_ids":list(item["query_ids"]),
        "hypothesis_ids":list(item["hypothesis_ids"]),
        "content_kind":item["content_kind"],
        "quality":item["quality"],
    }
    conn.execute(
        """INSERT INTO collector_sre_outbox
             (project_id,page_id,video_id,discovery_id,source_metadata_id,
              delivery_key,event_payload,retention_until)
           VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
        (pid,page_id,video_id,discovery_id,meta_id,delivery_key,canonical(evt),expires_at),
    )
    # Project SRE workspace is not force-created: delivery stays pending
    # until the independent relevance subsystem is explicitly integrated.



def ingest_page(ctx: dict, inspected: InspectedPage, *, page_ordinal: int = 0,
                connect=None) -> dict:
    """Atomically store a true D1 page receipt with every canonical association.

    Source input is an in-process inspected page, NOT a REST DTO, untrusted
    file, manually supplied video ID or signed raw DTK response. Real provider
    status remains 'submitted' until VC-MOD-10 commits terminal job state.
    """
    if type(inspected) is not InspectedPage:
        _conflict("ingest_worker_only", "Only an in-process Inspector result is accepted")
    require_int(page_ordinal,"page_ordinal",0,0)  # current approved task caps pagination at one page
    safe=serialize_inspected_page(inspected)
    parsed,_=parse_inspected_page(safe,project_id=safe["project_id"],
                                 provider_task_id=safe["provider_task_id"])
    task=_task_scope(ctx,safe)
    if parsed.source_id!=SOURCE_ID or any(
        item.content_kind!="video" and item.quality=="valid" for item in parsed.items
    ):
        _conflict("ingest_invalid_video", "Inspector candidate is not a verified TikTok video")
    with _connection(connect) as conn:
        with conn.transaction():
            pid,policy_id,snapshot_sha=_current_scope(conn,task,safe)
            query_links=_verified_query_links(conn,task,safe)
            replay=_replay_receipt(conn,task,safe,page_ordinal,
                                   safe["payload_sha256"],snapshot_sha)
            if replay is not None:
                return replay
            # Validate ALL query/hypothesis links before first canonical write.
            for item in parsed.items:
                if (set(item.query_ids)!=set(query_links)
                    or set(item.hypothesis_ids)!=set(h for h,_ in query_links.values())):
                    _conflict("ingest_query_lineage", "Candidate lineage differs from approved request")
            if len(parsed.items)>50:
                _conflict("ingest_page_cap","Maximum 50 accepted results per page")
            expires_at=_retention_deadline(conn,policy_id,parsed.fetched_at)
            video_rows=[_upsert_video(conn,item) for item in safe["items"]]
            inserted=sum(int(created) for _,created in video_rows)
            saved=len(safe["items"])
            duplicate=saved-inserted
            page_row=conn.execute(
                """INSERT INTO collector_ingest_pages
                     (project_id,run_id,provider_task_row_id,source_request_id,
                      page_ordinal,provider_task_id,page_sha256,snapshot_sha256,
                      cursor_sha256,lease_generation,fetched_at,has_more,
                      raw_count,examined_count,accepted_count,rejected_count,
                      duplicate_count,inserted_count,saved_count,qa_summary,
                      metrics_persisted,retention_until)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           %s,%s,%s,%s,%s,%s,%s,%s::jsonb,FALSE,%s)
                   RETURNING id""",
                (pid,task["run_id"],task["id"],task["request_id"],
                 page_ordinal,task["provider_task_id"],safe["payload_sha256"],
                 snapshot_sha,sha256({"cursor_present":True}) if safe["cursor_present"] else None,
                 task["generation"],parsed.fetched_at,safe["has_more"],
                 safe["raw_item_count"],safe["examined_count"],
                 safe["accepted_count"],safe["rejected_count"]-safe["overflow_count"],
                 duplicate,inserted,saved,
                 canonical({"inspector_version":safe["inspector_version"],
                            "valid_count":safe["valid_count"],
                            "partial_count":safe["partial_count"],
                            "overflow_count":safe["overflow_count"],
                            "rejected_total_count":safe["rejected_count"],
                            "quality_reasons":safe["quality_reasons"],
                            "ingestion_pending":False}),expires_at),
            ).fetchone()
            if page_row is None:
                _conflict("ingest_page_missing", "Page receipt insertion failed")
            page_id=page_row[0]
            for rank,(item,(video_id,_)) in enumerate(zip(safe["items"],video_rows)):
                _persist_candidate(conn,task,page_id,pid,policy_id,
                                   safe["payload_sha256"],video_id,item,
                                   rank,expires_at,query_links)
            # Single atomic transaction: receipt is inserted once with final
            # counts, never UPDATE immutable collector_ingest_pages.
            return {"schema_version":SCHEMA_VERSION,"page_id":page_id,
                    "project_id":pid,"run_id":task["run_id"],
                    "source_request_id":task["request_id"],
                    "page_sha256":safe["payload_sha256"],
                    "accepted_count":saved,"saved_count":saved,
                    "inserted_count":inserted,"duplicate_count":duplicate,
                    "rejected_count":safe["rejected_count"]-safe["overflow_count"],
                    "overflow_count":safe["overflow_count"],"sre_events":saved,
                    "metrics_persisted":False,"provider_completed":False,
                    "d1_persisted":True,"replayed":False}
