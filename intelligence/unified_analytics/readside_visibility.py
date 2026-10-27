"""UA-MOD-19 | Fail-closed current visibility for UAE snapshots.

This is an INTERNAL server-side reader, not a public query/API or dashboard.
A snapshot's historical 'qualified' status is NEVER read authorization.
Current UAE 085/084 have no positive approved qualified output; this reader
therefore returns no visible snapshots until independently approved exact
source-backed Group A/B/C/SRE/QDP proof and schema exist.

The injected PostgreSQL connection must already be project-authorized and
use psycopg dict_row/autocommit. Read takes one fresh REPEATABLE READ txn.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import AnalyticsBoundaryError, canonical_hash, PROJECT_RE, SHA256_RE
from .joined_context import JoinedCandidatePins, JoinedContextAssembler, REQUIRED_PORTS
from .qualification_orchestrator import QualificationOrchestrator
from .source_policy_adapter import SourcePolicyCurrentReader

VISIBILITY_WIRE = "uae-readside-visibility/v1"
MAX_VISIBLE_BATCH = 50

VISIBILITY_CANDIDATE_SQL = """
SELECT project_id,snapshot_id,video_id
FROM uae_readside_visibility_candidates
WHERE project_id=%s AND snapshot_id=%s AND video_id=%s
"""
SNAPSHOT_PROOF_SQL = """
SELECT sn.id,sn.project_id,sn.request_id::text AS request_id,
 sn.video_id,sn.source_id,sn.source_policy_version_id,
 sn.content_kind,sn.qdp_run_id::text AS qdp_run_id,
 sn.qdp_input_sha256,sn.status,sn.scope_sha256,
 sn.inputs_sha256,sn.decision_sha256,sn.sre_decision_sha256,
 sn.group_a_analysis_id,sn.group_b_analysis_id,
 sn.calculated_metric_id,sn.metric_snapshot_id,
 sn.verified_as_of,
 sp.assembly_schema,sp.payload,sp.payload_sha256,
 sp.evidence_set_sha256
FROM uae_unified_snapshots sn
JOIN uae_snapshot_payloads sp
 ON sp.project_id=sn.project_id AND sp.snapshot_id=sn.id
WHERE sn.project_id=%s AND sn.id=%s
"""
EVIDENCE_PROOF_SQL = """
SELECT port,state,checked,is_current,record_key,producer_schema,
 producer_version,evidence_sha256,lineage_sha256,reason_code
FROM uae_snapshot_evidence
WHERE project_id=%s AND snapshot_id=%s
ORDER BY array_position(
 ARRAY['source_policy','d1','qdp','sre',
       'group_a','group_b','group_c']::text[],port)
