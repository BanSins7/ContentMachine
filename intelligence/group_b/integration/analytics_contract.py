"""GB-MOD-25 pure, fail-closed Group B -> Unified Analytics contract.

This is NOT an UAE qualification result: UAE also requires authentic QDP,
Group A, C, source policy, SRE, D1 and its own independent qualification.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from ..contracts import canonical_hash
from ..errors import GroupBError

VERSION="group-b-uae-handoff/v1"
UAE_PORT="uae-group-b-reader/v1"
UAE_PEER_WIRE="uae-group-b-exact/v1"
BLOCKS=tuple("B"+str(i) for i in range(1,9))
SHA=re.compile(r"[0-9a-f]{64}\Z")
VIDEO_KINDS=("video","slideshow_video")
CAROUSEL_KINDS=("photo_carousel","mixed_carousel")
REQUIRED_COMPONENTS={
    "B1":"hook","B2":"format","B3":"story","B4":"visual",
    "B5":"editing","B6":"subtitles","B7":"cta_summary","B8":"topic",
}


class HandoffError(GroupBError):
    def __init__(self,reason):
        self.reason=reason
        super().__init__("gb25_analytics_denied","group_b_analytics: "+reason)


def _deny(reason):
    raise HandoffError(reason)


def _sha(x):
    return type(x) is str and SHA.fullmatch(x) is not None


class HandoffState(str,Enum):
    BLOCKED="blocked"
    CANDIDATE="candidate"


@dataclass(frozen=True,slots=True)
class UAECompatibility:
    """Explicit consumer-side declaration, never self-authenticating."""
    adapter_schema:str
    reader_wire:str
    understands_b25_version:bool
    understands_stale_invalidation:bool
    understands_immutable_review:bool
    understands_qdp_receipt:bool
    understands_carousel_na:bool
    capability_sha256:str

    def verify(self):
        fields={
            "adapter_schema":self.adapter_schema,"reader_wire":self.reader_wire,
            "understands_b25_version":self.understands_b25_version,
            "understands_stale_invalidation":self.understands_stale_invalidation,
            "understands_immutable_review":self.understands_immutable_review,
            "understands_qdp_receipt":self.understands_qdp_receipt,
            "understands_carousel_na":self.understands_carousel_na,
        }
        return (self.adapter_schema==UAE_PORT and
                self.reader_wire==UAE_PEER_WIRE and
                all(type(x) is bool for x in (
                    self.understands_b25_version,
                    self.understands_stale_invalidation,
                    self.understands_immutable_review,
                    self.understands_qdp_receipt,
                    self.understands_carousel_na,
                )) and _sha(self.capability_sha256) and
                canonical_hash(fields)==self.capability_sha256)

    @property
    def compatible(self):
        return (self.verify() and self.understands_b25_version and
                self.understands_stale_invalidation and
                self.understands_immutable_review and
                self.understands_qdp_receipt)


def make_compatibility(*,understands_b25_version,
                       understands_stale_invalidation,
                       understands_immutable_review,
                       understands_qdp_receipt,
                       understands_carousel_na,
                       adapter_schema=UAE_PORT,
                       reader_wire=UAE_PEER_WIRE):
    values={
        "adapter_schema":adapter_schema,"reader_wire":reader_wire,
        "understands_b25_version":understands_b25_version,
        "understands_stale_invalidation":understands_stale_invalidation,
        "understands_immutable_review":understands_immutable_review,
        "understands_qdp_receipt":understands_qdp_receipt,
        "understands_carousel_na":understands_carousel_na,
    }
    return UAECompatibility(**values,capability_sha256=canonical_hash(values))


@dataclass(frozen=True,slots=True)
class HandoffDecision:
    state:HandoffState
    reasons:tuple[str,...]
    project_id:str
    run_id:str
    analysis_id:int|None
    payload_sha256:str|None
    payload:dict|None
    uae_qualified:bool=False

    @property
    def can_offer(self):
        return self.state is HandoffState.CANDIDATE and not self.reasons

    def verify_digest(self):
        return (self.can_offer and self.uae_qualified is False and
                type(self.payload) is dict and
                _sha(self.payload_sha256) and
                canonical_hash(self.payload)==self.payload_sha256)


def blocked(project_id,run_id,*reasons):
    return HandoffDecision(
        HandoffState.BLOCKED,tuple(sorted(set(reasons or ("not_authorized",)))),
        project_id,run_id,None,None,None,
    )


def validate_snapshot(*,project_id,run_id,analysis_id,analysis_version,
                      content_kind,block_rows,parts,qualified_freshness,
                      signed_source,quality_passed,compatibility,
                      expected_a_id,linked_a_id,
                      expected_policy_id,linked_policy_id,
                      expected_qdp_sha,received_qdp_sha,
                      has_correction,has_invalidation):
    """Pure structural gate; candidate only, never UAE-qualified or attested.

    `signed_source` and `quality_passed` MUST be supplied by a trusted
    host in the SQL caller. Direct use from untrusted JSON proves nothing.
    """
    if (type(project_id) is not str or not project_id or
        type(run_id) is not str or not run_id or
        type(analysis_id) is not int or analysis_id<=0):
        _deny("handoff_identity_invalid")
    if (type(content_kind) is not str or
        content_kind not in VIDEO_KINDS+CAROUSEL_KINDS):
        _deny("handoff_content_kind_unsupported")
    if type(block_rows) is not dict or type(parts) is not dict:
        _deny("handoff_component_map_invalid")
    reasons=[]
    if not isinstance(compatibility,UAECompatibility) or not compatibility.compatible:
        reasons.append("uae_contract_not_negotiated")
    if content_kind in CAROUSEL_KINDS and (
        not isinstance(compatibility,UAECompatibility) or
        not compatibility.understands_carousel_na):
        reasons.append("uae_carousel_na_contract_unavailable")
    if type(analysis_version) is not str or not analysis_version:
        reasons.append("missing_analysis_version")
    if (qualified_freshness is not True or signed_source is not True):
        reasons.append("fresh_signed_a_qdp_media_unverified")
    if quality_passed is not True:
        reasons.append("expert_quality_gate_unverified")
    if has_correction is not False or has_invalidation is not False:
        reasons.append("source_derived_result_stale_or_reviewed")
    if (type(expected_a_id) is not int or expected_a_id<=0 or
        type(linked_a_id) is not int or linked_a_id!=expected_a_id):
        reasons.append("exact_group_a_fk_mismatch")
    if (type(expected_policy_id) is not int or expected_policy_id<=0 or
        linked_policy_id!=expected_policy_id):
        reasons.append("source_policy_version_mismatch")
    if not _sha(expected_qdp_sha) or received_qdp_sha!=expected_qdp_sha:
        reasons.append("qdp_preparation_receipt_mismatch")
    if set(block_rows)!=set(BLOCKS):
        reasons.append("eight_block_matrix_incomplete")
    else:
        for block in BLOCKS:
            row=block_rows[block]
            if type(row) is not dict:
                reasons.append("block_state_not_original")
                continue
            if (content_kind in CAROUSEL_KINDS and block in ("B5","B6")):
                if (row.get("status") not in ("not_run","unavailable") or
                    row.get("evidence_sha256") is not None or
                    row.get("error_code") not in (
                        None,"editing_video_only","video_subtitles_only",
                    )):
                    reasons.append("carousel_video_only_block_fabricated")
            elif (row.get("status")!="complete" or
                  not _sha(row.get("evidence_sha256")) or
                  row.get("error_code") is not None):
                reasons.append("block_unverified_"+block.lower())
    required=set(REQUIRED_COMPONENTS.values())-(
        {"editing","subtitles"} if content_kind in CAROUSEL_KINDS else set()
    )
    if set(parts)!=required:
        reasons.append("component_payload_incomplete_or_fabricated")
    else:
        if (parts["hook"].get("classification_status")!="inferred" or
            parts["format"].get("classification_status")!="inferred" or
            parts["topic"].get("needs_review") is not False or
            parts["cta_summary"].get("analysis_status")!="complete" or
            parts["cta_summary"].get("has_cta") is None):
            reasons.append("classification_unknown_or_no_whole_scan")
        story=parts["story"]
        if (type(story) is not dict or
            any(type(story.get(k)) is not list for k in
                ("frames","intervals","slides")) or
            any(item.get("needs_review") is not False for row in (
                story.get("frames",[]),story.get("intervals",[]),
                story.get("slides",[])) for item in row if type(item) is dict) or
            (content_kind in CAROUSEL_KINDS and (
                story.get("frames")!=[] or story.get("intervals")!=[] or
                not story.get("slides"))) or
            (content_kind in VIDEO_KINDS and (
                story.get("slides")!=[] or not (
                    story.get("frames") or story.get("intervals"))))):
            reasons.append("story_coverage_or_unreviewed_roles_missing")
        if content_kind in VIDEO_KINDS and (
            parts["subtitles"].get("subtitle_type") not in (
                "none","standard","dynamic") or
            parts["editing"].get("editing_type") not in (
                "single_take","slow_cuts","medium_cuts","fast_cuts")
        ):
            reasons.append("video_editing_or_subtitle_detector_incomplete")
        if (parts["cta_summary"].get("has_cta") is True and
            not parts["cta_summary"].get("detected_occurrences")):
            reasons.append("cta_occurrence_count_inconsistent")
    if reasons:
        return blocked(project_id,run_id,*reasons)
    # No original evidence bytes/text are supplied by the pure function.
    # The SQL reader will construct the exact bounded B payload and hash it.
    return HandoffDecision(
        HandoffState.CANDIDATE,(),project_id,run_id,analysis_id,None,None,
    )
