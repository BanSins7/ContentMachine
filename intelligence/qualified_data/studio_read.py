"""FIX-16 QDP Studio's read-only, bounded, project-scoped database projections.

No provider calls, no media reads/downloads, no implicit consumer delivery.
Historical immutable facts never bypass current rights and human veto.
"""
from __future__ import annotations
import uuid
from datetime import datetime
from .contracts import QDPError,project_id,positive_id
from .repository import _connect,_uuid


def _serialize(row):
    return {k:v.isoformat() if isinstance(v,datetime) else
            str(v) if isinstance(v,uuid.UUID) else v
            for k,v in dict(row).items()}


def eligible_sre_runs(project, *, limit=25, cursor=None, connect=None):
    pid=project_id(project)
    if type(limit) is not int or not 1<=limit<=100:
        raise QDPError("invalid_limit","SRE page size must be 1..100",422)
    with (connect or _connect)() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            from .intake import read_eligible_sre_run_list
            page=read_eligible_sre_run_list(conn,pid,limit=limit,cursor=cursor)
    return {"ok":True,"runs":[_serialize(row) for row in page["runs"]],
            "next_cursor":page["next_cursor"],"paid_calls":0}


def _require_run(conn,pid,rid):
    exists=conn.execute(
        """SELECT id FROM qdp_runs WHERE project_id=%s AND id=%s::uuid""",
        (pid,rid)).fetchone()
    if not exists:
        raise QDPError("run_not_found","No QDP run in this project",404)


def diagnostics(project,run_id,*,connect=None):
    pid,rid=project_id(project),_uuid(run_id,"QDP run")
    with (connect or _connect)() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            _require_run(conn,pid,rid)
            group=conn.execute(
                """SELECT p.analysis_group,
                   count(*) FILTER(WHERE p.state='ready') AS historical_ready,
                   count(*) FILTER(WHERE live.run_id IS NOT NULL) AS currently_ready,
                   count(*) FILTER(WHERE p.state='blocked') AS historical_blocked
                   FROM qdp_preparations p
                   LEFT JOIN qdp_group_current_preparations live
                     ON live.project_id=p.project_id AND live.run_id=p.run_id
                    AND live.video_id=p.video_id
                    AND live.analysis_group=p.analysis_group
                  WHERE p.project_id=%s AND p.run_id=%s::uuid
                  GROUP BY p.analysis_group ORDER BY p.analysis_group""",
                (pid,rid)).fetchall()
            delivery=conn.execute(
                """SELECT o.analysis_group,o.event_type,d.consumer,d.state,count(*) AS n
                     FROM qdp_outbox o JOIN qdp_outbox_deliveries d
                       ON d.event_id=o.event_id
                    WHERE o.project_id=%s AND o.run_id=%s::uuid
                    GROUP BY o.analysis_group,o.event_type,d.consumer,d.state
                    ORDER BY o.analysis_group,o.event_type,d.consumer,d.state""",
                (pid,rid)).fetchall()
            rev=conn.execute(
                """SELECT count(*) AS n FROM qdp_invalidations
                   WHERE project_id=%s AND run_id=%s::uuid""",
                (pid,rid)).fetchone()
            watermark=conn.execute(
                """SELECT invalidation_generation, cancel_requested
                   FROM qdp_runs WHERE project_id=%s AND id=%s::uuid""",
                (pid,rid)).fetchone()
    return {"ok":True,"run_id":rid,
            "groups":[_serialize(r) for r in group],
            "deliveries":[_serialize(r) for r in delivery],
            "invalidation_count":rev["n"],
            "invalidation_generation":watermark["invalidation_generation"],
            "cancel_requested":watermark["cancel_requested"],
            "consumer_ack_is_analysis_complete":False,"paid_calls":0}


