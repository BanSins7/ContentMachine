"""UA-MOD-14: immutable, exact-version UAE snapshot assembly and persistence.

Never turns seven unreviewed evidence references into producer approval.
Assemble under a single newly-started project-authorized PostgreSQL
REPEATABLE READ transaction; persist only PARTIAL/BLOCKED, not QUALIFIED.
Source and rights are rechecked by the UA12/13 source-backed adapters.

081/080 own canonical request/snapshot/evidence tables; 084 extends with
immutable normalized JSONB metadata. No Group C calculation or raw media.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping
from uuid import UUID

from .contracts import AnalyticsBoundaryError, canonical_hash, content_type_for_kind
from .dto import EvidenceRef, InputScope
from .joined_context import (
    JOIN_SCHEMA, REQUIRED_PORTS, JoinedCandidatePins,
    JoinedContextAssembler, JoinedSelection,
)
from .qualification_orchestrator import (
    DECISION_SCHEMA, QualificationDecision, QualificationOrchestrator,
    QualificationStatus,
)

SNAPSHOT_ASSEMBLY_SCHEMA = "uae-snapshot-assembly/v1"
_PAYLOAD_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MAX_DEPTH = 12
MAX_UTC_DRIFT_SECONDS = 300
MAX_CONCURRENCY_ATTEMPTS = 4
RETRYABLE_SQLSTATES = frozenset(("40001","40P01"))
# Unique races can only be retried for our immutable UAE snapshot
# version / per-request content-fingerprint constraints, never unrelated FKs.
CONFLICT_CONSTRAINT_PREFIX = "uae_unified_snapshots_"


REQUEST_SQL = """
SELECT id::text AS request_id,project_id,video_id,source_id,
       source_policy_version_id,qdp_run_id::text AS qdp_run_id,
       qdp_input_sha256,content_kind,state
FROM uae_qualification_requests
WHERE project_id=%s AND id=%s::uuid FOR UPDATE
"""
SERIALIZE_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 71144014))"
CLOCK_SQL = "SELECT clock_timestamp() AS db_now"
REPLAY_SQL = """
SELECT sn.id,sn.snapshot_version,sn.status,
       sn.scope_sha256,sn.inputs_sha256,sn.decision_sha256,
       pl.payload,pl.payload_sha256,pl.evidence_set_sha256
FROM uae_unified_snapshots sn
JOIN uae_snapshot_payloads pl ON pl.project_id=sn.project_id
 AND pl.snapshot_id=sn.id
WHERE sn.project_id=%s AND sn.request_id=%s::uuid
  AND sn.inputs_sha256=%s
"""
REPLAY_ORPHAN_SQL = """
SELECT EXISTS(
  SELECT 1 FROM uae_unified_snapshots sn
  WHERE sn.project_id=%s AND sn.request_id=%s::uuid
    AND sn.inputs_sha256=%s
) AS existing_without_payload
"""
REPLAY_INTEGRITY_SQL = """
SELECT port,state,checked,is_current,record_key,
       producer_schema,producer_version,evidence_sha256,
       lineage_sha256,reason_code