"""
CLOCK_SQL = "SELECT clock_timestamp() AS db_now"
CURRENT_INVALIDATION_SQL = """
SELECT EXISTS(
 SELECT 1 FROM uae_snapshot_invalidations i
 WHERE i.project_id=%s AND i.snapshot_id=%s
) AS invalidated
"""


def _fail(code:str)->None:
    raise AnalyticsBoundaryError(code,"UAE snapshot visibility denied: "+code)


def _utc(value:Any)->datetime | None:
    if type(value) is not datetime or value.tzinfo is None:
        return None
    try:
        utc=value.astimezone(timezone.utc)
        return utc if utc.utcoffset() is not None else None
    except (ValueError,OverflowError):
        return None


def _one(db:Any,sql:str,args:tuple=())->dict | None:
    result=db.execute(sql,args).fetchone()
    if result is None:
        return None
    if not isinstance(result,Mapping):
        _fail("visibility_db_shape_invalid")
    return dict(result)


def _many(db:Any,sql:str,args:tuple)->list[dict]:
    rows=db.execute(sql,args).fetchall()
    if type(rows) is not list or any(not isinstance(r,Mapping) for r in rows):
        _fail("visibility_db_shape_invalid")
    return [dict(r) for r in rows]


@dataclass(frozen=True,slots=True)
class VisibilityPins:
    """Server-provided exact candidate evidence, never a user grant."""
    snapshot_id:int
    candidate:JoinedCandidatePins

    def __post_init__(self)->None:
        if (type(self.snapshot_id) is not int or self.snapshot_id<=0
            or not isinstance(self.candidate,JoinedCandidatePins)):
            _fail("visibility_invalid_pins")

    @property
    def project_id(self)->str:
        return self.candidate.scope.project_id


@dataclass(frozen=True,slots=True)
class VisibleSnapshot:
    """Minimal reference only; no scores, metrics, media or raw payload."""
    project_id:str
    snapshot_id:int
    video_id:int
    schema_version:str=VISIBILITY_WIRE

    def __post_init__(self)->None:
        if (type(self.project_id) is not str
            or PROJECT_RE.fullmatch(self.project_id) is None
            or type(self.snapshot_id) is not int or self.snapshot_id<=0
            or type(self.video_id) is not int or self.video_id<=0
            or self.schema_version!=VISIBILITY_WIRE):
            _fail("visibility_invalid_result")


class VisibleSnapshotReader:
    """Authorized candidate->live-peer->same-snapshot gate, no enumeration.

    All requested snapshots are checked in one fresh transaction. No
    'list every project' or status-count endpoint, and no positive output
    from old, partial, invalidated or feature-unavailable snapshots.
    """

    def __init__(self,conn:Any):
        if (conn is None or not callable(getattr(conn,"execute",None))
            or not callable(getattr(conn,"transaction",None))):
            _fail("visibility_connection_required")
        self._conn=conn

    @staticmethod
    def _history_matches(row:dict,p:VisibilityPins,context:Any,
                         evidence:list[dict],at:datetime)->bool:
        scope=p.candidate.scope
        if any(row.get(key)!=value for key,value in (
            ("id",p.snapshot_id),
            ("project_id",scope.project_id),
            ("video_id",scope.video_id),
            ("source_id",scope.source_id),
            ("source_policy_version_id",scope.source_policy_version_id),
            ("content_kind",scope.content_kind),
            ("qdp_run_id",scope.qdp_run_id),
            ("qdp_input_sha256",scope.qdp_input_sha256),
            ("status","qualified"),
            ("scope_sha256",scope.scope_sha256),
            ("sre_decision_sha256",p.candidate.lineage.sre_decision_sha256),
            ("group_a_analysis_id",p.candidate.group_a.analysis_id),
            ("group_b_analysis_id",p.candidate.group_b.b_analysis_id),
            ("calculated_metric_id",p.candidate.group_c.calculated_metric_id),
            ("metric_snapshot_id",p.candidate.group_c.current_snapshot_id),
            ("assembly_schema","uae-snapshot-assembly/v1"),
        )):
            return False
        recorded=_utc(row.get("verified_as_of"))
        if recorded is None or recorded>at:
            return False
        payload=row.get("payload")
        if type(payload) is not dict:
            return False
        if any(
            type(row.get(key)) is not str
            or SHA256_RE.fullmatch(row[key]) is None
            for key in ("payload_sha256","evidence_set_sha256",
                        "decision_sha256","inputs_sha256")
        ):
            return False
        try:
            if canonical_hash(payload)!=row["payload_sha256"]:
                return False
        except AnalyticsBoundaryError:
            return False
        if any(payload.get(key)!=expected for key,expected in (
            ("schema_version","uae-snapshot-assembly/v1"),
            ("status","qualified"),
            ("project_id",scope.project_id),
            ("video_id",scope.video_id),
            ("source_id",scope.source_id),
            ("source_policy_version_id",scope.source_policy_version_id),
            ("content_kind",scope.content_kind),
            ("scope_sha256",scope.scope_sha256),
            ("input_sha256",row["inputs_sha256"]),
            ("decision_sha256",row["decision_sha256"]),
            ("evidence_set_sha256",row["evidence_set_sha256"]),
            ("current_source_rights_sha256",context.source_rights_sha256),
            ("lineage_sha256",context.lineage_sha256),
            ("joined_context_sha256",context.binding_sha256),
        )):
            return False
        versions=payload.get("versions")
        if type(versions) is not dict:
            return False
        if any(versions.get(key)!=expected for key,expected in (
            ("sre_taxonomy_version_id",
             p.candidate.lineage.sre_taxonomy_version_id),
            ("group_a_analysis_id",p.candidate.group_a.analysis_id),
            ("group_a_result_sha256",p.candidate.group_a.result_sha256),
            ("group_b_analysis_id",p.candidate.group_b.b_analysis_id),
            ("calculated_metric_id",p.candidate.group_c.calculated_metric_id),
            ("calculation_config_id",p.candidate.group_c.config_id),
            ("qdp_run_id",scope.qdp_run_id),
            ("qdp_input_sha256",scope.qdp_input_sha256),
        )):
            return False
        if (len(evidence)!=len(REQUIRED_PORTS)
            or tuple(r.get("port") for r in evidence)!=REQUIRED_PORTS
            or len(context.evidence)!=len(REQUIRED_PORTS)
            or type(payload.get("evidence")) is not list
            or payload["evidence"]!=evidence):
            return False
        # Recompute from actual ordered child rows, not a supplied hash.
        # A canonical digest by itself is never producer authentication.
        try:
            evidence_sha=canonical_hash({
                "schema":"uae-snapshot-assembly/v1","items":evidence,
            })
        except AnalyticsBoundaryError:
            return False
        if evidence_sha!=row["evidence_set_sha256"]:
            return False
        for actual,ref in zip(evidence,context.evidence):
            if (actual.get("port")!=ref.port
                or actual.get("state")!="complete"
                or actual.get("checked") is not True
                or actual.get("is_current") is not True
                or actual.get("record_key")!=ref.record_key
                or actual.get("producer_schema")!=ref.producer_schema
                or actual.get("producer_version")!=ref.producer_version
                or actual.get("evidence_sha256")!=ref.payload_sha256
                or actual.get("lineage_sha256")!=ref.lineage_sha256
                or actual.get("reason_code") is not None):
                return False
        return True

    def _read(self,db:Any,p:VisibilityPins,at:datetime
              )->VisibleSnapshot | None:
        scope=p.candidate.scope
        locator=_one(db,VISIBILITY_CANDIDATE_SQL,(
            scope.project_id,p.snapshot_id,scope.video_id
        ))
        if locator is None or any(locator.get(k)!=v for k,v in (
            ("project_id",scope.project_id),
            ("snapshot_id",p.snapshot_id),
            ("video_id",scope.video_id),
        )):
            return None
        # Exact metadata, ALL pinned metric observations and media assets
        # (including expiry, permissions and version) checked at read time.
        # A snapshot row contains no media IDs: never infer them from it.
        try:
            rights=SourcePolicyCurrentReader(
                db,p.candidate.rights,as_of=at
            ).check_current_rights(scope)
            assembler=JoinedContextAssembler(
                db,scope,(p.candidate,),as_of=at
            )
            selection=assembler.select()
            if selection.selected is None:
                return None
            context=selection.selected
            if (context.fingerprint!=p.candidate.fingerprint
                or context.scope!=scope
                or context.source_rights_sha256!=rights.evidence_sha256):
                return None
            # CURRENT UA-MOD-13 .qualified returns False, even when all
            # seven raw peer refs were found. Never accept a caller-declared
            # approval flag or simply trust 'complete' historical evidence.
            approval=QualificationOrchestrator(
                assembler
            )._from_selection(selection)
            if approval.qualified is not True:
                return None
        except AnalyticsBoundaryError:
            return None
        row=_one(db,SNAPSHOT_PROOF_SQL,(scope.project_id,p.snapshot_id))
        if row is None:
            return None
        evidence=_many(db,EVIDENCE_PROOF_SQL,(scope.project_id,p.snapshot_id))
        if not self._history_matches(row,p,context,evidence,at):
            return None
        # Last recheck within the same snapshot transaction. Concurrent
        # revocations committed later require fresh transaction/re-read.
        last=_one(db,CURRENT_INVALIDATION_SQL,(
            scope.project_id,p.snapshot_id
        ))
        if last is None or last.get("invalidated") is not False:
            return None
        return VisibleSnapshot(scope.project_id,p.snapshot_id,scope.video_id)

    def visible(self,pins:tuple[VisibilityPins,...]
                )->tuple[VisibleSnapshot,...]:
        if (type(pins) is not tuple or not 1<=len(pins)<=MAX_VISIBLE_BATCH
            or any(not isinstance(p,VisibilityPins) for p in pins)
            or len({p.snapshot_id for p in pins})!=len(pins)
            or len({p.project_id for p in pins})!=1):
            _fail("visibility_batch_invalid")
        db=self._conn
        if getattr(getattr(db,"info",None),"transaction_status",1)!=0:
            _fail("visibility_fresh_transaction_required")
        try:
            with db.transaction():
                db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                now=_one(db,CLOCK_SQL)
                at=_utc(now.get("db_now")) if now is not None else None
                if at is None:
                    _fail("visibility_db_clock_invalid")
                return tuple(
                    item for p in pins
                    if (item:=self._read(db,p,at)) is not None
                )
        except AnalyticsBoundaryError:
            raise
        except Exception as exc:
            raise AnalyticsBoundaryError(
                "visibility_source_unavailable",
                "UAE visibility read not completed",
            ) from exc

    def visible_one(self,pins:VisibilityPins)->VisibleSnapshot | None:
        if not isinstance(pins,VisibilityPins):
            _fail("visibility_invalid_pins")
        items=self.visible((pins,))
        return items[0] if items else None


__all__=(
    "VISIBILITY_WIRE","MAX_VISIBLE_BATCH","VisibilityPins",
    "VisibleSnapshot","VisibleSnapshotReader",
    "VISIBILITY_CANDIDATE_SQL","SNAPSHOT_PROOF_SQL",
    "EVIDENCE_PROOF_SQL","CURRENT_INVALIDATION_SQL","CLOCK_SQL",
)
