"""UA-MOD-16 verified revision inbox -> immutable invalidations -> outbox.

Every event is independently checked against a *real current D1/peer row*
on the same authorized PostgreSQL transaction before any new invalidation.
Missing peer feature schemas are UNAVAILABLE, not synthetic verification.
Historical UAE snapshots are never updated. Replays and collisions remain
idempotent and forensic; the downstream outbox is NOT automatically delivered.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Mapping
from uuid import UUID

from .contracts import AnalyticsBoundaryError, PROJECT_RE, SHA256_RE, canonical_hash

EVENT_WIRE = "uae-revision-event/v1"
MAX_CLOCK_DRIFT_SECONDS = 300
CLOCK_SQL = "SELECT clock_timestamp() AS db_now"
KINDS = (
    "source_policy_revoked","source_retention_expired","qdp_invalidated",
    "sre_taxonomy_approved","group_a_result_ready",
    "group_b_reviewed","group_c_revised",
)
REASON = {
    "source_policy_revoked":"source_policy_revoked",
    "source_retention_expired":"source_retention_expired",
    "qdp_invalidated":"qdp_invalidation",
    "sre_taxonomy_approved":"sre_taxonomy_superseded",
    "group_a_result_ready":"group_a_result_superseded",
    "group_b_reviewed":"group_b_review_changed",
    "group_c_revised":"group_c_revision",
}
PRODUCER = {
    "source_policy_revoked":"source_policy",
    "source_retention_expired":"source_policy",
    "qdp_invalidated":"qdp","sre_taxonomy_approved":"sre",
    "group_a_result_ready":"group_a","group_b_reviewed":"group_b",
    "group_c_revised":"group_c",
}
SOURCE_RIGHTS_SQL = """
SELECT s.id AS source_id,s.status AS source_status,
       s.can_store_metadata,s.can_analyze_media,
       s.can_store_metrics,s.can_derive_scores,
       p.id AS policy_id,p.is_active AS policy_active,
       p.source_id AS policy_source_id,p.permissions,
       s.retention_days,p.retention_policy,
       EXISTS(SELECT 1 FROM sre_project_videos v WHERE
         v.project_id=%s AND v.video_id=%s) AS project_link
FROM sources s JOIN source_policy_versions p
 ON p.source_id=s.id AND p.id=%s
WHERE s.id=%s
"""
QDP_PROOF_SQL = """
SELECT i.id::text AS record_id,i.project_id,i.run_id::text AS run_id,
 i.video_id,i.analysis_group,i.provenance_sha256 AS evidence_sha256,
 i.id AS revision,i.created_at AS recorded_at
FROM qdp_invalidations i
WHERE i.id=%s::bigint AND i.project_id=%s AND i.run_id=%s::uuid
 AND (i.video_id IS NULL OR i.video_id=%s)
"""
SRE_PROOF_SQL = """
SELECT t.id::text AS record_id,t.project_id,
 t.version AS revision,t.snapshot_checksum AS evidence_sha256,
 t.approved_at AS recorded_at
FROM sre_taxonomy_versions t
JOIN sre_workspaces w ON w.project_id=t.project_id
 AND w.active_taxonomy_id=t.id
WHERE t.id=%s::bigint AND t.project_id=%s
 AND t.status='approved' AND t.approved_at IS NOT NULL
"""
A_PROOF_SQL = """
SELECT o.id::text AS record_id,r.project_id,r.video_id,r.source_id,
 r.source_policy_version_id,
 o.id AS revision,o.payload_sha256 AS evidence_sha256,
 o.created_at AS recorded_at,
 gr.result_sha256 AS result_sha256
FROM ga_outbox o
JOIN ga_runs r ON r.id=o.run_id AND r.project_id=o.project_id
JOIN ga_results gr ON gr.id=o.result_id AND gr.run_id=o.run_id
WHERE o.id=%s::bigint AND o.project_id=%s
 AND r.video_id=%s AND r.source_id=%s
 AND r.source_policy_version_id=%s AND gr.state='completed'
