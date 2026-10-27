"""UA-MOD-13 source-backed qualification decisions.

Only JoinedContextAssembler is accepted; offline checked=True observations
and fabricated producer approvals are not accepted. Actual Group C approval
wire is not ready, so qualified remains a reserved, unreachable status.
No write, score recomputation, model/provider call or publication.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re

from .contracts import AnalyticsBoundaryError, canonical_hash
from .dto import InputScope
from .joined_context import (
    JOIN_SCHEMA, REQUIRED_PORTS, JoinedContextAssembler, JoinedSelection,
)

DECISION_SCHEMA = "uae-qualified-decision/v1"
_REASON = re.compile(r"[a-z][a-z0-9_]{1,79}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class QualificationStatus(str, Enum):
    QUALIFIED = "qualified"
    PARTIAL = "partial"
    BLOCKED = "blocked"


class BlockerSeverity(str, Enum):
    HARD = "hard"
    INCOMPLETE = "incomplete"


_HARD_CODES = frozenset((
    "join_scope_or_sre_lineage_mismatch",
    "join_metadata_version_mismatch",
    "join_metric_snapshot_pair_mismatch",
    "join_sre_qdp_version_mismatch",
    "join_abc_analysis_id_mismatch",
    "join_abc_preparation_sha_mismatch",
    "join_b_wrong_group_a_parent",
    "join_c_config_or_snapshot_changed",
    "join_b_taxonomy_version_mismatch",
    "join_group_a_media_count_mismatch",
    "join_ga_source_media_mismatch",
    "join_canonical_version_changed",
    "join_current_rights_changed",
    "join_media_dependency_unavailable",
    "join_media_rows_invalid",
    "join_dependency_failed",
    "join_source_policy_not_current",
    "join_d1_not_current",
    "join_qdp_not_current",
    "join_sre_not_current",
    "source_policy_missing",
    "source_policy_revoked_or_superseded",
    "source_project_link_revoked",
    "source_policy_unreviewed",
    "source_permission_intersection_denied",
    "source_derived_retention_unapproved",
    "source_retention_expired",
    "source_expiry_invalid",
    "source_schema_unavailable",
    "source_read_failed",
    "qdp_qualified_item_not_found",
    "qdp_not_qualified_or_sre_not_live",
))
_INCOMPLETE_CODES = frozenset((
    "join_group_a_not_current",
    "join_group_b_not_current",
    "join_group_c_not_current",
    "ga_schema_missing",
    "gb_schema_missing",
    "c_peer_schema_missing",
    "c_effective_approval_unavailable",
    "ga_result_missing_or_revoked",
    "gb_completed_run_missing",
    "c_persisted_score_missing",
))
_INFORMATIONAL = frozenset(("join_duplicate_candidate", "join_not_inspected_after_selection"))
_PORT_STATES = frozenset(("unverified","located_unapproved","incomplete","blocked"))


def _fail(code: str) -> None:
    raise AnalyticsBoundaryError(code, "UAE qualification orchestration rejected: "+code)


def _stage(code: str) -> str:
    if code.startswith(("source_","rights_")) or code in (
        "join_current_rights_changed","join_source_policy_not_current",
    ):
        return "source_policy"
    if code.startswith("qdp_") or code=="join_qdp_not_current":
        return "qdp"
    if code.startswith("sre_") or code=="join_sre_not_current":
        return "sre"
    if code.startswith(("ga_","join_ga_","join_group_a_")):
        return "group_a"
    if code.startswith(("gb_","join_group_b_","join_b_")):
        return "group_b"
    if code.startswith(("c_","join_group_c_","join_c_")):
        return "group_c"
    if code.startswith(("d1_","join_d1_","join_canonical_")):
        return "d1"
    return "joined_context"


def _severity(code: str) -> BlockerSeverity:
    if code in _INCOMPLETE_CODES:
        return BlockerSeverity.INCOMPLETE
    # Unknown codes are ALWAYS HARD, not guessed to be incomplete.
    return BlockerSeverity.HARD


@dataclass(frozen=True,slots=True)
class QualificationBlocker:
    code: str
    stage: str
    severity: BlockerSeverity
    candidate_sha256: str | None = None

    def __post_init__(self) -> None:
        if (type(self.code) is not str or not _REASON.fullmatch(self.code)
            or self.stage not in (*REQUIRED_PORTS,"joined_context")
            or type(self.severity) is not BlockerSeverity
            or (self.candidate_sha256 is not None and (
                type(self.candidate_sha256) is not str or
                not _SHA.fullmatch(self.candidate_sha256)
            ))):
            _fail("qualification_invalid_blocker")


@dataclass(frozen=True,slots=True)
class PortQualification:
    port: str
    state: str

    def __post_init__(self) -> None:
        if self.port not in REQUIRED_PORTS or self.state not in _PORT_STATES:
            _fail("qualification_invalid_readiness")


@dataclass(frozen=True,slots=True)
class QualificationDecision:
    """Immutable observation without raw media or implicit approval."""
    scope: InputScope
    as_of: datetime
    status: QualificationStatus
    blockers: tuple[QualificationBlocker,...]
    readiness: tuple[PortQualification,...]
    attempted: int
    candidate_sha256: str | None
    input_sha256: str
    decision_sha256: str
    schema_version: str = DECISION_SCHEMA

    def __post_init__(self) -> None:
        if (not isinstance(self.scope,InputScope)
            or type(self.as_of) is not datetime or self.as_of.tzinfo is None
            or self.as_of.utcoffset() is None
            or self.as_of.utcoffset().total_seconds()!=0
            or type(self.status) is not QualificationStatus
            or type(self.blockers) is not tuple
            or any(not isinstance(x,QualificationBlocker) for x in self.blockers)
            or type(self.readiness) is not tuple
            or tuple(r.port for r in self.readiness)!=REQUIRED_PORTS
            or type(self.attempted) is not int or self.attempted<0
            or (self.candidate_sha256 is not None and (
                type(self.candidate_sha256) is not str
                or not _SHA.fullmatch(self.candidate_sha256)))
            or any(type(h) is not str or not _SHA.fullmatch(h) for h in (
                self.input_sha256,self.decision_sha256
            ))
            or self.schema_version!=DECISION_SCHEMA):
            _fail("qualification_invalid_decision")
        if self.status is QualificationStatus.QUALIFIED:
            # Reviewed effective producer attestation is absent. Even a
            # user-created dataclass cannot forge current qualification.
            _fail("qualification_effective_approval_unavailable")
        if self.status is QualificationStatus.PARTIAL and any(
            x.severity is BlockerSeverity.HARD for x in self.blockers
        ):
            _fail("qualification_hard_cannot_be_partial")

    @property
    def qualified(self) -> bool:
        return False

    def as_dict(self) -> dict:
        return {
            "schema_version":self.schema_version,
            "status":self.status.value,
            "scope_sha256":self.scope.scope_sha256,
            "as_of":self.as_of.isoformat(),
            "blockers":[{
                "code":b.code,"stage":b.stage,
                "severity":b.severity.value,
                "candidate_sha256":b.candidate_sha256,
            } for b in self.blockers],
            "readiness":{p.port:p.state for p in self.readiness},
            "attempted":self.attempted,
            "candidate_sha256":self.candidate_sha256,
            "input_sha256":self.input_sha256,
            "decision_sha256":self.decision_sha256,
            "qualified":False,
        }


class QualificationOrchestrator:
    """Execute real source-backed joined selection, never offline facts."""

    def __init__(self, assembler:JoinedContextAssembler):
        if not isinstance(assembler,JoinedContextAssembler):
            _fail("qualification_joined_assembler_required")
        self._assembler=assembler

    def evaluate(self) -> QualificationDecision:
        assembler=self._assembler
        selection=assembler.select()
        if not isinstance(selection,JoinedSelection):
            _fail("qualification_invalid_joined_selection")
        scope=assembler._scope
        as_of=assembler._as_of
        if not isinstance(scope,InputScope):
            _fail("qualification_scope_invalid")
        blockers:list[QualificationBlocker]=[]
        seen:set[tuple[str,str,str]]=set()
        for ex in selection.excluded:
            if ex.reason_code in _INFORMATIONAL:
                continue
            key=(ex.reason_code,_stage(ex.reason_code),ex.fingerprint)
            if key not in seen:
                blockers.append(QualificationBlocker(
                    ex.reason_code,_stage(ex.reason_code),
                    _severity(ex.reason_code),ex.fingerprint,
                ))
                seen.add(key)

        if selection.selected is not None:
            ctx=selection.selected
            allowed={p.fingerprint for p in assembler._candidates}
            if (ctx.scope!=scope or ctx.as_of!=as_of
                or ctx.schema_version!=JOIN_SCHEMA
                or tuple(x.port for x in ctx.evidence)!=REQUIRED_PORTS
                or ctx.fingerprint not in allowed
                or type(ctx.binding_sha256) is not str
                or not _SHA.fullmatch(ctx.binding_sha256)
                or type(ctx.source_rights_sha256) is not str
                or not _SHA.fullmatch(ctx.source_rights_sha256)
                or type(ctx.lineage_sha256) is not str
                or not _SHA.fullmatch(ctx.lineage_sha256)):
                _fail("qualification_joined_context_invalid")
            # Earlier rejected candidates do not veto the selected
            # combination. But seven located refs are still UNAPPROVED.
            blockers=[QualificationBlocker(
                "qualification_effective_approval_unavailable",
                "group_c",BlockerSeverity.INCOMPLETE,ctx.fingerprint,
            )]
            statuses=("located_unapproved",)*len(REQUIRED_PORTS)
            status=QualificationStatus.PARTIAL
            candidate=ctx.fingerprint
            selected_hash=ctx.binding_sha256
        else:
            if not blockers:
                blockers.append(QualificationBlocker(
                    "qualification_no_eligible_candidate",
                    "joined_context",BlockerSeverity.HARD,
                ))
            hard=any(x.severity is BlockerSeverity.HARD for x in blockers)
            status=QualificationStatus.BLOCKED if hard else QualificationStatus.PARTIAL
            states={port:"unverified" for port in REQUIRED_PORTS}
            for item in blockers:
                if item.stage in states:
                    state="blocked" if item.severity is BlockerSeverity.HARD else "incomplete"
                    if states[item.stage]!="blocked":
                        states[item.stage]=state
            statuses=tuple(states[x] for x in REQUIRED_PORTS)
            candidate=None
            selected_hash=None

        readiness=tuple(PortQualification(port,state)
                        for port,state in zip(REQUIRED_PORTS,statuses))
        fingerprints=sorted(p.fingerprint for p in assembler._candidates)
        input_hash=canonical_hash({
            "schema":DECISION_SCHEMA,
            "scope":scope.scope_sha256,"as_of":as_of.isoformat(),
            "candidate_pool":fingerprints,
            "joined_status":selection.status,
            "selected_hash":selected_hash,
            "attempted":selection.attempted,
            "exclusions":[[e.fingerprint,e.reason_code]
                          for e in selection.excluded],
        })
        decision_hash=canonical_hash({
            "schema":DECISION_SCHEMA,
            "scope":scope.scope_sha256,"as_of":as_of.isoformat(),
            "status":status.value,
            "readiness":[[v.port,v.state] for v in readiness],
            "blockers":[[v.code,v.stage,v.severity.value,v.candidate_sha256]
                        for v in blockers],
            "candidate":candidate,"input":input_hash,
        })
        return QualificationDecision(
            scope=scope,as_of=as_of,status=status,blockers=tuple(blockers),
            readiness=readiness,attempted=selection.attempted,
            candidate_sha256=candidate,input_sha256=input_hash,
            decision_sha256=decision_hash,
        )


__all__=(
    "DECISION_SCHEMA","QualificationStatus","BlockerSeverity",
    "QualificationBlocker","PortQualification","QualificationDecision",
    "QualificationOrchestrator",
)
