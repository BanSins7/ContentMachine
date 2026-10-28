"""GB-MOD-24 immutable current dependency proofs and fail-closed freshness.

A digest is NOT a receipt. The only valid CurrentLineage is one independently
authenticated by a fresh host at the saving/read point. No latest-A guessing,
no reuse of a QDP event to claim new A/media evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from ..contracts import canonical_hash
from ..errors import GroupBError

VERSION="group-b-freshness/v1"
SHA=re.compile(r"[0-9a-f]{64}\Z")
SOURCES=("source_revoked","source_policy_changed","qdp_invalidated",
         "qdp_input_changed","group_a_revised","group_a_id_changed",
         "original_media_changed","human_correction",
         "media_rights_revoked","upstream_unavailable")
BLOCKED=frozenset(SOURCES)
VIDEO=frozenset(("video","carousel"))


class InvalidationError(GroupBError):
    def __init__(self,code:str):
        self.reason=code
        super().__init__("gb24_invalidation_denied","group_b_invalidation: "+code)


def _deny(code):
    raise InvalidationError(code)


def _sha(v):
    return type(v) is str and SHA.fullmatch(v) is not None


@dataclass(frozen=True,slots=True)
class LineagePins:
    project_id:str
    run_id:str
    video_id:int
    source_id:str
    content_type:str
    qdp_run_id:str
    qdp_input_sha256:str
    ga_result_id:int|None
    ga_result_sha256:str
    media_manifest_sha256:str
    source_policy_version_id:int|None
    review_correction_count:int
    review_correction_sha256:str|None

    def verify(self):
        return (type(self.project_id) is str and bool(self.project_id) and
                type(self.run_id) is str and bool(self.run_id) and
                type(self.video_id) is int and self.video_id>0 and
                type(self.source_id) is str and bool(self.source_id) and
                self.content_type in VIDEO and
                type(self.qdp_run_id) is str and bool(self.qdp_run_id) and
                all(_sha(x) for x in (self.qdp_input_sha256,
                                      self.ga_result_sha256,
                                      self.media_manifest_sha256)) and
                (self.ga_result_id is None or
                 type(self.ga_result_id) is int and self.ga_result_id>0) and
                (self.source_policy_version_id is None or
                 type(self.source_policy_version_id) is int and
                 self.source_policy_version_id>0) and
                type(self.review_correction_count) is int and
                self.review_correction_count>=0 and
                (self.review_correction_sha256 is None if
                 self.review_correction_count==0 else
                 _sha(self.review_correction_sha256)))


@dataclass(frozen=True,slots=True)
class CurrentLineage:
    """Must come from actual verified live producer, never HTTP model JSON."""
    project_id:str
    video_id:int
    source_id:str
    content_type:str
    qdp_run_id:str
    qdp_input_sha256:str
    ga_result_id:int
    ga_result_sha256:str
    media_manifest_sha256:str
    source_policy_version_id:int|None
    source_rights_active:bool
    source_media_bytes_current:bool
    qdp_accepted_and_current:bool
    ga_completed_and_current:bool
    evidence_sha256:str

    def payload(self):
        return {
            "schema_version":VERSION,"project_id":self.project_id,
            "video_id":self.video_id,"source_id":self.source_id,
            "content_type":self.content_type,"qdp_run_id":self.qdp_run_id,
            "qdp_input_sha256":self.qdp_input_sha256,
            "ga_result_id":self.ga_result_id,
            "ga_result_sha256":self.ga_result_sha256,
            "media_manifest_sha256":self.media_manifest_sha256,
            "source_policy_version_id":self.source_policy_version_id,
            "source_rights_active":self.source_rights_active,
            "source_media_bytes_current":self.source_media_bytes_current,
            "qdp_accepted_and_current":self.qdp_accepted_and_current,
            "ga_completed_and_current":self.ga_completed_and_current,
        }

    def verify_digest(self):
        return (type(self.project_id) is str and bool(self.project_id) and
                type(self.video_id) is int and self.video_id>0 and
                type(self.source_id) is str and bool(self.source_id) and
                self.content_type in VIDEO and
                type(self.qdp_run_id) is str and bool(self.qdp_run_id) and
                all(_sha(x) for x in (self.qdp_input_sha256,
                   self.ga_result_sha256,self.media_manifest_sha256)) and
                type(self.ga_result_id) is int and self.ga_result_id>0 and
                (self.source_policy_version_id is None or
                 type(self.source_policy_version_id) is int and
                 self.source_policy_version_id>0) and
                all(type(x) is bool for x in (
                   self.source_rights_active,self.source_media_bytes_current,
                   self.qdp_accepted_and_current,self.ga_completed_and_current)) and
                _sha(self.evidence_sha256) and
                canonical_hash(self.payload())==self.evidence_sha256)


class FreshnessState(str,Enum):
    FRESH="fresh"
    STALE="stale"
    UNVERIFIED="unverified"


@dataclass(frozen=True,slots=True)
class FreshnessDecision:
    state:FreshnessState
    reasons:tuple[str,...]
    current_evidence_sha256:str|None
    old_pins_sha256:str

    @property
    def qualified(self):
        return self.state is FreshnessState.FRESH and not self.reasons


def pins_digest(pins:LineagePins):
    if not isinstance(pins,LineagePins) or not pins.verify():
        _deny("stored_lineage_pins_invalid")
    return canonical_hash({
        "schema_version":VERSION,
        **{field:getattr(pins,field) for field in
           LineagePins.__dataclass_fields__},
    })


def compare_lineage(pins:LineagePins,current:CurrentLineage|None,
                    *,independently_verified:bool)->FreshnessDecision:
    """Pure comparison; cannot turn an untrusted host answer into qualified."""
    old=pins_digest(pins)
    if (current is None or not isinstance(current,CurrentLineage) or
        not current.verify_digest() or independently_verified is not True):
        return FreshnessDecision(
            FreshnessState.UNVERIFIED,("upstream_unavailable",),
            current.evidence_sha256 if isinstance(current,CurrentLineage) and
            current.verify_digest() else None,old,
        )
    reasons=[]
    if (pins.project_id!=current.project_id or
        pins.video_id!=current.video_id or
        pins.source_id!=current.source_id or
        pins.content_type!=current.content_type):
        _deny("cross_project_video_source_or_type_lineage_forged")
    if not current.source_rights_active:
        reasons.append("source_revoked")
    if not current.source_media_bytes_current:
        reasons.append("media_rights_revoked")
    if not current.qdp_accepted_and_current:
        reasons.append("qdp_invalidated")
    if not current.ga_completed_and_current:
        reasons.append("group_a_revised")
    if pins.source_policy_version_id!=current.source_policy_version_id:
        reasons.append("source_policy_changed")
    if (pins.qdp_run_id!=current.qdp_run_id or
        pins.qdp_input_sha256!=current.qdp_input_sha256):
        reasons.append("qdp_input_changed")
    if pins.ga_result_id!=current.ga_result_id:
        reasons.append("group_a_id_changed")
    if pins.ga_result_sha256!=current.ga_result_sha256:
        reasons.append("group_a_revised")
    if pins.media_manifest_sha256!=current.media_manifest_sha256:
        reasons.append("original_media_changed")
    if pins.review_correction_count:
        reasons.append("human_correction")
    distinct=tuple(sorted(set(reasons)))
    return FreshnessDecision(
        FreshnessState.STALE if distinct else FreshnessState.FRESH,
        distinct,current.evidence_sha256,old,
    )


def make_current_lineage(*,project_id,video_id,source_id,content_type,
                         qdp_run_id,qdp_input_sha256,ga_result_id,
                         ga_result_sha256,media_manifest_sha256,
                         source_policy_version_id,source_rights_active,
                         source_media_bytes_current,qdp_accepted_and_current,
                         ga_completed_and_current):
    seed=CurrentLineage(
        project_id,video_id,source_id,content_type,
        qdp_run_id,qdp_input_sha256,ga_result_id,ga_result_sha256,
        media_manifest_sha256,source_policy_version_id,
        source_rights_active,source_media_bytes_current,
        qdp_accepted_and_current,ga_completed_and_current,"",
    )
    from dataclasses import replace
    return replace(seed,evidence_sha256=canonical_hash(seed.payload()))
