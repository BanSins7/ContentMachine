"""VC-MOD-10 — D1 checkpoint receipt is the only Collector completion gate.

A DTK 'done' response is never a completion record. Recovery can finalize a
previously committed immutable page without a new GET, another timestamp,
a second video upsert or a second DTK POST. No network calls in this module.
"""
from __future__ import annotations

from .contracts import CollectorError, require_int
from .repository import _connection
from intelligence.taxonomy_builder.pipeline_contracts import canonical

SCHEMA_VERSION="collector.d1_completion/v1"


def finish_from_checkpoint(ctx: dict, *, receipt: dict | None = None,
                           connect=None) -> dict | None:
    """Atomic stage transition with exact current lease and project scope.

    Return None only if D1 checkpoint is not yet present. A malformed,
    mismatched or incomplete checkpoint raises instead of marking completed.
    """
    if type(ctx) is not dict:
        raise CollectorError("ingest_worker_only","Claimed task context required",409)
    for key in ("id","run_id","job_id","request_id","generation"):
        require_int(ctx.get(key),key,1,2**63-1)
    if (type(ctx.get("worker_id")) is not str
        or not 1<=len(ctx["worker_id"])<=100
        or type(ctx.get("provider_task_id")) is not str):
        raise CollectorError("ingest_worker_only","Worker lease or provider ID invalid",409)
    if receipt is not None and (
        type(receipt) is not dict or receipt.get("d1_persisted") is not True
        or receipt.get("provider_completed") is not False
        or receipt.get("run_id") != ctx["run_id"]
        or receipt.get("source_request_id") != ctx["request_id"]
    ):
        raise CollectorError("ingest_receipt_invalid","D1 writer receipt required",409)
    with _connection(connect) as conn:
        with conn.transaction():
            run=conn.execute(
                "SELECT cancel_requested FROM search_runs WHERE id=%s FOR UPDATE",
                (ctx["run_id"],),
            ).fetchone()
            if run is None or run[0] is True:
                raise CollectorError("collector_cancelled","Run was cancelled",409)
            owner=conn.execute(
                "SELECT project_id,stage FROM collector_runs WHERE run_id=%s FOR UPDATE",
                (ctx["run_id"],),
            ).fetchone()
            if owner is None or owner[1]!="released":
                raise CollectorError("collector_not_released","Collector release is not active",409)
            pid=owner[0]
            claimed=conn.execute(
                """SELECT t.state,t.provider_task_id,j.state,j.locked_by,
                          j.lease_generation
                   FROM collector_provider_tasks t JOIN jobs j ON j.id=t.job_id
                   WHERE t.id=%s AND t.run_id=%s AND t.source_request_id=%s
                     AND t.job_id=%s AND j.job_type='collector.search.held'
                   FOR UPDATE OF t,j""",
                (ctx["id"],ctx["run_id"],ctx["request_id"],ctx["job_id"]),
            ).fetchone()
            fence=conn.execute(
                """SELECT id FROM jobs WHERE id=%s AND state='running'
                     AND locked_by=%s AND lease_generation=%s
                     AND lease_until>now()""",
                (ctx["job_id"],ctx["worker_id"],ctx["generation"]),
            ).fetchone()
            if (claimed is None or fence is None or claimed[0]!="submitted"
                or claimed[1]!=ctx["provider_task_id"]
                or claimed[2]!="running" or claimed[3]!=ctx["worker_id"]
                or claimed[4]!=ctx["generation"]):
                raise CollectorError("collector_lost_lease","Stale owner cannot complete D1 job",409)
            rows=conn.execute(
                """SELECT id,project_id,run_id,source_request_id,provider_task_id,
                          page_sha256,accepted_count,rejected_count,inserted_count,
                          duplicate_count,saved_count,qa_summary
                   FROM collector_ingest_pages
                   WHERE provider_task_row_id=%s AND run_id=%s
                     AND source_request_id=%s AND page_ordinal=0
                     AND project_id=%s""",
                (ctx["id"],ctx["run_id"],ctx["request_id"],pid),
            ).fetchall()
            if not rows:
                if receipt is not None:
                    raise CollectorError("ingest_checkpoint_missing","Writer receipt not committed",409)
                return None
            if len(rows)!=1:
                raise CollectorError("ingest_checkpoint_conflict","More than one page checkpoint",409)
            (page_id,project_id,run_id,request_id,provider_task_id,digest,
             accepted,rejected,inserted,duplicates,saved,qa)=rows[0]
            if (project_id!=pid or run_id!=ctx["run_id"]
                or request_id!=ctx["request_id"]
                or provider_task_id!=ctx["provider_task_id"]
                or type(qa) is not dict
                or accepted!=saved or inserted+duplicates!=saved
                or type(accepted) is not int or accepted<0):
                raise CollectorError("ingest_checkpoint_conflict","Persisted page identity mismatch",409)
            if receipt is not None and (
                receipt.get("project_id")!=pid
                or receipt.get("page_id")!=page_id
                or receipt.get("page_sha256")!=digest
                or receipt.get("saved_count")!=saved
            ):
                raise CollectorError("ingest_checkpoint_conflict","D1 writer and checkpoint disagree",409)
            counts=conn.execute(
                """SELECT
                  (SELECT count(*) FROM collector_ingest_page_videos
                   WHERE page_id=%s AND project_id=%s),
                  (SELECT count(*) FROM collector_page_video_queries
                   WHERE page_id=%s AND project_id=%s),
                  (SELECT count(*) FROM collector_sre_outbox
                   WHERE page_id=%s AND project_id=%s),
                  (SELECT count(*) FROM collector_task_queries
                   WHERE source_request_id=%s)""",
                (page_id,pid,page_id,pid,page_id,pid,ctx["request_id"]),
            ).fetchone()
            if (counts is None or counts[0]!=saved or counts[2]!=saved
                or counts[3]<1 or counts[1]!=saved*counts[3]):
                raise CollectorError("ingest_checkpoint_incomplete",
                                     "Missing video links, QV identifiers or SRE events",409)
            summary={
                "schema_version":SCHEMA_VERSION,
                "page_id":page_id,
                "page_sha256":digest,
                "accepted_count":accepted,
                "saved_count":saved,
                "inserted_count":inserted,
                "duplicate_count":duplicates,
                "rejected_count":rejected,
                "overflow_count":qa.get("overflow_count",0),
                "sre_events":counts[2],
                "metadata_persisted":True,
                "d1_persisted":True,
                "ingestion_pending":False,
                "metrics_persisted":False,
                "raw_result_persisted":False,
            }
            updated=conn.execute(
                """UPDATE collector_provider_tasks SET state='completed',
                     response_summary=%s::jsonb,polled_at=now(),
                     finished_at=now(),last_error_code=NULL
                   WHERE id=%s AND state='submitted' RETURNING id""",
                (canonical(summary),ctx["id"]),
            ).fetchone()
            if updated is None:
                raise CollectorError("collector_lost_lease","Task completion conflict",409)
            request=conn.execute(
                """UPDATE source_requests SET state='succeeded',finished_at=now(),
                     error_code=NULL WHERE id=%s AND run_id=%s
                     AND state='started' RETURNING id""",
                (ctx["request_id"],ctx["run_id"]),
            ).fetchone()
            if request is None:
                raise CollectorError("collector_request_state","Source request not started",409)
            job=conn.execute(
                """UPDATE jobs SET state='completed',locked_by=NULL,lease_until=NULL,
                     updated_at=now(),result=%s::jsonb
                   WHERE id=%s AND state='running' AND locked_by=%s
                     AND lease_generation=%s RETURNING id""",
                (canonical(summary),ctx["job_id"],ctx["worker_id"],ctx["generation"]),
            ).fetchone()
            if job is None:
                raise CollectorError("collector_lost_lease","Job completion conflict",409)
            from .worker import _finalize
            _finalize(conn,ctx["run_id"])
            return {"status":"completed","run_id":ctx["run_id"],
                    "page_id":page_id,"page_sha256":digest,
                    "saved_count":saved,"inserted_count":inserted,
                    "duplicate_count":duplicates,"rejected_count":rejected,
                    "d1_persisted":True,"ingestion_pending":False,
                    "replayed_checkpoint":receipt is None}
