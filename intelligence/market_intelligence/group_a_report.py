"""Self-contained Marketing Group A audit of one real SHA-verified video.

The source-oriented A report consumes ONLY measurements made by the copied
GA video/audio extractors. Each stage has one proof digest/status/reason.
No QDP references, synthetic coverages, invented timeline or remote models.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from .contracts import digest

VERSION="mi-group-a-media-report/v1"
SHA=re.compile(r"[a-f0-9]{64}\Z")
STAGES=("technical","frame_sampling","ocr","text_tracking","audio_decode",
        "vad","asr","speech_metrics","subtitle_probe","subtitles","audio_events")
MANDATORY=("technical","frame_sampling","ocr","text_tracking","audio_decode",
           "vad","asr","speech_metrics","subtitle_probe","subtitles","audio_events")


def _step(state,sha=None,reason=None,details=None):
    if sha is not None and (type(sha) is not str or SHA.fullmatch(sha) is None):
        raise ValueError("ga_stage_unverified_sha")
    return {"state":state,"observation_sha256":sha,"reason":reason,
            "details":details or {}}


def _state(raw):
    if not isinstance(raw,dict):
        return "unavailable","not_produced"
    status=raw.get("status",raw.get("state","unavailable"))
    if status not in ("partial","complete","absent","silent","decoded",
            "no_audio_stream","digital_silence","classified_partial",
            "observing","speech_candidates","no_candidates","sampled","inspected",
            "no_audio","model_observed_partial"):
        return "unavailable",raw.get("reason_code") or raw.get("reason") or str(status)
    return status,raw.get("reason_code") or raw.get("reason")


def _clock(value,duration):
    try:
        value=Decimal(str(value))
    except (ValueError,InvalidOperation,TypeError):
        raise ValueError("ga_invalid_timestamp") from None
    if not value.is_finite() or value<0 or value>duration:
        raise ValueError("ga_time_out_of_source_range")
    return value


def build_group_a_report(technical,visual,media_sha256):
    if type(media_sha256) is not str or not SHA.fullmatch(media_sha256):
        raise ValueError("ga_invalid_source_sha")
    if not isinstance(visual,dict) or visual.get("source_media_sha256")!=media_sha256:
        raise ValueError("ga_media_lineage_mismatch")
    measurement=technical.measurement
    duration=Decimal(str(measurement.duration_sec))
    if not duration.is_finite() or not 0<duration<=600:
        raise ValueError("ga_invalid_video_duration")
    audio=visual.get("group_a_audio") or {}
    if audio and audio.get("source_media_sha256")!=media_sha256:
        raise ValueError("ga_audio_lineage_mismatch")
    manifest=visual.get("group_a_frame_manifest") or {}
    if manifest and manifest.get("source_media_sha256")!=media_sha256:
        raise ValueError("ga_frame_manifest_source_mismatch")

    stages={}
    stages["technical"]=_step("complete",details={
        "duration_sec":str(duration),"aspect_ratio":measurement.aspect_ratio,
        "measured_scenes":len(measurement.scenes),
        "method":measurement.method_version})
    frames=visual.get("frames",[])
    stages["frame_sampling"]=_step(
        "partial" if frames else "unavailable",
        sha=manifest.get("manifest_sha256"),
        reason="sampled_instants_not_continuous_video" if frames else "frame_sampling_unavailable",
        details={"observed_frames":len(frames),"declared_sampling_budget":
                 visual.get("group_a_coverage",{}).get("requested_frame_budget")})
    ocr=visual.get("group_a_ocr") or {}
    state,reason=_state(ocr)
    stages["ocr"]=_step(state,ocr.get("observation_sha256"),reason,
                        {"frames_checked":(ocr.get("counts") or {}).get("attempted",0),
                         "frames_observed":(ocr.get("counts") or {}).get("observed",0)})
    tracking=visual.get("group_a_text_tracking") or {}
    state,reason=_state(tracking)
    stages["text_tracking"]=_step(state,tracking.get("observation_sha256"),reason,
                         {"tracked_occurrences":(tracking.get("counts") or {}).get("distinct_occurrences",0)})
    decode=audio.get("decode") or {}
    state,reason=_state(decode)
    stages["audio_decode"]=_step(state,None,reason,{
        "pcm_sha256":decode.get("pcm_sha256"),
        "sample_count":decode.get("sample_count"),
        "duration_ms":decode.get("duration_ms")})
    for name in ("vad","asr","speech_metrics","audio_events"):
        raw=audio.get(name) or {}
        state,reason=_state(raw)
        stages[name]=_step(state,raw.get("observation_sha256"),reason,
             {"measured":bool(raw.get("observation_sha256")),
              "model":raw.get("model") or raw.get("classifier_model")})
    probe=visual.get("group_a_subtitle_probe") or {}
    state,reason=_state(probe)
    stages["subtitle_probe"]=_step(state,probe.get("observation_sha256"),reason)
    sub=visual.get("group_a_subtitles") or {}
    state,reason=_state(sub)
    stages["subtitles"]=_step(state,sub.get("observation_sha256"),reason,{
        "presence":sub.get("has_subtitles","unknown"),
        "proof_kind":sub.get("proof_kind")})

    # Video-local reported timeline, not a guessed transcript/media alignment.
    timeline=[]
    for scene in measurement.scenes:
        start=_clock(scene.start_sec,duration)
        end=_clock(scene.end_sec,duration)
        if end<=start:
            raise ValueError("ga_invalid_scene_span")
        timeline.append({"kind":"technical_scene",
          "start_sec":str(start),"end_sec":str(end),"scene_index":scene.index})
    for frame in frames:
        second=_clock(frame.get("observed_sec"),duration)
        ref=frame.get("ref")
        if type(ref) is not str or not ref.startswith("ga-frame:"):
            raise ValueError("ga_frame_ref_invalid")
        timeline.append({"kind":"sampled_frame","start_sec":str(second),
                         "end_sec":str(second),"ref":ref})
    for seg in audio.get("transcript_segments",[]):
        a=_clock(seg.get("start_sec"),duration)
        b=_clock(seg.get("end_sec"),duration)
        if b<=a:
            raise ValueError("ga_transcript_interval_invalid")
        timeline.append({"kind":"asr_observed_utterance","start_sec":str(a),
                         "end_sec":str(b),"transcript_text":seg.get("text","")})
    # Audio events are added only if GA15 verifies mapping to video timebase.
    ev=audio.get("audio_events") or {}
    if ev.get("a003_mapping_state")=="verified_audio_video_timebase":
        for row in ev.get("a003_audio_events_projection",[]):
            a=_clock(row.get("start_sec"),duration)
            b=_clock(row.get("end_sec"),duration)
            if b<=a:
                raise ValueError("ga_audio_event_interval_invalid")
            timeline.append({"kind":"observed_audio_event","start_sec":str(a),
                "end_sec":str(b),"event_type":row.get("event_type")})
    timeline.sort(key=lambda x:(Decimal(x["start_sec"]),x["kind"],x.get("scene_index",0)))
    # A scientifically honest status: one complete technical scan does not
    # certify semantic coverage across unsampled image/time windows.
    unavailable=[key for key in MANDATORY
                 if stages[key]["state"]=="unavailable"]
    summary={
       "schema_version":VERSION,
       "source_media_sha256":media_sha256,
       "duration_sec":str(duration),
       "execution_state":"partial" if unavailable else "observed_with_sampling_limits",
       "unavailable_stages":unavailable,
       "stages":stages,
       "timeline":timeline,
       "temporal_video_probe_complete":True,
       "visual_sampling_is_continuous":False,
       "speech_coverage_certified":False,
       "original_full_group_a_certified":False,
       "no_qdp_dependencies":True,
    }
    return {**summary,"report_sha256":digest(summary)}


def verify_group_a_report(report,source_sha256):
    if not isinstance(report,dict) or report.get("source_media_sha256")!=source_sha256:
        return False
    fingerprint=report.get("report_sha256")
    if type(fingerprint) is not str or SHA.fullmatch(fingerprint) is None:
        return False
    try:
        return fingerprint==digest({k:v for k,v in report.items() if k!="report_sha256"})
    except (TypeError,ValueError,OverflowError):
        return False
