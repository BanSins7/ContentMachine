"""GB-MOD-14 — exact B2 video/carousel format proposals (no provider calls).

Authoritative categories are the original 005 SQL enums; no extra UGC/people,
layout, density, or image_post categories are inferred. Inferred labels require
a verified response, delivery of actual sampled image bytes and conservative
temporal/slide coverage. Unknown != observed negative.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import re

from ..contracts import canonical_hash
from ..context.builder import ContextPacket
from ..context.manifest import MediaEvidenceManifest
from ..errors import GroupBError
from ..providers.protocols import VerifiedChatResponse

SCHEMA_VERSION = "group-b-b2-format/v1"
TAXONOMY_VERSION = "1.0"
VIDEO_FORMATS = (
    "talking_head", "dialogue_interview", "skit", "demonstration",
    "product_showcase", "screen_recording", "pov", "reaction",
    "voiceover_montage", "animation_motion_graphics", "slideshow_video",
    "vlog_bts",
)
CAROUSEL_FORMATS = (
    "explainer", "listicle", "step_by_step", "comparison",
    "storytelling", "case_study", "product_catalog", "visual_gallery",
)
VIDEO_KINDS = ("video", "slideshow_video")
CAROUSEL_KINDS = ("photo_carousel", "mixed_carousel")
OUTPUT_FIELDS = frozenset((
    "video_primary","video_secondary","carousel_format","confidence",
    "evidence_refs",
))
SHA = re.compile(r"[0-9a-f]{64}\Z")
IMAGE_KINDS = frozenset(("sampled_frame","slide_ref"))
MAX_CANDIDATE_REFS = 40


class B2FormatError(GroupBError):
    def __init__(self,reason: str):
        self.reason=reason
        super().__init__("b2_unavailable","b2: "+reason)


def _deny(reason: str):
    raise B2FormatError(reason)


def _sha(v):
    return type(v) is str and SHA.fullmatch(v) is not None


def _source_matches(entry: dict,original) -> bool:
    required={
        "kind","ref_sha256","content_sha256","media_asset_id",
        "asset_sha256","start_ms","end_ms","slide_index",
        "frame_ref_sha256","carrier","content_in_packet",
    }
    return (
        type(entry) is dict and set(entry)==required and
        entry["kind"] in IMAGE_KINDS and
        entry["carrier"]=="unresolved_image_ref" and
        entry["content_in_packet"] is False and
        entry["kind"]==original.kind and
        entry["ref_sha256"]==original.ref_sha256 and
        entry["content_sha256"]==original.content_sha256 and
        entry["media_asset_id"]==original.media_asset_id and
        entry["asset_sha256"]==original.asset_sha256 and
        entry["start_ms"]==original.start_ms and
        entry["end_ms"]==original.end_ms and
        entry["slide_index"]==original.slide_index and
        entry["frame_ref_sha256"]==original.frame_ref_sha256
    )


@dataclass(frozen=True,slots=True)
class B2FormatProposal:
    payload: dict
    proposal_sha256: str
    authenticated_inference: bool

    def verify_digest(self) -> bool:
        try:
            return canonical_hash(self.payload)==self.proposal_sha256
        except (TypeError,ValueError):
            return False

    def db_fields(self) -> dict:
        """Exactly the mutable component columns in 005 content_b_formats."""
        p=self.payload
        return {
            "video_primary":p["video_primary"],
            "video_secondary":p["video_secondary"],
            "carousel_format":p["carousel_format"],
            "classification_status":p["classification_status"],
            "taxonomy_version":TAXONOMY_VERSION,
        }


def unknown_b2(*,manifest: MediaEvidenceManifest,reason: str) -> B2FormatProposal:
    """No authenticated model observation => nullable, not a guessed label."""
    if (not isinstance(manifest,MediaEvidenceManifest) or
        not manifest.verify_digest()):
        _deny("source_manifest_unverified")
    if manifest.content_kind not in VIDEO_KINDS+CAROUSEL_KINDS:
        _deny("content_kind_not_supported")
    if type(reason) is not str or re.fullmatch(r"[a-z][a-z0-9_]{1,79}",reason) is None:
        _deny("reason_code_invalid")
    payload={
        "schema_version":SCHEMA_VERSION,"taxonomy_version":TAXONOMY_VERSION,
        "project_id":manifest.project_id,"b_run_id":manifest.b_run_id,
        "video_id":manifest.video_id,"ga_result_sha256":manifest.ga_result_sha256,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "content_kind":manifest.content_kind,
        "model_request_sha256":None,"provider_response_id":None,
        "video_primary":None,"video_secondary":None,"carousel_format":None,
        "confidence":None,"classification_status":"unknown",
        "evidence_refs":[],"delivered_image_refs":[],
        "review_reasons":[reason],"verification_state":"unknown",
    }
    return B2FormatProposal(payload,canonical_hash(payload),False)


def _verified_packet(manifest,packet) -> tuple[dict,dict]:
    if (not isinstance(manifest,MediaEvidenceManifest) or
        not manifest.verify_digest() or
        not isinstance(packet,ContextPacket) or not packet.verify_digest() or
        packet.payload.get("schema_version")!="group-b-context/v1" or
        packet.payload.get("block")!="B2" or
        packet.payload.get("project_id")!=manifest.project_id or
        packet.payload.get("b_run_id")!=manifest.b_run_id or
        packet.payload.get("video_id")!=manifest.video_id or
        packet.payload.get("ga_result_sha256")!=manifest.ga_result_sha256 or
        packet.payload.get("source_evidence_manifest_sha256")!=manifest.manifest_sha256 or
        packet.payload.get("input_content_kind")!=manifest.content_kind or
        packet.payload.get("delivery_state")!="not_sent" or
        packet.payload.get("media_delivery_state")!="opaque_refs_not_attached"):
        _deny("input_lineage_or_context_invalid")
    if manifest.content_kind not in VIDEO_KINDS+CAROUSEL_KINDS:
        _deny("content_kind_not_supported")
    raw=packet.payload.get("entries")
    if type(raw) is not list or len(raw)>2000:
        _deny("entries_invalid")
    relevant={v.ref_sha256:v for v in manifest.observations if v.kind in IMAGE_KINDS}
    selected={}
    for item in raw:
        if type(item) is not dict:
            _deny("entry_not_verified")
        key=item.get("ref_sha256")
        if not _sha(key) or key in selected or key not in relevant or not _source_matches(item,relevant[key]):
            _deny("entry_not_verified")
        selected[key]=item
    if not selected:
        _deny("no_selected_visual_proofs")
    omitted=packet.payload.get("omitted")
    coverage=packet.payload.get("coverage")
    if type(coverage) is not dict or type(omitted) is not list:
        _deny("context_coverage_invalid")
    all_source={v.ref_sha256:v for v in manifest.observations}
    omitted_refs=set()
    for x in omitted:
        if (type(x) is not dict or
            set(x)!= {"kind","ref_sha256","reason"} or
            type(x["ref_sha256"]) is not str or
            x["ref_sha256"] in omitted_refs or
            x["ref_sha256"] in selected or
            x["ref_sha256"] not in all_source or
            x["kind"]!=all_source[x["ref_sha256"]].kind or
            type(x["reason"]) is not str):
            _deny("context_omission_manifest_invalid")
        omitted_refs.add(x["ref_sha256"])
    # Full accounting: every original source ref is either selected or
    # explicitly omitted; an attacker cannot hide counterevidence.
    if set(selected)|omitted_refs!=set(all_source):
        _deny("context_source_accounting_incomplete")
    if (type(coverage.get("coverage_complete")) is not bool or
        coverage["coverage_complete"]!=(set(selected)==set(relevant)) or
        type(coverage.get("selected_image_refs")) is not int or
        coverage["selected_image_refs"]!=len(selected) or
        type(coverage.get("available_frame_refs")) is not int or
        coverage["available_frame_refs"]!=sum(x.kind=="sampled_frame" for x in relevant.values()) or
        type(coverage.get("available_slide_refs")) is not int or
        coverage["available_slide_refs"]!=sum(x.kind=="slide_ref" for x in relevant.values()) or
        type(coverage.get("source_observations")) is not int or
        coverage["source_observations"]!=len(all_source) or
        type(coverage.get("relevant_observations")) is not int or
        coverage["relevant_observations"]!=len(relevant) or
        type(coverage.get("selected_observations")) is not int or
        coverage["selected_observations"]!=len(selected) or
        type(coverage.get("omitted_relevant")) is not int or
        coverage["omitted_relevant"]!=len(relevant)-len(selected)):
        _deny("context_coverage_forged")
    if manifest.content_kind in VIDEO_KINDS:
        if any(v["kind"]!="sampled_frame" for v in selected.values()):
            _deny("video_contains_carousel_visual")
    elif any(v["kind"]!="slide_ref" for v in selected.values()):
        _deny("carousel_contains_video_sample")
    return selected,relevant


def _review_reasons(manifest,packet,selected,relevant,confidence,cited_refs) -> list[str]:
    reasons=[]
    if confidence=="low":
        reasons.append("low_model_confidence")
    if set(cited_refs)!=set(selected):
        reasons.append("incomplete_cited_visual_evidence")
    if set(selected)!=set(relevant):
        reasons.append("incomplete_visual_selection")
    if packet.payload["coverage"].get("coverage_complete") is not True:
        reasons.append("incomplete_context_coverage")
    if manifest.content_kind in VIDEO_KINDS:
        times={v["start_ms"] for v in selected.values()
               if type(v.get("start_ms")) is int and v["start_ms"]>=0}
        lengths=[a.duration_ms for a in manifest.assets if a.media_kind=="video"]
        if not lengths or type(lengths[0]) is not int or lengths[0]<=0:
            reasons.append("duration_not_verified")
        elif (len(times)<2 or min(times)>5000 or
              max(times)<lengths[0]*3//4):
            reasons.append("visual_time_coverage_weak")
    else:
        indices={v["slide_index"] for v in selected.values()
                 if type(v.get("slide_index")) is int and v["slide_index"]>0}
        actual={x.slide_index for x in manifest.assets}
        if len(indices)<2 or indices!=actual:
            reasons.append("slide_coverage_weak")
    return reasons


def validate_b2_response(*,manifest: MediaEvidenceManifest,
                         packet: ContextPacket,
                         response: VerifiedChatResponse,
                         receipt_verifier: object,
                         delivered_image_refs: tuple[str,...]) -> B2FormatProposal:
    """Typed reviewable inference; caller must attest real media *delivery*.

    Trusted verifier.verify_b2_response(response,packet,delivered_image_refs)
    must independently authenticate the charged provider request ID, model,
    expected exact body digest, response and actually attached image hashes.
    This callable MUST originate from trusted infrastructure (not a user).
    """
    selected,relevant=_verified_packet(manifest,packet)
    if (not isinstance(response,VerifiedChatResponse) or
        not _sha(response.request_sha256) or
        type(response.response_id) is not str or not response.response_id):
        _deny("provider_response_invalid")
    if (type(delivered_image_refs) is not tuple or
        len(delivered_image_refs)!=len(selected) or
        len(set(delivered_image_refs))!=len(delivered_image_refs) or
        set(delivered_image_refs)!=set(selected)):
        _deny("image_delivery_incomplete")
    verify=getattr(receipt_verifier,"verify_b2_response",None)
    if not callable(verify):
        _deny("authenticated_provider_receipt_missing")
    try:
        accepted=verify(response,packet,delivered_image_refs)
    except Exception:
        _deny("authenticated_provider_receipt_invalid")
    if accepted is not True:
        _deny("authenticated_provider_receipt_invalid")
    raw=response.json_data
    if type(raw) is not dict or set(raw)!=OUTPUT_FIELDS:
        _deny("model_response_shape_invalid")
    if (type(raw["video_primary"]) is not str or
        type(raw["video_secondary"]) is not str or
        type(raw["carousel_format"]) is not str):
        _deny("format_type_invalid")
    video=manifest.content_kind in VIDEO_KINDS
    if video:
        if (raw["video_primary"] not in VIDEO_FORMATS or
            raw["video_secondary"] not in ("",)+VIDEO_FORMATS or
            raw["video_secondary"]==raw["video_primary"] or
            raw["carousel_format"]!=""):
            _deny("video_format_incompatible_with_005")
    else:
        if (raw["carousel_format"] not in CAROUSEL_FORMATS or
            raw["video_primary"]!="" or raw["video_secondary"]!=""):
            _deny("carousel_format_incompatible_with_005")
    if raw["confidence"] not in ("high","medium","low") or type(raw["confidence"]) is not str:
        _deny("confidence_invalid")
    refs=raw["evidence_refs"]
    if (type(refs) is not list or not 1<=len(refs)<=MAX_CANDIDATE_REFS or
        any(not _sha(x) for x in refs) or len(set(refs))!=len(refs) or
        not set(refs).issubset(selected)):
        _deny("classification_evidence_invalid")
    # Classification cannot be supported by an unnamed image reference.
    if len(refs)<2 and len(selected)>1:
        _deny("classification_evidence_insufficient")
    reasons=_review_reasons(manifest,packet,selected,relevant,raw["confidence"],refs)
    status="needs_review" if reasons else "inferred"
    payload={
        "schema_version":SCHEMA_VERSION,"taxonomy_version":TAXONOMY_VERSION,
        "project_id":manifest.project_id,"b_run_id":manifest.b_run_id,
        "video_id":manifest.video_id,"ga_result_sha256":manifest.ga_result_sha256,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "content_kind":manifest.content_kind,
        "model_request_sha256":response.request_sha256,
        "provider_response_id":response.response_id,
        "video_primary":raw["video_primary"] or None,
        "video_secondary":raw["video_secondary"] or None,
        "carousel_format":raw["carousel_format"] or None,
        "confidence":raw["confidence"],"classification_status":status,
        "evidence_refs":list(refs),
        "delivered_image_refs":list(delivered_image_refs),
        "review_reasons":reasons,
        "verification_state":"authenticated_provider_and_media_delivery",
    }
    return B2FormatProposal(payload,canonical_hash(payload),True)


@dataclass(frozen=True,slots=True)
class B2PreparedRequest:
    """Hash-pinned request plan: no network, key or paid provider dispatch."""
    body: dict
    request_sha256: str
    prompt_sha256: str

    def verify_digest(self) -> bool:
        try:
            return canonical_hash(self.body)==self.request_sha256
        except (ValueError,TypeError):
            return False


def prepare_b2_request(*,catalog,model: str,packet: ContextPacket,
                       max_output_tokens: int,attachments: tuple=()) -> B2PreparedRequest:
    """Require verified *actual* image attachments, not opaque image refs."""
    if (not isinstance(packet,ContextPacket) or not packet.verify_digest() or
        packet.payload.get("block")!="B2" or
        packet.payload.get("delivery_state")!="not_sent" or
        packet.payload.get("media_delivery_state")!="opaque_refs_not_attached"):
        _deny("b2_request_context_invalid")
    from .b2_schema import B2_OUTPUT_SCHEMA
    from ..providers.openrouter import prepare_chat_request
    try:
        trusted=(Path(__file__).resolve().parents[1]/
                 "prompts"/"b2_format_v1.txt").read_text(encoding="utf-8")
    except (OSError,UnicodeError):
        _deny("trusted_b2_prompt_unavailable")
    if not trusted or len(trusted.encode("utf-8"))>16_000:
        _deny("trusted_b2_prompt_invalid")
    body=prepare_chat_request(
        catalog=catalog,model=model,packet=packet,
        output_schema=B2_OUTPUT_SCHEMA,
        max_output_tokens=max_output_tokens,attachments=attachments,
    )
    body["messages"][0]["content"]+="\n\n"+trusted
    if len(json.dumps(body,ensure_ascii=False,sort_keys=True,
                      allow_nan=False,separators=(",",":")).encode("utf-8"))>4_000_000:
        _deny("b2_request_exceeds_limit_after_prompt")
    return B2PreparedRequest(
        body,canonical_hash(body),sha256(trusted.encode("utf-8")).hexdigest()
    )
