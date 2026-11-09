"""GB-MOD-16 B3 semantic story roles: strict provider/evidence-gated proposals.

Stage15 sampling is NOT an inference. This validator only accepts actual
authenticated, media-delivered story classifications for pre-existing sampled
frame/slide refs and separately supported meaning intervals. It never turns
A technical scene cuts into semantic boundaries. No paid HTTP, DB writes or
fabricated placeholders occur in this module.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from ..context.builder import ContextPacket
import json
import re

from ..contracts import canonical_hash
from ..context.manifest import MediaEvidenceManifest
from ..providers.protocols import VerifiedChatResponse
from ..errors import GroupBError
from .b3_sampling import (
    B3SamplingPlan, SamplingPolicy, verify_b3_plan_against_manifest,
    VIDEO_KINDS, CAROUSEL_KINDS,
)

VERSION="group-b-b3-story/v1"
TAXONOMY="1.0"
ROLES=(
    "hook","context","problem","consequence","goal","myth","mistake","cause",
    "explanation","fact","tip","step","demonstration","comparison","example",
    "proof","testimonial","product_intro","product_demo","before_after",
    "result","reveal","summary","offer","cta","other",
)
PHASES=("before","after","both")
BOUNDARIES=("known","estimated","unknown")
CONFIDENCE=("high","medium","low")
_SHA=re.compile(r"[0-9a-f]{64}\Z")
_FRAME_KEYS=frozenset(("slot_index","role","before_after_phase","confidence",
                       "evidence_refs"))
_INTERVAL_KEYS=frozenset(("start_ms","end_ms","role","before_after_phase",
                          "boundary_quality","confidence","evidence_refs"))
_SLIDE_KEYS=frozenset(("slide_index","role","before_after_phase","confidence",
                       "evidence_refs","screen_text","visual_description"))
_CANDIDATE_KEYS=frozenset(("frames","intervals","slides"))


class B3StoryError(GroupBError):
    def __init__(self,reason: str):
        self.reason=reason
        super().__init__("b3_story_unavailable","b3: "+reason)


def _deny(reason):
    raise B3StoryError(reason)


def _sha(x):
    return type(x) is str and _SHA.fullmatch(x) is not None


def _classify(row,*,kind):
    if type(row) is not dict:
        _deny(kind+"_not_object")
    role=row.get("role")
    phase=row.get("before_after_phase")
    confidence=row.get("confidence")
    if role not in ROLES or type(role) is not str or confidence not in CONFIDENCE:
        _deny(kind+"_invalid_label_or_confidence")
    if phase not in ("",)+PHASES or type(phase) is not str:
        _deny(kind+"_invalid_before_after_phase")
    if role!="before_after" and phase!="":
        _deny(kind+"_phase_without_before_after")
    review=confidence=="low" or role=="other" or (role=="before_after" and phase=="")
    return role,phase or None,review


def _verified_text_packet(manifest,packet):
    """Verify actual UTF8 delivered as untrusted B3 data, not hallucinated ASR."""
    if packet is None:
        return {}
    if (not isinstance(packet,ContextPacket) or not packet.verify_digest() or
        packet.payload.get("schema_version")!="group-b-context/v1" or
        packet.payload.get("block")!="B3" or
        packet.payload.get("project_id")!=manifest.project_id or
        packet.payload.get("b_run_id")!=manifest.b_run_id or
        packet.payload.get("qdp_run_id")!=manifest.qdp_run_id or
        packet.payload.get("video_id")!=manifest.video_id or
        packet.payload.get("ga_result_id")!=manifest.ga_result_id or
        packet.payload.get("ga_result_sha256")!=manifest.ga_result_sha256 or
        packet.payload.get("source_policy_version_id")!=manifest.source_policy_version_id or
        packet.payload.get("input_content_kind")!=manifest.content_kind or
        packet.payload.get("source_evidence_manifest_sha256")!=manifest.manifest_sha256 or
        packet.payload.get("delivery_state")!="not_sent" or
        packet.payload.get("media_delivery_state")!="opaque_refs_not_attached"):
        _deny("text_packet_lineage_invalid")
    source={x.ref_sha256:x for x in manifest.observations
            if x.kind in ("ocr_span","transcript_segment")}
    trusted={}
    for entry in packet.payload.get("entries",()):
        if type(entry) is not dict:
            _deny("text_packet_entry_invalid")
        if entry.get("kind") not in ("ocr_span","transcript_segment"):
            continue
        ref=entry.get("ref_sha256")
        original=source.get(ref)
        value=entry.get("untrusted_text")
        assets={a.asset_id:a for a in manifest.assets}
        media=assets.get(original.media_asset_id) if original is not None else None
        if (media is None or media.media_kind!="video" or
            type(media.duration_ms) is not int or media.duration_ms<=0 or
            original.timebase!="video_absolute" or
            type(original.start_ms) is not int or type(original.end_ms) is not int or
            not 0<=original.start_ms<=original.end_ms<=media.duration_ms):
            _deny("text_original_timebase_invalid")
        if (original is None or ref in trusted or type(value) is not str or
            len(value.encode("utf-8"))>100000 or
            sha256(value.encode("utf-8")).hexdigest()!=original.content_sha256 or
            entry.get("carrier")!="untrusted_utf8_data" or
            entry.get("content_in_packet") is not True or
            entry.get("media_asset_id")!=original.media_asset_id or
            entry.get("asset_sha256")!=original.asset_sha256 or
            entry.get("start_ms")!=original.start_ms or
            entry.get("end_ms")!=original.end_ms or
            entry.get("slide_index")!=original.slide_index or
            entry.get("frame_ref_sha256")!=original.frame_ref_sha256):
            _deny("text_evidence_provenance_invalid")
        trusted[ref]=(original,value)
    return trusted


def _text_at(refs,texts,slot,*,video):
    for ref in refs:
        if ref not in texts:
            continue
        item,_=texts[ref]
        if item.media_asset_id!=slot["asset_id"]:
            _deny("text_media_identity_mismatch")
        if video:
            t=slot["observed_ms"]
            if (type(t) is not int or type(item.start_ms) is not int or
                type(item.end_ms) is not int or not item.start_ms<=t<=item.end_ms):
                _deny("text_outside_observed_frame")
        elif item.slide_index!=slot["slide_index"]:
            _deny("text_outside_original_slide")


def _refs(refs, allowed):
    if (type(refs) is not list or not 1<=len(refs)<=40 or
        any(not _sha(x) for x in refs) or len(set(refs))!=len(refs) or
        not set(refs).issubset(allowed)):
        _deny("story_evidence_refs_invalid")
    return refs


@dataclass(frozen=True,slots=True)
class B3StoryProposal:
    payload: dict
    proposal_sha256: str
    authenticated_inference: bool

    def verify_digest(self):
        try:
            return canonical_hash(self.payload)==self.proposal_sha256
        except (TypeError,ValueError):
            return False

    def frame_db_rows(self):
        return tuple({
            "frame_index":r["frame_index"],
            "time_sec":Decimal(r["observed_ms"])/1000,
            "sampled_frame_ref":r["sampled_frame_ref"],
            "role":r["role"],"before_after_phase":r["before_after_phase"],
            "needs_review":r["needs_review"],
        } for r in self.payload["frames"])

    def interval_db_rows(self):
        return tuple({
            "interval_index":r["interval_index"],
            "start_sec":Decimal(r["start_ms"])/1000,
            "end_sec":Decimal(r["end_ms"])/1000,
            "role":r["role"],"before_after_phase":r["before_after_phase"],
            "boundary_quality":r["boundary_quality"],
            "needs_review":r["needs_review"],
        } for r in self.payload["intervals"])

    def slide_db_rows(self):
        return tuple({
            "slide_index":r["slide_index"],
            "role":r["role"],"before_after_phase":r["before_after_phase"],
            "screen_text":r["screen_text"],
            "visual_description":r["visual_description"],
            "needs_review":r["needs_review"],
        } for r in self.payload["slides"])

    @property
    def story_pattern(self):
        """Exactly mirrors 005 view: video interval roles; carousel slide roles."""
        rows=self.payload["intervals"] if self.payload["content_type"]=="video" else self.payload["slides"]
        return tuple(x["role"] for x in rows)

    @property
    def needs_review(self):
        return self.payload["classification_status"]!="inferred"


def unknown_b3(*,plan:B3SamplingPlan,reason:str)->B3StoryProposal:
    """No verified inference: empty role rows, not fake 'other' story."""
    if not isinstance(plan,B3SamplingPlan) or not plan.verify_digest():
        _deny("sampling_plan_unverified")
    if type(reason) is not str or re.fullmatch(r"[a-z][a-z0-9_]{1,79}",reason) is None:
        _deny("unknown_reason_invalid")
    p=plan.payload
    payload={
        "schema_version":VERSION,"taxonomy_version":TAXONOMY,
        "project_id":p["project_id"],"b_run_id":p["b_run_id"],
        "video_id":p["video_id"],"qdp_run_id":p["qdp_run_id"],
        "ga_result_sha256":p["ga_result_sha256"],
        "media_evidence_manifest_sha256":p["media_evidence_manifest_sha256"],
        "sampling_plan_sha256":plan.plan_sha256,
        "content_type":"video" if p["content_kind"] in VIDEO_KINDS else "carousel",
        "provider_response_id":None,"model_request_sha256":None,
        "frames":[],"intervals":[],"slides":[],
        "classification_status":"unknown","reason_codes":[reason],
        "verification_state":"not_inferred",
    }
    return B3StoryProposal(payload,canonical_hash(payload),False)


def validate_b3_story(*,manifest:MediaEvidenceManifest,plan:B3SamplingPlan,
                       response:VerifiedChatResponse,receipt_verifier:object,
                       delivered_visual_refs:tuple[str,...],
                       policy:SamplingPolicy|None=None,
                       boundary_verifier:object=None,
                       text_packet:ContextPacket|None=None,
                       delivered_text_refs:tuple[str,...]=())->B3StoryProposal:
    """Strict host-verifier gate with independent original-plan recomputation.

    receipt_verifier.verify_b3_response(response,plan,delivered_refs) must
    authenticate approved exact remote attempt/body, provider response ID,
    and delivered IMAGE BYTES. Merely providing an image pointer or dict
    pretending approval never authorizes an inferred result.
    """
    if not verify_b3_plan_against_manifest(plan,manifest,policy=policy):
        _deny("sampling_plan_not_current")
    src=plan.payload
    if (src["ga_result_sha256"]!=manifest.ga_result_sha256 or
        src["project_id"]!=manifest.project_id or
        src["video_id"]!=manifest.video_id or
        src["qdp_run_id"]!=manifest.qdp_run_id):
        _deny("sampling_lineage_mismatch")
    slots=src["slots"]
    originals={x.ref_sha256:x for x in manifest.observations
               if x.kind in ("sampled_frame","slide_ref")}
    matched={s["matched_ref_sha256"]:s for s in slots if s["status"]=="matched"}
    if (not matched or len(matched)!=sum(s["status"]=="matched" for s in slots)
        or any(x not in originals for x in matched)):
        _deny("matched_visual_sources_invalid")
    if (type(delivered_visual_refs) is not tuple or
        len(set(delivered_visual_refs))!=len(delivered_visual_refs) or
        set(delivered_visual_refs)!=set(matched)):
        _deny("incomplete_or_forged_image_delivery")
    if (not isinstance(response,VerifiedChatResponse) or
        not _sha(response.request_sha256) or
        type(response.response_id) is not str or not response.response_id):
        _deny("provider_response_invalid")
    verified_texts=_verified_text_packet(manifest,text_packet)
    if (type(delivered_text_refs) is not tuple or
        len(delivered_text_refs)!=len(set(delivered_text_refs)) or
        set(delivered_text_refs)!=set(verified_texts)):
        _deny("text_delivery_not_authenticated")
    checker=getattr(receipt_verifier,"verify_b3_response",None)
    if not callable(checker):
        _deny("authenticated_provider_receipt_missing")
    try:
        # A verifier that supports texts must attest the exact text refs in
        # the remote request; historical three-arg visual-only gate remains.
        trusted=(checker(response,plan,delivered_visual_refs,delivered_text_refs)
                 if verified_texts else checker(response,plan,delivered_visual_refs))
    except Exception:
        _deny("authenticated_provider_receipt_invalid")
    if trusted is not True:
        _deny("authenticated_provider_receipt_invalid")
    raw=response.json_data
    if type(raw) is not dict or set(raw)!=_CANDIDATE_KEYS:
        _deny("story_response_shape_invalid")
    video=src["content_kind"] in VIDEO_KINDS
    rows=raw["frames"] if video else raw["slides"]
    opposite=raw["slides"] if video else raw["frames"]
    if type(rows) is not list or not rows or type(opposite) is not list or opposite:
        _deny("story_kind_mismatch")
    if type(raw["intervals"]) is not list or (
        not video and raw["intervals"]
    ):
        _deny("story_interval_kind_invalid")
    if len(rows)>len(slots) or len(raw["intervals"])>120:
        _deny("story_rows_exceed_sampling_budget")
    reasons=set()
    if not src["coverage"]["complete"]:
        reasons.add("sampling_incomplete")
    rendered=[]
    chosen=set()
    source_by_slot={x["slot_index"]:x for x in slots}
    for row in rows:
        expected=_FRAME_KEYS if video else _SLIDE_KEYS
        if type(row) is not dict or set(row)!=expected:
            _deny("story_row_shape_invalid")
        pos=row["slot_index"] if video else row["slide_index"]
        if type(pos) is not int or pos<=0 or pos in chosen:
            _deny("duplicate_or_invalid_story_index")
        chosen.add(pos)
        slot=(source_by_slot.get(pos) if video else
              next((x for x in slots if x["slide_index"]==pos),None))
        if slot is None or slot["status"]!="matched":
            _deny("story_role_on_unobserved_slot")
        refs=_refs(row["evidence_refs"],set(matched)|set(verified_texts))
        if slot["matched_ref_sha256"] not in refs:
            _deny("story_role_not_grounded_in_own_visual")
        _text_at(refs,verified_texts,slot,video=video)
        role,phase,review=_classify(row,kind="story")
        if review:
            reasons.add("uncertain_story_role")
        if video:
            rendered.append({
                "frame_index":pos,"observed_ms":slot["observed_ms"],
                "sampled_frame_ref":slot["matched_ref_sha256"],
                "role":role,"before_after_phase":phase,
                "needs_review":review,"evidence_refs":list(refs),
            })
        else:
            texts={}
            for key in ("screen_text","visual_description"):
                value=row[key]
                if type(value) is not str or len(value)>8192 or "\x00" in value:
                    _deny("story_text_invalid")
                # A visual description with delivered slide is an
                # untrusted semantic assertion, not a verified OCR quote.
                texts[key]=value or None
            if texts["screen_text"]:
                # Even a delivered vision model is not an OCR witness.
                # Require exact SHA-verified OCR bytes in that same slide.
                if not any(r in verified_texts and
                           verified_texts[r][0].kind=="ocr_span" and
                           texts["screen_text"] in verified_texts[r][1]
                           for r in refs):
                    _deny("screen_text_requires_independent_ocr")
            rendered.append({
                "slide_index":pos,"role":role,
                "before_after_phase":phase,
                "screen_text":None,
                "visual_description":texts["visual_description"],
                "needs_review":review,"evidence_refs":list(refs),
            })
    matched_slots={x["slot_index"] if video else x["slide_index"]
                   for x in slots if x["status"]=="matched"}
    if chosen!=matched_slots:
        reasons.add("unclassified_sampled_visuals")
    if video:
        rendered.sort(key=lambda x:x["frame_index"])
    else:
        rendered.sort(key=lambda x:x["slide_index"])
    # Interval semantic endpoints must be explicitly time-backed by
    # delivered observed *frame timestamps*. GA scene boundaries alone are
    # not allowed to create semantic intervals or 'known' quality.
    semantic=[]
    if video:
        times={x["observed_ms"] for x in rendered}
        media_duration=src["coverage"]["source_duration_ms"]
        if type(media_duration) is not int or media_duration<=0:
            _deny("verified_video_duration_unavailable")
        previous_end=-1
        for i,row in enumerate(raw["intervals"],1):
            if type(row) is not dict or set(row)!=_INTERVAL_KEYS:
                _deny("story_interval_shape_invalid")
            start,end=row["start_ms"],row["end_ms"]
            if (type(start) is not int or type(end) is not int or
                start<0 or end<=start or start<previous_end or
                start not in times or
                (end not in times and end!=media_duration) or
                end>src["coverage"]["scheduled_duration_ms"]):
                _deny("semantic_boundaries_not_frame_grounded")
            previous_end=end
            role,phase,review=_classify(row,kind="interval")
            quality=row["boundary_quality"]
            if quality not in BOUNDARIES or type(quality) is not str:
                _deny("boundary_quality_invalid")
            terminal=end==media_duration and end not in times
            # The original video end is verified media metadata, not an
            # observed picture. Only separate trusted semantic attestation
            # with a <=500ms original terminal frame may close that boundary.
            if terminal and (quality!="known" or media_duration-max(times)>500):
                _deny("unattested_video_terminal_boundary")
            refs=_refs(row["evidence_refs"],set(matched)|set(verified_texts))
            for ref in refs:
                if ref in verified_texts:
                    obs,_=verified_texts[ref]
                    if (obs.media_asset_id!=slots[0]["asset_id"] or
                        type(obs.start_ms) is not int or type(obs.end_ms) is not int or
                        obs.end_ms<start or obs.start_ms>end):
                        _deny("interval_text_not_temporally_supported")
            start_proofs=[r for r in refs if r in matched and matched[r]["observed_ms"]==start]
            end_proofs=[r for r in refs if r in matched and matched[r]["observed_ms"]==end]
            if terminal:
                end_proofs=[r for r in refs if r in matched and matched[r]["observed_ms"]==max(times)]
            if not start_proofs or not end_proofs or len(set(start_proofs+end_proofs))<2:
                _deny("semantic_interval_endpoints_not_evidenced")
            if quality=="known":
                # Trusted semantic annotation is DISTINCT from technical A
                # scene boundaries or an arbitrary model-generated time range.
                checker=getattr(boundary_verifier,"verify_semantic_boundary",None)
                if not callable(checker):
                    _deny("known_boundary_requires_external_attestation")
                try:
                    accepted=checker(manifest,plan,start,end,role,tuple(row["evidence_refs"]))
                except Exception:
                    _deny("known_boundary_attestation_failed")
                if accepted is not True:
                    _deny("known_boundary_attestation_failed")
            else:
                reasons.add("semantic_boundary_not_verified")
                review=True
            if review:
                reasons.add("uncertain_story_interval")
            if i==1 and start>min(times):
                reasons.add("story_opening_gap")
            semantic.append({
                "interval_index":i,"start_ms":start,"end_ms":end,
                "role":role,"before_after_phase":phase,
                "boundary_quality":quality,"needs_review":review,
                "evidence_refs":list(refs),
            })
        if not semantic:
            reasons.add("semantic_intervals_not_measured")
        elif semantic[-1]["end_ms"]<media_duration:
            reasons.add("story_closing_gap")
        for left,right in zip(semantic,semantic[1:]):
            if right["start_ms"]>left["end_ms"]:
                reasons.add("story_interval_gap")
    payload={
        "schema_version":VERSION,"taxonomy_version":TAXONOMY,
        "project_id":manifest.project_id,"b_run_id":manifest.b_run_id,
        "video_id":manifest.video_id,"qdp_run_id":manifest.qdp_run_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "media_evidence_manifest_sha256":manifest.manifest_sha256,
        "sampling_plan_sha256":plan.plan_sha256,
        "content_type":"video" if video else "carousel",
        "provider_response_id":response.response_id,
        "model_request_sha256":response.request_sha256,
        "delivered_visual_refs":list(delivered_visual_refs),
        "delivered_text_refs":list(delivered_text_refs),
        "frames":rendered if video else [],
        "intervals":semantic,"slides":rendered if not video else [],
        "classification_status":"needs_review" if reasons else "inferred",
        "reason_codes":sorted(reasons),
        "verification_state":"authenticated_receipt_and_exact_visual_source",
    }
    return B3StoryProposal(payload,canonical_hash(payload),True)


@dataclass(frozen=True,slots=True)
class B3PreparedRequest:
    """Hash-pinned but UNSENT strict model request; no paid authorization."""
    body: dict
    request_sha256: str
    prompt_sha256: str
    sampling_plan_sha256: str

    def verify_digest(self):
        try:
            return canonical_hash(self.body)==self.request_sha256
        except (TypeError,ValueError):
            return False


def prepare_b3_story_request(*,manifest:MediaEvidenceManifest,
                             plan:B3SamplingPlan,packet:ContextPacket,
                             catalog,model:str,max_output_tokens:int,
                             attachments:tuple=(),policy:SamplingPolicy|None=None
                             )->B3PreparedRequest:
    """Full-pinned B3 model planning; real images mandatory, NO HTTP POST.

    Refuse if the Stage08 packet selected fewer image refs than the
    independently recomputed Stage15 plan. A future smaller-budget
    classifier needs its own versioned partial-delivery evidence contract;
    it must never pretend all planned frames were seen.
    """
    if not verify_b3_plan_against_manifest(plan,manifest,policy=policy):
        _deny("request_sampling_plan_invalid")
    if (not isinstance(packet,ContextPacket) or not packet.verify_digest() or
        packet.payload.get("schema_version")!="group-b-context/v1" or
        packet.payload.get("block")!="B3" or
        packet.payload.get("project_id")!=manifest.project_id or
        packet.payload.get("b_run_id")!=manifest.b_run_id or
        packet.payload.get("qdp_run_id")!=manifest.qdp_run_id or
        packet.payload.get("video_id")!=manifest.video_id or
        packet.payload.get("ga_result_id")!=manifest.ga_result_id or
        packet.payload.get("ga_result_sha256")!=manifest.ga_result_sha256 or
        packet.payload.get("source_policy_version_id")!=manifest.source_policy_version_id or
        packet.payload.get("source_evidence_manifest_sha256")!=manifest.manifest_sha256 or
        packet.payload.get("input_content_kind")!=manifest.content_kind or
        packet.payload.get("delivery_state")!="not_sent" or
        packet.payload.get("media_delivery_state")!="opaque_refs_not_attached"):
        _deny("request_context_lineage_invalid")
    slots=plan.payload["slots"]
    expected={s["matched_ref_sha256"] for s in slots if s["status"]=="matched"}
    source={x.ref_sha256:x for x in manifest.observations
            if x.kind in ("sampled_frame","slide_ref")}
    entries=packet.payload.get("entries")
    if type(entries) is not list:
        _deny("request_entries_invalid")
    selected={}
    for item in entries:
        if type(item) is not dict:
            _deny("request_entry_invalid")
        if item.get("kind") not in ("sampled_frame","slide_ref"):
            continue
        ref=item.get("ref_sha256")
        original=source.get(ref)
        if (original is None or ref in selected or
            item.get("content_sha256")!=original.content_sha256 or
            item.get("media_asset_id")!=original.media_asset_id or
            item.get("asset_sha256")!=original.asset_sha256 or
            item.get("start_ms")!=original.start_ms or
            item.get("end_ms")!=original.end_ms or
            item.get("slide_index")!=original.slide_index or
            item.get("carrier")!="unresolved_image_ref" or
            item.get("content_in_packet") is not False):
            _deny("request_visual_source_forged")
        selected[ref]=item
    if not expected or set(selected)!=expected:
        _deny("request_visual_selection_not_full_plan")
    from ..providers.openrouter import prepare_chat_request
    from .b3_schema import B3_STORY_SCHEMA
    try:
        prompt=(Path(__file__).resolve().parents[1]/
                "prompts"/"b3_story_v1.txt").read_text(encoding="utf-8")
    except (OSError,UnicodeError):
        _deny("trusted_b3_prompt_unavailable")
    if not prompt or len(prompt.encode("utf-8"))>16_000:
        _deny("trusted_b3_prompt_invalid")
    body=prepare_chat_request(
        catalog=catalog,model=model,packet=packet,
        output_schema=B3_STORY_SCHEMA,
        max_output_tokens=max_output_tokens,attachments=attachments,
    )
    body["messages"][0]["content"]+="\n\n"+prompt
    packed=json.dumps(body,sort_keys=True,ensure_ascii=False,allow_nan=False,
                      separators=(",",":")).encode("utf-8")
    if len(packed)>4_000_000:
        _deny("b3_request_too_large")
    return B3PreparedRequest(
        body,canonical_hash(body),
        sha256(prompt.encode("utf-8")).hexdigest(),
        plan.plan_sha256,
    )
