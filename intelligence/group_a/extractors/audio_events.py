"""GA15 local audio-event observations: music, SFX, other (multi-label).

The classifier is a strictly opt-in, preinstalled and host-authorized port.
Missing labels/weights do NOT prove absence. Candidate windows may overlap
with observed speech; no music/SFX is inferred from VAD or volume alone.
The final A003 rows are emitted only with authenticated video time origins.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
from typing import Protocol, runtime_checkable
import re
import time

from ..contracts_v1 import canonical_sha256
from .audio_decode import DecodedAudio, SAMPLE_RATE, MAX_AUDIO_SECONDS, pcm_int16
from .technical import ProbeInfo

SCHEMA="ga-audio-events/v1"
METHOD="group-a-audio-events-local-v1"
LABELS=("music","sound_effect","other")
SHA=re.compile(r"^[0-9a-f]{64}$")
UNSIGNED=re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{3}$")
SIGNED=re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]{1,6}$")
VERSION=re.compile(r"^[a-zA-Z0-9._+-]{1,80}$")
MAX_WINDOWS=3000
MAX_EVENTS=3000


class AudioEventError(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__("Group A audio event evidence rejected: "+code)


def _decimal(raw, *, high=1):
    if type(raw) not in (str,int,float,Decimal) or type(raw) is bool:
        raise AudioEventError("audio_event_score_invalid")
    try:
        n=Decimal(str(raw))
        if not n.is_finite() or not Decimal(0)<=n<=Decimal(str(high)):
            raise ValueError()
        return n
    except (ValueError,InvalidOperation,OverflowError) as exc:
        raise AudioEventError("audio_event_score_invalid") from exc


def _fixed(n,places=3):
    unit=Decimal(1).scaleb(-places)
    return format(n.quantize(unit,rounding=ROUND_HALF_UP),f".{places}f")


def _time(raw,*,signed=False):
    if type(raw) is not str or (SIGNED if signed else UNSIGNED).fullmatch(raw) is None:
        raise AudioEventError("audio_event_time_invalid")
    value=Decimal(raw)
    if abs(value) > (Decimal(86400) if signed else Decimal(MAX_AUDIO_SECONDS+1)):
        raise AudioEventError("audio_event_time_invalid")
    return value


@dataclass(frozen=True,slots=True)
class AudioEventConfig:
    window_ms: int=1000
    hop_ms: int=500
    music_threshold: str="0.750"
    sound_effect_threshold: str="0.800"
    other_threshold: str="0.850"
    min_positive_windows: int=1
    wallclock_budget_sec: int=240
    classifier_policy_version: str="audio-events-threshold-v1"
    device: str="cpu"

    def __post_init__(self):
        if (type(self.window_ms) is not int or self.window_ms not in (250,500,1000)
            or type(self.hop_ms) is not int or self.hop_ms not in (125,250,500)
            or self.hop_ms>self.window_ms
            or type(self.min_positive_windows) is not int or not 1<=self.min_positive_windows<=4
            or type(self.wallclock_budget_sec) is not int or not 10<=self.wallclock_budget_sec<=600
            or type(self.classifier_policy_version) is not str
            or VERSION.fullmatch(self.classifier_policy_version) is None
            or self.device!="cpu"):
            raise AudioEventError("audio_event_config_invalid")
        for value in (self.music_threshold,self.sound_effect_threshold,self.other_threshold):
            if type(value) is not str or re.fullmatch(r"0\.[0-9]{3}|1\.000",value) is None:
                raise AudioEventError("audio_event_config_invalid")
            if not Decimal("0.500")<=Decimal(value)<=1:
                raise AudioEventError("audio_event_config_invalid")

    def wire(self):
        return {"window_ms":self.window_ms,"hop_ms":self.hop_ms,
                "music_threshold":self.music_threshold,
                "sound_effect_threshold":self.sound_effect_threshold,
                "other_threshold":self.other_threshold,
                "min_positive_windows":self.min_positive_windows,
                "wallclock_budget_sec":self.wallclock_budget_sec,
                "classifier_policy_version":self.classifier_policy_version,
                "device":self.device}


@runtime_checkable
class AudioEventClassifier(Protocol):
    @property
    def provenance(self) -> dict: ...
    def predict(self, window_pcm: bytes, *, sample_rate: int) -> dict: ...


def _provenance(provider,config):
    try:
        obj=provider.provenance
        fn=provider.predict
    except (AttributeError,TypeError) as exc:
        raise AudioEventError("audio_event_classifier_invalid") from exc
    if not callable(fn) or type(obj) is not dict or set(obj)!={
        "engine","engine_version","model_version","model_sha256",
        "labelmap_sha256","device","license_authorization"
    }:
        raise AudioEventError("audio_event_provenance_invalid")
    if (obj["engine"] not in ("yamnet_onnx","panns_onnx","injected_offline")
        or any(type(obj[k]) is not str or VERSION.fullmatch(obj[k]) is None
               for k in ("engine_version","model_version"))
        or any(type(obj[k]) is not str or SHA.fullmatch(obj[k]) is None
               for k in ("model_sha256","labelmap_sha256"))
        or obj["device"]!=config.device
        or obj["license_authorization"]!="host_verified"):
        raise AudioEventError("audio_event_provenance_invalid")
    return dict(obj)


def _scores(value):
    if type(value) is not dict or set(value)!=set(LABELS):
        raise AudioEventError("audio_event_output_invalid")
    return {name:_decimal(value[name]) for name in LABELS}


def _window_source(audio,config):
    if audio.sample_rate!=SAMPLE_RATE or audio.channel_count!=1:
        raise AudioEventError("audio_event_pcm_format_mismatch")
    samples=audio.sample_count
    if samples<=0 or samples>MAX_AUDIO_SECONDS*SAMPLE_RATE+SAMPLE_RATE:
        raise AudioEventError("audio_event_pcm_bounds_invalid")
    width=config.window_ms*SAMPLE_RATE//1000
    hop=config.hop_ms*SAMPLE_RATE//1000
    if not width or not hop:
        raise AudioEventError("audio_event_config_invalid")
    count=(samples-1)//hop+1
    if count>MAX_WINDOWS:
        raise AudioEventError("audio_event_window_budget_exceeded")
    for idx in range(count):
        first=idx*hop
        last=min(first+width,samples)
        if last<=first:
            continue
        frame=audio.pcm[2*first:2*last]
        if len(frame)!=2*(last-first):
            raise AudioEventError("audio_event_pcm_slice_invalid")
        # Only the final short window is zero-padded; boundaries are
        # always clipped to the actual decoded clip length.
        yield idx,first,last,frame+b"\0"*(2*width-len(frame))


def _validate_video_origin(audio,probe):
    if probe is None:
        return None,"video_clock_missing",None
    if not isinstance(probe,ProbeInfo) or type(probe.duration) is not Decimal or (
        not probe.duration.is_finite() or not Decimal(0)<probe.duration<=Decimal(600)
    ):
        raise AudioEventError("audio_event_video_probe_invalid")
    dur=probe.duration
    if audio.probe.state=="absent":
        return None,"no_audio_stream",dur
    if audio.probe.start_time_sec is None or probe.start_time is None:
        return None,"stream_start_offset_unavailable",dur
    if type(probe.start_time) is not Decimal or not probe.start_time.is_finite():
        raise AudioEventError("audio_event_video_probe_invalid")
    offset=_time(audio.probe.start_time_sec,signed=True)-probe.start_time
    if abs(offset)>Decimal(86400):
        raise AudioEventError("audio_event_clock_offset_invalid")
    return offset,"ffprobe_audio_video_stream_origin",dur


def _make_event(label,windows,*,sample_count,config):
    if not windows:
        return None
    first=windows[0]["start_sample"]
    last=max(x["end_sample"] for x in windows)
    if first>=last or last>sample_count:
        raise AudioEventError("audio_event_interval_invalid")
    raw=max(Decimal(x["scores"][label]) for x in windows)
    return {"event_type":label,
            "start_sec":_fixed(Decimal(first)/SAMPLE_RATE),
            "end_sec":_fixed(Decimal(last)/SAMPLE_RATE),
            "source_start_sample":first,"source_end_sample":last,
            "source_window_indices":[x["window_index"] for x in windows],
            "max_engine_score":_fixed(raw),
            "threshold":getattr(config,label+"_threshold"),
            "evidence_level":"uncalibrated_local_model_candidate",
            "confidence_pct":None}


def _events(windows,audio,cfg):
    active={k:[] for k in LABELS}
    candidates=[]
    for item in windows:
        for label in LABELS:
            keep=(item["state"]=="observed" and
                  Decimal(item["scores"][label])>=Decimal(getattr(cfg,label+"_threshold")))
            if not keep:
                if len(active[label])>=cfg.min_positive_windows:
                    candidates.append(_make_event(label,active[label],
                        sample_count=audio.sample_count,config=cfg))
                active[label]=[]
                continue
            if active[label] and active[label][-1]["end_sample"]<item["start_sample"]:
                if len(active[label])>=cfg.min_positive_windows:
                    candidates.append(_make_event(label,active[label],
                        sample_count=audio.sample_count,config=cfg))
                active[label]=[]
            active[label].append(item)
    for label in LABELS:
        if len(active[label])>=cfg.min_positive_windows:
            candidates.append(_make_event(label,active[label],
                sample_count=audio.sample_count,config=cfg))
    if len(candidates)>MAX_EVENTS:
        raise AudioEventError("audio_event_count_limit")
    return sorted(candidates,key=lambda x:(x["source_start_sample"],
        x["event_type"],x["source_end_sample"]))


def observe_audio_events(audio:DecodedAudio,*,classifier=None,config=None,
                         video_probe=None,verify_current=None):
    """Produce incomplete, immutable window candidates; retain speech overlaps.

    Authorization must be provided by the current original media host; media
    digest is lineage evidence, not permission. No external network/persistent
    write. TRUE event means the pinned classifier observed a positive window,
    NOT human-verified calibrated music probability.
    """
    if not isinstance(audio,DecodedAudio):
        raise AudioEventError("audio_event_verified_audio_required")
    cfg=AudioEventConfig() if config is None else config
    if not isinstance(cfg,AudioEventConfig):
        raise AudioEventError("audio_event_config_invalid")
    context={"source_media_sha256":audio.source_sha256,
             "source_stream_index":audio.probe.stream_index,
             "pcm_sha256":sha256(audio.pcm).hexdigest() if audio.pcm else None}
    if not callable(verify_current) or verify_current(context) is not True:
        raise AudioEventError("audio_event_current_rights_unverified")
    if audio.probe.state=="absent":
        state="no_audio_stream";reason="verified_no_audio_stream";source="verified_absence"
    elif not audio.pcm:
        state="unavailable";reason="decoded_pcm_missing";source="unknown"
    elif not any(audio.pcm):
        state="digital_silence";reason="verified_exact_zero_pcm";source="verified_absence"
    elif classifier is None:
        state="classifier_unavailable";reason="local_classifier_not_installed";source="unknown"
    else:
        state="observing";reason=None;source="local_classifier"
    provenance=_provenance(classifier,cfg) if state=="observing" else None
    offset,clock_state,video_duration=_validate_video_origin(audio,video_probe)
    windows=[];events=[]
    if state=="observing":
        budget_start=time.monotonic()
        for idx,start,end,pcm in _window_source(audio,cfg):
            if time.monotonic()-budget_start>cfg.wallclock_budget_sec:
                state="partial_failure";reason="classifier_wallclock_budget_exceeded"
                windows.append({"window_index":idx,"start_sample":start,
                    "end_sample":end,"start_sec":_fixed(Decimal(start)/SAMPLE_RATE),
                    "end_sec":_fixed(Decimal(end)/SAMPLE_RATE),
                    "state":"unavailable","scores":None,
                    "reason_code":"classifier_wallclock_budget_exceeded"})
                break
            try:
                result=classifier.predict(pcm,sample_rate=SAMPLE_RATE)
                scores=_scores(result)
                status="observed";error=None
                scores={k:_fixed(v) for k,v in scores.items()}
            except AudioEventError as exc:
                status="failed";error=exc.code;scores=None
            except Exception:
                status="failed";error="classifier_inference_failed";scores=None
            windows.append({"window_index":idx,"start_sample":start,
                "end_sample":end,"start_sec":_fixed(Decimal(start)/SAMPLE_RATE),
                "end_sec":_fixed(Decimal(end)/SAMPLE_RATE),
                "state":status,"scores":scores,"reason_code":error})
            if status!="observed":
                state="partial_failure";reason="classifier_windows_failed"
        if state=="observing":
            state="classified_partial"
        events=_events(windows,audio,cfg)
    presence={label:("false" if source=="verified_absence" else
                      "true" if any(e["event_type"]==label for e in events)
                      else "unknown") for label in LABELS}
    rows=[];mapping_status="blocked_no_events"
    if events:
        if offset is None or video_duration is None:
            mapping_status="blocked_unverified_video_timebase"
        else:
            mapping_status="verified_audio_video_timebase"
            for e in events:
                a=Decimal(e["start_sec"])+offset
                b=Decimal(e["end_sec"])+offset
                if a<0 or b>video_duration or b<=a:
                    raise AudioEventError("audio_event_outside_video_timebase")
                rows.append({
                    "event_type":e["event_type"],
                    "start_sec":_fixed(a),"end_sec":_fixed(b),
                    "label":e["event_type"],
                    "method_name":METHOD,
                    "confidence_pct":None,
                })
    core={
        "schema_version":SCHEMA,"method_version":METHOD,
        "source_media_sha256":audio.source_sha256,
        "source_pcm_sha256":sha256(audio.pcm).hexdigest() if audio.pcm else None,
        "source_stream_index":audio.probe.stream_index,
        "state":state,"reason_code":reason,
        "time_origin":"audio_stream_local_zero",
        "selected_audio_duration_ms":audio.duration_ms,
        "music_presence":presence["music"],
        "sound_effect_presence":presence["sound_effect"],
        "other_presence":presence["other"],
        "audio_absence_verified":source=="verified_absence",
        "classifier_model":provenance,
        "class_probability_calibrated":False,
        "full_video_acoustic_coverage":False,
        "window_coverage":"complete_selected_pcm" if state=="classified_partial" else
            "verified_absence" if source=="verified_absence" else
            "partial_with_failures" if state=="partial_failure" else "unknown",
        "counts":{"attempted":len(windows),
            "observed":sum(w["state"]=="observed" for w in windows),
            "failed":sum(w["state"]=="failed" for w in windows),
            "unavailable":sum(w["state"]=="unavailable" for w in windows),
            "events":len(events)},
        "windows":windows,"events":events,
        "video_duration_sec":_fixed(video_duration) if video_duration else None,
        "video_clock_offset_sec":_fixed(offset) if offset is not None else None,
        "video_clock_state":clock_state,
        "a003_audio_events_projection":rows,
        "a003_mapping_state":mapping_status,
        "config":cfg.wire(),
    }
    return {**core,"observation_sha256":canonical_sha256(core)}
