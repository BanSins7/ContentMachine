"""GB-MOD-13: B1 evidence-linked hook classifier/validator, no model calls.

Accepts semantic judgments only from an explicitly authenticated provider
response (future GB11 paid execution integration). Neither source text alone
nor the SHA of a candidate JSON constitutes an inference. The current code
constructs a typed, reviewable B1 proposal; never writes content_b_hooks.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from collections.abc import Mapping
from typing import Any
import re

from ..contracts import canonical_hash
from ..context.builder import ContextPacket
from ..context.manifest import MediaEvidenceManifest
from ..providers.protocols import VerifiedChatResponse
from ..errors import GroupBError

VERSION = "group-b-b1-hook/v1"
TAXONOMY = "1.0"
MECHANISMS = (
    "problem","promise","curiosity","myth_busting","mistake",
    "surprise","story","challenge","comparison","proof",
)
DELIVERY = ("speech","on_screen_text","visual_action","sound")
FLAGS = ("is_question","has_number","direct_address","has_negation","is_command")
CHANNEL_KINDS = {
    "speech": frozenset(("transcript_segment",)),
    "on_screen_text": frozenset(("ocr_span",)),
    "visual_action": frozenset(("sampled_frame","slide_ref")),
    "sound": frozenset(("audio_event",)),
}
SHA = re.compile(r"[a-f0-9]{64}\Z")
TEXT_TYPES = ("transcript_segment","ocr_span")
IMAGE_TYPES = ("sampled_frame","slide_ref")
VIDEO_KINDS = ("video","slideshow_video")
CAROUSEL_KINDS = ("photo_carousel","mixed_carousel")
CANDIDATE_KEYS = frozenset((
    "classification_status","start_ms","end_ms","slide_index",
    "spoken_text","screen_text","visual_description","mechanisms",
    "flags","delivery","evidence_refs","hook_parts",
))
PART_KEYS = frozenset((
    "part_index","start_ms","end_ms","slide_index","verbatim_text",
    "mechanisms","evidence_refs",
))


class B1EvidenceError(GroupBError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("b1_unavailable","b1: "+reason)


def _deny(reason):
    raise B1EvidenceError(reason)


def _canonical_labels(value, permitted, *, nonempty=False):
    if (type(value) is not list or len(value)>len(permitted) or
        (nonempty and not value) or any(type(x) is not str or x not in permitted for x in value) or
        len(set(value))!=len(value) or
        value != sorted(value, key=permitted.index)):
        _deny("label_set_invalid")
    return tuple(value)


def _ref_list(value, *, nonempty=False):
    if (type(value) is not list or len(value)>100 or
        (nonempty and not value) or any(type(v) is not str or not SHA.fullmatch(v) for v in value) or
        len(set(value))!=len(value)):
        _deny("evidence_ref_list_invalid")
    return tuple(value)


def _text(value: Any, *, max_len=8192) -> str:
    if type(value) is not str or len(value)>max_len or "\x00" in value:
        _deny("text_field_invalid")
    return value


def _place(obj, *, video, measured, entries):
    start,end,slide=(obj.get("start_ms"),obj.get("end_ms"),obj.get("slide_index"))
    if video:
        if (type(start) is not int or type(end) is not int or
            start<0 or end<=start or end>5000 or slide!=0):
            _deny("hook_video_position_invalid")
        if not measured or end>measured:
            _deny("hook_exceeds_verified_duration")
        if not any(x.get("start_ms") is not None and x["start_ms"]<end
                   and x.get("end_ms") is not None and x["end_ms"]>=start
                   for x in entries):
            _deny("hook_time_uncovered")
    else:
        if (start!=0 or end!=0 or type(slide) is not int or slide<1 or
            not any(x["kind"]=="slide_ref" and x["slide_index"]==slide for x in entries)):
            _deny("hook_slide_not_observed")


def _touch(entry, position, *, video) -> bool:
    if video:
        if entry.get("start_ms") is None or entry.get("end_ms") is None:
            return False
        return entry["start_ms"] < position["end_ms"] and entry["end_ms"] >= position["start_ms"]
    return entry.get("slide_index")==position["slide_index"] and entry["kind"]=="slide_ref"


def _verified_source(entry, observed):
    return (entry["kind"]==observed.kind and
            entry["ref_sha256"]==observed.ref_sha256 and
            entry["content_sha256"]==observed.content_sha256 and
            entry["media_asset_id"]==observed.media_asset_id and
            entry["asset_sha256"]==observed.asset_sha256 and
            entry.get("start_ms")==observed.start_ms and
            entry.get("end_ms")==observed.end_ms and
            entry.get("slide_index")==observed.slide_index and
            entry.get("frame_ref_sha256")==observed.frame_ref_sha256)


def _text_proof(quoted, refs, entries):
    if not quoted:
        return False
    # Actual OCR or ASR UTF-8 must be in this exact budgeted context,
    # not merely listed as an unused SHA or guessed from another publication.
    for ref in refs:
        item=entries[ref]
        if (item["kind"] in TEXT_TYPES and item.get("content_in_packet") is True and
            item.get("carrier")=="untrusted_utf8_data" and
            type(item.get("untrusted_text")) is str and
            quoted in item["untrusted_text"]):
            return True
    return False


def _display_refs(refs, entries, *, delivered_image_refs, delivered_audio_refs, video, location):
    for ref in refs:
        if ref not in entries:
            _deny("reference_not_selected")
        item=entries[ref]
        if not _touch(item,location,video=video):
            _deny("evidence_outside_position")
        if item["kind"] in IMAGE_TYPES and ref not in delivered_image_refs:
            _deny("image_not_delivered")
        if item["kind"]=="audio_event" and ref not in delivered_audio_refs:
            _deny("sound_not_delivered")


@dataclass(frozen=True,slots=True)
class B1HookProposal:
    payload: dict
    proposal_sha256: str
    authenticated_inference: bool

    def verify_digest(self) -> bool:
        try:
            return canonical_hash(self.payload)==self.proposal_sha256
        except (ValueError,TypeError):
            return False

    def hook_db_fields(self) -> dict:
        x=self.payload["hook"]
        return {
            "start_sec":Decimal(x["start_ms"])/1000 if x["content_position"]=="video" else None,
            "end_sec":Decimal(x["end_ms"])/1000 if x["content_position"]=="video" else None,
            "slide_index":x["slide_index"] if x["content_position"]=="carousel" else None,
            "spoken_text":x["spoken_text"],"screen_text":x["screen_text"],
            "visual_description":x["visual_description"],
            "mechanisms":x["mechanisms"],
            **x["flags"],
            "delivery":x["delivery"],
            "classification_status":x["classification_status"],
            "taxonomy_version":TAXONOMY,
        }

    def parts_db_rows(self) -> tuple[dict,...]:
        return tuple({
            "part_index":p["part_index"],
            "start_sec":Decimal(p["start_ms"])/1000 if p["content_position"]=="video" else None,
            "end_sec":Decimal(p["end_ms"])/1000 if p["content_position"]=="video" else None,
            "slide_index":p["slide_index"] if p["content_position"]=="carousel" else None,
            "verbatim_text":p["verbatim_text"],
            "mechanisms":p["mechanisms"],
        } for p in self.payload["hook_parts"])


def unknown_b1(*, manifest: MediaEvidenceManifest, reason: str) -> B1HookProposal:
    """No model, complete observations or verified receipt => unknown, NOT false."""
    if not isinstance(manifest,MediaEvidenceManifest) or not manifest.verify_digest():
        _deny("source_manifest_unverified")
    if type(reason) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{1,79}",reason):
        _deny("unknown_reason_invalid")
    x={
        "content_position":"video" if manifest.content_kind in VIDEO_KINDS else "carousel",
        "classification_status":"unknown","start_ms":None,"end_ms":None,
        "slide_index":None,"spoken_text":None,"screen_text":None,
        "visual_description":None,"mechanisms":[],
        "flags":{k:None for k in FLAGS},"delivery":[],
    }
    payload={
        "schema_version":VERSION,"taxonomy_version":TAXONOMY,
        "project_id":manifest.project_id,"b_run_id":manifest.b_run_id,
        "video_id":manifest.video_id,"ga_result_sha256":manifest.ga_result_sha256,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "model_request_sha256":None,"provider_response_id":None,
        "evidence_refs":[],"hook":x,"hook_parts":[],
        "verification_state":"unknown","reason_codes":[reason],
    }
    return B1HookProposal(payload,canonical_hash(payload),False)


def validate_b1_response(
    *, manifest: MediaEvidenceManifest,packet: ContextPacket,
    response: VerifiedChatResponse,receipt_verifier: object,
    delivered_image_refs: tuple[str,...] = (),
    delivered_audio_refs: tuple[str,...] = (),
) -> B1HookProposal:
    """Inspect an already decoded model response; NEVER initiate inference.

    The verifier is a trusted system adapter, not client data:
      verify_b1_response(response, packet)->True
    must authenticate the remote response & request ID against durable
    approved attempt + provider receipt, including actual media delivery.
    Supplied delivery ref sets must be attested by that verifier.
    """
    if (not isinstance(manifest,MediaEvidenceManifest) or not manifest.verify_digest() or
        not isinstance(packet,ContextPacket) or not packet.verify_digest() or
        not isinstance(response,VerifiedChatResponse) or
        packet.payload.get("schema_version")!="group-b-context/v1" or
        packet.payload.get("block")!="B1" or
        packet.payload.get("project_id")!=manifest.project_id or
        packet.payload.get("b_run_id")!=manifest.b_run_id or
        packet.payload.get("video_id")!=manifest.video_id or
        packet.payload.get("ga_result_sha256")!=manifest.ga_result_sha256 or
        packet.payload.get("source_evidence_manifest_sha256")!=manifest.manifest_sha256 or
        packet.payload.get("delivery_state")!="not_sent" or
        packet.payload.get("media_delivery_state")!="opaque_refs_not_attached"):
        _deny("input_lineage_or_packet_invalid")
    if (packet.payload.get("coverage",{}).get("omitted_relevant")!=0 or
        packet.payload.get("coverage",{}).get("coverage_complete") is not True):
        _deny("incomplete_hook_context")
    entries_raw=packet.payload.get("entries")
    if type(entries_raw) is not list or len(entries_raw)>2000:
        _deny("context_entries_invalid")
    catalog={x.ref_sha256:x for x in manifest.observations}
    entries={}
    for entry in entries_raw:
        if type(entry) is not dict or type(entry.get("ref_sha256")) is not str:
            _deny("context_entry_invalid")
        ref=entry["ref_sha256"]
        if ref in entries or ref not in catalog or not _verified_source(entry,catalog[ref]):
            _deny("context_source_provenance_mismatch")
        entries[ref]=entry
    video=manifest.content_kind in VIDEO_KINDS
    if not video and manifest.content_kind not in CAROUSEL_KINDS:
        _deny("content_kind_not_supported")
    selected_images={ref for ref,e in entries.items() if e["kind"] in IMAGE_TYPES}
    selected_audio={ref for ref,e in entries.items() if e["kind"]=="audio_event"}
    if (type(delivered_image_refs) is not tuple or
        type(delivered_audio_refs) is not tuple or
        any(x not in selected_images for x in delivered_image_refs) or
        any(x not in selected_audio for x in delivered_audio_refs) or
        len(set(delivered_image_refs))!=len(delivered_image_refs) or
        len(set(delivered_audio_refs))!=len(delivered_audio_refs)):
        _deny("delivery_refs_invalid")
    verifier=getattr(receipt_verifier,"verify_b1_response",None)
    if not callable(verifier):
        _deny("authenticated_provider_receipt_missing")
    try:
        verified=verifier(response,packet,delivered_image_refs,delivered_audio_refs)
    except Exception:
        _deny("authenticated_provider_receipt_invalid")
    if verified is not True:
        _deny("authenticated_provider_receipt_invalid")
    raw=response.json_data
    if type(raw) is not dict or set(raw)!=CANDIDATE_KEYS:
        _deny("candidate_shape_invalid")
    if raw["classification_status"]!="inferred":
        _deny("use_unknown_b1_for_noninferred")
    if any(not isinstance(x,dict) for x in raw["hook_parts"] if type(raw["hook_parts"]) is list):
        _deny("part_shape_invalid")
    mechanisms=_canonical_labels(raw["mechanisms"],MECHANISMS,nonempty=True)
    channels=_canonical_labels(raw["delivery"],DELIVERY,nonempty=True)
    flags=raw["flags"]
    if type(flags) is not dict or set(flags)!=set(FLAGS) or any(
        type(flags[x]) not in (bool,type(None)) for x in FLAGS
    ):
        _deny("flags_invalid")
    texts={}
    for name in ("spoken_text","screen_text","visual_description"):
        texts[name]=_text(raw[name])
    overall=_ref_list(raw["evidence_refs"],nonempty=True)
    _display_refs(overall,entries,delivered_image_refs=delivered_image_refs,
                  delivered_audio_refs=delivered_audio_refs,video=video,location=raw)
    durations=[a.duration_ms for a in manifest.assets if a.media_kind=="video"]
    measured=max(durations) if durations else None
    _place(raw,video=video,measured=measured,entries=list(entries.values()))
    refs_by_kind={channel:tuple(ref for ref in overall if entries[ref]["kind"] in types)
                  for channel,types in CHANNEL_KINDS.items()}
    for channel in channels:
        if not refs_by_kind[channel]:
            _deny("claimed_delivery_without_evidence")
    if texts["spoken_text"] and (not refs_by_kind["speech"] or
                                  not _text_proof(texts["spoken_text"],refs_by_kind["speech"],entries)):
        _deny("spoken_quote_not_verbatim")
    if texts["screen_text"] and (not refs_by_kind["on_screen_text"] or
                                  not _text_proof(texts["screen_text"],refs_by_kind["on_screen_text"],entries)):
        _deny("screen_quote_not_verbatim")
    if "speech" in channels and not texts["spoken_text"]:
        _deny("speech_delivery_without_quote")
    if "on_screen_text" in channels and not texts["screen_text"]:
        _deny("screen_delivery_without_quote")
    if texts["spoken_text"] and "speech" not in channels:
        _deny("spoken_quote_without_channel")
    if texts["screen_text"] and "on_screen_text" not in channels:
        _deny("screen_quote_without_channel")
    if texts["visual_description"] and "visual_action" not in channels:
        _deny("visual_description_not_delivered")
    parts=raw["hook_parts"]
    if type(parts) is not list or not 1<=len(parts)<=32:
        _deny("hook_parts_missing_or_excessive")
    normalized=[]
    for i,p in enumerate(parts,1):
        if type(p) is not dict or set(p)!=PART_KEYS or p["part_index"]!=i or type(p["part_index"]) is not int:
            _deny("part_order_invalid")
        _place(p,video=video,measured=measured,entries=list(entries.values()))
        if video:
            if p["start_ms"]<raw["start_ms"] or p["end_ms"]>raw["end_ms"]:
                _deny("part_outside_hook")
        elif p["slide_index"]!=raw["slide_index"]:
            _deny("part_outside_hook")
        part_mechanisms=_canonical_labels(p["mechanisms"],MECHANISMS,nonempty=True)
        if not set(part_mechanisms).issubset(mechanisms):
            _deny("part_mechanism_not_in_hook")
        refs=_ref_list(p["evidence_refs"],nonempty=True)
        if not set(refs).issubset(overall):
            _deny("part_ref_not_in_hook")
        _display_refs(refs,entries,delivered_image_refs=delivered_image_refs,
                      delivered_audio_refs=delivered_audio_refs,video=video,location=p)
        exact=_text(p["verbatim_text"],max_len=4096)
        if exact and not _text_proof(exact,refs,entries):
            _deny("part_quote_not_verbatim")
        if not exact and not any(entries[ref]["kind"] in IMAGE_TYPES+("audio_event",) for ref in refs):
            _deny("unmeasured_part_without_quote")
        normalized.append({
            "part_index":i,"content_position":"video" if video else "carousel",
            "start_ms":p["start_ms"],"end_ms":p["end_ms"],"slide_index":p["slide_index"],
            "verbatim_text":exact or None,"mechanisms":list(part_mechanisms),
            "evidence_refs":list(refs),
        })
    if not set(mechanisms).issubset({v for part in normalized for v in part["mechanisms"]}):
        _deny("mechanism_without_part_proof")
    out={
        "schema_version":VERSION,"taxonomy_version":TAXONOMY,
        "project_id":manifest.project_id,"b_run_id":manifest.b_run_id,
        "video_id":manifest.video_id,"ga_result_sha256":manifest.ga_result_sha256,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "model_request_sha256":response.request_sha256,
        "provider_response_id":response.response_id,
        "evidence_refs":list(overall),
        "hook":{
            "content_position":"video" if video else "carousel",
            "classification_status":"inferred",
            "start_ms":raw["start_ms"],"end_ms":raw["end_ms"],
            "slide_index":raw["slide_index"],
            **texts,"mechanisms":list(mechanisms),
            "flags":{key:flags[key] for key in FLAGS},
            "delivery":list(channels),
        },
        "hook_parts":normalized,
        "verification_state":"provider_receipt_and_sources_verified",
        "reason_codes":[],
    }
    return B1HookProposal(out,canonical_hash(out),True)