"""
B_PROOF_SQL = """
SELECT e.id::text AS record_id,r.project_id,r.video_id,r.source_id,
 r.source_policy_version_id,e.revision,
 e.decision_sha256 AS evidence_sha256,e.created_at AS recorded_at,
 r.result_analysis_id
FROM group_b_review_events e
JOIN group_b_runs r ON r.id=e.run_id AND r.project_id=e.project_id
WHERE e.id=%s::uuid AND e.project_id=%s
 AND r.video_id=%s AND r.source_id=%s
 AND r.source_policy_version_id=%s
"""
C_PROOF_SQL = """
SELECT rev.id::text AS record_id,r.project_id,r.video_id,r.source_id,
 r.source_policy_version_id,rev.revision,
 rev.evidence_sha256 AS evidence_sha256,
 rev.recorded_at,rev.previous_calculated_metric_id,
 r.id::text AS c_run_id
FROM group_c_revisions rev
JOIN group_c_runs r ON r.id=rev.run_id AND r.project_id=rev.project_id
WHERE rev.id=%s::uuid AND rev.project_id=%s
 AND r.video_id=%s AND r.source_id=%s
 AND r.source_policy_version_id=%s
"""
INBOX_FIND_SQL = """
SELECT project_id,producer,event_id::text AS event_id,event_sha256
FROM uae_revision_inbox
WHERE project_id=%s AND producer=%s AND event_id=%s::uuid
"""
PROVENANCE_FIND_SQL = """
SELECT project_id,producer,producer_sha256,event_sha256
FROM uae_revision_inbox
WHERE project_id=%s AND video_id=%s AND source_id=%s
 AND producer=%s AND event_kind=%s
 AND producer_record_id=%s AND producer_revision=%s AND producer_sha256=%s