FROM uae_snapshot_evidence
WHERE project_id=%s AND snapshot_id=%s
ORDER BY port
"""
VERSION_SQL = """
SELECT coalesce(max(snapshot_version),0)+1 AS next_version
FROM uae_unified_snapshots
WHERE project_id=%s AND video_id=%s AND source_id=%s
"""
INSERT_SNAPSHOT_SQL = """
INSERT INTO uae_unified_snapshots(
 project_id,request_id,video_id,source_id,source_policy_version_id,
 qdp_run_id,qdp_input_sha256,content_kind,content_type,
 snapshot_version,gate_schema,input_schema,status,scope_sha256,
 inputs_sha256,decision_sha256,sre_run_id,sre_decision_sha256,
 group_a_analysis_id,group_b_analysis_id,calculated_metric_id,
 metric_snapshot_id,blockers,readiness,verified_as_of
) VALUES (
 %s,%s::uuid,%s,%s,%s,%s::uuid,%s,%s,%s,
 %s,%s,%s,%s,%s,
 %s,%s,%s::uuid,%s,
 %s,%s,%s,%s,%s::jsonb,%s::jsonb,%s
) RETURNING id
"""
INSERT_EVIDENCE_SQL = """
INSERT INTO uae_snapshot_evidence(
 project_id,snapshot_id,port,state,checked,is_current,
 record_key,producer_schema,producer_version,
 evidence_sha256,lineage_sha256,reason_code
) VALUES (%s,%s,%s,%s,FALSE,FALSE,%s,%s,%s,%s,%s,%s)
"""
INSERT_PAYLOAD_SQL = """
INSERT INTO uae_snapshot_payloads(
 project_id,snapshot_id,assembly_schema,payload,
 payload_sha256,evidence_set_sha256,decision_sha256
) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s)
"""


def _fail(code: str) -> None:
    raise AnalyticsBoundaryError(code, "UAE snapshot assembly denied: "+code)


def _uuid(value: Any) -> bool:
    if type(value) is not str:
        return False
    try: return str(UUID(value)) == value
    except (TypeError,ValueError,AttributeError): return False


def _utc(value: Any) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _sha(value: Any) -> bool:
    return type(value) is str and bool(_PAYLOAD_SHA.fullmatch(value))


def canonical_decimal(value: Decimal) -> str:
    """JSON-neutral, exact finite Decimal; never convert decimal to float."""
    if type(value) is not Decimal or not value.is_finite():
        _fail("snapshot_decimal_invalid")
    if value.is_zero():
        return "0"
    # Guard exponent before formatting; enormous Decimal exponents can
    # otherwise allocate gigabytes before checking the string length.
    if abs(value.adjusted())>2000:
        _fail("snapshot_decimal_too_large")
    # Decimal.normalize() obeys the ambient precision and may round long
    # producer values. Render the exact coefficient first, then trim only
    # insignificant fractional zeros without any arithmetic or float cast.
    result=format(value,"f")
    if "." in result:
        result=result.rstrip("0").rstrip(".")
    if len(result)>2048:
        _fail("snapshot_decimal_too_large")
    return result


def normalize_payload(value: Any, *, _depth:int=0) -> Any:
    """Normalize only explicitly allowed, bounded, source-safe metadata."""
    if _depth>_MAX_DEPTH:
        _fail("snapshot_payload_too_deep")
    if value is None or type(value) in (str,bool,int):
        if type(value) is str and len(value)>8192:
            _fail("snapshot_string_too_large")
        return value
    if type(value) is Decimal:
        return canonical_decimal(value)
    if type(value) is float:
        _fail("snapshot_float_forbidden")
    if type(value) is datetime:
        instant=_utc(value)
        if instant is None:
            _fail("snapshot_timestamp_invalid")
        return instant.isoformat()
    if type(value) in (tuple,list):
        if len(value)>256:
            _fail("snapshot_array_too_large")
        return [normalize_payload(x,_depth=_depth+1) for x in value]
    if type(value) is dict:
        if len(value)>256 or any(type(k) is not str or len(k)>120 for k in value):
            _fail("snapshot_object_invalid")
        return {k:normalize_payload(v,_depth=_depth+1) for k,v in sorted(value.items())}
    _fail("snapshot_payload_type_invalid")


@dataclass(frozen=True,slots=True)
class SnapshotEvidence:
    port: str
    state: str
    record_key: str | None
    producer_schema: str | None
    producer_version: str | None
    evidence_sha256: str | None
    lineage_sha256: str | None
    reason_code: str | None

    def __post_init__(self) -> None:
        if self.port not in REQUIRED_PORTS:
            _fail("snapshot_evidence_port_invalid")
        if self.state=="partial":
            if (self.record_key is None or self.producer_schema is None
                or self.producer_version is None or
                not _sha(self.evidence_sha256) or not _sha(self.lineage_sha256)
                or self.reason_code is not None):
                _fail("snapshot_partial_ref_invalid")
        elif self.state in ("missing","unavailable"):
            if (any(v is not None for v in (
                self.record_key,self.producer_schema,self.producer_version,
                self.evidence_sha256,self.lineage_sha256))
                or type(self.reason_code) is not str or
                not re.fullmatch(r"[a-z][a-z0-9_]{1,79}\Z",self.reason_code)):
                _fail("snapshot_missing_ref_invalid")
        else:
            _fail("snapshot_state_not_approved")

    @classmethod
    def from_ref(cls,ref:EvidenceRef) -> "SnapshotEvidence":
        return cls(
            ref.port,"partial",ref.record_key,ref.producer_schema,
            ref.producer_version,ref.payload_sha256,ref.lineage_sha256,None,
        )

    @classmethod
    def absent(cls,port:str,reason:str) -> "SnapshotEvidence":
        return cls(port,"missing",None,None,None,None,None,reason)

    def sql_fields(self) -> tuple:
        return (
            self.port,self.state,self.record_key,self.producer_schema,
            self.producer_version,self.evidence_sha256,
            self.lineage_sha256,self.reason_code,
        )


@dataclass(frozen=True,slots=True)
class ImmutableSnapshotDraft:
    scope: InputScope
    decision: QualificationDecision
    payload: dict
    payload_sha256: str
    evidence_set_sha256: str
    evidence: tuple[SnapshotEvidence,...]
    sre_run_id: str | None
    sre_decision_sha256: str | None
    group_a_analysis_id: int | None
    group_b_analysis_id: int | None
    calculated_metric_id: int | None
    metric_snapshot_id: int | None
    assembly_schema: str = SNAPSHOT_ASSEMBLY_SCHEMA

    def __post_init__(self) -> None:
        if (self.scope!=self.decision.scope
            or self.decision.status is QualificationStatus.QUALIFIED
            or type(self.payload) is not dict
            or self.payload.get("status")!=self.decision.status.value
            or self.payload.get("scope_sha256")!=self.scope.scope_sha256
            or not _sha(self.payload_sha256)
            or canonical_hash(self.payload)!=self.payload_sha256
            or not _sha(self.evidence_set_sha256)
            or tuple(e.port for e in self.evidence)!=REQUIRED_PORTS
            or self.assembly_schema!=SNAPSHOT_ASSEMBLY_SCHEMA):
            _fail("snapshot_draft_invalid")

    @property
    def qualified(self) -> bool:
        return False


@dataclass(frozen=True,slots=True)
class PersistedSnapshotReceipt:
    scope_sha256: str
    snapshot_id: int
    snapshot_version: int
    status: str
    payload_sha256: str
    decision_sha256: str
    replayed: bool

    def __post_init__(self) -> None:
        if (not _sha(self.scope_sha256) or
            type(self.snapshot_id) is not int or self.snapshot_id<=0 or
            type(self.snapshot_version) is not int or self.snapshot_version<=0 or
            self.status not in ("partial","blocked") or
            not _sha(self.payload_sha256) or not _sha(self.decision_sha256)
            or type(self.replayed) is not bool):
            _fail("snapshot_receipt_invalid")

    @property
    def qualified(self) -> bool:
        return False


class ImmutableSnapshotAssembler:
    """From current joined selection to deterministic, minimal immutable draft.

    Never accepts a raw QualificationInput or a supplied source payload.
    Its only entrypoint calls the *same* assembler used in UA-MOD-13.
    """
    def __init__(self,assembler:JoinedContextAssembler):
        if not isinstance(assembler,JoinedContextAssembler):
            _fail("snapshot_join_required")
        self._assembler=assembler

    def assemble(self) -> ImmutableSnapshotDraft:
        selection=self._assembler.select()
        decision=QualificationOrchestrator(self._assembler)._from_selection(selection)
        return self._make(selection,decision)

    def _make(self,selection:JoinedSelection,
              decision:QualificationDecision) -> ImmutableSnapshotDraft:
        scope=decision.scope
        context=selection.selected
        chosen=None
        if context is not None:
            matches=[p for p in self._assembler._candidates
                     if p.fingerprint==context.fingerprint]
            if len(matches)!=1:
                _fail("snapshot_selected_candidate_ambiguous")
            chosen=matches[0]
            evidence=tuple(SnapshotEvidence.from_ref(ref) for ref in context.evidence)
        else:
            evidence=tuple(SnapshotEvidence.absent(
                port,next((b.code for b in decision.blockers if b.stage==port),
                          "unverified_no_join"),
            ) for port in REQUIRED_PORTS)
        versions=(
            {
                "sre_run_id":chosen.lineage.sre_run_id,
                "sre_taxonomy_version_id":chosen.lineage.sre_taxonomy_version_id,
                "sre_content_version_id":chosen.lineage.sre_content_version_id,
                "sre_decision_sha256":chosen.lineage.sre_decision_sha256,
                "qdp_run_id":scope.qdp_run_id,
                "qdp_input_sha256":scope.qdp_input_sha256,
                "qdp_preparation_shas":list(chosen.qdp.preparation_shas),
                "group_a_analysis_id":chosen.group_a.analysis_id,
                "group_a_result_sha256":chosen.group_a.result_sha256,
                "group_a_media_manifest_sha256":chosen.group_a.media_manifest_sha256,
                "group_a_analysis_version":chosen.group_a.analysis_version,
                "group_b_analysis_id":chosen.group_b.b_analysis_id,
                "group_b_model_version":chosen.group_b.model_version,
                "group_b_prompt_version":chosen.group_b.prompt_version,
                "group_b_taxonomy_version":chosen.group_b.taxonomy_version,
                "group_b_block_evidence_shas":list(
                    chosen.group_b.block_evidence_sha256s
                ),
                "calculated_metric_id":chosen.group_c.calculated_metric_id,
                "metric_snapshot_ids":[
                    chosen.group_c.previous_snapshot_id,
                    chosen.group_c.current_snapshot_id,
                ],
                "metric_source_version":chosen.group_c.source_metric_version,
                "calculation_config_id":chosen.group_c.config_id,
                "calculation_version":chosen.group_c.config_version,
                "creator_freeze_sha256":chosen.group_c.creator_freeze_sha256,
                "niche_freeze_sha256":chosen.group_c.niche_freeze_sha256,
                "prior_freeze_sha256":chosen.group_c.prior_freeze_sha256,
            } if chosen is not None else None
        )
        items=[{
            "port":item.port,"state":item.state,
            "record_key":item.record_key,
            "producer_schema":item.producer_schema,
            "producer_version":item.producer_version,
            "evidence_sha256":item.evidence_sha256,
            "lineage_sha256":item.lineage_sha256,
            "reason_code":item.reason_code,
            "checked":False,"is_current":False,
        } for item in evidence]
        evidence_hash=canonical_hash({"schema":SNAPSHOT_ASSEMBLY_SCHEMA,"items":items})
        payload=normalize_payload({
            "schema_version":SNAPSHOT_ASSEMBLY_SCHEMA,
            "status":decision.status.value,
            "scope_sha256":scope.scope_sha256,
            "project_id":scope.project_id,"video_id":scope.video_id,
            "source_id":scope.source_id,
            "source_policy_version_id":scope.source_policy_version_id,
            "content_kind":scope.content_kind,
            "verified_as_of":decision.as_of,
            "engine_schema":DECISION_SCHEMA,
            "joined_schema":JOIN_SCHEMA,
            "candidate_sha256":decision.candidate_sha256,
            "joined_context_sha256":(
                context.binding_sha256 if context is not None else None
            ),
            "current_source_rights_sha256":(
                context.source_rights_sha256 if context is not None else None
            ),
            "lineage_sha256":(
                context.lineage_sha256 if context is not None else None
            ),
            "input_sha256":decision.input_sha256,
            "decision_sha256":decision.decision_sha256,
            "blockers":[{
                "code":b.code,"stage":b.stage,
                "severity":b.severity.value,
                "candidate_sha256":b.candidate_sha256,
            } for b in decision.blockers],
            "readiness":{x.port:x.state for x in decision.readiness},
            "versions":versions,
            "evidence":items,
            "evidence_set_sha256":evidence_hash,
        })
        return ImmutableSnapshotDraft(
            scope=scope,decision=decision,payload=payload,
            payload_sha256=canonical_hash(payload),
            evidence_set_sha256=evidence_hash,
            evidence=evidence,
            sre_run_id=chosen.lineage.sre_run_id if chosen else None,
            sre_decision_sha256=(
                chosen.lineage.sre_decision_sha256 if chosen else None),
            group_a_analysis_id=chosen.group_a.analysis_id if chosen else None,
            group_b_analysis_id=chosen.group_b.b_analysis_id if chosen else None,
            calculated_metric_id=(
                chosen.group_c.calculated_metric_id if chosen else None),
            metric_snapshot_id=(
                chosen.group_c.current_snapshot_id if chosen else None),
        )


def _one(db:Any,sql:str,args:tuple=()) -> dict | None:
    try: result=db.execute(sql,args).fetchone()
    except Exception as exc:
        raise AnalyticsBoundaryError("snapshot_db_unavailable",
                                     "UAE snapshot database read failed") from exc
    if result is None: return None
    if not isinstance(result,Mapping):
        _fail("snapshot_db_row_invalid")
    return dict(result)


class ImmutableSnapshotRepository:
    """Atomic replay-safe historical write. Never publishes/approves a score.

    The only public write entrypoint assembles the source-backed draft
    *inside the transaction*, then locks the request and per-video version.
    An existing request+inputs fingerprint returns its immutable id iff ALL
    stored checksums and seven evidence rows still match.
    """
    def __init__(self,assembler:JoinedContextAssembler,request_id:str):
        if not isinstance(assembler,JoinedContextAssembler) or not _uuid(request_id):
            _fail("snapshot_request_invalid")
        self._assembler=assembler
        self._request_id=request_id

    @staticmethod
    def _retryable(exc:BaseException) -> bool:
        """Only PostgreSQL concurrency exceptions, not arbitrary DB errors.

        _one() wraps a database exception in AnalyticsBoundaryError; inspect
        that immediate cause too without exposing details to the caller.
        """
        current:BaseException | None=exc
        for _ in range(3):
            if current is None:
                return False
            state=getattr(current,"sqlstate",None)
            if state in RETRYABLE_SQLSTATES:
                return True
            if state=="23505":
                diag=getattr(current,"diag",None)
                constraint=getattr(diag,"constraint_name",None)
                if (type(constraint) is str
                    and constraint.startswith(CONFLICT_CONSTRAINT_PREFIX)
                    and constraint.endswith("_key")):
                    return True
            current=getattr(current,"__cause__",None)
        return False

    def assemble_and_save(self) -> PersistedSnapshotReceipt:
        db=self._assembler._conn
        if not callable(getattr(db,"transaction",None)):
            _fail("snapshot_transaction_required")
        # psycopg transaction() creates a nested SAVEPOINT when the session
        # is already in a transaction. SET TRANSACTION then cannot establish
        # a fresh snapshot. Reject such callers rather than weakening rights.
        status=getattr(getattr(db,"info",None),"transaction_status",0)
        if status != 0:  # psycopg.pq.TransactionStatus.IDLE
            _fail("snapshot_fresh_transaction_required")
        for attempt in range(MAX_CONCURRENCY_ATTEMPTS):
            try:
                with db.transaction():
                    db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    clock=_one(db,CLOCK_SQL)
                    if clock is None:
                        _fail("snapshot_clock_unavailable")
                    db_now=_utc(clock.get("db_now"))
                    as_of=self._assembler._as_of
                    if (db_now is None or as_of>db_now or
                        (db_now-as_of).total_seconds()>MAX_UTC_DRIFT_SECONDS):
                        _fail("snapshot_stale_evaluation_clock")
                    # Retry NEVER reuses a cached joined selection. Source
                    # rights, QDP and all A/B/C data are read anew in the
                    # new transaction on each attempt.
                    draft=ImmutableSnapshotAssembler(self._assembler).assemble()
                    return self._write(db,draft)
            except Exception as exc:
                if self._retryable(exc):
                    if attempt+1<MAX_CONCURRENCY_ATTEMPTS:
                        continue
                    raise AnalyticsBoundaryError(
                        "snapshot_concurrency_exhausted",
                        "UAE concurrent snapshot transaction could not complete",
                    ) from exc
                if isinstance(exc,AnalyticsBoundaryError):
                    raise
                raise AnalyticsBoundaryError(
                    "snapshot_write_unavailable",
                    "UAE immutable snapshot write failed",
                ) from exc
        _fail("snapshot_concurrency_exhausted")

    def _write(self,db:Any,draft:ImmutableSnapshotDraft) -> PersistedSnapshotReceipt:
        scope=draft.scope
        row=_one(db,REQUEST_SQL,(scope.project_id,self._request_id))
        if row is None or any(row.get(k)!=v for k,v in (
            ("request_id",self._request_id),
            ("project_id",scope.project_id),("video_id",scope.video_id),
            ("source_id",scope.source_id),
            ("source_policy_version_id",scope.source_policy_version_id),
            ("qdp_run_id",scope.qdp_run_id),
            ("qdp_input_sha256",scope.qdp_input_sha256),
            ("content_kind",scope.content_kind),
        )):
            _fail("snapshot_request_scope_mismatch")
        if row.get("state") not in ("registered","processing","resolved"):
            _fail("snapshot_request_not_writable")
        # Request lock alone does not serialize concurrent request UUIDs for
        # the same project+video+source. Serialize version allocation as well.
        db.execute(SERIALIZE_SQL,(
            scope.project_id+":"+str(scope.video_id)+":"+scope.source_id,
        ))
        existing=_one(db,REPLAY_SQL,(
            scope.project_id,self._request_id,draft.decision.input_sha256,
        ))
        if existing is not None:
            if any(existing.get(k)!=v for k,v in (
                ("status",draft.decision.status.value),
                ("scope_sha256",scope.scope_sha256),
                ("inputs_sha256",draft.decision.input_sha256),
                ("decision_sha256",draft.decision.decision_sha256),
                ("payload_sha256",draft.payload_sha256),
                ("evidence_set_sha256",draft.evidence_set_sha256),
            )):
                _fail("snapshot_idempotency_collision")
            try:
                previous=normalize_payload(existing.get("payload"))
                previous_hash=canonical_hash(previous)
            except AnalyticsBoundaryError:
                _fail("snapshot_idempotency_collision")
            if (previous!=draft.payload or
                previous_hash!=existing["payload_sha256"]):
                _fail("snapshot_idempotency_collision")
            self._assert_evidence_replay(db,existing["id"],draft)
            return self._receipt(draft,existing["id"],
                                 existing["snapshot_version"],True)
        # Old 080-era records without their 084 payload (or a corrupted
        # payload relation) must NOT fall through to an INSERT that only
        # discovers the conflict as a retryable UNIQUE race.
        orphan=_one(db,REPLAY_ORPHAN_SQL,(
            scope.project_id,self._request_id,draft.decision.input_sha256,
        ))
        if orphan is None or orphan.get("existing_without_payload") is not False:
            _fail("snapshot_legacy_payload_missing")
        if row["state"]=="resolved":
            _fail("snapshot_resolved_request_changed_input")
        version=_one(db,VERSION_SQL,(scope.project_id,scope.video_id,scope.source_id))
        n=version.get("next_version") if version is not None else None
        if type(n) is not int or n<=0:
            _fail("snapshot_version_unavailable")
        import json
        blocker_json=json.dumps(draft.payload["blockers"],sort_keys=True,
                                separators=(",",":"),ensure_ascii=False)
        readiness_json=json.dumps(draft.payload["readiness"],sort_keys=True,
                                  separators=(",",":"),ensure_ascii=False)
        status=draft.decision.status.value
        row=_one(db,INSERT_SNAPSHOT_SQL,(
            scope.project_id,self._request_id,scope.video_id,scope.source_id,
            scope.source_policy_version_id,scope.qdp_run_id,
            scope.qdp_input_sha256,scope.content_kind,
            content_type_for_kind(scope.content_kind),n,
            "uae-qualification/v1","unified-analytics-input/v1",
            status,scope.scope_sha256,draft.decision.input_sha256,
            draft.decision.decision_sha256,draft.sre_run_id,
            draft.sre_decision_sha256,draft.group_a_analysis_id,
            draft.group_b_analysis_id,draft.calculated_metric_id,
            draft.metric_snapshot_id,blocker_json,readiness_json,
            draft.decision.as_of,
        ))
        ident=row.get("id") if row is not None else None
        if type(ident) is not int or ident<=0:
            _fail("snapshot_insert_no_identity")
        for entry in draft.evidence:
            db.execute(INSERT_EVIDENCE_SQL,(
                scope.project_id,ident,*entry.sql_fields(),
            ))
        payload_json=json.dumps(draft.payload,ensure_ascii=False,sort_keys=True,
                                separators=(",",":"),allow_nan=False)
        db.execute(INSERT_PAYLOAD_SQL,(
            scope.project_id,ident,SNAPSHOT_ASSEMBLY_SCHEMA,payload_json,
            draft.payload_sha256,draft.evidence_set_sha256,
            draft.decision.decision_sha256,
        ))
        return self._receipt(draft,ident,n,False)

    @staticmethod
    def _assert_evidence_replay(db:Any,snapshot_id:int,
                                draft:ImmutableSnapshotDraft) -> None:
        try:
            rows=db.execute(REPLAY_INTEGRITY_SQL,(
                draft.scope.project_id,snapshot_id,
            )).fetchall()
        except Exception as exc:
            raise AnalyticsBoundaryError("snapshot_db_unavailable",
                                         "UAE replay audit failed") from exc
        if type(rows) is not list or len(rows)!=len(REQUIRED_PORTS) or any(
            not isinstance(r,Mapping) for r in rows
        ):
            _fail("snapshot_replay_evidence_incomplete")
        actual={r["port"]:dict(r) for r in rows}
        for entry in draft.evidence:
            row=actual.get(entry.port)
            if row is None or row.get("state")!=entry.state:
                _fail("snapshot_replay_evidence_changed")
            if row.get("checked") is not False or row.get("is_current") is not False:
                _fail("snapshot_replay_evidence_approval_forged")
            for key,want in (
                ("record_key",entry.record_key),
                ("producer_schema",entry.producer_schema),
                ("producer_version",entry.producer_version),
                ("evidence_sha256",entry.evidence_sha256),
                ("lineage_sha256",entry.lineage_sha256),
                ("reason_code",entry.reason_code),
            ):
                if row.get(key)!=want:
                    _fail("snapshot_replay_evidence_changed")

    @staticmethod
    def _receipt(draft:ImmutableSnapshotDraft,sid:int,n:int,
                 replayed:bool) -> PersistedSnapshotReceipt:
        return PersistedSnapshotReceipt(
            draft.scope.scope_sha256,sid,n,draft.decision.status.value,
            draft.payload_sha256,draft.decision.decision_sha256,replayed,
        )


__all__=(
    "SNAPSHOT_ASSEMBLY_SCHEMA","canonical_decimal","normalize_payload",
    "SnapshotEvidence","ImmutableSnapshotDraft","PersistedSnapshotReceipt",
    "ImmutableSnapshotAssembler","ImmutableSnapshotRepository",
    "REQUEST_SQL","SERIALIZE_SQL","CLOCK_SQL","REPLAY_SQL",
    "REPLAY_ORPHAN_SQL","REPLAY_INTEGRITY_SQL","VERSION_SQL","INSERT_SNAPSHOT_SQL",
    "INSERT_EVIDENCE_SQL","INSERT_PAYLOAD_SQL",
)
