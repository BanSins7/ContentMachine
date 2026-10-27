"""GB-MOD-17 B4 visual content: fail-closed person/product/logo + AI origin.

Strictly four SQL005 fields. Presence is image-evidence-backed; absence
requires independently authenticated *whole-publication* visual inspection,
not negative predictions from sparsely sampled video. Visual aesthetics
CANNOT authenticate generative origin. No payment, transport or SQL write.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json
import re

from ..contracts import canonical_hash
from ..context.builder import ContextPacket
from ..context.manifest import MediaEvidenceManifest
from ..providers.protocols import VerifiedChatResponse
from ..errors import GroupBError

VERSION = "group-b-b4-visual/v1"
FEATURES = ("person_visible","product_visible","logo_visible")
ASSESSMENTS = ("present","absent","unknown")
CONFIDENCES = ("high","medium","low")
VIDEO_KINDS = ("video","slideshow_video")
CAROUSEL_KINDS = ("photo_carousel","mixed_carousel")
IMAGE_KINDS = ("sampled_frame","slide_ref")
CLAIM_TYPES = {
    "generated":"signed_generation_provenance",
    "not_generated":"full_chain_verified_non_ai",
}
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{1,79}\Z")
_FEATURE_KEYS = frozenset(("assessment","confidence","evidence_refs"))


class B4VisualError(GroupBError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("b4_visual_unavailable", "b4: "+reason)


def _deny(reason):
    raise B4VisualError(reason)


def _sha(x):
    return type(x) is str and _SHA.fullmatch(x) is not None


def _refs(v,allowed,*,require=False):
    if (type(v) is not list or len(v)>100 or
        (require and not v) or
        any(not _sha(x) or x not in allowed for x in v)):
        _deny("invalid_feature_evidence_refs")
    if len(set(v))!=len(v):
        _deny("invalid_feature_evidence_refs")
    return tuple(v)


@dataclass(frozen=True,slots=True)
class B4VisualProposal:
    payload: dict
    proposal_sha256: str
    authenticated_visual_pass: bool

    def verify_digest(self):
        try:
            return canonical_hash(self.payload)==self.proposal_sha256
        except (TypeError,ValueError):
            return False

    @property
    def can_persist(self):
        p=self.payload
        return (
            self.authenticated_visual_pass is True and
            self.verify_digest() and
            p.get("classification_status")=="inferred" and
            p.get("visual_pass_state")=="authenticated_complete" and
            all(type(p.get(k)) is bool for k in FEATURES) and
            type(p.get("ai_generated")) in (bool,type(None))
        )

    def db_fields(self):
        """Only 005 SQL columns; never write NULL to NOT NULL visible fields."""
        if not self.can_persist:
            _deny("b4_row_not_ready_for_sql")
        return {k:self.payload[k] for k in (
            "ai_generated",*FEATURES
        )}


def unknown_b4(*,manifest:MediaEvidenceManifest,reason:str)->B4VisualProposal:
    if (not isinstance(manifest,MediaEvidenceManifest) or
        not manifest.verify_digest() or
        manifest.content_kind not in VIDEO_KINDS+CAROUSEL_KINDS):
        _deny("original_media_manifest_unverified")
    if type(reason) is not str or _REASON.fullmatch(reason) is None:
        _deny("reason_code_invalid")
    p={
        "schema_version":VERSION,"project_id":manifest.project_id,
        "b_run_id":manifest.b_run_id,"video_id":manifest.video_id,
        "qdp_run_id":manifest.qdp_run_id,
        "ga_result_id":manifest.ga_result_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "source_policy_version_id":manifest.source_policy_version_id,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "content_kind":manifest.content_kind,
        "model_request_sha256":None,"provider_response_id":None,
        "ai_generated":None,**{k:None for k in FEATURES},
        "feature_evidence_refs":{k:[] for k in FEATURES},
        "feature_claims":{k:"unknown" for k in FEATURES},
        "delivered_visual_refs":[],
        "ai_origin_evidence_sha256":None,
        "ai_origin_claim_type":None,
        "classification_status":"unknown",
        "visual_pass_state":"not_run",
        "review_reasons":[reason],
    }
    return B4VisualProposal(p,canonical_hash(p),False)


def _verified_visual_packet(manifest,packet):
    """Fully reaccount original observations; no forged 'coverage_complete'."""
    if (not isinstance(manifest,MediaEvidenceManifest) or
        not manifest.verify_digest() or
        manifest.content_kind not in VIDEO_KINDS+CAROUSEL_KINDS or
        not isinstance(packet,ContextPacket) or not packet.verify_digest()):
        _deny("source_or_context_invalid")
    keys={
        "schema_version":"group-b-context/v1","block":"B4",
        "project_id":manifest.project_id,"b_run_id":manifest.b_run_id,
        "video_id":manifest.video_id,"qdp_run_id":manifest.qdp_run_id,
        "ga_result_id":manifest.ga_result_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "source_policy_version_id":manifest.source_policy_version_id,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "input_content_kind":manifest.content_kind,
        "delivery_state":"not_sent",
        "media_delivery_state":"opaque_refs_not_attached",
    }
    if any(packet.payload.get(k)!=v for k,v in keys.items()):
        _deny("visual_context_lineage_invalid")
    originals={x.ref_sha256:x for x in manifest.observations}
    if len(originals)!=len(manifest.observations):
        _deny("duplicate_original_source_ref")
    relevant={key:x for key,x in originals.items()
              if x.kind in IMAGE_KINDS}
    media={x.asset_id:x for x in manifest.assets}
    if len(media)!=len(manifest.assets):
        _deny("duplicate_media_assets")
    entries=packet.payload.get("entries")
    omissions=packet.payload.get("omitted")
    coverage=packet.payload.get("coverage")
    if (type(entries) is not list or len(entries)>2000 or
        type(omissions) is not list or type(coverage) is not dict):
        _deny("context_selection_not_bounded")
    selected={}
    for e in entries:
        if type(e) is not dict or not _sha(e.get("ref_sha256")):
            _deny("context_entry_invalid")
        ref=e["ref_sha256"]
        if ref not in originals or ref in selected:
            _deny("duplicate_or_forged_selected_ref")
        o=originals[ref]
        if (e.get("kind")!=o.kind or e.get("content_sha256")!=o.content_sha256 or
            e.get("media_asset_id")!=o.media_asset_id or
            e.get("asset_sha256")!=o.asset_sha256 or
            e.get("start_ms")!=o.start_ms or e.get("end_ms")!=o.end_ms or
            e.get("slide_index")!=o.slide_index or
            e.get("frame_ref_sha256")!=o.frame_ref_sha256):
            _deny("source_ref_provenance_mismatch")
        if o.kind not in IMAGE_KINDS:
            # B4's input is visual-only: an included transcript must not
            # be silently taken as an image classification observation.
            _deny("non_visual_selected_in_b4")
        if (e.get("carrier")!="unresolved_image_ref" or
            e.get("content_in_packet") is not False or
            set(e)!={
                "kind","ref_sha256","content_sha256","media_asset_id",
                "asset_sha256","start_ms","end_ms","slide_index",
                "frame_ref_sha256","carrier","content_in_packet"
            }):
            _deny("image_carrier_forged")
        asset=media.get(o.media_asset_id)
        if asset is None or asset.source_sha256!=o.asset_sha256:
            _deny("source_asset_identity_invalid")
        if manifest.content_kind in VIDEO_KINDS:
            if (o.kind!="sampled_frame" or asset.media_kind!="video" or
                type(o.start_ms) is not int or o.start_ms<0 or
                type(asset.duration_ms) is not int or asset.duration_ms<=0 or
                o.start_ms>asset.duration_ms):
                _deny("video_frame_not_original")
        else:
            if (type(o.slide_index) is not int or o.slide_index<1 or
                asset.slide_index!=o.slide_index or
                not ((o.kind=="slide_ref" and asset.media_kind=="image") or
                     (o.kind=="sampled_frame" and asset.media_kind=="video" and
                      type(o.start_ms) is int and o.start_ms>=0 and
                      type(asset.duration_ms) is int and asset.duration_ms>0 and
                      o.start_ms<=asset.duration_ms))):
                _deny("carousel_visual_not_original_slide")
        selected[ref]=e
    if not selected:
        _deny("no_visual_pass_possible")
    omitted=set()
    for e in omissions:
        if (type(e) is not dict or set(e)!={"kind","ref_sha256","reason"} or
            not _sha(e["ref_sha256"]) or
            e["ref_sha256"] not in originals or
            e["kind"]!=originals[e["ref_sha256"]].kind or
            e["ref_sha256"] in omitted or
            e["ref_sha256"] in selected or
            type(e["reason"]) is not str):
            _deny("omission_manifest_forged")
        omitted.add(e["ref_sha256"])
    if selected.keys() | omitted != originals.keys():
        _deny("omitted_source_counterevidence")
    # Original Stage08 coverage counts describe all relevant B4 visual
    # refs, not necessarily every source observation (e.g. ASR/scene).
    for field,expected in {
        "source_observations":len(originals),
        "relevant_observations":len(relevant),
        "selected_observations":len(selected),
        "omitted_relevant":len(relevant)-len(selected),
        "selected_image_refs":len(selected),
        "available_frame_refs":sum(x.kind=="sampled_frame" for x in relevant.values()),
        "available_slide_refs":sum(x.kind=="slide_ref" for x in relevant.values()),
    }.items():
        if type(coverage.get(field)) is not int or coverage[field]!=expected:
            _deny("visual_coverage_counter_forged")
    if (type(coverage.get("coverage_complete")) is not bool or
        coverage["coverage_complete"]!=(set(selected)==set(relevant))):
        _deny("visual_coverage_flag_forged")
    return selected,relevant


def _coverage_reasons(manifest,selected,relevant):
    reasons=[]
    if set(selected)!=set(relevant):
        reasons.append("visual_selection_incomplete")
    if manifest.content_kind in VIDEO_KINDS:
        times={x["start_ms"] for x in selected.values()}
        durations={a.duration_ms for a in manifest.assets if a.media_kind=="video"}
        if (len(durations)!=1 or any(type(d) is not int or d<=0 for d in durations)
            or len(times)<2 or min(times)>5000 or
            max(times)<max(durations)*3//4):
            reasons.append("video_temporal_coverage_weak")
    else:
        slides={x["slide_index"] for x in selected.values()}
        expected={a.slide_index for a in manifest.assets}
        if len(slides)<2 or slides!=expected:
            reasons.append("carousel_slide_coverage_weak")
    return reasons


def _verified_origin(manifest,verifier):
    """Trusted host-side provenance ONLY; never assume generated from style."""
    verify=getattr(verifier,"verify_ai_origin",None)
    if not callable(verify):
        return None,None,None
    try:
        claim=verify(manifest)
    except Exception:
        _deny("ai_origin_provenance_verifier_failed")
    if claim is None:
        return None,None,None
    if (type(claim) is not dict or
        set(claim)!={"status","claim_type","evidence_sha256","verified"} or
        claim.get("verified") is not True or
        claim.get("status") not in CLAIM_TYPES or
        claim.get("claim_type")!=CLAIM_TYPES.get(claim.get("status")) or
        not _sha(claim.get("evidence_sha256"))):
        _deny("ai_origin_provenance_not_trusted")
    return claim["status"]=="generated",claim["claim_type"],claim["evidence_sha256"]


def validate_b4_visual(*,manifest:MediaEvidenceManifest,packet:ContextPacket,
                       response:VerifiedChatResponse,receipt_verifier:object,
                       delivered_image_refs:tuple[str,...],
                       absence_verifier:object=None,
                       origin_verifier:object=None)->B4VisualProposal:
    """Accept a model's VISUAL pass only with independent delivery receipt.

    A negative 'not visible' can become SQL FALSE only after a separately
    attested exhaustive inspection of ALL original visual material.
    """
    selected,relevant=_verified_visual_packet(manifest,packet)
    if (type(delivered_image_refs) is not tuple or
        len(set(delivered_image_refs))!=len(delivered_image_refs) or
        set(delivered_image_refs)!=set(selected)):
        _deny("visual_delivery_not_authenticated")
    if (not isinstance(response,VerifiedChatResponse) or
        not _sha(response.request_sha256) or
        type(response.response_id) is not str or not response.response_id):
        _deny("model_response_identity_invalid")
    receipt=getattr(receipt_verifier,"verify_b4_response",None)
    if not callable(receipt):
        _deny("trusted_model_delivery_receipt_missing")
    try:
        accepted=receipt(response,packet,delivered_image_refs)
    except Exception:
        _deny("trusted_model_delivery_receipt_invalid")
    if accepted is not True:
        _deny("trusted_model_delivery_receipt_invalid")
    raw=response.json_data
    if type(raw) is not dict or set(raw)!=set(FEATURES):
        _deny("unexpected_visual_response_fields")
    reviews=_coverage_reasons(manifest,selected,relevant)
    complete_visual_selection=not reviews
    values={}
    evidence={}
    assessments={}
    for feature in FEATURES:
        item=raw[feature]
        if type(item) is not dict or set(item)!=_FEATURE_KEYS:
            _deny("feature_output_shape_invalid")
        state=item["assessment"]
        confidence=item["confidence"]
        if (type(state) is not str or state not in ASSESSMENTS or
            type(confidence) is not str or confidence not in CONFIDENCES):
            _deny("assessment_enum_invalid")
        refs=_refs(item["evidence_refs"],selected,require=state=="present")
        assessments[feature]=state
        evidence[feature]=list(refs)
        if state=="present":
            values[feature]=True
            if confidence=="low":
                reviews.append(feature+"_low_confidence")
        elif state=="unknown":
            if refs:
                _deny("unknown_assessment_claimed_positive_evidence")
            values[feature]=None
            reviews.append(feature+"_unknown")
        else:
            if refs:
                _deny("absence_must_be_proven_by_full_visual_pass")
            # A verified finite set of still frames alone CANNOT establish
            # absence in an entire original video.
            absent=getattr(absence_verifier,"verify_whole_media_absence",None)
            if not callable(absent):
                values[feature]=None
                reviews.append(feature+"_absence_unverified")
            else:
                try:
                    ok=absent(manifest,feature,tuple(delivered_image_refs))
                except Exception:
                    _deny("whole_media_absence_attestor_failed")
                if ok is True and complete_visual_selection and confidence!="low":
                    values[feature]=False
                else:
                    values[feature]=None
                    reviews.append(feature+"_absence_unverified")
        if confidence=="low" and feature+"_low_confidence" not in reviews:
            reviews.append(feature+"_low_confidence")
    generated,claim_type,claim_sha=_verified_origin(manifest,origin_verifier)
    if any(type(values[k]) is not bool for k in FEATURES):
        state="needs_review"
    else:
        state="needs_review" if reviews else "inferred"
    p={
        "schema_version":VERSION,"project_id":manifest.project_id,
        "b_run_id":manifest.b_run_id,"video_id":manifest.video_id,
        "qdp_run_id":manifest.qdp_run_id,"ga_result_id":manifest.ga_result_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "source_policy_version_id":manifest.source_policy_version_id,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "content_kind":manifest.content_kind,
        "model_request_sha256":response.request_sha256,
        "provider_response_id":response.response_id,
        "ai_generated":generated,**values,
        "feature_evidence_refs":evidence,"feature_claims":assessments,
        "delivered_visual_refs":list(delivered_image_refs),
        "ai_origin_evidence_sha256":claim_sha,
        "ai_origin_claim_type":claim_type,
        "classification_status":state,
        "visual_pass_state":"authenticated_complete" if not reviews else "incomplete_or_review",
        "review_reasons":sorted(set(reviews)),
    }
    return B4VisualProposal(p,canonical_hash(p),True)


@dataclass(frozen=True,slots=True)
class B4PreparedRequest:
    body:dict
    request_sha256:str
    prompt_sha256:str
    def verify_digest(self):
        try:
            return canonical_hash(self.body)==self.request_sha256
        except (TypeError,ValueError):
            return False


def prepare_b4_request(*,catalog,model:str,packet:ContextPacket,
                       max_output_tokens:int,attachments:tuple=())->B4PreparedRequest:
    """Never sends a request or infers AI origin from model judgement."""
    if (not isinstance(packet,ContextPacket) or not packet.verify_digest() or
        packet.payload.get("block")!="B4" or
        packet.payload.get("delivery_state")!="not_sent" or
        packet.payload.get("media_delivery_state")!="opaque_refs_not_attached"):
        _deny("b4_packet_not_prepared")
    from ..providers.openrouter import prepare_chat_request
    from .b4_schema import B4_OUTPUT_SCHEMA
    try:
        prompt=(Path(__file__).resolve().parents[1]/
                "prompts"/"b4_visual_v1.txt").read_text(encoding="utf-8")
    except (OSError,UnicodeError):
        _deny("b4_trusted_prompt_missing")
    if not prompt or len(prompt.encode("utf-8"))>16000:
        _deny("b4_trusted_prompt_invalid")
    body=prepare_chat_request(
        catalog=catalog,model=model,packet=packet,
        output_schema=B4_OUTPUT_SCHEMA,
        max_output_tokens=max_output_tokens,attachments=attachments,
    )
    body["messages"][0]["content"]+="\n\n"+prompt
    data=json.dumps(body,ensure_ascii=False,sort_keys=True,
                    separators=(",",":"),allow_nan=False).encode("utf-8")
    if len(data)>4_000_000:
        _deny("b4_request_too_large")
    return B4PreparedRequest(body,canonical_hash(body),sha256(prompt.encode("utf-8")).hexdigest())