"""
INBOX_INSERT_SQL = """
INSERT INTO uae_revision_inbox(
 project_id,event_id,video_id,source_id,source_policy_version_id,
 producer,event_kind,producer_record_id,producer_revision,producer_sha256,
 event_sha256,observed_at
) VALUES(%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT DO NOTHING RETURNING event_sha256
"""
QUARANTINE_SQL = """
INSERT INTO uae_revision_quarantine(
 project_id,producer,event_id,observed_sha256,recorded_sha256,reason_code
) VALUES(%s,%s,%s::uuid,%s,%s,'event_identity_collision')
ON CONFLICT DO NOTHING
"""
SNAPSHOTS_SQL = """
SELECT sn.id,sn.status,sn.qdp_run_id::text AS qdp_run_id,
 sn.verified_as_of,sn.sre_run_id::text AS sre_run_id,
 sn.group_a_analysis_id,sn.group_b_analysis_id,
 sn.calculated_metric_id,
 pl.payload
FROM uae_unified_snapshots sn
LEFT JOIN uae_snapshot_payloads pl
 ON pl.project_id=sn.project_id AND pl.snapshot_id=sn.id
WHERE sn.project_id=%s AND sn.video_id=%s AND sn.source_id=%s
  AND sn.source_policy_version_id=%s
ORDER BY sn.id
FOR UPDATE OF sn
"""
INVALIDATE_SQL = """
INSERT INTO uae_snapshot_invalidations(
 project_id,snapshot_id,reason_code,reason_sha256
) VALUES(%s,%s,%s,%s)
ON CONFLICT DO NOTHING RETURNING id::text AS id
"""
OUTBOX_INSERT_SQL = """
INSERT INTO uae_revision_outbox(
 project_id,snapshot_id,invalidation_id,event_sha256
) VALUES(%s,%s,%s::uuid,%s)
ON CONFLICT DO NOTHING
"""
_OUTBOX_TYPE = "uae.snapshot_invalidated/v1"


def _fail(code: str) -> None:
    raise AnalyticsBoundaryError(code, "UAE revision event denied: "+code)


def _valid_uuid(v:Any) -> bool:
    if type(v) is not str: return False
    try: return str(UUID(v))==v
    except (ValueError,TypeError,AttributeError): return False


def _utc(v:Any) -> datetime | None:
    if type(v) is not datetime or v.tzinfo is None or v.utcoffset() is None:
        return None
    try: return v.astimezone(timezone.utc)
    except (OverflowError,ValueError): return None


def _row(db:Any,sql:str,params:tuple) -> dict | None:
    try: result=db.execute(sql,params).fetchone()
    except Exception as exc:
        raise AnalyticsBoundaryError(
            "revision_producer_unavailable",
            "Authoritative peer revision read unavailable",
        ) from exc
    if result is None: return None
    if not isinstance(result,Mapping): _fail("revision_db_shape_invalid")
    return dict(result)


def _rows(db:Any,sql:str,params:tuple) -> list[dict]:
    try: results=db.execute(sql,params).fetchall()
    except Exception as exc:
        raise AnalyticsBoundaryError(
            "revision_producer_unavailable",
            "Authoritative UAE history read unavailable",
        ) from exc
    if type(results) is not list or any(not isinstance(v,Mapping) for v in results):
        _fail("revision_db_shape_invalid")
    return [dict(x) for x in results]


@dataclass(frozen=True,slots=True)
class RevisionEvent:
    project_id: str
    video_id: int
    source_id: str
    source_policy_version_id: int
    qdp_run_id: str
    event_id: str
    kind: str
    producer_record_id: str
    producer_revision: int
    producer_sha256: str
    observed_at: datetime
    schema_version: str = EVENT_WIRE

    def __post_init__(self) -> None:
        if (type(self.project_id) is not str
            or not PROJECT_RE.fullmatch(self.project_id)
            or type(self.video_id) is not int or self.video_id<=0
            or type(self.source_id) is not str or not self.source_id.strip()
            or len(self.source_id)>250
            or type(self.source_policy_version_id) is not int
            or self.source_policy_version_id<=0
            or not _valid_uuid(self.qdp_run_id) or not _valid_uuid(self.event_id)
            or self.kind not in KINDS
            or type(self.producer_record_id) is not str
            or not 1<=len(self.producer_record_id)<=80
            or type(self.producer_revision) is not int
            or self.producer_revision<0
            or type(self.producer_sha256) is not str
            or not SHA256_RE.fullmatch(self.producer_sha256)
            or _utc(self.observed_at) is None
            or self.observed_at.utcoffset().total_seconds()!=0
            or self.schema_version!=EVENT_WIRE):
            _fail("revision_event_invalid")
        if self.kind in ("source_policy_revoked","source_retention_expired"):
            if self.producer_record_id!=str(self.source_policy_version_id):
                _fail("revision_source_policy_pin_invalid")
        elif self.kind in ("qdp_invalidated","sre_taxonomy_approved",
                           "group_a_result_ready"):
            if not self.producer_record_id.isdecimal() or int(self.producer_record_id)<=0:
                _fail("revision_record_identity_invalid")
        elif not _valid_uuid(self.producer_record_id):
            _fail("revision_record_identity_invalid")

    @property
    def producer(self) -> str:
        return PRODUCER[self.kind]

    @property
    def event_sha256(self) -> str:
        return canonical_hash({
            "wire":self.schema_version,"project":self.project_id,
            "video":self.video_id,"source":self.source_id,
            "policy":self.source_policy_version_id,"qdp_run":self.qdp_run_id,
            "event":self.event_id,"kind":self.kind,
            "record":self.producer_record_id,
            "revision":self.producer_revision,
            "producer_sha":self.producer_sha256,
            "observed_at":self.observed_at.isoformat(),
        })


@dataclass(frozen=True,slots=True)
class RevisionOutcome:
    state: str
    event_sha256: str
    invalidated_snapshot_ids: tuple[int,...]
    outbox_created: int
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if (self.state not in ("accepted","replayed","quarantined","unavailable","unverified")
            or type(self.event_sha256) is not str
            or not SHA256_RE.fullmatch(self.event_sha256)
            or type(self.invalidated_snapshot_ids) is not tuple
            or any(type(i) is not int or i<=0 for i in self.invalidated_snapshot_ids)
            or type(self.outbox_created) is not int or self.outbox_created<0):
            _fail("revision_outcome_invalid")


class RevisionEventProcessor:
    """Verified on-DB producer row, append-only inbox+invalidations+outbox."""

    def __init__(self,conn:Any,*,as_of:datetime):
        if (conn is None or not callable(getattr(conn,"execute",None))
            or not callable(getattr(conn,"transaction",None))
            or _utc(as_of) is None or as_of.utcoffset().total_seconds()!=0):
            _fail("revision_context_invalid")
        self._conn,self._as_of=conn,as_of

    def _proof(self,event:RevisionEvent) -> dict | None:
        e=event
        db=self._conn
        if e.kind in ("source_policy_revoked","source_retention_expired"):
            r=_row(db,SOURCE_RIGHTS_SQL,(
                e.project_id,e.video_id,e.source_policy_version_id,e.source_id,
            ))
            if r is None or r.get("project_link") is not True:
                return None
            revoked=(r.get("source_status")!="enabled"
                     or r.get("policy_active") is not True
                     or any(r.get(x) is not True for x in (
                         "can_store_metadata","can_analyze_media",
                         "can_store_metrics","can_derive_scores"))
                     or not isinstance(r.get("permissions"),dict)
                     or any(r["permissions"].get(x) is not True for x in (
                         "can_store_metadata","can_analyze_media",
                         "can_store_metrics","can_derive_scores")))
            if e.kind=="source_policy_revoked" and not revoked:
                return None
            source_days=r.get("retention_days")
            options=r.get("retention_policy")
            policy_days=(options.get("derived_retention_days")
                         if isinstance(options,dict) else None)
            if e.kind=="source_retention_expired":
                if (type(source_days) is not int or source_days<=0
                    or type(policy_days) is not int
                    or not 1<=policy_days<=365):
                    return None
            actual_sha=canonical_hash({
                "source":e.source_id,"policy":e.source_policy_version_id,
                "status":r.get("source_status"),"active":r.get("policy_active"),
                "rights":[r.get(x) for x in (
                    "can_store_metadata","can_analyze_media",
                    "can_store_metrics","can_derive_scores")],
                "permissions":r.get("permissions"),
                "retention_days":source_days,
                "retention_policy":options,
            })
            if e.producer_revision!=0 or actual_sha!=e.producer_sha256:
                return None
            return {"recorded_at":self._as_of,"kind":e.kind,
                    "effective_days":min(source_days,policy_days)
                      if e.kind=="source_retention_expired" else None}
        data={
            "qdp_invalidated":(QDP_PROOF_SQL,(
                e.producer_record_id,e.project_id,e.qdp_run_id,e.video_id)),
            "sre_taxonomy_approved":(SRE_PROOF_SQL,(
                e.producer_record_id,e.project_id)),
            "group_a_result_ready":(A_PROOF_SQL,(
                e.producer_record_id,e.project_id,e.video_id,e.source_id,
                e.source_policy_version_id)),
            "group_b_reviewed":(B_PROOF_SQL,(
                e.producer_record_id,e.project_id,e.video_id,e.source_id,
                e.source_policy_version_id)),
            "group_c_revised":(C_PROOF_SQL,(
                e.producer_record_id,e.project_id,e.video_id,e.source_id,
                e.source_policy_version_id)),
        }
        sql,args=data[e.kind]
        r=_row(db,sql,args)
        if (r is None or r.get("record_id")!=e.producer_record_id
            or r.get("project_id")!=e.project_id
            or r.get("evidence_sha256")!=e.producer_sha256
            or r.get("revision")!=e.producer_revision):
            return None
        for key,want in (("video_id",e.video_id),("source_id",e.source_id),
                         ("source_policy_version_id",e.source_policy_version_id)):
            if key in r and r[key]!=want: return None
        recorded=_utc(r.get("recorded_at"))
        if recorded is None or recorded!=e.observed_at or recorded>self._as_of:
            return None
        if e.kind=="qdp_invalidated":
            if r.get("run_id")!=e.qdp_run_id:
                return None
        return r

    @staticmethod
    def _applies(e:RevisionEvent,proof:dict,snap:dict) -> bool:
        if e.kind=="source_policy_revoked":
            return True  # rights revocation beats any stored historical status
        if e.kind=="source_retention_expired":
            seen=_utc(snap.get("verified_as_of"))
            days=proof.get("effective_days")
            if seen is None or type(days) is not int:
                return False
            from datetime import timedelta
            return seen+timedelta(days=days)<=proof.get("recorded_at")
        seen=_utc(snap.get("verified_as_of"))
        when=_utc(proof.get("recorded_at"))
        if seen is None or when is None or seen>=when:
            return False  # event delivered out of order; never revoke newer result
        payload=snap.get("payload")
        versions=payload.get("versions") if isinstance(payload,dict) else None
        versions=versions if isinstance(versions,dict) else {}
        if e.kind=="qdp_invalidated":
            return snap.get("qdp_run_id")==e.qdp_run_id
        if e.kind=="sre_taxonomy_approved":
            return versions.get("sre_taxonomy_version_id")!=e.producer_revision
        if e.kind=="group_a_result_ready":
            return versions.get("group_a_result_sha256")!=proof.get("result_sha256")
        if e.kind=="group_b_reviewed":
            # Project/video/source/policy verified at producer row; a review
            # event alters effective authorization of older B observations.
            result_id=proof.get("result_analysis_id")
            return (result_id is None or snap.get("group_b_analysis_id")==result_id)
        if e.kind=="group_c_revised":
            prior=proof.get("previous_calculated_metric_id")
            if prior is not None:
                return snap.get("calculated_metric_id")==prior
            # Revisions without prior score IDs may revoke all older C
            # observations for the same exact project/video/source/policy.
            return True
        return False

    def consume(self,event:RevisionEvent) -> RevisionOutcome:
        if not isinstance(event,RevisionEvent):
            _fail("revision_event_required")
        e=event
        if e.observed_at>self._as_of:
            return RevisionOutcome("unverified",e.event_sha256,(),0,
                                   "revision_future_event")
        db=self._conn
        # Caller owns permission/authentication for project; event content
        # cannot provide authorization by itself.
        status=getattr(getattr(db,"info",None),"transaction_status",0)
        if status!=0:
            _fail("revision_fresh_transaction_required")
        try:
            with db.transaction():
                db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                clock=_row(db,CLOCK_SQL,())
                db_now=_utc(clock.get("db_now")) if clock is not None else None
                if (db_now is None or self._as_of>db_now or
                    (db_now-self._as_of).total_seconds()>MAX_CLOCK_DRIFT_SECONDS):
                    _fail("revision_stale_clock")
                key=(e.project_id,e.producer,e.event_id)
                existing=_row(db,INBOX_FIND_SQL,key)
                if existing is not None:
                    if existing.get("event_sha256")==e.event_sha256:
                        return RevisionOutcome("replayed",e.event_sha256,(),0)
                    db.execute(QUARANTINE_SQL,(
                        *key,e.event_sha256,existing["event_sha256"],
                    ))
                    return RevisionOutcome("quarantined",e.event_sha256,(),0,
                                           "event_identity_collision")
                try:
                    proof=self._proof(e)
                except AnalyticsBoundaryError as exc:
                    if exc.code=="revision_producer_unavailable":
                        return RevisionOutcome("unavailable",e.event_sha256,(),0,
                                               exc.code)
                    raise
                if proof is None:
                    return RevisionOutcome("unverified",e.event_sha256,(),0,
                                           "revision_source_not_authoritative")
                inserted=_row(db,INBOX_INSERT_SQL,(
                    e.project_id,e.event_id,e.video_id,e.source_id,
                    e.source_policy_version_id,e.producer,e.kind,
                    e.producer_record_id,e.producer_revision,
                    e.producer_sha256,e.event_sha256,e.observed_at,
                ))
                if inserted is None:  # another same event won the unique race
                    previous=_row(db,INBOX_FIND_SQL,key)
                    if previous is None:
                        # A different delivery UUID may represent the exact
                        # same immutable producer record + revision. Replay
                        # by authoritative producer identity, not UUID.
                        same=_row(db,PROVENANCE_FIND_SQL,(
                            e.project_id,e.video_id,e.source_id,
                            e.producer,e.kind,e.producer_record_id,
                            e.producer_revision,e.producer_sha256,
                        ))
                        if same is not None:
                            return RevisionOutcome(
                                "replayed",e.event_sha256,(),0)
                        _fail("revision_inbox_race_unresolved")
                    if previous.get("event_sha256")!=e.event_sha256:
                        db.execute(QUARANTINE_SQL,(
                            *key,e.event_sha256,previous["event_sha256"],
                        ))
                        return RevisionOutcome("quarantined",e.event_sha256,(),0,
                                               "event_identity_collision")
                    return RevisionOutcome("replayed",e.event_sha256,(),0)
                snapshots=_rows(db,SNAPSHOTS_SQL,(
                    e.project_id,e.video_id,e.source_id,
                    e.source_policy_version_id,
                ))
                changed=[]
                for sn in snapshots:
                    if not self._applies(e,proof,sn):
                        continue
                    sid=sn.get("id")
                    if type(sid) is not int or sid<=0:
                        _fail("revision_snapshot_identity_invalid")
                    reason=REASON[e.kind]
                    reason_sha=canonical_hash({
                        "wire":EVENT_WIRE,"producer":e.producer,
                        "kind":e.kind,"record_id":e.producer_record_id,
                        "revision":e.producer_revision,
                        "producer_sha":e.producer_sha256,
                        "snapshot_id":sid,"reason":reason,
                    })
                    added=_row(db,INVALIDATE_SQL,(
                        e.project_id,sid,reason,reason_sha,
                    ))
                    if added is None:
                        continue
                    db.execute(OUTBOX_INSERT_SQL,(
                        e.project_id,sid,added["id"],e.event_sha256,
                    ))
                    changed.append(sid)
                return RevisionOutcome("accepted",e.event_sha256,
                                       tuple(changed),len(changed))
        except AnalyticsBoundaryError:
            raise
        except Exception as exc:
            raise AnalyticsBoundaryError(
                "revision_transaction_failed",
                "UAE revision transaction did not complete",
            ) from exc


__all__=(
    "EVENT_WIRE","MAX_CLOCK_DRIFT_SECONDS","CLOCK_SQL",
    "KINDS","RevisionEvent","RevisionOutcome",
    "RevisionEventProcessor","SOURCE_RIGHTS_SQL","QDP_PROOF_SQL",
    "SRE_PROOF_SQL","A_PROOF_SQL","B_PROOF_SQL","C_PROOF_SQL",
    "INBOX_FIND_SQL","PROVENANCE_FIND_SQL","INBOX_INSERT_SQL",
    "QUARANTINE_SQL","SNAPSHOTS_SQL",
    "INVALIDATE_SQL","OUTBOX_INSERT_SQL",
)
