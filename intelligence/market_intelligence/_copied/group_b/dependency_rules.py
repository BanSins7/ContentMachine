"""GB-MOD-07: conservative, side-effect-free B1–B8 readiness policy.

Classification is evidence *admission*, not inference of labels or a model
execution grant. Unknown/partial is never coerced into a measured false.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from collections.abc import Mapping

from .context.manifest import MediaEvidenceManifest, SCHEMA as MANIFEST_SCHEMA

BLOCKS = ("B1","B2","B3","B4","B5","B6","B7","B8")
VIDEO_STREAMS = frozenset(("technical","speech","screen_text","audio","subtitles"))
CAROUSEL_STREAMS = frozenset(("slides","screen_text"))
MIXED_STREAMS = frozenset(("technical","speech","screen_text","audio","subtitles","slides"))
_OBSERVATION_KINDS = frozenset((
    "scene","transcript_segment","ocr_span","sampled_frame","audio_event","slide_ref"
))


class ReadinessState(str,Enum):
    READY = "ready"
    PARTIAL = "partial"
    BLOCKED = "blocked"


@dataclass(frozen=True,slots=True)
class BlockReadiness:
    block: str
    state: ReadinessState
    reason_codes: tuple[str,...]
    evidence_sha256: str | None

    @property
    def can_attempt(self)->bool:
        """Readiness only: never approves paid model calls or dispatch."""
        return self.state is ReadinessState.READY


@dataclass(frozen=True,slots=True)
class ReadinessReport:
    project_id: str
    run_id: str
    manifest_sha256: str | None
    blocks: tuple[BlockReadiness,...]
    global_reason_codes: tuple[str,...]

    @property
    def all_ready(self) -> bool:
        return bool(self.blocks) and all(v.can_attempt for v in self.blocks)

    def for_block(self,block: str) -> BlockReadiness:
        if block not in BLOCKS:
            raise ValueError("Unknown B block")
        return next(x for x in self.blocks if x.block==block)


def _decision(block: str, state: ReadinessState, *reasons: str,
              digest: str | None = None) -> BlockReadiness:
    return BlockReadiness(block,state,tuple(dict.fromkeys(reasons)),digest)


def blocked_report(project_id: str,run_id: str,reason: str, *,
                   state: ReadinessState = ReadinessState.BLOCKED) -> ReadinessReport:
    """An upstream failure blocks every block, without writing SQL statuses."""
    if state is ReadinessState.READY:
        raise ValueError("Global failure cannot be ready")
    return ReadinessReport(project_id,run_id,None,
        tuple(_decision(b,state,reason) for b in BLOCKS),(reason,))


def _truth(streams: Mapping, name: str) -> tuple[str,int|None,bool]:
    raw = streams.get(name)
    if type(raw) is not dict:
        return ("not_run",None,False)
    status=raw.get("status")
    count=raw.get("evidence_count")
    absence=raw.get("verified_absence")
    reason=raw.get("reason_code")
    if (status not in ("complete","partial","not_run","unavailable") or
        type(absence) is not bool or
        (count is not None and (type(count) is not int or count<0)) or
        (status=="complete" and (count is None or reason is not None or
                                  absence!=(count==0))) or
        (status!="complete" and (absence or type(reason) is not str)) or
        (status in ("not_run","unavailable") and count is not None)):
        return ("invalid",None,False)
    return status,count,absence


def evaluate_block_dependencies(manifest: MediaEvidenceManifest, *,
                                a_streams: object) -> ReadinessReport:
    """Evaluate all B blocks only from a verified GA/media manifest.

    A missing observation or stream is PARTIAL/unknown. BLOCKED is reserved
    for nonapplicable content kinds and malformed external evidence/contracts.
    A complete stream with verified_absence=True is an observed negative, not
    a missing stream; it still requires its A proof to be hash-resolved.
    """
    if (not isinstance(manifest,MediaEvidenceManifest) or
        manifest.schema_version!=MANIFEST_SCHEMA or
        not manifest.verify_digest() or
        type(a_streams) is not dict):
        raise ValueError("Only an authenticated valid media manifest/A truth accepted")
    kind=manifest.content_kind
    if kind not in ("video","slideshow_video","photo_carousel","mixed_carousel"):
        return blocked_report(manifest.project_id,manifest.b_run_id,"content_kind_unsupported")
    video=kind in ("video","slideshow_video")
    expected=VIDEO_STREAMS if video else (MIXED_STREAMS if kind=="mixed_carousel"
                                           else CAROUSEL_STREAMS)
    if set(a_streams)!=expected:
        return blocked_report(manifest.project_id,manifest.b_run_id,
                              "a_stream_matrix_invalid")
    statuses={s:_truth(a_streams,s) for s in expected}
    if any(v[0]=="invalid" for v in statuses.values()):
        return blocked_report(manifest.project_id,manifest.b_run_id,
                              "a_stream_truth_invalid")
    observed = {name:sum(o.kind==name for o in manifest.observations)
                for name in _OBSERVATION_KINDS}
    asset_kinds={a.media_kind for a in manifest.assets}
    # Some Group A streams legitimately produce negative, hash-anchored
    # observations. An absent audio_event alone is not proof of audio=FALSE.
    def require_stream(names, codes):
        for name in names:
            if name not in statuses or statuses[name][0]!="complete":
                codes.append("a_"+name+"_not_measured")
    def require_any(kinds,code,codes):
        if not any(observed.get(k,0)>0 for k in kinds):
            codes.append(code)
    if not manifest.assets:
        return blocked_report(manifest.project_id,manifest.b_run_id,
                              "verified_media_missing")
    out=[]
    for block in BLOCKS:
        reasons=[]
        if block == "B1":
            if video:
                # Hooks may be conveyed visually, through speech, screen text
                # or audio action. Silence alone does not disable visual hooks.
                require_stream(("technical","speech","screen_text","audio"),reasons)
                require_any(("transcript_segment","ocr_span","sampled_frame",
                             "audio_event"),"hook_evidence_missing",reasons)
            else:
                require_stream(("slides","screen_text"),reasons)
                require_any(("slide_ref","ocr_span"),"hook_evidence_missing",reasons)
        elif block == "B2":
            if video:
                require_stream(("technical",),reasons)
                require_any(("sampled_frame",),"visual_frame_missing",reasons)
            else:
                require_stream(("slides",),reasons)
                require_any(("slide_ref",),"carousel_slide_missing",reasons)
        elif block == "B3":
            if video:
                require_stream(("technical",),reasons)
                require_any(("sampled_frame",),"story_sampling_not_complete",reasons)
            else:
                require_stream(("slides",),reasons)
                # Never infer unseen slide layouts or skip missing slides.
                slide_ids={o.slide_index for o in manifest.observations
                           if o.kind=="slide_ref"}
                required_ids={a.slide_index for a in manifest.assets}
                if not required_ids or slide_ids!=required_ids:
                    reasons.append("carousel_slide_coverage_incomplete")
        elif block == "B4":
            if video:
                require_any(("sampled_frame",),"visual_frame_missing",reasons)
            else:
                require_any(("slide_ref",),"carousel_slide_missing",reasons)
        elif block == "B5":
            if not video:
                out.append(_decision(block,ReadinessState.BLOCKED,
                                     "editing_video_only"))
                continue
            require_stream(("technical",),reasons)
            require_any(("scene",),"scene_measurement_missing",reasons)
            if not any(a.duration_ms is not None and a.duration_ms>0
                       for a in manifest.assets if a.media_kind=="video"):
                reasons.append("video_duration_not_measured")
        elif block == "B6":
            if not video:
                out.append(_decision(block,ReadinessState.BLOCKED,
                                     "video_subtitles_only"))
                continue
            require_stream(("screen_text","subtitles"),reasons)
            # 'none' may only be decided from completed explicit negative A
            # subtitle truth, never merely zero OCR rows or ASR being silent.
            absence=statuses["subtitles"][2]
            if not absence:
                require_any(("ocr_span",),"subtitle_ocr_missing",reasons)
            elif not observed.get("ocr_span",0):
                # Negative observation proof is required in A v1; the
                # actual catalog may encode it as a hash-resolved OCR span.
                reasons.append("subtitle_absence_proof_missing")
        elif block == "B7":
            if video:
                require_stream(("speech","screen_text"),reasons)
                require_any(("transcript_segment","ocr_span"),
                            "cta_evidence_missing",reasons)
            else:
                require_stream(("screen_text","slides"),reasons)
                require_any(("ocr_span","slide_ref"),"cta_evidence_missing",reasons)
        else:  # B8: actual publication topic, NOT QDP/SRE hypothesis keys.
            if video:
                require_stream(("speech","screen_text"),reasons)
                require_any(("transcript_segment","ocr_span","sampled_frame"),
                            "topic_evidence_missing",reasons)
            else:
                require_stream(("slides","screen_text"),reasons)
                require_any(("slide_ref","ocr_span"),"topic_evidence_missing",reasons)
        out.append(_decision(block,
                             ReadinessState.PARTIAL if reasons else ReadinessState.READY,
                             *reasons,digest=manifest.manifest_sha256 if not reasons else None))
    return ReadinessReport(manifest.project_id,manifest.b_run_id,
                           manifest.manifest_sha256,tuple(out),())
