"""GB-MOD-24 consumer-facing source-version refresh preparation.

Requests a NEW verified QDP inbox, never forges an inbox ACK, GA output,
paid approval or model job. An optional new draft is created only when a new
accepted inbox already exists and a fresh trusted host authenticates it.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from ..contracts import canonical_hash
from .freshness import InvalidationError
from .invalidation import (
    _id,_deny,_row,_queue_event,read_pinned_lineage,
    INVALIDATED,REFRESH_REQUESTED,REFRESH_DRAFT_CREATED,EVENT_VERSION,
)


@dataclass(frozen=True,slots=True)
class RefreshDraft:
    new_run_id:str
    old_run_id:str
    new_inbox_id:str
    event_id:str
    replayed:bool


def create_refresh_draft(conn,*,project_id,old_run_id,new_inbox_id,
                         config_sha256,verifier)->RefreshDraft:
    """No fake readiness. The new run is DRAFT without Group A pins, model
    quotes/attempts/jobs, or publication status. Later stages must bind exact
    A, rehash media, check the QDP delivery ACK and reapprove paid attempts.
    """
    from .freshness import _sha
    _id(project_id,old_run_id)
    if (type(new_inbox_id) is not str or
        type(config_sha256) is not str or not _sha(config_sha256) or
        not callable(getattr(conn,"transaction",None))):
        _deny("refresh_draft_input_invalid")
    try:
        if str(UUID(new_inbox_id))!=new_inbox_id:
            _deny("refresh_inbox_uuid_invalid")
    except ValueError:
        _deny("refresh_inbox_uuid_invalid")
    with conn.transaction():
        old_pins,prior=read_pinned_lineage(
            conn,project_id=project_id,run_id=old_run_id,locked=True,
        )
        if prior["state"]!="needs_review":
            _deny("refresh_draft_requires_invalidated_parent")
        event=conn.execute(
            """SELECT payload_sha256 FROM group_b_outbox
               WHERE project_id=%s AND run_id=%s::uuid AND event_type=%s
               ORDER BY created_at DESC,id DESC LIMIT 1""",
            (project_id,old_run_id,INVALIDATED)
        ).fetchone()
        if event is None:
            _deny("refresh_draft_requires_saved_invalidating_event")
        if new_inbox_id==str(prior["inbox_id"]):
            _deny("refresh_must_use_new_authenticated_qdp_handoff")
        qdp=_row(conn.execute(
            """SELECT id::text AS id,project_id,video_id,source_id,
                      event_key,qdp_run_id::text AS qdp_run_id,
                      preparation_sha256,event_sha256,status
               FROM group_b_inbox
               WHERE project_id=%s AND id=%s::uuid FOR SHARE""",
            (project_id,new_inbox_id)
        ))
        if (qdp is None or qdp["status"]!="accepted" or
            qdp["video_id"]!=old_pins.video_id or
            qdp["source_id"]!=old_pins.source_id):
            _deny("new_qdp_handoff_unverified_or_cross_source")
        source=_row(conn.execute(
            "SELECT status,can_analyze_media FROM sources WHERE id=%s FOR SHARE",
            (old_pins.source_id,)
        ))
        if source is None or source["status"]!="enabled" or source["can_analyze_media"] is not True:
            _deny("refresh_source_rights_revoked")
        verify=getattr(verifier,"verify_refresh_draft",None)
        if not callable(verify):
            _deny("refresh_draft_host_admission_missing")
        try:
            ok=verify(project_id,old_run_id,old_pins,qdp,
                      config_sha256,event[0],conn) is True
        except Exception:
            ok=False
        if not ok:
            _deny("refresh_draft_host_admission_denied")
        identity={
            "schema_version":EVENT_VERSION,
            "parent_run_id":old_run_id,"new_inbox_id":new_inbox_id,
            "qdp_event_sha256":qdp["event_sha256"],
            "qdp_preparation_sha256":qdp["preparation_sha256"],
            "config_sha256":config_sha256,
            "invalidation_payload_sha256":event[0],
        }
        unique="gb24-refresh-"+canonical_hash(identity)
        row=_row(conn.execute(
            """INSERT INTO group_b_runs(
                 project_id,inbox_id,video_id,source_id,content_type,
                 config_sha256,idempotency_key,state)
               VALUES (%s,%s::uuid,%s,%s,%s,%s,%s,'draft')
               ON CONFLICT(project_id,idempotency_key) DO NOTHING
               RETURNING id::text AS id,inbox_id::text AS inbox_id,
                         state,config_sha256""",
            (project_id,new_inbox_id,old_pins.video_id,old_pins.source_id,
             old_pins.content_type,config_sha256,unique)
        ))
        replayed=row is None
        if replayed:
            row=_row(conn.execute(
                """SELECT id::text AS id,inbox_id::text AS inbox_id,
                          state,config_sha256,video_id,source_id
                   FROM group_b_runs WHERE project_id=%s AND idempotency_key=%s""",
                (project_id,unique)
            ))
            if (row is None or row["video_id"]!=old_pins.video_id or
                row["source_id"]!=old_pins.source_id):
                _deny("refresh_draft_replay_identity_conflict")
        if (row["inbox_id"]!=new_inbox_id or
            row["config_sha256"]!=config_sha256 or
            row["state"] not in ("draft","quoted","queued","running",
                                "partial","needs_review","completed")):
            _deny("refresh_draft_replay_different_lineage")
        if not replayed:
            for block in ("B1","B2","B3","B4","B5","B6","B7","B8"):
                conn.execute(
                    """INSERT INTO group_b_block_states(project_id,run_id,block)
                       VALUES (%s,%s::uuid,%s)""",
                    (project_id,row["id"],block)
                )
        event_payload={
            "schema_version":EVENT_VERSION,
            "event_type":REFRESH_DRAFT_CREATED,
            "project_id":project_id,"run_id":old_run_id,
            "new_run_id":row["id"],"new_inbox_id":new_inbox_id,
            "new_qdp_event_sha256":qdp["event_sha256"],
            "previous_invalidation_sha256":event[0],
            "qualified_for_downstream":False,
            "ga_and_media_rebinding_required":True,
            "paid_execution_approved":False,
        }
        event_id,_=_queue_event(
            conn,project_id=project_id,run_id=old_run_id,
            event_type=REFRESH_DRAFT_CREATED,payload=event_payload,
        )
        return RefreshDraft(row["id"],old_run_id,new_inbox_id,
                            event_id,replayed)
