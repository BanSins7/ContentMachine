"""GA-MOD-14: conservative subtitles presence truth over verified sources.

Tri-state subtitle presence for A003, not Group B subtitle style.
A signed timed subtitle or verified embedded subtitle packets may prove TRUE.
Sparse GA12/13 OCR + GA10 ASR is candidate evidence unless independently
gold-calibrated. FALSE requires authenticated dense entire-video negative
review; default is UNKNOWN. No external calls, no database side effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import re
import unicodedata

from ..contracts_v1 import canonical_sha256

SCHEMA = "ga-subtitle-presence/v1"
METHOD = "group-a-subtitle-truth-v1"
SHA = re.compile(r"^[a-f0-9]{64}$")
TIME = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{3}$")
SIGNED = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]{3}$")
WORDS = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)*",re.UNICODE)
ASR_SOURCES = frozenset(("local_faster_whisper","psi_asr"))
CAPTION_SOURCES = frozenset(("sidecar_srt","psi_srt"))
SUBTITLE_CODECS = frozenset(("subrip","ass","ssa","webvtt","mov_text",
    "hdmv_pgs_subtitle","dvd_subtitle","dvb_subtitle","text","srt"))
MAX_TRACKS = 4000


class SubtitleError(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__("Group A subtitle truth rejected: "+code)


@dataclass(frozen=True,slots=True)
class SubtitleConfig:
    min_distinct_frames: int = 2
    min_distinct_asr_segments: int = 2
    max_asr_distance_sec: str = "0.600"
    min_word_overlap: str = "0.65"
    lower_region_y: str = "0.52"
    max_candidate_tracks: int = 4000

    def __post_init__(self):
        if (type(self.min_distinct_frames) is not int or not 2<=self.min_distinct_frames<=12
            or type(self.min_distinct_asr_segments) is not int
            or not 1<=self.min_distinct_asr_segments<=10
            or type(self.max_candidate_tracks) is not int or not 1<=self.max_candidate_tracks<=MAX_TRACKS):
            raise SubtitleError("invalid_subtitle_config")
        for k,low,high in (
            ("max_asr_distance_sec",Decimal("0"),Decimal("2")),
            ("min_word_overlap",Decimal("0.50"),Decimal("1")),
            ("lower_region_y",Decimal("0.40"),Decimal("0.90"))
        ):
            value=getattr(self,k)
            if type(value) is not str or not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d{1,3})?",value) or not low<=Decimal(value)<=high:
                raise SubtitleError("invalid_subtitle_config")

    def wire(self):
        return {"min_distinct_frames":self.min_distinct_frames,
                "min_distinct_asr_segments":self.min_distinct_asr_segments,
                "max_asr_distance_sec":self.max_asr_distance_sec,
                "min_word_overlap":self.min_word_overlap,
                "lower_region_y":self.lower_region_y,
                "max_candidate_tracks":self.max_candidate_tracks}


@dataclass(frozen=True,slots=True)
class SubtitleCalibration:
    """External held-out *gold* reference, never minted by GA14 itself."""
    calibration_id: str
    dataset_sha256: str
    extractor_method: str
    language_scope: tuple[str,...]
    min_precision_pct: str
    heldout_positive_count: int
    decision_threshold: str

    def __post_init__(self):
        if (type(self.calibration_id) is not str
            or not re.fullmatch(r"[A-Za-z0-9._-]{8,80}",self.calibration_id)
            or type(self.dataset_sha256) is not str or not SHA.fullmatch(self.dataset_sha256)
            or self.extractor_method!="group-a-subtitle-ocr-asr-rule-v1"
            or type(self.language_scope) is not tuple or not self.language_scope
            or any(x not in ("ru","en") for x in self.language_scope)
            or len(set(self.language_scope))!=len(self.language_scope)
            or type(self.heldout_positive_count) is not int or self.heldout_positive_count<50):
            raise SubtitleError("invalid_subtitle_calibration")
        try:
            precision=Decimal(self.min_precision_pct)
            threshold=Decimal(self.decision_threshold)
        except Exception as exc:
            raise SubtitleError("invalid_subtitle_calibration") from exc
        if (type(self.min_precision_pct) is not str or not precision.is_finite()
            or not Decimal("95")<=precision<=Decimal("100")
            or type(self.decision_threshold) is not str or not threshold.is_finite()
            or not Decimal("0.5")<=threshold<=Decimal("1")):
            raise SubtitleError("invalid_subtitle_calibration")

    def wire(self):
        return {"calibration_id":self.calibration_id,
                "dataset_sha256":self.dataset_sha256,
                "extractor_method":self.extractor_method,
                "language_scope":list(self.language_scope),
                "min_precision_pct":self.min_precision_pct,
                "heldout_positive_count":self.heldout_positive_count,
                "decision_threshold":self.decision_threshold}


@dataclass(frozen=True,slots=True)
class DenseNegativeReview:
    """Certified complete negative video review, not sparse frame sampling."""
    source_media_sha256: str
    review_sha256: str
    start_sec: str
    end_sec: str
    scanned_frame_count: int
    subtitle_tracks_checked: bool
    visual_text_checked: bool
    sidecars_checked: bool
    all_intervals_observed: bool
    finding: str = "no_subtitles"

    def __post_init__(self):
        if (type(self.source_media_sha256) is not str or not SHA.fullmatch(self.source_media_sha256)
            or type(self.review_sha256) is not str or not SHA.fullmatch(self.review_sha256)
            or _sec(self.start_sec)!=Decimal(0)
            or _sec(self.end_sec)<=Decimal(0)
            or type(self.scanned_frame_count) is not int or self.scanned_frame_count<1
            or any(getattr(self,name) is not True for name in (
                "subtitle_tracks_checked","visual_text_checked","sidecars_checked",
                "all_intervals_observed"))
            or self.finding!="no_subtitles"):
            raise SubtitleError("invalid_dense_negative_review")

    def wire(self):
        return {k:getattr(self,k) for k in (
            "source_media_sha256","review_sha256","start_sec","end_sec",
            "scanned_frame_count","subtitle_tracks_checked","visual_text_checked",
            "sidecars_checked","all_intervals_observed","finding")}


def _sec(x):
    if type(x) is not str or TIME.fullmatch(x) is None:
        raise SubtitleError("invalid_subtitle_timestamp")
    t=Decimal(x)
    if t>Decimal("600.000"):
        raise SubtitleError("invalid_subtitle_timestamp")
    return t


def _digest(obj,kind):
    if type(obj) is not dict or type(obj.get("observation_sha256")) is not str or not SHA.fullmatch(obj["observation_sha256"]):
        raise SubtitleError("unverified_"+kind)
    try:
        expected=canonical_sha256({k:v for k,v in obj.items() if k!="observation_sha256"})
    except (TypeError,ValueError) as exc:
        raise SubtitleError("invalid_"+kind) from exc
    if obj["observation_sha256"]!=expected:
        raise SubtitleError(kind+"_hash_mismatch")


def _tokens(value):
    if type(value) is not str or len(value)>4000:
        raise SubtitleError("invalid_subtitle_text")
    normalized=unicodedata.normalize("NFKC",value).casefold()
    return tuple(WORDS.findall(normalized))


def _validate_track_probe(probe,sha):
    if probe is None:return "not_checked",[],None
    _digest(probe,"embedded_probe")
    if (probe.get("schema_version")!="ga-subtitle-stream-probe/v1"
        or probe.get("source_media_sha256")!=sha
        or probe.get("status") not in ("inspected","unavailable")
        or type(probe.get("tracks")) is not list or len(probe["tracks"])>64):
        raise SubtitleError("embedded_probe_lineage_invalid")
    if probe["status"]=="unavailable":
        if probe["tracks"]:raise SubtitleError("embedded_probe_unavailable_with_tracks")
        return "unavailable",[],probe["observation_sha256"]
    validated=[]
    seen=set()
    for row in probe["tracks"]:
        if type(row) is not dict or set(row)!={"stream_index","codec_name","packet_count","language"}:
            raise SubtitleError("embedded_track_invalid")
        n=row["stream_index"]
        if type(n) is not int or not 0<=n<=1024 or n in seen:
            raise SubtitleError("embedded_track_invalid")
        seen.add(n)
        if row["codec_name"] not in SUBTITLE_CODECS:
            raise SubtitleError("embedded_codec_unrecognized")
        count=row["packet_count"]
        if count is not None and (type(count) is not int or count<0 or count>10000000):
            raise SubtitleError("embedded_packet_count_invalid")
        if row["language"] is not None and (
            type(row["language"]) is not str or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z]{2,8})?",row["language"])):
            raise SubtitleError("embedded_language_invalid")
        validated.append(row)
    return "inspected",validated,probe["observation_sha256"]


def _validate_caption_or_asr(transcript,sha,verify_current):
    if transcript is None:return "none",[],None,None
    _digest(transcript,"asr")
    if (transcript.get("schema_version")!="ga-asr-transcript/v1"
        or transcript.get("source_media_sha256")!=sha
        or transcript.get("source") not in CAPTION_SOURCES|ASR_SOURCES|{"sidecar_txt","none"}
        or transcript.get("complete_speech_coverage") is not False
        or type(transcript.get("segments")) is not list
        or len(transcript["segments"])>12000):
        raise SubtitleError("transcript_lineage_invalid")
    if not callable(verify_current) or verify_current({
        "type":"transcript","source_media_sha256":sha,
        "observation_sha256":transcript["observation_sha256"],
        "upstream":transcript.get("upstream"),
    }) is not True:
        raise SubtitleError("transcript_rights_unverified")
    kind=transcript["source"]
    if kind in CAPTION_SOURCES and not transcript["segments"]:
        raise SubtitleError("timed_caption_evidence_missing")
    if kind in CAPTION_SOURCES and transcript.get("timing_reliability")!="segment_observed":
        raise SubtitleError("caption_timing_invalid")
    return kind,transcript["segments"],transcript["observation_sha256"],transcript


def _visual_candidate(tracking,transcript,metrics,sha,cfg,verify_current):
    if tracking is None:return {"state":"unavailable","matches":[],"candidate_count":0}
    _digest(tracking,"tracking")
    if (tracking.get("schema_version")!="ga-screen-text-tracking/v1"
        or tracking.get("source_media_sha256")!=sha
        or tracking.get("sampling_truth")!="sparse_instants_only"
        or tracking.get("temporal_coverage")!="unknown"
        or tracking.get("continuous_intervals_verified") is not False
        or tracking.get("full_video_text_absence")!="unknown"
        or type(tracking.get("tracks")) is not list
        or len(tracking["tracks"])>MAX_TRACKS):
        raise SubtitleError("tracking_lineage_invalid")
    if not callable(verify_current) or verify_current({
        "type":"tracking","source_media_sha256":sha,
        "observation_sha256":tracking["observation_sha256"],
        "project_id":tracking.get("project_id"),"run_id":tracking.get("run_id"),
        "frame_manifest_sha256":tracking.get("frame_manifest_sha256")
    }) is not True:
        raise SubtitleError("tracking_rights_unverified")
    if transcript is None or transcript.get("source") not in ASR_SOURCES:
        return {"state":"ocr_only_not_audible_speech_proof","matches":[],"candidate_count":0}
    if metrics is None:
        return {"state":"asr_video_clock_unverified","matches":[],"candidate_count":0}
    _digest(metrics,"speech_metrics")
    if (metrics.get("schema_version")!="ga-speech-metrics/v1"
        or metrics.get("source_media_sha256")!=sha
        or metrics.get("asr_observation_sha256")!=transcript["observation_sha256"]
        or metrics.get("clock_status") not in ("ffprobe_stream_origins","verified_psi_acquisition")
        or metrics.get("speech_status")!="observed_partial"
        or type(metrics.get("video_clock_offset_sec")) is not str
        or SIGNED.fullmatch(metrics["video_clock_offset_sec"]) is None):
        raise SubtitleError("speech_alignment_unverified")
    if not callable(verify_current) or verify_current({
        "type":"speech_metrics","source_media_sha256":sha,
        "observation_sha256":metrics["observation_sha256"],
        "asr_observation_sha256":transcript["observation_sha256"]
    }) is not True:
        raise SubtitleError("speech_metrics_rights_unverified")
    offset=Decimal(metrics["video_clock_offset_sec"])
    text_matches=[];segments=transcript["segments"]
    for track in tracking["tracks"]:
        if type(track) is not dict or type(track.get("observations")) is not list:
            raise SubtitleError("tracking_observations_invalid")
        if len(text_matches)>=cfg.max_candidate_tracks:break
        for obs in track["observations"]:
            box=obs.get("bbox")
            if type(box) is not dict or type(obs.get("frame_height")) is not int or obs["frame_height"]<1:
                raise SubtitleError("tracking_box_invalid")
            bottom=Decimal(box.get("y",0)+box.get("height",0))/obs["frame_height"]
            if bottom<Decimal(cfg.lower_region_y):
                continue
            words=set(_tokens(obs.get("text")))
            if not words:continue
            instant=_sec(obs.get("observed_sec"))
            for idx,seg in enumerate(segments):
                a=_sec(seg.get("start_sec"))+offset
                b=_sec(seg.get("end_sec"))+offset
                if b<=a:raise SubtitleError("asr_segment_invalid")
                tolerance=Decimal(cfg.max_asr_distance_sec)
                if instant<a-tolerance or instant>b+tolerance:continue
                spoken=set(_tokens(seg.get("text")))
                if not spoken:continue
                overlap=Decimal(len(words&spoken))/Decimal(len(words))
                if overlap>=Decimal(cfg.min_word_overlap):
                    text_matches.append({
                        "track_id":track["track_id"],
                        "frame_ref":obs["frame_ref"],
                        "observed_sec":obs["observed_sec"],
                        "asr_segment_index":idx,
                        "token_overlap":format(overlap.quantize(Decimal("0.001")),".3f"),
                        "position":"lower_region",
                    })
                    break
    distinct_frames={m["frame_ref"] for m in text_matches}
    distinct_asr={m["asr_segment_index"] for m in text_matches}
    sufficient=(len(distinct_frames)>=cfg.min_distinct_frames
                and len(distinct_asr)>=cfg.min_distinct_asr_segments)
    return {
        "state":"corroborated_candidate" if sufficient else "insufficient_correlated_evidence",
        "matches":text_matches,
        "candidate_count":len(text_matches),
        "distinct_frames":len(distinct_frames),
        "distinct_asr_segments":len(distinct_asr),
    }


def detect_subtitles(*,source_media_sha256,verify_current,embedded_probe=None,
                     transcript=None,tracking=None,speech_metrics=None,
                     dense_review=None,verify_dense_review=None,
                     calibration=None,verify_calibration=None,config=None):
    """Evidence-first tri-state; never infer FALSE from GA07 sparse OCR."""
    cfg=SubtitleConfig() if config is None else config
    if not isinstance(cfg,SubtitleConfig) or type(source_media_sha256) is not str or not SHA.fullmatch(source_media_sha256):
        raise SubtitleError("invalid_subtitle_input")
    context={"type":"subtitle_analysis","source_media_sha256":source_media_sha256}
    if not callable(verify_current) or verify_current(context) is not True:
        raise SubtitleError("subtitle_current_rights_unverified")
    probe_state,substreams,probe_sha=_validate_track_probe(embedded_probe,source_media_sha256)
    source_kind,segments,transcript_sha,checked_transcript=_validate_caption_or_asr(
        transcript,source_media_sha256,verify_current)
    if embedded_probe is not None and (not callable(verify_current) or verify_current({
        "type":"embedded_probe","source_media_sha256":source_media_sha256,
        "observation_sha256":probe_sha,
    }) is not True):
        raise SubtitleError("embedded_probe_rights_unverified")
    visual=_visual_candidate(tracking,checked_transcript,speech_metrics,
                             source_media_sha256,cfg,verify_current)
    proven_streams=[x for x in substreams if x["packet_count"] is not None and x["packet_count"]>0]
    proof_kind="none"
    if proven_streams:
        proof_kind="embedded_subtitle_packets"
    elif source_kind in CAPTION_SOURCES and segments:
        proof_kind="verified_timed_captions"
    has_subtitles="true" if proof_kind!="none" else "unknown"
    reasons=[]
    if probe_state=="not_checked":reasons.append("embedded_subtitle_tracks_not_checked")
    elif probe_state=="unavailable":reasons.append("embedded_subtitle_probe_unavailable")
    elif any(x["packet_count"] is None for x in substreams):
        reasons.append("embedded_subtitle_packet_counts_unknown")
    if visual["state"]=="corroborated_candidate" and has_subtitles=="unknown":
        reasons.append("ocr_asr_correlated_but_visual_precision_not_calibrated")
    if visual["state"]=="ocr_only_not_audible_speech_proof":
        reasons.append("ocr_without_speech_alignment")
    if visual["state"]=="asr_video_clock_unverified":
        reasons.append("asr_video_clock_unverified")
    if visual["state"]=="insufficient_correlated_evidence":
        reasons.append("sparse_ocr_not_sufficient")
    if source_kind=="sidecar_txt":
        reasons.append("untimed_text_is_not_subtitle_evidence")
    calibration_info=None
    if calibration is not None:
        if not isinstance(calibration,SubtitleCalibration) or not callable(verify_calibration) or verify_calibration(
            {"source_media_sha256":source_media_sha256,**calibration.wire()}
        ) is not True:
            raise SubtitleError("subtitle_calibration_unverified")
        calibration_info=calibration.wire()
        if (has_subtitles=="unknown"
            and visual["state"]=="corroborated_candidate"
            and "en" in calibration.language_scope or False):
            # The external calibration is deliberately *not* enough to
            # prove language coverage if this particular source language is
            # unknown. Below, require explicit ASR language match as well.
            pass
        if (has_subtitles=="unknown"
            and visual["state"]=="corroborated_candidate"
            and checked_transcript is not None
            and checked_transcript.get("language") in calibration.language_scope
            and Decimal(calibration.decision_threshold)<=Decimal("1")):
            has_subtitles="true"
            proof_kind="calibrated_ocr_asr"
            reasons=[x for x in reasons if x!="ocr_asr_correlated_but_visual_precision_not_calibrated"]
    review_info=None
    if dense_review is not None:
        if not isinstance(dense_review,DenseNegativeReview) or dense_review.source_media_sha256!=source_media_sha256:
            raise SubtitleError("dense_review_lineage_invalid")
        if (not callable(verify_dense_review) or verify_dense_review({
            "source_media_sha256":source_media_sha256,**dense_review.wire()
        }) is not True):
            raise SubtitleError("dense_review_rights_unverified")
        review_info=dense_review.wire()
        if has_subtitles=="true":
            raise SubtitleError("dense_negative_contradicts_positive_subtitles")
        if probe_state!="inspected" or substreams or source_kind in CAPTION_SOURCES:
            raise SubtitleError("dense_review_not_corroborated")
        if any(x["packet_count"] is not None and x["packet_count"]>0 for x in substreams):
            raise SubtitleError("dense_review_not_corroborated")
        # Caller must authenticate the dense actual-media duration and
        # scan/gap coverage; GA13 sparse observations NEVER meet this gate.
        has_subtitles="false";proof_kind="certified_dense_negative_review"
        reasons=[]
    if has_subtitles=="unknown" and not reasons:
        reasons=["insufficient_complete_subtitle_evidence"]
    core={
        "schema_version":SCHEMA,"method_version":METHOD,
        "source_media_sha256":source_media_sha256,
        "status":"partial" if (has_subtitles!="unknown" or probe_state=="inspected" or tracking is not None)
                 else "unavailable",
        "has_subtitles":has_subtitles,
        "proof_kind":proof_kind,
        "reason_codes":sorted(set(reasons)),
        "embedded_probe_sha256":probe_sha,
        "asr_observation_sha256":transcript_sha,
        "tracking_observation_sha256":tracking.get("observation_sha256") if tracking is not None else None,
        "speech_metrics_observation_sha256":speech_metrics.get("observation_sha256") if speech_metrics is not None else None,
        "embedded_track_count":len(substreams),
        "embedded_packet_track_count":len(proven_streams),
        "caption_source":source_kind if source_kind in CAPTION_SOURCES else None,
        "visual":visual,
        "calibration":calibration_info,
        "dense_negative_review":review_info,
        "a003_has_subtitles_projection":has_subtitles,
        "screen_text_intervals_created":False,
        "subtitle_style_classified":False,
        "config":cfg.wire(),
    }
    return {**core,"observation_sha256":canonical_sha256(core)}