def events(project,run_id,*,limit=50,cursor=None,connect=None):
    pid,rid=project_id(project),_uuid(run_id,"QDP run")
    if type(limit) is not int or not 1<=limit<=100:
        raise QDPError("invalid_limit","Event page size must be 1..100",422)
    with (connect or _connect)() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            _require_run(conn,pid,rid)
            anchor=None
            if cursor is not None:
                cid=_uuid(cursor,"outbox event cursor")
                anchor=conn.execute(
                    """SELECT created_at,event_id FROM qdp_outbox
                       WHERE project_id=%s AND run_id=%s::uuid
                         AND event_id=%s::uuid""",
                    (pid,rid,cid)).fetchone()
                if anchor is None:
                    raise QDPError("event_cursor_not_found","Event not in QDP run",404)
            t=anchor["created_at"] if anchor else None
            eid=anchor["event_id"] if anchor else None
            rows=conn.execute(
                """SELECT o.event_id,o.event_type,o.video_id,o.analysis_group,
                          o.created_at,d.consumer,d.state AS delivery_state,
                          d.attempts,d.reason_code
                   FROM qdp_outbox o LEFT JOIN qdp_outbox_deliveries d
                     ON d.event_id=o.event_id
                  WHERE o.project_id=%s AND o.run_id=%s::uuid
                    AND (%s::timestamptz IS NULL OR
                         (o.created_at,o.event_id)<(%s::timestamptz,%s::uuid))
                  ORDER BY o.created_at DESC,o.event_id DESC LIMIT %s""",
                (pid,rid,t,t,eid,limit+1)).fetchall()
    page=rows[:limit]
    return {"ok":True,"events":[_serialize(v) for v in page],
            "next_cursor":str(page[-1]["event_id"]) if len(rows)>limit else None}


def lineage(project,run_id,video_id,*,connect=None):
    """Current-granted only; no historical evidence leaked after human veto."""
    pid,rid,vid=project_id(project),_uuid(run_id,"QDP run"),positive_id(video_id,"video")
    with (connect or _connect)() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            _require_run(conn,pid,rid)
            scoped=conn.execute(
                """SELECT 1 FROM qdp_items
                   WHERE project_id=%s AND run_id=%s::uuid AND video_id=%s""",
                (pid,rid,vid)).fetchone()
            if scoped is None:
                raise QDPError("video_not_in_run","Video is not qualified in this run",404)
            visible=conn.execute(
                """SELECT 1 FROM qdp_group_current_preparations
                   WHERE project_id=%s AND run_id=%s::uuid AND video_id=%s
                   LIMIT 1""",(pid,rid,vid)).fetchone()
            if visible is None:
                return {"ok":True,"video_id":vid,"current":False,
                        "lineage":[],"source":None,
                        "reason_code":"current_evidence_or_rights_revoked"}
            rows=conn.execute(
                """SELECT category_key,sre_result_id,sre_live_result_id::text AS sre_live_result_id,content_version_id,
                          evidence_sha256,model_decision,effective_decision,
                          human_label_id,sre_live_review_id::text AS sre_live_review_id
                   FROM qdp_item_lineage
                   WHERE project_id=%s AND run_id=%s::uuid AND video_id=%s
                   ORDER BY category_key""",(pid,rid,vid)).fetchall()
            data=conn.execute(
                """SELECT e.source_id,e.source_policy_version_id,e.metadata_id,
                          e.media_asset_id,e.metric_snapshot_ids
                     FROM qdp_current_base_enrichments e
                    WHERE e.project_id=%s AND e.run_id=%s::uuid AND e.video_id=%s""",
                (pid,rid,vid)).fetchone()
            allowed=conn.execute(
                """SELECT analysis_group FROM qdp_group_current_preparations
                   WHERE project_id=%s AND run_id=%s::uuid AND video_id=%s""",
                (pid,rid,vid)).fetchall()
    authorized_groups={x["analysis_group"] for x in allowed}
    safe_source=_serialize(data) if data else None
    if safe_source is not None:
        # A/B media rights are independent from C metric/scoring rights.
        if not authorized_groups.intersection({"A","B"}):
            safe_source["media_asset_id"]=None
        if "C" not in authorized_groups:
            safe_source["metric_snapshot_ids"]=[]
    return {"ok":True,"video_id":vid,"current":True,
            "current_groups":sorted(authorized_groups),
            "lineage":[_serialize(x) for x in rows],
            "source":safe_source}


def manifest_summary(project,run_id,*,connect=None):
    pid,rid=project_id(project),_uuid(run_id,"QDP run")
    with (connect or _connect)() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            _require_run(conn,pid,rid)
            row=conn.execute(
                """SELECT manifest_sha256,selected_sha256,taxonomy_sha256,
                          expected_videos,expected_pairs,created_at
                     FROM qdp_run_manifests
                    WHERE project_id=%s AND run_id=%s::uuid""",
                (pid,rid)).fetchone()
    if row is None:
        raise QDPError("manifest_not_found","Run manifest unavailable",503)
    return {"ok":True,"run_id":rid,"manifest":_serialize(row),"paid_calls":0}
