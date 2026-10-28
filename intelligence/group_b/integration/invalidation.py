"""GB-MOD-24 transactionally revoke stale Group B run qualification.

Existing 055 run state `needs_review` is the stale/quarantine marker.
Never rewrite completed SQL005 rows, release reserved money, fake QDP/GA
events or publish directly to Analytics. Every external-current assertion must
be rechecked by a trusted host under the run lock. Outbox is write-only here.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from uuid import UUID

from ..contracts import canonical_hash
from ..review.repository import _latest_events
from .freshness import (
    InvalidationError,LineagePins,CurrentLineage,
    FreshnessDecision,FreshnessState,compare_lineage,pins_digest,
)

INVALIDATED="group_b.invalidated.v1"
REFRESH_REQUESTED="group_b.refresh_requested.v1"
REFRESH_DRAFT_CREATED="group_b.refresh_draft_created.v1"
EVENT_VERSION="group-b-invalidation-event/v1"


def _deny(reason):
    raise InvalidationError(reason)


def _row(cursor):
    x=cursor.fetchone()
    if x is None:
        return None
    if isinstance(x,Mapping):
        return dict(x)
    return dict(zip((col.name for col in cursor.description),x))


def _uuid(v):
    if type(v) is not str:
        return False
    try:
        return str(UUID(v))==v
    except ValueError:
        return False


def _id(project_id,run_id):
    if type(project_id) is not str or not project_id or not _uuid(run_id):
        _deny("project_run_scope_invalid")


def read_pinned_lineage(conn,*,project_id,run_id,locked=False):
    """Only original DB run, accepted QDP inbox and immutable review events.

    A snapshot proves historic pins, not that A/QDP/media is still current.
    The independent live CurrentLineage host is a mandatory separate source.
    """
    _id(project_id,run_id)
    query="""SELECT r.video_id,r.source_id,r.content_type,r.a_snapshot_sha256,
                    r.media_manifest_sha256,r.source_policy_version_id,
                    r.video_a_analysis_id,r.carousel_a_analysis_id,
                    r.state,r.result_analysis_id,r.inbox_id,
                    i.status AS inbox_status,i.qdp_run_id::text AS qdp_run_id,
                    i.preparation_sha256 AS qdp_input_sha256,
                    a.id AS analysis_id,a.state AS analysis_state,
                    a.video_id AS analysis_video_id,
                    a.source_id AS analysis_source_id,
                    a.content_type AS analysis_content_type,
                    a.source_policy_version_id AS analysis_policy_version_id,
                    a.method_details
             FROM group_b_runs r
             JOIN group_b_inbox i ON i.id=r.inbox_id AND i.project_id=r.project_id
             LEFT JOIN content_b_analyses a ON a.id=r.result_analysis_id
             WHERE r.project_id=%s AND r.id=%s::uuid"""
    if locked:
        query+=" FOR UPDATE OF r"
    x=_row(conn.execute(query,(project_id,run_id)))
    if x is None:
        _deny("run_not_found_or_wrong_project")
    if x["inbox_status"]!="accepted":
        _deny("qdp_historical_inbox_not_accepted")
    if (x["a_snapshot_sha256"] is None or
        x["media_manifest_sha256"] is None):
        _deny("run_upstream_pins_missing")
    if (x["analysis_id"] is not None and
        (x["analysis_video_id"]!=x["video_id"] or
         x["analysis_source_id"]!=x["source_id"] or
         x["analysis_content_type"]!=x["content_type"] or
         x["analysis_policy_version_id"]!=x["source_policy_version_id"] or
         type(x["method_details"]) is not dict or
         x["method_details"].get("group_b_project_id")!=project_id or
         x["method_details"].get("group_b_run_id")!=run_id)):
        _deny("analysis_run_source_pins_mismatch")
    events=_latest_events(conn,project_id,run_id)
    corrections=[e for _,_,e in events if e.payload["kind"]=="correction"]
    last_correction=corrections[-1].decision_sha256 if corrections else None
    ga_id=(x["video_a_analysis_id"] if x["content_type"]=="video"
           else x["carousel_a_analysis_id"])
    p=LineagePins(
        project_id,run_id,x["video_id"],x["source_id"],x["content_type"],
        x["qdp_run_id"],x["qdp_input_sha256"],ga_id,
        x["a_snapshot_sha256"],x["media_manifest_sha256"],
        x["source_policy_version_id"],len(corrections),last_correction,
    )
    if not p.verify():
        _deny("historic_run_lineage_invalid")
    return p,x


def _verify_fresh_host(host,project_id,run_id,pins,current,conn):
    verify=getattr(host,"verify_current_lineage",None)
    if not callable(verify):
        return False
    try:
        return verify(project_id,run_id,pins,current,conn) is True
    except Exception:
        return False


def assess_current_run(conn,*,project_id,run_id,current,verifier):
    """Consumer-safe: unauthenticated/absent/partial/invalidated never qualify."""
    pins,row=read_pinned_lineage(conn,project_id=project_id,run_id=run_id)
    verified=_verify_fresh_host(verifier,project_id,run_id,pins,current,conn)
    verdict=compare_lineage(pins,current,independently_verified=verified)
    already=conn.execute(
        """SELECT EXISTS(SELECT 1 FROM group_b_outbox
             WHERE project_id=%s AND run_id=%s::uuid AND event_type=%s)""",
        (project_id,run_id,INVALIDATED)
    ).fetchone()[0]
    if already or row["state"]!="completed" or row["analysis_state"]!="completed":
        reasons=tuple(sorted(set(verdict.reasons+(
            ("previously_invalidated",) if already else
            ("run_not_completed",) if row["state"]!="completed" or
                                    row["analysis_state"]!="completed" else ()
        ))))
        return FreshnessDecision(
            FreshnessState.STALE if already else FreshnessState.UNVERIFIED,
            reasons,verdict.current_evidence_sha256,verdict.old_pins_sha256,
        )
    return verdict


def _queue_event(conn,*,project_id,run_id,event_type,payload):
    if event_type not in (INVALIDATED,REFRESH_REQUESTED,REFRESH_DRAFT_CREATED):
        _deny("downstream_event_not_whitelisted")
    if type(payload) is not dict:
        _deny("event_payload_invalid")
    digest=canonical_hash(payload)
    # Deterministic idempotency scoped to project, old run, event contents.
    # The event payload contains ONLY safe source/digest identifiers.
    event_key="gb24-"+digest
    got=_row(conn.execute(
        """INSERT INTO group_b_outbox(
               project_id,run_id,event_key,event_type,payload_sha256,payload)
           VALUES (%s,%s::uuid,%s,%s,%s,%s::jsonb)
           ON CONFLICT(project_id,event_key) DO NOTHING
           RETURNING id::text AS id,payload_sha256,event_type,payload""",
        (project_id,run_id,event_key,event_type,digest,
         json.dumps(payload,sort_keys=True,ensure_ascii=False,allow_nan=False))
    ))
    if got is None:
        got=_row(conn.execute(
            """SELECT id::text AS id,run_id::text AS run_id,payload_sha256,
                      event_type,payload
               FROM group_b_outbox WHERE project_id=%s AND event_key=%s""",
            (project_id,event_key),
        ))
        if (got is None or got["run_id"]!=run_id or
            got["payload_sha256"]!=digest or
            got["event_type"]!=event_type or got["payload"]!=payload):
            _deny("outbox_event_key_collision_or_replay_tamper")
        return got["id"],True
    return got["id"],False


@dataclass(frozen=True,slots=True)
class InvalidationResult:
    state:str
    reasons:tuple[str,...]
    event_id:str
    replayed:bool
    fenced_jobs:int


def invalidate_run(conn,*,project_id,run_id,current:CurrentLineage,
                   verifier)->InvalidationResult:
    """Durable invalidate + job fencing + Analytics outbox in ONE transaction.

    Caller must own/commit its outer transaction; no synchronous network or
    model call, no updating 005 SQL rows or paid ledger. Historical versions
    remain queryable only as non-qualified audit results.
    """
    _id(project_id,run_id)
    if not callable(getattr(conn,"transaction",None)):
        _deny("transactional_connection_required")
    with conn.transaction():
        pins,run=read_pinned_lineage(conn,project_id=project_id,run_id=run_id,
                                    locked=True)
        verified=_verify_fresh_host(verifier,project_id,run_id,pins,current,conn)
        verdict=compare_lineage(pins,current,independently_verified=verified)
        if verdict.state is not FreshnessState.STALE or not verdict.reasons:
            _deny("invalidation_requires_proven_source_change")
        approved=getattr(verifier,"verify_invalidation_admission",None)
        if not callable(approved):
            _deny("fresh_invalidation_authorization_missing")
        try:
            ok=approved(project_id,run_id,pins,current,verdict,conn) is True
        except Exception:
            ok=False
        if not ok:
            _deny("fresh_invalidation_authorization_denied")
        if run["state"] in ("cancelled","failed"):
            _deny("terminal_run_cannot_rewrite_state")
        cancelled=conn.execute(
            """UPDATE group_b_jobs
                 SET state='cancelled',lease_token=NULL,lease_until=NULL,
                     last_error_code='source_invalidated',updated_at=now()
               WHERE project_id=%s AND run_id=%s::uuid
                 AND state IN ('ready','running','partial')
               RETURNING id::text,block,lease_generation,evidence_sha256""",
            (project_id,run_id)
        ).fetchall()
        for job_id,block,generation,source_sha in cancelled:
            conn.execute(
                """INSERT INTO group_b_job_events(
                     project_id,run_id,job_id,lease_generation,event_type,
                     evidence_sha256,reason_code)
                   VALUES (%s,%s::uuid,%s::uuid,%s,'cancelled',%s,
                           'source_invalidated')""",
                (project_id,run_id,job_id,generation,source_sha)
            )
        conn.execute(
            """UPDATE group_b_block_states SET status='unavailable',
                     error_code='source_invalidated',updated_at=now()
               WHERE project_id=%s AND run_id=%s::uuid
                 AND status IN ('not_run','ready','running','partial')""",
            (project_id,run_id)
        )
        conn.execute(
            """UPDATE group_b_runs SET state='needs_review',
                     lease_token=NULL,lease_until=NULL,
                     last_error_code='source_invalidated',updated_at=now()
               WHERE project_id=%s AND id=%s::uuid
                 AND state IN ('draft','quoted','queued','running','partial',
                               'completed','needs_review')""",
            (project_id,run_id)
        )
        payload={
            "schema_version":EVENT_VERSION,
            "event_type":INVALIDATED,
            "project_id":project_id,"run_id":run_id,
            "old_analysis_id":run["result_analysis_id"],
            "old_source_pins_sha256":verdict.old_pins_sha256,
            "current_evidence_sha256":verdict.current_evidence_sha256,
            "reasons":list(verdict.reasons),
            "qualified_for_downstream":False,
            "requires_new_run":True,
        }
        event_id,replay=_queue_event(
            conn,project_id=project_id,run_id=run_id,
            event_type=INVALIDATED,payload=payload,
        )
        return InvalidationResult("needs_review",verdict.reasons,
                                  event_id,replay,len(cancelled))


def request_refresh(conn,*,project_id,run_id,verifier):
    """Queue a reanalysis request for a stale old run. NOT a QDP event,
    job enqueue, automatically approved model call or new Group A output.
    """
    _id(project_id,run_id)
    with conn.transaction():
        pins,run=read_pinned_lineage(conn,project_id=project_id,run_id=run_id,
                                    locked=True)
        record=conn.execute(
            """SELECT payload FROM group_b_outbox
               WHERE project_id=%s AND run_id=%s::uuid
                 AND event_type=%s ORDER BY created_at DESC,id DESC LIMIT 1""",
            (project_id,run_id,INVALIDATED)
        ).fetchone()
        if (record is None or run["state"]!="needs_review" or
            record[0].get("qualified_for_downstream") is not False):
            _deny("refresh_requires_committed_invalidation")
        verify=getattr(verifier,"verify_refresh_request",None)
        if not callable(verify):
            _deny("refresh_authorization_missing")
        try:
            ok=verify(project_id,run_id,pins,record[0],conn) is True
        except Exception:
            ok=False
        if not ok:
            _deny("refresh_authorization_denied")
        reason=record[0]
        payload={
            "schema_version":EVENT_VERSION,
            "event_type":REFRESH_REQUESTED,
            "project_id":project_id,"run_id":run_id,
            "old_analysis_id":run["result_analysis_id"],
            "invalidation_event_sha256":canonical_hash(reason),
            "old_source_pins_sha256":pins_digest(pins),
            "qualified_for_downstream":False,
            "needs_new_qdp_ga_or_review_verified_lineage":True,
        }
        return _queue_event(
            conn,project_id=project_id,run_id=run_id,
            event_type=REFRESH_REQUESTED,payload=payload,
        )


def invalidate_human_correction(conn,*,project_id,run_id,decision_sha256):
    """INTERNAL companion to append_review after a newly authorized
    correction is inserted in the same transaction, under its run lock.
    The exact immutable event SHA must match the latest trusted correction.
    Gold annotations MUST NOT trigger this.
    """
    _id(project_id,run_id)
    from .freshness import _sha
    if not _sha(decision_sha256):
        _deny("human_correction_digest_invalid")
    pins,run=read_pinned_lineage(conn,project_id=project_id,run_id=run_id,
                                locked=True)
    if (pins.review_correction_count==0 or
        pins.review_correction_sha256!=decision_sha256):
        _deny("human_invalidation_event_not_authorized_review")
    if run["state"] in ("cancelled","failed"):
        _deny("terminal_run_cannot_invalidate_human_review")
    affected=conn.execute(
        """UPDATE group_b_jobs SET state='cancelled',lease_token=NULL,
                  lease_until=NULL,last_error_code='human_correction',
                  updated_at=now()
           WHERE project_id=%s AND run_id=%s::uuid
             AND state IN ('ready','running','partial')
           RETURNING id::text,block,lease_generation,evidence_sha256""",
        (project_id,run_id)
    ).fetchall()
    for job_id,block,generation,digest in affected:
        conn.execute(
            """INSERT INTO group_b_job_events(
                  project_id,run_id,job_id,lease_generation,event_type,
                  evidence_sha256,reason_code)
               VALUES (%s,%s::uuid,%s::uuid,%s,'cancelled',%s,
                       'human_correction')""",
            (project_id,run_id,job_id,generation,digest)
        )
    conn.execute(
        """UPDATE group_b_block_states SET status='unavailable',
                  error_code='human_correction',updated_at=now()
           WHERE project_id=%s AND run_id=%s::uuid
             AND status IN ('not_run','ready','running','partial')""",
        (project_id,run_id)
    )
    conn.execute(
        """UPDATE group_b_runs SET state='needs_review',
                  lease_token=NULL,lease_until=NULL,
                  last_error_code='human_correction',updated_at=now()
           WHERE project_id=%s AND id=%s::uuid""",
        (project_id,run_id)
    )
    payload={
        "schema_version":EVENT_VERSION,"event_type":INVALIDATED,
        "project_id":project_id,"run_id":run_id,
        "old_analysis_id":run["result_analysis_id"],
        "old_source_pins_sha256":pins_digest(pins),
        "current_evidence_sha256":decision_sha256,
        "reasons":["human_correction"],
        "qualified_for_downstream":False,"requires_new_run":True,
    }
    event_id,replayed=_queue_event(
        conn,project_id=project_id,run_id=run_id,
        event_type=INVALIDATED,payload=payload,
    )
    return InvalidationResult("needs_review",("human_correction",),
                              event_id,replayed,len(affected))


def read_verified_current_result(conn,*,project_id,run_id,current,verifier):
    """Never expose a stale historical result as CURRENT/qualified.

    Does NOT publish to Analytics; GB25 will still require qualified
    B1-B8, A/C, source-policy and an independently passed quality gate.
    """
    from ..persistence.repository import read_group_b_result
    verdict=assess_current_run(
        conn,project_id=project_id,run_id=run_id,current=current,
        verifier=verifier,
    )
    base=read_group_b_result(conn,project_id=project_id,run_id=run_id)
    if base is None:
        _deny("current_result_parent_missing")
    return {
        "analysis":base,
        "freshness_state":verdict.state.value,
        "freshness_reasons":verdict.reasons,
        "freshness_evidence_sha256":verdict.current_evidence_sha256,
        "eligible_for_qualified_handoff":verdict.qualified and
            all(state=="complete" for state in base["block_states"].values()) and
            not base["has_human_corrections"] and
            base["state"]=="completed" and not base["needs_review"],
    }
