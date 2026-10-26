"""Transactional, project-scoped, non-executing Collector run repository.

Query sets must be sealed by a *trusted* future SQG Query Validation publisher.
No public endpoint inserts into collector_query_sets. Only completed SQG runs
with matching, immutable RTB snapshots may be loaded for a committed plan.

New jobs are held at run_after=infinity: existing generic job workers must not
accidentally dispatch them before Phase III explicit release and rights checks.
"""
from __future__ import annotations

from intelligence.taxonomy_builder.pipeline_contracts import canonical
from intelligence.taxonomy_builder.pipeline_repository import _connect
from intelligence.search_planner.input_loader import read_published_snapshot

from .contracts import CollectorError, HASH_RE, require_uuid
from .query_intake import parse_validated_query_set
from .planner import build_preview
from .policy_gate import inspect_source_policy, require_policy, zero_spend_cap
import re

_KEY = re.compile(r"[A-Za-z0-9_.-]{12,100}\Z")


def _connection(connect):
    return (connect or _connect)()


def _trusted_query_set(conn, query_set_id: str):
    """Revalidate DB-stored attestation, completed generation and RTB ancestry."""
    qid = require_uuid(query_set_id, "query_set_id")
    row = conn.execute(
        """SELECT q.project_id,q.publication_id,q.generation_id,
                  q.query_set,q.query_set_sha256,q.validation_origin,
                  g.status,g.publication_id,g.taxonomy_sha256,g.taxonomy_version
           FROM collector_query_sets q
           JOIN sqg_generations g ON g.id=q.generation_id AND g.project_id=q.project_id
           WHERE q.id=%s::uuid""",
        (qid,),
    ).fetchone()
    if row is None:
        raise CollectorError("query_set_not_found", "No trusted query set found", 404)
    (pid,pub,gid,raw,digest,origin,state,gen_pub,gen_tax,gen_version) = row
    if origin != "trusted_query_validation/v1" or state != "completed":
        raise CollectorError("query_set_not_verified", "Query Validation/SQG is not completed", 409)
    parsed = parse_validated_query_set(
        raw, expected_project_id=pid, expected_publication_id=str(pub))
    if (parsed.fingerprint != digest or parsed.generation_id != str(gid)
        or parsed.taxonomy_sha256 != gen_tax
        or parsed.taxonomy_version != gen_version
        or parsed.taxonomy_publication_id != str(gen_pub)):
        raise CollectorError("query_set_integrity", "Query set and SQG snapshot differ", 409)
    approved = read_published_snapshot(conn,pid,str(pub))
    if approved["taxonomy_sha256"] != parsed.taxonomy_sha256:
        raise CollectorError("stale_taxonomy", "RTB taxonomy digest mismatch", 409)
    nodes = {n["id"]:n for n in approved["tree"]}
    for query in parsed.queries:
        hyp = nodes.get(query.hypothesis_id)
        direction = nodes.get(query.direction_id)
        if not hyp or hyp["kind"] != "hypothesis" or not direction or direction["kind"] != "direction":
            raise CollectorError("invalid_query_lineage", "Query references unapproved RTB nodes", 409)
        current = hyp
        seen = set()
        while current["parent_id"] is not None:
            if current["id"] in seen:
                raise CollectorError("invalid_query_lineage", "Cyclic RTB path", 409)
            seen.add(current["id"])
            current = nodes.get(current["parent_id"])
            if current is None:
                raise CollectorError("invalid_query_lineage", "Invalid RTB ancestor", 409)
        if current["id"] != query.direction_id:
            raise CollectorError("invalid_query_lineage", "Query RTB direction mismatch", 409)
    return qid, parsed


def trusted_preview(query_set_id: str, source_id: str, limits=None, *, connect=None):
    with _connection(connect) as conn:
        with conn.transaction():
            _, query_set = _trusted_query_set(conn,query_set_id)
            plan = build_preview(query_set,source_id=source_id,limits=limits)
            policy = inspect_source_policy(
                conn,source_id,
                max_requests=plan["summary"]["max_external_requests_upper_bound"],
                max_pages=plan["limits"]["max_pages_per_task"])
    plan["query_provenance_verified"] = True
    plan["blocks"] = [b for b in plan["blocks"] if b not in
                      ("query_set_not_server_verified","source_policy_not_verified")]
    plan["blocks"] = list(dict.fromkeys(plan["blocks"]+policy["blocks"]))
    plan["policy"] = policy
    plan["trusted_query_set_id"] = query_set_id
    return plan


