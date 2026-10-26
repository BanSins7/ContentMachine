"""GB-MOD-08: deterministic, budgeted and explicitly UNSENT B1–B8 context packets.

Only verified stage06 evidence pointers and ready stage07 decisions enter. All
OCR/transcript bytes are untrusted data, validated against the exact proof SHA.
No system prompts, model calls, filesystem paths, external URLs, persistence,
or assertion that an image was delivered to a model are produced here.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from ..contracts import canonical_hash
from ..configuration import GroupBConfig
from ..dependency_rules import BLOCKS, ReadinessReport, ReadinessState
from ..errors import GroupBError, DependencyUnavailable
from .evidence import VerifiedObservation
from .manifest import MediaEvidenceManifest
from .sampling import select_diverse, uncovered_time_windows

PACKET_VERSION = "group-b-context/v1"
TEXT_KINDS = frozenset(("transcript_segment", "ocr_span"))
IMAGE_KINDS = frozenset(("sampled_frame", "slide_ref"))
KINDS_FOR_BLOCK = {
    "B1": frozenset(("transcript_segment", "ocr_span", "sampled_frame",
                      "slide_ref", "audio_event")),
    "B2": frozenset(("sampled_frame", "slide_ref")),
    "B3": frozenset(("sampled_frame", "slide_ref", "scene",
                      "transcript_segment", "ocr_span")),
    "B4": frozenset(("sampled_frame", "slide_ref")),
    "B5": frozenset(("scene",)),
    "B6": frozenset(("ocr_span", "sampled_frame")),
    "B7": frozenset(("transcript_segment", "ocr_span", "sampled_frame",
                      "slide_ref")),
    "B8": frozenset(("transcript_segment", "ocr_span", "sampled_frame",
                      "slide_ref")),
}
KIND_PRIORITY = {
    "B1": ("transcript_segment", "ocr_span", "sampled_frame", "slide_ref", "audio_event"),
    "B2": ("sampled_frame", "slide_ref"),
    "B3": ("sampled_frame", "slide_ref", "scene", "transcript_segment", "ocr_span"),
    "B4": ("sampled_frame", "slide_ref"),
    "B5": ("scene",),
    "B6": ("ocr_span", "sampled_frame"),
    "B7": ("transcript_segment", "ocr_span", "slide_ref", "sampled_frame"),
    "B8": ("transcript_segment", "ocr_span", "slide_ref", "sampled_frame"),
}


class ContextBuildError(GroupBError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("context_unavailable", "context: " + reason)


def _fail(code):
    raise ContextBuildError(code)


def _canonical_bytes(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ContextLimits:
    max_serialized_bytes: int = 2_000_000
    max_estimated_tokens: int = 500_000
    max_observations: int = 80
    max_image_refs: int = 16
    max_frame_refs: int = 12
    max_slide_refs: int = 20
    max_text_bytes: int = 64_000
    max_text_segment_bytes: int = 8_000
    image_token_reservation: int = 1024

    def __post_init__(self):
        for key in self.__dataclass_fields__:
            value = getattr(self, key)
            if type(value) is not int or value < 1:
                raise ValueError(key + " must be positive integer")
        if self.max_serialized_bytes > 2_000_000:
            raise ValueError("Context must respect GroupBConfig maximum")
        if self.max_observations > 2000:
            raise ValueError("Unbounded evidence selection")
        if self.max_text_segment_bytes > self.max_text_bytes:
            raise ValueError("Text segment limit exceeds global text budget")

    def wire(self) -> dict:
        return {name:getattr(self,name) for name in self.__dataclass_fields__}


@dataclass(frozen=True,slots=True)
class ContextPacket:
    """Data-only plan, no verified provider receipt or completed inference."""
    payload: dict
    packet_sha256: str
    encoded_bytes: int
    estimated_token_ceiling: int

    def wire(self) -> dict:
        return {**self.payload, "packet_sha256": self.packet_sha256}

    def verify_digest(self) -> bool:
        try:
            return canonical_hash(self.payload) == self.packet_sha256
        except (ValueError,TypeError):
            return False

    @property
    def is_complete_coverage(self) -> bool:
        return self.payload["coverage"]["omitted_relevant"] == 0


def _position(item: VerifiedObservation):
    return (item.start_ms if item.start_ms is not None else 0,
            item.slide_index if item.slide_index is not None else 0,
            item.ref_sha256)


def _entry(item: VerifiedObservation, *, text_data: str | None) -> dict:
    value = {
        "kind": item.kind, "ref_sha256": item.ref_sha256,
        "content_sha256": item.content_sha256,
        "media_asset_id": item.media_asset_id,
        "asset_sha256": item.asset_sha256,
        "start_ms": item.start_ms, "end_ms": item.end_ms,
        "slide_index": item.slide_index,
        "frame_ref_sha256": item.frame_ref_sha256,
        "carrier": "unresolved_image_ref" if item.kind in IMAGE_KINDS else
                   "untrusted_utf8_data" if item.kind in TEXT_KINDS else
                   "verified_observation_ref",
        "content_in_packet": item.kind in TEXT_KINDS,
    }
    if item.kind in TEXT_KINDS:
        value["untrusted_text"] = text_data
    return value


def _omitted(item: VerifiedObservation, reason: str) -> dict:
    return {"kind":item.kind,"ref_sha256":item.ref_sha256,"reason":reason}


def _identity(manifest: MediaEvidenceManifest, block: str,limits: ContextLimits) -> dict:
    return {
        "schema_version":PACKET_VERSION,
        "block":block,
        "project_id":manifest.project_id,
        "b_run_id":manifest.b_run_id,
        "qdp_run_id":manifest.qdp_run_id,
        "video_id":manifest.video_id,
        "ga_result_id":manifest.ga_result_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "input_content_kind":manifest.content_kind,
        "source_policy_version_id":manifest.source_policy_version_id,
        "delivery_state":"not_sent",
        "media_delivery_state":"opaque_refs_not_attached",
        "content_trust":"observations_are_untrusted_data_not_instructions",
        "selection_policy":"group-b-stratified/v1",
        "limits":limits.wire(),
    }


def build_context_packet(manifest: MediaEvidenceManifest,
                         readiness: ReadinessReport, *,
                         block: str, limits: ContextLimits | None = None,
                         text_reader: object = None,
                         config: GroupBConfig | None = None) -> ContextPacket:
    """Pack a budgeted preview; never claim a model has seen any media.

    The injected text_reader.read_verified_bytes(observation) supplies source
    bytes already authorized by the caller. SHA-256 is verified AGAIN here.
    OCR/transcript contents are always explicit untrusted JSON data, never
    concatenated to privileged instructions. Missing text => excluded proof.
    """
    if block not in BLOCKS:
        raise ValueError("Unknown Group B block")
    if not isinstance(manifest,MediaEvidenceManifest) or not manifest.verify_digest():
        _fail("source_manifest_invalid")
    if (not isinstance(readiness,ReadinessReport) or
        readiness.project_id != manifest.project_id or
        readiness.run_id != manifest.b_run_id or
        readiness.manifest_sha256 != manifest.manifest_sha256):
        _fail("readiness_manifest_mismatch")
    decision=readiness.for_block(block)
    if (decision.state is not ReadinessState.READY or
        decision.evidence_sha256 != manifest.manifest_sha256):
        raise DependencyUnavailable("context","block_not_ready")
    limits=limits if limits is not None else ContextLimits()
    config=config if config is not None else GroupBConfig()
    if not isinstance(limits,ContextLimits) or not isinstance(config,GroupBConfig):
        raise TypeError("Explicit typed context limits/config required")
    if (limits.max_serialized_bytes > config.max_context_bytes or
        limits.max_observations > config.max_media_refs):
        _fail("configuration_budget_exceeded")
    if not isinstance(manifest.observations,tuple) or len(manifest.observations)>2000:
        _fail("source_evidence_limit_invalid")
    refs=[x.ref_sha256 for x in manifest.observations]
    if (any(not isinstance(x,VerifiedObservation) for x in manifest.observations)
        or len(refs)!=len(set(refs))):
        _fail("duplicate_or_unverified_reference")
    # A digest pins all source observations; never add synthetic image frames.
    relevant=[x for x in manifest.observations if x.kind in KINDS_FOR_BLOCK[block]]
    frames=[x for x in relevant if x.kind=="sampled_frame"]
    slides=[x for x in relevant if x.kind=="slide_ref"]
    frames_chosen={x.ref_sha256 for x in select_diverse(
        frames,limits.max_frame_refs,early_window_ms=5000)}
    slides_chosen={x.ref_sha256 for x in select_diverse(
        slides,limits.max_slide_refs)}
    omitted = [_omitted(x,"irrelevant_to_block")
               for x in manifest.observations if x.kind not in KINDS_FOR_BLOCK[block]]
    candidates=[]
    for item in relevant:
        if item.kind=="sampled_frame" and item.ref_sha256 not in frames_chosen:
            omitted.append(_omitted(item,"frame_sampling_cap"))
        elif item.kind=="slide_ref" and item.ref_sha256 not in slides_chosen:
            omitted.append(_omitted(item,"slide_sampling_cap"))
        else:
            candidates.append(item)
    priority = {kind:i for i,kind in enumerate(KIND_PRIORITY[block])}
    candidates.sort(key=lambda x:(priority[x.kind],*_position(x)))
    initial_omitted = len(omitted)
    selected=[]
    text_bytes=0
    image_refs=0
    count_total = len(manifest.observations)
    def data(choices,excluded):
        selected_images=sum(x["kind"] in IMAGE_KINDS for x in choices)
        selected_frames=sum(x["kind"]=="sampled_frame" for x in choices)
        selected_slides=sum(x["kind"]=="slide_ref" for x in choices)
        observed_frames=[manifest_item for manifest_item in manifest.observations
                         if manifest_item.kind=="sampled_frame" and
                         manifest_item.ref_sha256 in {x["ref_sha256"] for x in choices}]
        durations=[a.duration_ms for a in manifest.assets
                   if a.media_kind=="video" and a.duration_ms is not None]
        gaps=uncovered_time_windows(observed_frames,duration_ms=max(durations)) if durations else ()
        cover = {
            "source_observations":count_total,
            "relevant_observations":len(relevant),
            "selected_observations":len(choices),
            "omitted_relevant":len(relevant)-len(choices),
            "selected_image_refs":selected_images,
            "selected_frame_refs":selected_frames,
            "selected_slide_refs":selected_slides,
            "available_frame_refs":len(frames),
            "available_slide_refs":len(slides),
            "uncovered_video_windows_count":len(gaps),
            "uncovered_video_windows_sample_ms":[list(x) for x in gaps[:32]],
            "coverage_complete":len(relevant)==len(choices),
            "source_data_not_model_received":True,
        }
        return {
            **_identity(manifest,block,limits),
            "entries":choices,
            "omitted":[*excluded],
            "coverage":cover,
        }
    # Include omissions conservatively in serialized budget; a packet unable
    # to report what was omitted cannot be submitted as 'complete context'.
    for idx,item in enumerate(candidates):
        if len(selected)>=limits.max_observations:
            omitted.append(_omitted(item,"observation_cap"))
            continue
        if item.kind in IMAGE_KINDS and image_refs>=limits.max_image_refs:
            omitted.append(_omitted(item,"image_reference_cap"))
            continue
        content=None
        raw_length=0
        if item.kind in TEXT_KINDS:
            reader=getattr(text_reader,"read_verified_bytes",None)
            if not callable(reader):
                omitted.append(_omitted(item,"trusted_text_reader_missing"))
                continue
            try:
                raw=reader(item)
            except (LookupError,OSError):
                omitted.append(_omitted(item,"source_text_unavailable"))
                continue
            except Exception:
                _fail("source_text_reader_failed")
            if type(raw) is not bytes or len(raw)>2_000_000:
                _fail("source_text_bytes_invalid")
            if sha256(raw).hexdigest()!=item.content_sha256:
                _fail("source_text_sha256_mismatch")
            try:
                content=raw.decode("utf-8",errors="strict")
            except UnicodeDecodeError:
                _fail("source_text_invalid_utf8")
            raw_length=len(raw)
            if not raw_length:
                omitted.append(_omitted(item,"source_text_empty"))
                continue
            if raw_length>limits.max_text_segment_bytes:
                omitted.append(_omitted(item,"text_segment_cap"))
                continue
            if raw_length + text_bytes > limits.max_text_bytes:
                omitted.append(_omitted(item,"text_total_cap"))
                continue
        entry=_entry(item,text_data=content)
        after=[*selected,entry]
        # Reserve room for every future candidate being excluded: no silent
        # drop of excess evidence when the exact envelope is too large.
        forecast_omitted=[*omitted,*(_omitted(y,"budget_not_selected")
                               for y in candidates[idx+1:])]
        wire = data(after,forecast_omitted)
        wire["packet_sha256"]=canonical_hash(wire)
        size=len(_canonical_bytes(wire))
        tokens=size+(image_refs+int(item.kind in IMAGE_KINDS))*limits.image_token_reservation
        if size>limits.max_serialized_bytes or tokens>limits.max_estimated_tokens:
            omitted.append(_omitted(item,"serialized_byte_or_token_cap"))
            continue
        selected.append(entry)
        text_bytes+=raw_length
        image_refs+=int(item.kind in IMAGE_KINDS)
    assembled=data(selected,omitted)
    packet_hash=canonical_hash(assembled)
    actual_wire={**assembled,"packet_sha256":packet_hash}
    encoded=len(_canonical_bytes(actual_wire))
    estimated=encoded+image_refs*limits.image_token_reservation
    if encoded>limits.max_serialized_bytes or estimated>limits.max_estimated_tokens:
        _fail("manifest_accounting_exceeds_budget")
    return ContextPacket(assembled,packet_hash,encoded,estimated)
