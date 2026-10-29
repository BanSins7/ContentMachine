"""UAE-owned integration boundary for Group B25 handoff *candidates*.

The separate Group B branch exposes group-b-uae-handoff/v1 with
UAECompatibility and HandoffDecision. It offers only a CANDIDATE (not a
trusted/qualified UAE EvidenceRead). This strict, pure validator checks the
published fields and cross-references the exact current UAE GroupBExactPins.
It never imports the peer code and never connects to its DB, reads media,
trusts signed_source booleans or promotes a UAE snapshot to 'qualified'.

Even a well-formed candidate with matching digest is NOT proof of a
source-current and independently authenticated B result. The existing
GroupBExactReader + QDP/A/SRE/GC/rights gates remain mandatory. The old
B005 reader must not be bypassed by this compatibility checker.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping
import re

from .contracts import AnalyticsBoundaryError, canonical_hash
from .group_b_adapter import B_READER_WIRE, GroupBExactPins

BRIDGE_WIRE="uae-b25-candidate-audit/v1"
B25_WIRE="group-b-uae-handoff/v1"
B25_ADAPTER_SCHEMA="uae-group-b-reader/v1"
SHA=re.compile(r"[0-9a-f]{64}\Z")
COMPAT_FIELDS=(
    "adapter_schema","reader_wire",
    "understands_b25_version","understands_stale_invalidation",
    "understands_immutable_review","understands_qdp_receipt",
    "understands_carousel_na",
)
DECISION_FIELDS=(
    "state","reasons","project_id","run_id","analysis_id",
    "payload_sha256","payload","uae_qualified",
)
FLAGS=COMPAT_FIELDS[2:]


@dataclass(frozen=True, slots=True)
class B25CandidateAudit:
    schema_version:str
    state:str
    reason_code:str
    compatible:bool
    digest_checked:bool
    has_independent_producer_proof:bool=False
    approved_for_uae_qualification:bool=False

    def __post_init__(self)->None:
        if (self.schema_version!=BRIDGE_WIRE
            or type(self.state) is not str
            or self.state not in ("blocked","candidate_unapproved")
            or type(self.reason_code) is not str or not self.reason_code
            or type(self.compatible) is not bool
            or type(self.digest_checked) is not bool
            or self.has_independent_producer_proof is not False
            or self.approved_for_uae_qualification is not False):
            raise AnalyticsBoundaryError("b25_audit_shape_invalid",
                                         "Unapproved peer audit only")


def _result(code:str,*,compatible:bool=False,
            digest_checked:bool=False)->B25CandidateAudit:
    state="candidate_unapproved" if compatible and digest_checked else "blocked"
    return B25CandidateAudit(
        BRIDGE_WIRE,state,code,compatible,digest_checked,
    )


def _fields(value:Any,names:tuple[str,...])->dict|None:
    """Mapping or peer dataclass only; never dynamically look up fields."""
    if isinstance(value,Mapping):
        if type(value) is not dict or set(value)!=set(names):
            return None
        return value
    # The real Group B HandoffDecision/UAECompatibility are frozen
    # dataclasses (not Mapping). Do not import them across branches.
    # Restrict extraction to known declared fields, not arbitrary duck types.
    from dataclasses import is_dataclass, fields
    if not is_dataclass(value) or isinstance(value,type):
        return None
    try:
        if {f.name for f in fields(value)}!=set(names):
            return None
        return {name:getattr(value,name) for name in names}
    except (TypeError,AttributeError):
        return None


def audit_b25_candidate(pins:GroupBExactPins,compatibility:Any,
                        candidate:Any)->B25CandidateAudit:
    """Check exact current B pins against peer candidate; NEVER authorize.

    Caller must independently authenticate producer, confirm current QDP,
    media, Group A, rights and Group B results via source-backed adapters.
    This pure audit cannot verify any of those facts.
    """
    if not isinstance(pins,GroupBExactPins):
        raise AnalyticsBoundaryError("b25_pins_required","Exact B pins required")
    c=_fields(compatibility,COMPAT_FIELDS+("capability_sha256",))
    if c is None:
        return _result("b25_contract_shape_invalid")
    values={name:c[name] for name in COMPAT_FIELDS}
    if (c["adapter_schema"]!=B25_ADAPTER_SCHEMA
        or c["reader_wire"]!=B_READER_WIRE
        or any(type(c[k]) is not bool for k in FLAGS)
        or type(c["capability_sha256"]) is not str
        or SHA.fullmatch(c["capability_sha256"]) is None):
        return _result("b25_contract_not_negotiated")
    if canonical_hash(values)!=c["capability_sha256"]:
        return _result("b25_contract_digest_mismatch")
    if not all(c[k] for k in FLAGS[:4]):
        return _result("b25_required_capability_missing")
    carousel=pins.scope.content_kind in ("photo_carousel","mixed_carousel")
    if carousel and c["understands_carousel_na"] is not True:
        return _result("b25_carousel_na_not_supported")
    x=_fields(candidate,DECISION_FIELDS)
    if x is None:
        return _result("b25_candidate_shape_invalid")
    state=x["state"]
    if isinstance(state,Enum):
        state=state.value
    if state!="candidate":
        return _result("b25_candidate_not_ready")
    if (type(x["reasons"]) is not tuple or x["reasons"]
        or x["uae_qualified"] is not False):
        return _result("b25_qualification_claim_forbidden")
    if (x["project_id"]!=pins.scope.project_id
        or x["run_id"]!=pins.b_run_id
        or type(x["analysis_id"]) is not int
        or x["analysis_id"]!=pins.b_analysis_id):
        return _result("b25_lineage_scope_mismatch")
    if x["payload"] is None and x["payload_sha256"] is None:
        # This is the *normal* Group B25 pure validate_snapshot candidate:
        # until DB/source-backed producer hydrates it, there are no facts.
        return _result("b25_candidate_payload_not_emitted",
                       compatible=True)
    if (type(x["payload"]) is not dict
        or type(x["payload_sha256"]) is not str
        or SHA.fullmatch(x["payload_sha256"]) is None):
        return _result("b25_candidate_payload_invalid")
    try:
        digest=canonical_hash(x["payload"])
    except AnalyticsBoundaryError:
        return _result("b25_candidate_payload_invalid")
    if digest!=x["payload_sha256"]:
        return _result("b25_candidate_payload_hash_mismatch")
    # SHA verifies payload consistency only, never the provenance of the
    # signed_source/quality_passed flags or current rights in the peer DB.
    return _result("b25_independent_producer_authority_unverified",
                   compatible=True,digest_checked=True)


__all__=(
    "BRIDGE_WIRE","B25_WIRE","B25_ADAPTER_SCHEMA",
    "B25CandidateAudit","audit_b25_candidate",
)