def _validated_commit_request(request):
    expected = {"query_set_id","source_id","limits","plan_hash",
                "idempotency_key","confirm","max_spend_usd"}
    if type(request) is not dict or set(request) != expected:
        raise CollectorError("invalid_commit", "Invalid or unknown commit fields")
    if request["confirm"] is not True:
        raise CollectorError("consent_required", "Explicit confirmation required")
    key = request["idempotency_key"]
    if type(key) is not str or _KEY.fullmatch(key) is None:
        raise CollectorError("invalid_idempotency_key", "Invalid idempotency key")
    digest = request["plan_hash"]
    if type(digest) is not str or HASH_RE.fullmatch(digest) is None:
        raise CollectorError("invalid_plan_hash", "Invalid plan hash")
    zero_spend_cap(request["max_spend_usd"])
    return key


def commit_run(request: dict, *, connect=None) -> dict:
    """One transaction: held run, source requests, jobs, task lineage and audit.

    This function NEVER sends HTTP or configures a worker. Existing workers
    see a future infinity run_after and cannot claim these jobs.
    """
    key = _validated_commit_request(request)
    with _connection(connect) as conn:
        with conn.transaction():
            # Resolve immutable query set and lock its project to serialize
            # concurrent owners/keys and provider quota snapshot checks.
            qid, queryset = _trusted_query_set(conn,request["query_set_id"])
            row=conn.execute(
                "SELECT id FROM project_registry WHERE id=%s FOR UPDATE",
                (queryset.project_id,)).fetchone()
            if row is None:
                raise CollectorError("project_not_found", "Project no longer exists",404)
            # Recheck after lock to avoid race with concurrent approvals.
            qid, queryset = _trusted_query_set(conn,request["query_set_id"])
            preview = build_preview(queryset,source_id=request["source_id"],
                                    limits=request["limits"])
            if preview["plan_hash"] != request["plan_hash"]:
                raise CollectorError("stale_preview", "Preview changed; refresh first",409)
            hard_blocks = set(preview["blocks"]) - {
                "query_set_not_server_verified","source_policy_not_verified",
                "provider_connection_not_verified"}
            if hard_blocks:
                raise CollectorError("plan_blocked", "Plan not executable: "+",".join(sorted(hard_blocks)),409)
            # Only an authorized *plan*, not external dispatch or paid consent.
            config={"plan_sha256":preview["plan_hash"],"query_set_id":qid,
                    "source_id":request["source_id"],"held":True}
            global_key="collector:"+queryset.project_id+":"+key
            existing=conn.execute(
                """SELECT r.id,r.config,c.project_id,c.plan_id,r.state
                   FROM search_runs r
                   LEFT JOIN collector_runs c ON c.run_id=r.id
                   WHERE r.idempotency_key=%s""", (global_key,),
            ).fetchone()
            if existing is not None:
                if existing[1]!=config or existing[2]!=queryset.project_id:
                    raise CollectorError("idempotency_conflict","Key reused for different plan",409)
                return {"run_id":existing[0],"state":existing[4],
                        "status":"planned_held" if existing[4]=="created" else existing[4],
                        "replayed":True,"external_calls":0,"spend_usd":"0.00"}
            policy=inspect_source_policy(
                conn,request["source_id"],
                max_requests=preview["summary"]["max_external_requests_upper_bound"],
                max_pages=preview["limits"]["max_pages_per_task"])
            require_policy(policy)
            plan_record=conn.execute(
                """INSERT INTO collector_plans
                   (project_id,query_set_id,source_id,policy_version_id,plan_sha256,snapshot)
                   VALUES (%s,%s::uuid,%s,%s,%s,%s::jsonb)
                   ON CONFLICT (project_id,plan_sha256,source_id,policy_version_id)
                   DO NOTHING RETURNING id""",
                (queryset.project_id,qid,request["source_id"],policy["policy_version_id"],
                 preview["plan_hash"],canonical(preview)),
            ).fetchone()
            if plan_record is None:
                plan_record=conn.execute(
                    """SELECT id FROM collector_plans WHERE project_id=%s
                       AND plan_sha256=%s AND source_id=%s AND policy_version_id=%s""",
                    (queryset.project_id,preview["plan_hash"],
                     request["source_id"],policy["policy_version_id"]),
                ).fetchone()
            plan_id=str(plan_record[0])
            run_id=conn.execute(
                """INSERT INTO search_runs(idempotency_key,config,planner_version,state)
                   VALUES (%s,%s::jsonb,'collector-stage2-held-v1','created')
                   RETURNING id""", (global_key,canonical(config)),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO collector_runs
                   (run_id,project_id,plan_id,query_set_id,policy_version_id)
                   VALUES (%s,%s,%s::uuid,%s::uuid,%s)""",
                (run_id,queryset.project_id,plan_id,qid,policy["policy_version_id"]),
            )
            query_by_id={q.query_id:q for q in queryset.queries}
            for task in preview["tasks"]:
                key_hash=task["task_fingerprint"]
                request_id=conn.execute(
                    """INSERT INTO source_requests
                       (run_id,source_id,logical_request_key,state,charge_state)
                       VALUES (%s,%s,%s,'planned','not_applicable')
                       RETURNING id""",
                    (run_id,request["source_id"],"collector:"+str(run_id)+":"+key_hash),
                ).fetchone()[0]
                job_id=conn.execute(
                    """INSERT INTO jobs
                       (job_type,idempotency_key,payload,max_attempts,state,run_after)
                       VALUES ('collector.search.held',%s,%s::jsonb,1,'queued',
                               'infinity'::timestamptz)
                       RETURNING id""",
                    ("collector:job:"+str(run_id)+":"+key_hash[:24],
                     canonical({"run_id":run_id,"source_request_id":request_id,
                                "task_sha256":key_hash})),
                ).fetchone()[0]
                conn.execute("UPDATE source_requests SET job_id=%s WHERE id=%s",
                             (job_id,request_id))
                conn.execute(
                    """INSERT INTO collector_provider_tasks
                       (run_id,source_request_id,job_id,task_sha256)
                       VALUES (%s,%s,%s,%s)""",
                    (run_id,request_id,job_id,key_hash),
                )
                for query_id in task["query_ids"]:
                    query = query_by_id[query_id]
                    conn.execute(
                        """INSERT INTO collector_task_queries
                           (source_request_id,query_id,hypothesis_id,direction_id)
                           VALUES (%s,%s,%s,%s)""",
                        (request_id,query_id,query.hypothesis_id,query.direction_id),
                    )
            conn.execute(
                """INSERT INTO audit_events(action,source_id,object_type,object_id,details)
                   VALUES ('collector.plan_committed',%s,'collector_run',%s,%s::jsonb)""",
                (request["source_id"],str(run_id),canonical({
                    "project_id":queryset.project_id,"plan_sha256":preview["plan_hash"],
                    "task_count":len(preview["tasks"]),
                    "held":True,"external_calls":0,"spend_usd":"0.00",
                })),
            )
            return {"run_id":run_id,"project_id":queryset.project_id,
                    "plan_id":plan_id,"plan_hash":preview["plan_hash"],
                    "state":"created","status":"planned_held",
                    "task_count":len(preview["tasks"]),"replayed":False,
                    "external_calls":0,"spend_usd":"0.00"}


def _run(conn, project_id, run_id, lock=False):
    if type(run_id) is not int or run_id<=0:
        raise CollectorError("invalid_run_id","Invalid run ID")
    suffix = " FOR UPDATE OF r" if lock else ""
    row=conn.execute(
        """SELECT r.id,r.state,r.cancel_requested,r.created_at,r.finished_at,
                  c.stage,c.plan_id,p.plan_sha256,p.snapshot,c.policy_version_id
           FROM search_runs r
           JOIN collector_runs c ON c.run_id=r.id
           JOIN collector_plans p ON p.id=c.plan_id AND p.project_id=c.project_id
           WHERE r.id=%s AND c.project_id=%s"""+suffix,(run_id,project_id),
    ).fetchone()
    if row is None:
        raise CollectorError("run_not_found","Collector run not found for project",404)
    return row


def get_run(project_id: str, run_id: int, *, connect=None) -> dict:
    from .contracts import PROJECT_RE, require_id
    pid=require_id(project_id,"project",PROJECT_RE)
    with _connection(connect) as conn:
        row=_run(conn,pid,run_id)
        grant=conn.execute(
            """SELECT consumed,reserved_remaining,request_cap
               FROM collector_execution_grants WHERE run_id=%s""",(run_id,),
        ).fetchone()
        tasks=conn.execute(
            """SELECT t.id,t.state,t.provider_task_id,t.task_sha256,
                      j.state,s.state,s.id
               FROM collector_provider_tasks t
               JOIN source_requests s ON s.id=t.source_request_id
               JOIN jobs j ON j.id=t.job_id
               WHERE t.run_id=%s ORDER BY t.id""", (run_id,),
        ).fetchall()
        persisted=conn.execute(
            """SELECT COUNT(*),COALESCE(SUM(saved_count),0),
                      COALESCE(SUM(inserted_count),0),
                      COALESCE(SUM(duplicate_count),0)
               FROM collector_ingest_pages
               WHERE project_id=%s AND run_id=%s""",(pid,run_id),
        ).fetchone()
        outgoing=conn.execute(
            """SELECT COUNT(*) FROM collector_sre_outbox e
               JOIN collector_ingest_pages p ON p.id=e.page_id
               WHERE p.project_id=%s AND p.run_id=%s AND e.project_id=%s""",
            (pid,run_id,pid),
        ).fetchone()
        saved_pages,saved_videos,new_videos,duplicate_videos=(persisted or (0,0,0,0))
        sre_events=(outgoing or (0,))[0]
        completed_tasks=sum(x[1]=="completed" for x in tasks)
        # The public status is grounded only in committed, immutable D1 rows.
        d1_complete=(bool(tasks) and saved_pages==completed_tasks==len(tasks)
                     and sre_events==saved_videos)
        return {
            "d1_persisted_pages":saved_pages,
            "d1_saved_videos":saved_videos,
            "d1_new_videos":new_videos,
            "d1_duplicate_videos":duplicate_videos,
            "sre_ready_events":sre_events,
            "ingest_pending_tasks":sum(x[1]=="submitted" for x in tasks),
            "d1_completion_verified":d1_complete,
            "d1_integrity_warning":row[1]=="completed" and not d1_complete,
            "run_id":row[0],"project_id":pid,"state":row[1],
            "cancel_requested":row[2],"created_at":row[3].isoformat(),
            "finished_at":row[4].isoformat() if row[4] else None,
            "status":row[5],"plan_id":str(row[6]),"plan_hash":row[7],
            "policy_version_id":row[9],
            "task_count":len(tasks),
            "submit_intents":grant[0] if grant else 0,
            "quota_reserved_remaining":grant[1] if grant else 0,
            "provider_request_cap":grant[2] if grant else 0,
            "known_provider_task_ids":sum(x[2] is not None for x in tasks),
            "unknown_outcomes":sum(x[1]=="unknown" for x in tasks),
            "provider_charges_independently_verified":False,
            "actual_spend_usd":None,
            "tasks":[{"task_id":x[0],"state":x[1],"provider_task_id":x[2],
                      "task_sha256":x[3],"job_state":x[4],
                      "source_request_state":x[5],"source_request_id":x[6]}
                     for x in tasks],
            "external_dispatch_authorized":row[5] in ("released","cancelling"),
        }


def cancel_run(project_id: str, run_id: int, *, connect=None) -> dict:
    from .contracts import PROJECT_RE, require_id
    pid=require_id(project_id,"project",PROJECT_RE)
    with _connection(connect) as conn:
        with conn.transaction():
            row=_run(conn,pid,run_id,lock=True)
            if row[5] in ("released","cancelling"):
                from .execution import cancel_released
                return cancel_released(conn,pid,run_id)
            if row[1] not in ("created","cancelled") or row[5] not in ("planned_held","cancelled"):
                raise CollectorError("run_not_cancellable",
                                     "Only held, unsubmitted runs may be cancelled",409)
            if row[1] == "cancelled":
                return {"run_id":run_id,"project_id":pid,
                        "status":"cancelled","external_calls":0}
            conn.execute(
                """UPDATE search_runs SET cancel_requested=TRUE,state='cancelled',
                   finished_at=COALESCE(finished_at,now()) WHERE id=%s""", (run_id,),
            )
            conn.execute(
                "UPDATE collector_runs SET stage='cancelled' WHERE run_id=%s",(run_id,),
            )
            conn.execute(
                """UPDATE jobs SET state='cancelled',updated_at=now()
                   WHERE id IN (SELECT job_id FROM collector_provider_tasks WHERE run_id=%s)
                   AND state='queued'""",(run_id,),
            )
            conn.execute(
                """UPDATE collector_provider_tasks SET state='cancelled'
                   WHERE run_id=%s AND state='not_submitted'""",(run_id,),
            )
            conn.execute(
                """INSERT INTO audit_events(action,object_type,object_id,details)
                   VALUES ('collector.plan_cancelled','collector_run',%s,%s::jsonb)""",
                (str(run_id),canonical({"project_id":pid,"external_calls":0})),
            )
    return {"run_id":run_id,"project_id":pid,
            "status":"cancelled","external_calls":0}
