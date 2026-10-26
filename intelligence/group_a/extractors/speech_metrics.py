"""GA-MOD-11 — deterministic, provenance-bound speech metrics.

Counts lexical tokens from *observed ASR speech*, not subtitles/TXT/VAD.
Computes active duration from unioned ASR segments or explicitly selected
VAD candidates, never sums overlapping ranges. Video-local coverage/first
speech require authenticated timeline alignment. Unknown != zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import re
import unicodedata

from ..contracts_v1 import canonical_sha256
from .audio_decode import DecodedAudio, SAMPLE_RATE
from .technical import ProbeInfo

SCHEMA = "ga-speech-metrics/v1"
METHOD = "group-a-speech-metrics-v1"
MAX_WORDS = 50_000
SHA = re.compile(r"^[a-f0-9]{64}$")
TIMED = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{3}$")
SIGNED = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]{1,6}$")
WORD_PATTERN = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)*", re.UNICODE)
ACTUAL_ASR = frozenset(("local_faster_whisper", "psi_asr"))
CAPTION = frozenset(("sidecar_srt", "sidecar_txt", "psi_srt"))


class SpeechMetricsError(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__("Group A speech metrics rejected: "+code)


@dataclass(frozen=True, slots=True)
class SpeechMetricsConfig:
    duration_source: str = "prefer_vad"   # prefer_vad, asr_only, vad_only
    min_rate_duration_ms: int = 250
    require_video_clock: bool = False

    def __post_init__(self):
        if (self.duration_source not in ("prefer_vad","asr_only","vad_only")
            or type(self.min_rate_duration_ms) is not int
            or not 100<=self.min_rate_duration_ms<=3000
            or type(self.require_video_clock) is not bool):
            raise SpeechMetricsError("invalid_metrics_config")

    def to_dict(self):
        return {"duration_source":self.duration_source,
                "min_rate_duration_ms":self.min_rate_duration_ms,
                "require_video_clock":self.require_video_clock}


@dataclass(frozen=True, slots=True)
class PSITimelineBinding:
    """Explicit acquisition-side proof, not a guessed PSI URL equivalence.

    Host authenticates this mapping against the current project/source/version
    and acquisition output. output SHA must equal reused PSI output SHA.
    """
    source_media_sha256: str
    upstream_output_sha256: str
    offset_sec: str
    authority: str = "verified_psi_acquisition"

    def __post_init__(self):
        if (type(self.source_media_sha256) is not str
            or not SHA.fullmatch(self.source_media_sha256)
            or type(self.upstream_output_sha256) is not str
            or not SHA.fullmatch(self.upstream_output_sha256)
            or self.authority!="verified_psi_acquisition"):
            raise SpeechMetricsError("unverified_psi_timeline")
        _signed(self.offset_sec)


def _time(value,field):
    if type(value) is not str or not TIMED.fullmatch(value):
        raise SpeechMetricsError("invalid_"+field)
    result=Decimal(value)
    if result>Decimal("600.000"):
        raise SpeechMetricsError("invalid_"+field)
    return result


def _signed(value):
    if type(value) is not str or not SIGNED.fullmatch(value):
        raise SpeechMetricsError("invalid_video_clock")
    d=Decimal(value)
    if not d.is_finite() or abs(d)>Decimal("86400.000000"):
        raise SpeechMetricsError("invalid_video_clock")
    return d


def _fixed(value,places="0.001"):
    return format(value.quantize(Decimal(places),rounding=ROUND_HALF_UP),
                  ".3f" if places=="0.001" else ".2f")


def _digest(record,kind):
    if type(record) is not dict:
        raise SpeechMetricsError("invalid_"+kind)
    name=record.get("observation_sha256")
    if type(name) is not str or not SHA.fullmatch(name):
        raise SpeechMetricsError("unverified_"+kind)
    try:
        computed=canonical_sha256({k:v for k,v in record.items()
                                   if k!="observation_sha256"})
    except (ValueError,TypeError) as exc:
        raise SpeechMetricsError("invalid_"+kind) from exc
    if name!=computed:
        raise SpeechMetricsError(kind+"_digest_mismatch")


def _validate_asr(audio,asr):
    _digest(asr,"asr")
    if asr.get("schema_version")!="ga-asr-transcript/v1" or asr.get("method_version")!="group-a-asr-pipeline-v1":
        raise SpeechMetricsError("unsupported_asr_version")
    pcm_sha=sha256(audio.pcm).hexdigest() if audio.pcm else None
    if (asr.get("source_media_sha256")!=audio.source_sha256
        or asr.get("source_pcm_sha256")!=pcm_sha):
        raise SpeechMetricsError("asr_media_mismatch")
    kind=asr.get("source")
    if kind not in ACTUAL_ASR|CAPTION|{"none"}:
        raise SpeechMetricsError("untrusted_asr_source")
    status=asr.get("speech_status")
    if status not in ("unknown","speech_detected","verified_silent","no_audio_stream"):
        raise SpeechMetricsError("invalid_asr_state")
    if asr.get("complete_speech_coverage") is not False:
        raise SpeechMetricsError("false_complete_speech_coverage")
    if kind in CAPTION and status=="speech_detected":
        raise SpeechMetricsError("caption_not_audible_speech")
    if kind in ACTUAL_ASR and status!="speech_detected":
        raise SpeechMetricsError("invalid_audible_asr")
    if audio.probe.state=="absent":
        if status!="no_audio_stream":
            raise SpeechMetricsError("asr_absence_contradiction")
    elif audio.pcm and not any(audio.pcm):
        if status!="verified_silent":
            raise SpeechMetricsError("asr_silence_contradiction")
    elif status in ("no_audio_stream","verified_silent"):
        raise SpeechMetricsError("asr_audio_contradiction")
    rows=asr.get("segments")
    if type(rows) is not list or len(rows)>12000:
        raise SpeechMetricsError("invalid_asr_segments")
    if kind=="none" and rows:
        raise SpeechMetricsError("unexpected_asr_segments")
    if kind in ACTUAL_ASR and not rows:
        raise SpeechMetricsError("asr_speech_not_observed")
    last=Decimal(0)
    spans=[]
    for row in rows:
        if type(row) is not dict or type(row.get("text")) is not str or not row["text"].strip():
            raise SpeechMetricsError("invalid_asr_segment")
        a=_time(row.get("start_sec"),"asr_time")
        b=_time(row.get("end_sec"),"asr_time")
        if a<last or b<=a:
            raise SpeechMetricsError("invalid_asr_interval")
        last=b
        spans.append((a,b))
    if asr.get("source")=="local_faster_whisper":
        if asr.get("time_origin")!="audio_stream_local_zero":
            raise SpeechMetricsError("asr_time_origin_mismatch")
        if spans and spans[-1][1]>Decimal(audio.sample_count)/SAMPLE_RATE:
            raise SpeechMetricsError("asr_outside_decoded_audio")
    elif kind=="psi_asr" and asr.get("time_origin")!="psi_source_local_ms":
        raise SpeechMetricsError("asr_time_origin_mismatch")
    return kind,spans


def _validate_vad(audio,vad):
    if vad is None:
        return None
    _digest(vad,"vad")
    expected=sha256(audio.pcm).hexdigest() if audio.pcm else None
    if (vad.get("schema_version")!="ga-vad-observation/v1"
        or vad.get("source_media_sha256")!=audio.source_sha256
        or vad.get("pcm_sha256")!=expected
        or vad.get("time_origin")!="audio_stream_local_zero"
        or vad.get("source_duration_ms")!=audio.duration_ms):
        raise SpeechMetricsError("vad_media_or_version_mismatch")
    state=vad.get("state")
    if state not in ("speech_candidates","no_candidates","model_unavailable",
                     "no_audio_stream","digital_silence","unobservable"):
        raise SpeechMetricsError("vad_state_invalid")
    if (audio.probe.state=="absent")!=(state=="no_audio_stream"):
        raise SpeechMetricsError("vad_audio_state_mismatch")
    if audio.pcm and not any(audio.pcm) and state!="digital_silence":
        raise SpeechMetricsError("vad_silence_state_mismatch")
    spans=vad.get("intervals")
    if type(spans) is not list or len(spans)>19000:
        raise SpeechMetricsError("vad_intervals_invalid")
    rows=[]
    for row in spans:
        if type(row) is not dict or type(row.get("start_ms")) is not int or type(row.get("end_ms")) is not int:
            raise SpeechMetricsError("vad_interval_invalid")
        a,b=row["start_ms"],row["end_ms"]
        if (a<0 or a>=b or b>audio.duration_ms
            or row.get("start_sec")!=f"{Decimal(a)/1000:.3f}"
            or row.get("end_sec")!=f"{Decimal(b)/1000:.3f}"):
            raise SpeechMetricsError("vad_interval_invalid")
        rows.append((Decimal(a)/1000,Decimal(b)/1000))
    joined=union_intervals(rows,max_duration=Decimal(audio.sample_count)/SAMPLE_RATE)
    expected_ms=sum(row["end_ms"]-row["start_ms"] for row in spans)
    if (sum(int((b-a)*1000) for a,b in joined)!=expected_ms
        or vad.get("active_speech_ms")!=expected_ms
        or (state=="speech_candidates")!=(bool(rows))
        or (state in ("no_candidates","model_unavailable","no_audio_stream","digital_silence","unobservable") and rows)
        or vad.get("verified_no_speech") is not (state in ("no_audio_stream","digital_silence"))):
        raise SpeechMetricsError("vad_duration_or_truth_mismatch")
    return joined if state=="speech_candidates" else None


def union_intervals(intervals,*,max_duration):
    """Pure deterministic union; never silently clip out-of-bounds evidence."""
    if type(max_duration) is not Decimal or not max_duration.is_finite() or max_duration<0:
        raise SpeechMetricsError("invalid_interval_bounds")
    if type(intervals) not in (tuple,list) or len(intervals)>19000:
        raise SpeechMetricsError("invalid_interval_input")
    ordered=[]
    for a,b in intervals:
        if type(a) is not Decimal or type(b) is not Decimal or not (
            a.is_finite() and b.is_finite() and Decimal(0)<=a<b<=max_duration
        ):
            raise SpeechMetricsError("interval_out_of_bounds")
        ordered.append((a,b))
    merged=[]
    for a,b in sorted(ordered):
        if merged and a<=merged[-1][1]:
            merged[-1]=(merged[-1][0],max(merged[-1][1],b))
        else:
            merged.append((a,b))
    return tuple(merged)


def _intersection_duration(left,right):
    total=Decimal(0)
    for a,b in left:
        for c,d in right:
            lo,hi=max(a,c),min(b,d)
            if lo<hi:
                total+=hi-lo
    return total


def count_observed_words(segments):
    """Unicode NFC + apostrophe-preserving lexical tokenization ru/en/mixed.

    No grammatical correction, stemming, split-time imputation or guess
    about sounds. Numeric tokens count as lexical units by v1 convention.
    """
    if type(segments) is not list or len(segments)>12000:
        raise SpeechMetricsError("invalid_asr_segments")
    count=0
    for row in segments:
        if type(row) is not dict or type(row.get("text")) is not str:
            raise SpeechMetricsError("invalid_asr_segment")
        text=unicodedata.normalize("NFC",row["text"])
        count+=len(WORD_PATTERN.findall(text))
        if count>MAX_WORDS:
            raise SpeechMetricsError("too_many_spoken_words")
    return count


def _video_offset(audio,transcript,video_probe,psi_timeline):
    if video_probe is None:
        return None, "video_probe_missing"
    if not isinstance(video_probe,ProbeInfo) or type(video_probe.duration) is not Decimal or (
        not video_probe.duration.is_finite() or not 0<video_probe.duration<=Decimal("600.000")
    ):
        raise SpeechMetricsError("invalid_video_probe")
    kind=transcript["source"]
    if kind=="local_faster_whisper":
        if video_probe.start_time is None or audio.probe.start_time_sec is None:
            return None,"video_audio_start_offset_unknown"
        if type(video_probe.start_time) is not Decimal or not video_probe.start_time.is_finite():
            raise SpeechMetricsError("invalid_video_probe")
        return _signed(str(audio.probe.start_time_sec))-video_probe.start_time,"ffprobe_stream_origins"
    if kind=="psi_asr":
        if psi_timeline is None:
            return None,"psi_acquisition_clock_unverified"
        if not isinstance(psi_timeline,PSITimelineBinding) or (
            psi_timeline.source_media_sha256!=audio.source_sha256
            or psi_timeline.upstream_output_sha256!=transcript.get("upstream",{}).get("output_sha256")
        ):
            raise SpeechMetricsError("unverified_psi_timeline")
        return _signed(psi_timeline.offset_sec),"verified_psi_acquisition"
    return None,"no_audible_asr_video_timing"


def measure_speech(audio:DecodedAudio,transcript:dict,*,video_probe=None,
                   vad=None,config=None,psi_timeline=None):
    """Reproducible partial speech facts; no writes to A003 or Group B.

    Word count is based on validated audible ASR utterances (not subtitles).
    VAD is candidate evidence: only used for duration after actual ASR speech
    was observed. Absence can be zero ONLY if real decoded audio proves no
    track or all integer samples exactly zero.
    """
    if not isinstance(audio,DecodedAudio):
        raise SpeechMetricsError("verified_audio_required")
    cfg=SpeechMetricsConfig() if config is None else config
    if not isinstance(cfg,SpeechMetricsConfig):
        raise SpeechMetricsError("invalid_metrics_config")
    kind,asr_spans=_validate_asr(audio,transcript)
    vad_spans=_validate_vad(audio,vad)
    absent=audio.probe.state=="absent"
    silence=bool(audio.pcm) and not any(audio.pcm)
    words=None;active=None;active_basis="unknown"
    vad_consistency="not_compared"
    wpm=None;rate_status="unknown"
    first=None;coverage=None;clock_status="not_verified"
    if absent or silence:
        words=0
        active=Decimal(0)
        active_basis="verified_no_audio_stream" if absent else "verified_decoded_digital_silence"
        rate_status="verified_absence"
    elif kind in ACTUAL_ASR:
        words=count_observed_words(transcript["segments"])
        if words==0:
            # Speech detected with no lexical tokens is not a verified zero.
            words=None
        local=union_intervals(asr_spans,max_duration=Decimal("600.000"))
        use_vad=(cfg.duration_source!="asr_only" and vad_spans is not None
                 and kind=="local_faster_whisper")
        if use_vad:
            asr_time=sum((b-a for a,b in local),Decimal(0))
            vad_time=sum((b-a for a,b in vad_spans),Decimal(0))
            both=_intersection_duration(local,vad_spans)
            # A VAD range far from ASR speech is likely music/noise,
            # NOT evidence for a speech-rate denominator.
            if (both<asr_time*Decimal("0.5") or
                both<vad_time*Decimal("0.5")):
                use_vad=False
                vad_consistency="mismatched_with_asr"
            else:
                vad_consistency="correlated_with_asr"
        if use_vad:
            active=sum((b-a for a,b in vad_spans),Decimal(0))
            active_basis="vad_candidates_correlated_with_asr"
        elif cfg.duration_source!="vad_only":
            active=sum((b-a for a,b in local),Decimal(0))
            active_basis="observed_asr_utterance_union"
        if words is not None and active is not None and active>0:
            if active*1000>=cfg.min_rate_duration_ms:
                wpm=Decimal(words)*60/active
                rate_status="measured_from_observed_asr_and_"+(
                    "vad_candidates" if use_vad else "asr_utterances")
            else:
                rate_status="insufficient_active_duration_for_stable_wpm"
        elif words is None:
            rate_status="no_observable_lexical_tokens"
        elif cfg.duration_source=="vad_only":
            rate_status="vad_evidence_unavailable"
    # VAD candidates alone are NOT enough to call music or noise speech.
    vad_candidate_sec=(sum((b-a for a,b in vad_spans),Decimal(0))
                       if vad_spans is not None else None)
    video_duration=None
    if video_probe is not None:
        if not isinstance(video_probe,ProbeInfo) or type(video_probe.duration) is not Decimal or (
            not video_probe.duration.is_finite() or not 0<video_probe.duration<=Decimal("600.000")
        ):
            raise SpeechMetricsError("invalid_video_probe")
        video_duration=video_probe.duration
    offset,offset_origin=_video_offset(audio,transcript,video_probe,psi_timeline)
    if absent or silence:
        if video_duration is not None:
            coverage=Decimal(0)
            clock_status="verified_absence"
    elif kind in ACTUAL_ASR and video_duration is not None and offset is not None:
        start=asr_spans[0][0]+offset
        if start<0 or start>=video_duration:
            raise SpeechMetricsError("first_speech_outside_video")
        first=start
        # Each timed interval must fit inside the authenticated video clock;
        # never clip/truncate evidence silently.
        for a,b in asr_spans:
            if a+offset<0 or b+offset>video_duration:
                raise SpeechMetricsError("speech_interval_outside_video")
        if active_basis=="vad_candidates_correlated_with_asr":
            for a,b in vad_spans:
                if a+offset<0 or b+offset>video_duration:
                    raise SpeechMetricsError("vad_interval_outside_video")
        clock_status=offset_origin
        if active is not None:
            if active>video_duration:
                raise SpeechMetricsError("active_speech_exceeds_video")
            coverage=active*100/video_duration
    elif cfg.require_video_clock and kind in ACTUAL_ASR:
        raise SpeechMetricsError("video_clock_required")
    if active is not None and active>Decimal("600.000"):
        raise SpeechMetricsError("active_speech_limit")
    if wpm is not None and wpm>Decimal("3000"):
        wpm=None
        rate_status="wpm_outside_credible_range"
    if coverage is not None and not Decimal(0)<=coverage<=Decimal(100):
        raise SpeechMetricsError("speech_coverage_invalid")
    # 003_video_features_a.sql stores ONLY spoken_words and
    # speech_duration_sec in A003, while view speech_wpm/coverage are
    # derived from those columns, and first_speech_sec comes from this
    # separately stored observed speech segment table.
    a003_segment_rows=[]
    if kind in ACTUAL_ASR and offset is not None and video_duration is not None:
        for seg in transcript["segments"]:
            a=_time(seg["start_sec"],"asr_time")+offset
            b=_time(seg["end_sec"],"asr_time")+offset
            if a<0 or b>video_duration or b<=a:
                raise SpeechMetricsError("speech_interval_outside_video")
            a003_segment_rows.append({
                "start_sec":_fixed(a),
                "end_sec":_fixed(b),
                "spoken_text":seg["text"],
                "method_name":"ga11-"+kind,
                "confidence_pct":None,
            })
    a003_status=("partial" if words is not None else "unavailable")
    if words==0 and (absent or silence):
        # Verified absence of an audio stream is NOT completion of all
        # Group A streams. Preserve stage-local scope.
        a003_status="partial"
    numeric={
        "spoken_words":words,
        "speech_duration_sec":None if active is None else _fixed(active),
        "speech_wpm":None if wpm is None else _fixed(wpm,"0.01"),
        "speech_coverage_pct":None if coverage is None else _fixed(coverage,"0.01"),
        "first_speech_sec":None if first is None else _fixed(first),
    }
    result={
        "schema_version":SCHEMA,
        "method_version":METHOD,
        "source_media_sha256":audio.source_sha256,
        "asr_observation_sha256":transcript["observation_sha256"],
        "vad_observation_sha256":None if vad is None else vad["observation_sha256"],
        "video_duration_sec":None if video_duration is None else _fixed(video_duration),
        "transcript_source":kind,
        "speech_status":("verified_absence" if absent or silence
                         else "observed_partial" if kind in ACTUAL_ASR
                         else "unknown"),
        "word_count_policy":"unicode_nfc_alnum_apostrophe_v1",
        "active_duration_basis":active_basis,
        "vad_asr_consistency":vad_consistency,
        "wpm_status":rate_status,
        "clock_status":clock_status,
        "clock_offset_origin":offset_origin,
        "video_clock_offset_sec":None if offset is None else _fixed(offset),
        "vad_candidate_duration_sec":None if vad_candidate_sec is None else _fixed(vad_candidate_sec),
        "coverage_state":"known_derived_partial" if coverage is not None and first is not None
              else "verified_zero" if absent or silence and video_duration is not None
              else "unknown",
        "complete_speech_coverage":False,
        **numeric,
        "a003_speech_projection":{
            "speech_status":a003_status,
            "spoken_words":words,
            "speech_duration_sec":None if active is None else _fixed(active),
        },
        "a003_speech_segments_projection":a003_segment_rows,
        "config":cfg.to_dict(),
    }
    return {**result,"observation_sha256":canonical_sha256(result)}
