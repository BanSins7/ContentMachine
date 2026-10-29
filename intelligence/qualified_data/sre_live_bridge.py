"""Authenticated, DB-replayed SRE M36 -> QDP LIVE event consumer.

This is not the incompatible legacy 'sre-analytics-event/v1' receiver.
It imports only verified SRE public contracts, and authenticates *origin*
from the persisted SRE outbox on the SAME PostgreSQL database. A digest
alone is never a claim that the producer is trusted.
"""
from __future__ import annotations
from copy import deepcopy

from intelligence.qualified_data.contracts import QDPError,sha256
from intelligence.qualified_data.repository import _connect
from intelligence.relevance.ports.outbound import validate_live_result_event
from intelligence.relevance.ports.qdp_reference import validate_invalidation_event

_READY="sre.classifications_ready/v1"
_INVALID="sre.results_invalidated/v1"


def _deny(code):
    raise QDPError(code,"SRE LIVE source or QDP receipt cannot be verified",409)


def receive_sre_live_item(event, *, connect=None):
    """Commit one original signed-by-DB SRE item or immutable revocation.

    No QDP run/job is started here. The canonical QDP legacy schema binds
    BIGINT SRE results while SRE M30 results use UUIDs; silently building a
    legacy qualification would be a data-integrity violation.
    """
    if type(event) is not dict:
        _deny("sre_live_event_invalid")
    if event.get("schema_version")==_READY:
        verified=validate_live_result_event(event)
        kind="ready"
    elif event.get("schema_version")==_INVALID:
        verified=validate_invalidation_event(event)
        kind="invalidated"
    else:
        _deny("sre_live_event_version")
    pid,eid=verified["project_id"],verified["event_id"]
    factory=connect or _connect
    with factory() as conn:
        with conn.transaction():
            # SRE leases and receipts are the authority; client payload and
            # hashes alone are NOT trusted.
            source=conn.execute(
                """SELECT kind,project_id,run_id::text,item_id,
                          payload,event_sha256,state,prior_ready_event_id
                   FROM sre_live_result_outbox
                   WHERE project_id=%s AND event_id=%s::uuid
                   FOR SHARE""",(pid,eid),
            ).fetchone()
            if source is None:
                _deny("sre_live_outbox_missing")
            (src_kind,src_pid,rid,item_id,payload,digest,status,original)=source
            if (src_kind!=kind or src_pid!=pid or
                rid!=verified["run_id"] or
                type(item_id) is not int or item_id<1 or
                payload!=verified or digest!=verified["event_sha256"] or
                status not in ("leased","acked") or
                (kind=="invalidated" and str(original)!=verified["prior_ready_event_id"])):
                _deny("sre_live_outbox_mismatch")
            # Project scope and LIVE run must be authentic, not browser labels.
            run=conn.execute(
                """SELECT execution_mode,state,cancel_requested
                   FROM sre_live_runs
                   WHERE project_id=%s AND run_id=%s::uuid
                   FOR SHARE""",(pid,rid),
            ).fetchone()
            if run is None or run[0]!="live":
                _deny("sre_live_not_authenticated")
            if kind=="ready":
                if run[1]!="completed" or run[2] is not False:
                    _deny("sre_live_not_completed")
                current=conn.execute(
                    """SELECT 1 FROM sre_m36_live_only_export
                       WHERE project_id=%s AND event_id=%s::uuid
                         AND event_sha256=%s""",
                    (pid,eid,digest),
                ).fetchone()
                if current is None:
                    _deny("sre_live_rights_revoked")
                tombstone=conn.execute(
                    """SELECT prior_ready_event_sha256 FROM qdp_sre_live_tombstones
                       WHERE project_id=%s AND prior_ready_event_id=%s::uuid""",
                    (pid,eid),
                ).fetchone()
                if tombstone is not None:
                    if tombstone[0]!=digest:
                        _deny("sre_live_tombstone_collision")
                    _deny("sre_live_event_already_invalidated")
            else:
                prior=conn.execute(
                    """SELECT event_sha256,run_id::text,item_id,project_id
                       FROM sre_live_result_outbox
                       WHERE project_id=%s AND event_id=%s::uuid
                         AND kind='ready' FOR SHARE""",
                    (pid,verified["prior_ready_event_id"]),
                ).fetchone()
                if (prior is None or prior[0]!=verified["prior_ready_event_sha256"]
                    or prior[1]!=rid or prior[2]!=item_id or prior[3]!=pid):
                    _deny("sre_live_invalidation_not_authoritative")
            previous=conn.execute(
                """SELECT project_id,sre_run_id::text,item_id,video_id,
                          kind,event_sha256,prior_ready_event_id,
                          prior_ready_event_sha256
                   FROM qdp_sre_live_events
                   WHERE event_id=%s::uuid FOR UPDATE""",
                (eid,),
            ).fetchone()
            identity=(pid,rid,item_id,verified["video_id"],kind,digest,
                      verified.get("prior_ready_event_id"),
                      verified.get("prior_ready_event_sha256"))
            if previous is not None:
                original_identity=(previous[0],previous[1],previous[2],previous[3],
                                   previous[4],previous[5],
                                   str(previous[6]) if previous[6] else None,
                                   previous[7])
                if original_identity!=identity:
                    _deny("sre_live_event_id_collision")
            else:
                conn.execute(
                    """INSERT INTO qdp_sre_live_events
                       (event_id,project_id,sre_run_id,item_id,video_id,kind,
                        event_sha256,prior_ready_event_id,prior_ready_event_sha256)
                       VALUES(%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s::uuid,%s)""",
                    (eid,pid,rid,item_id,verified["video_id"],kind,digest,
                     verified.get("prior_ready_event_id"),
                     verified.get("prior_ready_event_sha256")),
                )
            if kind=="invalidated":
                tomb=conn.execute(
                    """SELECT prior_ready_event_sha256,invalidation_event_id
                       FROM qdp_sre_live_tombstones
                       WHERE project_id=%s AND prior_ready_event_id=%s::uuid
                       FOR UPDATE""",(pid,verified["prior_ready_event_id"]),
                ).fetchone()
                if tomb is not None and (
                    tomb[0]!=verified["prior_ready_event_sha256"] or
                    str(tomb[1])!=eid):
                    _deny("sre_live_invalidation_collision")
                if tomb is None:
                    conn.execute(
                        """INSERT INTO qdp_sre_live_tombstones
                           (project_id,prior_ready_event_id,
                            prior_ready_event_sha256,invalidation_event_id)
                           VALUES (%s,%s::uuid,%s,%s::uuid)""",
                        (pid,verified["prior_ready_event_id"],
                         verified["prior_ready_event_sha256"],eid),
                    )
    receipt=get_sre_live_receipt(pid,eid,connect=connect)
    if receipt is None or receipt["event_sha256"]!=digest:
        _deny("sre_live_receipt_readback_failed")
    return receipt


def get_sre_live_receipt(project_id,event_id,*,connect=None):
    factory=connect or _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            row=conn.execute(
                """SELECT kind,event_sha256,sre_run_id::text,video_id,
                          prior_ready_event_id::text
                   FROM qdp_sre_live_events
                   WHERE project_id=%s AND event_id=%s::uuid""",
                (project_id,event_id),
            ).fetchone()
            if row is None:
                return None
            kind,digest,rid,vid,prior=row
            if kind=="invalidated":
                tomb=conn.execute(
                    """SELECT prior_ready_event_sha256 FROM qdp_sre_live_tombstones
                       WHERE project_id=%s AND prior_ready_event_id=%s::uuid
                         AND invalidation_event_id=%s::uuid""",
                    (project_id,prior,event_id),
                ).fetchone()
                if tomb is None:
                    _deny("sre_live_tombstone_receipt_missing")
            return {
                "schema_version":"qdp.sre_live_item_receipt/v1",
                "event_id":event_id,"project_id":project_id,
                "sre_run_id":rid,"video_id":vid,"kind":kind,
                "event_sha256":digest,"persisted":True,
                "qualification_created":False,
            }
