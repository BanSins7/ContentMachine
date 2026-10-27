"""GB-MOD-21 deterministic B1–B8 schema and provenance quarantine gate.

This evaluates already created domain proposals. It NEVER infers, charges,
saves to SQL, mints provider receipts or treats a proposal hash as authority.
An external trusted attestor must independently verify each original source,
current QDP/GA/policy, exact request/prompt/model/receipt, delivered bytes
and (for B5/B6) original complete detector/timeline at point of use.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
import re

from ..contracts import canonical_hash
from ..context.builder import ContextPacket
from ..context.manifest import MediaEvidenceManifest
from ..blocks.b1_hook import B1HookProposal,MECHANISMS as B1_MECHANISMS
from ..blocks.b2_format import (
    B2FormatProposal,VIDEO_FORMATS,CAROUSEL_FORMATS,
)
from ..blocks.b3_story import B3StoryProposal,ROLES as B3_ROLES
from ..blocks.b4_visual import B4VisualProposal,FEATURES as B4_FEATURES
from ..blocks.b5_editing import B5Metrics,KINDS as B5_KINDS
from ..blocks.b6_subtitles import (
    B6SubtitleProposal,TYPES as B6_TYPES,STYLE_FIELDS as B6_FIELDS,
)
from ..blocks.b7_cta import (
    B7CTAProposal,CTA_TYPES as B7_TYPES,DESTINATIONS as B7_DESTINATIONS,
)
from ..blocks.b8_topic import B8TopicProposal
from .evidence import check_original_manifest, check_claims

VERSION="group-b-unified-validation/v1"
BLOCKS=("B1","B2","B3","B4","B5","B6","B7","B8")
TYPES={
    "B1":B1HookProposal,"B2":B2FormatProposal,"B3":B3StoryProposal,
    "B4":B4VisualProposal,"B5":B5Metrics,"B6":B6SubtitleProposal,
    "B7":B7CTAProposal,"B8":B8TopicProposal,
}
SCHEMAS={
    "B1":"group-b-b1-hook/v1",
    "B2":"group-b-b2-format/v1",
    "B3":"group-b-b3-story/v1",
    "B4":"group-b-b4-visual/v1",
    "B6":"group-b-b6-subtitles/v1",
    "B7":"group-b-b7-cta/v1",
    "B8":"group-b-b8-topic/v1",
}
_SHA=re.compile(r"[a-f0-9]{64}\Z")
_HEX=re.compile(r"#[0-9a-fA-F]{6}\Z")


class Gate(str,Enum):
    ACCEPTED="accepted"
    NEEDS_REVIEW="needs_review"
    QUARANTINED="quarantined"


@dataclass(frozen=True,slots=True)
class BlockAssessment:
    block:str
    state:Gate
    reason_codes:tuple[str,...]
    proposal_sha256:str|None

    @property
    def is_accepted(self):
        return self.state is Gate.ACCEPTED


@dataclass(frozen=True,slots=True)
class UnifiedAssessment:
    schema_version:str
    manifest_sha256:str
    blocks:tuple[BlockAssessment,...]
    can_complete:bool
    all_accepted:bool

    def block(self,name:str)->BlockAssessment:
        return next(x for x in self.blocks if x.block==name)

    @property
    def quarantine_count(self):
        return sum(x.state is Gate.QUARANTINED for x in self.blocks)


def _hash(x):
    return type(x) is str and _SHA.fullmatch(x) is not None


def _status(candidate):
    p=candidate.payload
    if p.get("classification_status") is not None:
        return p["classification_status"]
    if type(p.get("hook")) is dict:
        return p["hook"].get("classification_status")
    return None


def _known_review(candidate,block):
    p=candidate.payload
    status=_status(candidate)
    review={
        "B1":("unknown",),"B2":("unknown","needs_review"),
        "B3":("unknown","needs_review"),"B4":("unknown","needs_review"),
        "B6":("unknown","needs_review"),"B7":("unknown","partial"),
        "B8":("unknown","needs_review"),
    }[block]
    # Lack of an authenticated inference is not evidence of false/none.
    if status in review:
        return True
    return False


def _enum_labels(v,choices,*,empty=False):
    return (type(v) is list and (empty or bool(v)) and
            all(type(x) is str and x in choices for x in v) and
            len(v)==len(set(v)))


def _schema_errors(block,p):
    """Strict small set of cross-block SQL005 invariants, no semantics guessed."""
    errors=[]
    state=_status_obj(p)
    if block=="B1":
        hook=p.get("hook")
        if type(hook) is not dict or hook.get("classification_status") not in ("inferred","unknown"):
            return ("hook_status_invalid",)
        if not _enum_labels(hook.get("mechanisms"),B1_MECHANISMS,empty=True):
            errors.append("hook_mechanisms_not_sql005")
        if not _enum_labels(hook.get("delivery"),
                            ("speech","on_screen_text","visual_action","sound"),empty=True):
            errors.append("hook_delivery_not_sql005")
        flags=hook.get("flags")
        if (type(flags) is not dict or set(flags)!={
            "is_question","has_number","direct_address","has_negation","is_command"
        } or any(type(x) not in (bool,type(None)) for x in flags.values())):
            errors.append("hook_flags_invalid")
        if hook.get("classification_status")=="inferred":
            if (not _hash(p.get("model_request_sha256")) or
                not p.get("provider_response_id")):
                errors.append("hook_receipt_missing")
    elif block=="B2":
        if state not in ("inferred","needs_review","unknown"):
            errors.append("format_status_invalid")
        if p.get("video_primary") is not None and p["video_primary"] not in VIDEO_FORMATS:
            errors.append("format_primary_enum_invalid")
        if p.get("video_secondary") is not None and p["video_secondary"] not in VIDEO_FORMATS:
            errors.append("format_secondary_enum_invalid")
        if p.get("carousel_format") is not None and p["carousel_format"] not in CAROUSEL_FORMATS:
            errors.append("format_carousel_enum_invalid")
        if state=="inferred" and not p.get("evidence_refs"):
            errors.append("format_missing_original_proof")
    elif block=="B3":
        if state not in ("inferred","needs_review","unknown"):
            errors.append("story_status_invalid")
        for field in ("frames","intervals","slides"):
            rows=p.get(field)
            if type(rows) is not list:
                errors.append("story_rows_malformed")
                continue
            for row in rows:
                if type(row) is not dict or row.get("role") not in B3_ROLES:
                    errors.append("story_role_not_sql005")
                if row.get("role")=="other" and row.get("needs_review") is not True:
                    errors.append("story_other_requires_review")
        if p.get("content_type") not in ("video","carousel"):
            errors.append("story_wrong_media_type")
        if not _hash(p.get("sampling_plan_sha256")):
            errors.append("story_plan_digest_missing")
    elif block=="B4":
        if state not in ("inferred","needs_review","unknown"):
            errors.append("visual_status_invalid")
        for f in B4_FEATURES:
            if state=="inferred" and type(p.get(f)) is not bool:
                errors.append("visual_boolean_not_verified")
            if type(p.get(f)) not in (bool,type(None)):
                errors.append("visual_boolean_invalid")
        if type(p.get("ai_generated")) not in (bool,type(None)):
            errors.append("visual_ai_origin_invalid")
        if state=="inferred" and p.get("visual_pass_state")!="authenticated_complete":
            errors.append("visual_completeness_unproven")
    elif block=="B6":
        if state not in ("inferred","unknown","needs_review"):
            errors.append("subtitle_status_invalid")
        if state=="inferred":
            if p.get("subtitle_type") not in B6_TYPES:
                errors.append("subtitle_type_not_sql005")
            if p.get("inspection_scope")!="full_original_video":
                errors.append("subtitle_full_original_scan_missing")
            if p.get("subtitle_type")=="none":
                if any(p.get(k) is not None for k in B6_FIELDS):
                    errors.append("subtitle_none_has_visual_fields")
            else:
                for k in B6_FIELDS:
                    if k!="highlight_color" and p.get(k) is None:
                        errors.append("subtitle_style_required")
                for k in ("text_color","highlight_color"):
                    if p.get(k) is not None and (
                        type(p[k]) is not str or not _HEX.fullmatch(p[k])):
                        errors.append("subtitle_style_color_invalid")
    elif block=="B7":
        if state not in ("complete","partial","unknown"):
            errors.append("cta_status_invalid")
        if state=="complete" and (
            p.get("full_scan_attested") is not True or
            candidate_type_marker(p)!="complete"):
            errors.append("cta_false_requires_whole_media_scan")
        rows=p.get("occurrences")
        if type(rows) is not list:
            errors.append("cta_rows_invalid")
        else:
            for i,row in enumerate(rows,1):
                if (type(row) is not dict or
                    type(row.get("occurrence_index")) is not int or
                    row["occurrence_index"]!=i or
                    row.get("cta_type") not in B7_TYPES or
                    row.get("destination_type") not in B7_DESTINATIONS or
                    not _enum_labels(row.get("delivery"),("speech","on_screen_text"))):
                    errors.append("cta_exact_sql005_row_invalid")
    elif block=="B8":
        if state not in ("resolved","needs_review","unknown"):
            errors.append("topic_status_invalid")
        if state=="resolved":
            if (type(p.get("category_id")) is not int or
                type(p.get("topic_id")) is not int or
                p["category_id"]<=0 or p["topic_id"]<=0 or
                type(p.get("subtopic_id")) not in (int,type(None)) or
                (type(p.get("subtopic_id")) is int and p["subtopic_id"]<=0) or
                p.get("needs_review") is not False or
                not _hash(p.get("registry_snapshot_sha256"))):
                errors.append("topic_existing_ids_not_approved")
    return tuple(sorted(set(errors)))


def _status_obj(p):
    return p.get("classification_status",p.get("hook",{}).get("classification_status") if
                 type(p.get("hook")) is dict else None)


def candidate_type_marker(p):
    return p.get("analysis_status")


def _metrics_errors(m):
    try:
        rate=Decimal(m.cuts_per_minute)
        avg=Decimal(m.avg_shot_duration)
    except (TypeError,ValueError,ArithmeticError):
        return ("editing_quantities_invalid",)
    if (type(m.editing_type) is not str or m.editing_type not in B5_KINDS or
        type(m.cut_count) is not int or m.cut_count<0 or
        type(m.cuts_first_5_sec) is not int or not 0<=m.cuts_first_5_sec<=m.cut_count or
        not rate.is_finite() or not avg.is_finite() or
        rate<0 or avg<=0 or
        (m.editing_type=="single_take" and (m.cut_count!=0 or rate!=0)) or
        (m.editing_type=="slow_cuts" and not (0<rate<10 and m.cut_count>0)) or
        (m.editing_type=="medium_cuts" and not (10<=rate<30 and m.cut_count>0)) or
        (m.editing_type=="fast_cuts" and not (rate>=30 and m.cut_count>0))):
        return ("editing_sql005_metrics_invalid",)
    return ()


def validate_block(*,block:str,manifest:MediaEvidenceManifest,
                   candidate:object,packet:ContextPacket|None=None,
                   trusted_attestor:object=None,request_proof:object=None
                   )->BlockAssessment:
    """No exceptions for untrusted input. Always QUARANTINE forged proposals."""
    if block not in BLOCKS:
        raise ValueError("Unsupported Group B block")
    if type(candidate) is not TYPES[block]:
        return BlockAssessment(block,Gate.QUARANTINED,("wrong_proposal_type",),None)
    manifest_errors=check_original_manifest(manifest)
    if manifest_errors:
        return BlockAssessment(block,Gate.QUARANTINED,manifest_errors,None)
    if block=="B5":
        digest=None
        issues=list(_metrics_errors(candidate))
    else:
        p=candidate.payload
        digest=candidate.proposal_sha256
        if (not _hash(digest) or not candidate.verify_digest() or
            type(p) is not dict or p.get("schema_version")!=SCHEMAS[block]):
            return BlockAssessment(block,Gate.QUARANTINED,
                                   ("proposal_digest_or_schema_forged",),digest)
        pins={
            "project_id":manifest.project_id,"b_run_id":manifest.b_run_id,
            "video_id":manifest.video_id,
            "ga_result_sha256":manifest.ga_result_sha256,
        }
        for key in ("source_evidence_manifest_sha256","media_evidence_manifest_sha256"):
            if key in p:
                pins[key]=manifest.manifest_sha256
        for key in ("qdp_run_id","ga_result_id","source_policy_version_id"):
            if key in p:
                pins[key]=getattr(manifest,key)
        if "content_kind" in p:
            pins["content_kind"]=manifest.content_kind
        if any(p.get(k)!=v for k,v in pins.items()):
            return BlockAssessment(block,Gate.QUARANTINED,
                                   ("cross_block_source_pins_mismatch",),digest)
        issues=list(_schema_errors(block,p))
        # No weak "complete" inferred from an unknown/partial producer result.
        review=_known_review(candidate,block)
        authenticated=(
            getattr(candidate,"authenticated_inference",False) is True or
            getattr(candidate,"authenticated_visual_pass",False) is True or
            getattr(candidate,"authenticated_detector",False) is True or
            getattr(candidate,"authenticated_response",False) is True
        )
        if not review and not authenticated:
            issues.append("authenticated_result_missing")
        if block in ("B4","B6","B7","B8") and not review:
            if getattr(candidate,"can_persist",False) is not True:
                issues.append("domain_result_cannot_persist")
        if block=="B7" and p.get("analysis_status")=="complete" and (
            getattr(candidate,"full_scan_attested",False) is not True
        ):
            issues.append("cta_full_scan_unverified")
        if block=="B8" and not review and p.get("needs_review") is not False:
            issues.append("unapproved_new_taxonomy")
        if block!="B5" and packet is not None:
            issues.extend(check_claims(manifest,p,packet=packet,block=block))
        elif not review:
            issues.append("context_packet_missing")
        if issues:
            return BlockAssessment(block,Gate.QUARANTINED,
                                   tuple(sorted(set(issues))),digest)
        if review:
            reasons=p.get("reason_codes",p.get("review_reasons",[]))
            if type(reasons) is not list:
                reasons=[]
            return BlockAssessment(
                block,Gate.NEEDS_REVIEW,
                tuple(sorted(set(["domain_incomplete_or_review"]+
                                 [x for x in reasons if type(x) is str]))),digest,
            )
        # Force the attestor to prove exact request, prompt hash and model
        # version externally, not merely a model version string in the JSON.
        if not _hash(p.get("model_request_sha256")) and block not in ("B6",):
            return BlockAssessment(block,Gate.QUARANTINED,
                                   ("model_request_not_authenticated",),digest)
        if block not in ("B6",) and not p.get("provider_response_id"):
            return BlockAssessment(block,Gate.QUARANTINED,
                                   ("provider_receipt_absent",),digest)
    verify=getattr(trusted_attestor,"verify_unified_block",None)
    if not callable(verify):
        return BlockAssessment(block,Gate.QUARANTINED,
                               ("fresh_independent_attestation_missing",),digest)
    try:
        ok=verify(block,manifest,candidate,packet,request_proof)
    except Exception:
        ok=False
    if ok is not True:
        return BlockAssessment(block,Gate.QUARANTINED,
                               ("fresh_independent_attestation_denied",),digest)
    return BlockAssessment(block,Gate.ACCEPTED,(),digest)


def validate_bundle(*,manifest:MediaEvidenceManifest,
                    candidates:dict,packets:dict|None=None,
                    request_proofs:dict|None=None,
                    trusted_attestor:object=None)->UnifiedAssessment:
    """Only a separately attested complete B1..B8 bundle can be completed.

    Missing B6 for photo carousels is a supported NOT_APPLICABLE case but
    cannot be claimed completed without a separate explicit host decision
    (future stage22). In this stage all 8 slots remain required, safely.
    """
    if type(candidates) is not dict or any(k not in BLOCKS for k in candidates):
        raise ValueError("Unexpected Group B candidate map")
    if packets is not None and (type(packets) is not dict or
                                any(k not in BLOCKS for k in packets)):
        raise ValueError("Unexpected Group B packet map")
    if request_proofs is not None and (type(request_proofs) is not dict or
                                       any(k not in BLOCKS for k in request_proofs)):
        raise ValueError("Unexpected Group B request proof map")
    pieces=[]
    for block in BLOCKS:
        if block not in candidates:
            pieces.append(BlockAssessment(
                block,Gate.NEEDS_REVIEW,("required_block_missing",),None
            ))
        else:
            pieces.append(validate_block(
                block=block,manifest=manifest,candidate=candidates[block],
                packet=(packets or {}).get(block),
                request_proof=(request_proofs or {}).get(block),
                trusted_attestor=trusted_attestor,
            ))
    all_ok=all(item.is_accepted for item in pieces)
    # A syntactically complete candidate bundle is not a real authenticated
    # B analysis. The host must additionally prove every block's coverage,
    # consistency, paid receipts + auth, source freshness and policy.
    verify=getattr(trusted_attestor,"verify_unified_completion",None)
    approved=False
    if all_ok and callable(verify):
        try:
            approved=verify(manifest,tuple(pieces),candidates) is True
        except Exception:
            approved=False
    return UnifiedAssessment(
        VERSION,
        manifest.manifest_sha256 if isinstance(manifest,MediaEvidenceManifest)
        else "",
        tuple(pieces),all_ok and approved,all_ok,
    )
