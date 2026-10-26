"""GA-MOD-10: pure, deterministic and source-aware ASR normalization.

Speech is only evidence when an actual model/verified PSI ASR observed it.
Captions (SRT) and plain text (TXT) are NEVER proof of audible speech.
Word alignment is optional and MUST NOT be inferred from segment durations.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import re
from typing import Protocol, runtime_checkable

from ..contracts_v1 import canonical_sha256
from .audio_decode import DecodedAudio

ASR_SCHEMA = "ga-asr-transcript/v1"
ASR_METHOD = "group-a-asr-pipeline-v1"
MAX_SEGMENTS = 12000
MAX_WORDS = 50000
MAX_TEXT = 2000
MAX_SRT_BYTES = 2_000_000
HASH = re.compile(r"^[a-f0-9]{64}$")
LANG = re.compile(r"^(?:ru|en|[a-z]{2,3}(?:-[a-zA-Z]{2,8})?)$")
TIME = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{3}$")
SRT_TIME = re.compile(r"^(\d{2,3}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2,3}):(\d{2}):(\d{2})[,.](\d{3})(?:\s+.*)?$")
UNKNOWN = "unknown"


class ASRError(ValueError):
    def __init__(self, code: str):
        self.code=code
        super().__init__("Group A ASR failed: "+code)


@dataclass(frozen=True, slots=True)
class ASRConfig:
    language: str = "auto"
    source_policy: str = "prefer_verified"  # or psi_only/sidecar_only/local_only
    word_timestamps: bool = True
    timeout_sec: int = 240
    compute_type: str = "int8"
    device: str = "cpu"
    beam_size: int = 5

    def __post_init__(self):
        if (self.language not in ("auto","ru","en")
            or self.source_policy not in ("prefer_verified","psi_only","sidecar_only","local_only")
            or type(self.word_timestamps) is not bool
            or type(self.timeout_sec) is not int or not 10<=self.timeout_sec<=900
            or self.compute_type not in ("int8","float16","float32")
            or self.device not in ("cpu","cuda")
            or type(self.beam_size) is not int or not 1<=self.beam_size<=8):
            raise ASRError("invalid_asr_config")

    def wire(self):
        return {"language":self.language,"source_policy":self.source_policy,
                "word_timestamps":self.word_timestamps,"timeout_sec":self.timeout_sec,
                "compute_type":self.compute_type,"device":self.device,
                "beam_size":self.beam_size}


@runtime_checkable
class ASRBackend(Protocol):
    @property
    def provenance(self) -> dict: ...
    def transcribe(self, audio: DecodedAudio, config: ASRConfig) -> dict: ...


@dataclass(frozen=True,slots=True)
class Sidecar:
    kind: str  # srt or txt
    media_sha256: str
    content: bytes
    expected_sha256: str

    def __post_init__(self):
        if (self.kind not in ("srt","txt")
            or type(self.media_sha256) is not str or not HASH.fullmatch(self.media_sha256)
            or type(self.expected_sha256) is not str or not HASH.fullmatch(self.expected_sha256)
            or type(self.content) is not bytes or len(self.content)>MAX_SRT_BYTES
            or sha256(self.content).hexdigest()!=self.expected_sha256):
            raise ASRError("unverified_sidecar")


def _time(raw, *, allow_zero=True, maximum_ms=600_000):
    if type(raw) is not str or not TIME.fullmatch(raw):
        raise ASRError("invalid_asr_time")
    v=Decimal(raw)
    if v*1000>maximum_ms or (not allow_zero and v==0):
        raise ASRError("invalid_asr_time")
    return v


def _second(raw, *, allow_zero=True):
    if type(raw) not in (int,float,str,Decimal) or type(raw) is bool:
        raise ASRError("invalid_asr_time")
    try:
        v=Decimal(str(raw))
        if not v.is_finite() or v<0 or v>Decimal("600.000"):
            raise ValueError()
        rounded=v.quantize(Decimal("0.001"),rounding=ROUND_HALF_UP)
        if not allow_zero and rounded==0:
            raise ValueError()
        return format(rounded,".3f")
    except (InvalidOperation,ValueError,OverflowError) as exc:
        raise ASRError("invalid_asr_time") from exc


def _language(raw, *, allow_unknown=True):
    if raw is None or raw==UNKNOWN:
        if allow_unknown:return UNKNOWN
        raise ASRError("invalid_language")
    if type(raw) is not str or not LANG.fullmatch(raw):
        raise ASRError("invalid_language")
    return raw


def _text(raw):
    if type(raw) is not str or not raw.strip() or len(raw)>MAX_TEXT:
        raise ASRError("invalid_transcript_text")
    if any(ord(x)<32 and x not in "\n\t" for x in raw):
        raise ASRError("invalid_transcript_text")
    return raw.strip()


def _confidence(raw):
    if raw is None:return None
    if type(raw) not in (int,float,Decimal) or type(raw) is bool:
        raise ASRError("invalid_asr_confidence")
    v=Decimal(str(raw))
    if not v.is_finite() or not 0<=v<=1:
        raise ASRError("invalid_asr_confidence")
    return str(v)


def _normalize_segments(rows, *, duration, origin, language, word_timestamps=False):
    """Only observed segments/words, no invented phoneme/word alignment."""
    if type(rows) is not list or len(rows)>MAX_SEGMENTS:
        raise ASRError("invalid_asr_segments")
    duration=_time(duration,allow_zero=False)
    segments=[]
    prev_end=Decimal("0")
    total_words=0
    for row in rows:
        if type(row) is not dict or not {"start_sec","end_sec","text"}<=set(row):
            raise ASRError("invalid_asr_segment")
        start=_time(row["start_sec"])
        end=_time(row["end_sec"],allow_zero=False)
        if start<prev_end or end<=start or end>duration:
            raise ASRError("asr_segment_timeline_invalid")
        text=_text(row["text"])
        words=row.get("words")
        if words is not None and type(words) is not list:
            raise ASRError("invalid_asr_words")
        normalized_words=[]
        if words:
            last=start
            for word in words:
                if type(word) is not dict or not {"start_sec","end_sec","text"}<=set(word):
                    raise ASRError("invalid_asr_word")
                a=_time(word["start_sec"]);b=_time(word["end_sec"],allow_zero=False)
                if a<last or a<start or b<=a or b>end:
                    raise ASRError("asr_word_timeline_invalid")
                normalized_words.append({"start_sec":format(a,".3f"),
                    "end_sec":format(b,".3f"),"text":_text(word["text"]),
                    "confidence":_confidence(word.get("confidence"))})
                last=b
            total_words+=len(normalized_words)
            if total_words>MAX_WORDS:
                raise ASRError("too_many_asr_words")
        segments.append({
            "start_sec":format(start,".3f"),
            "end_sec":format(end,".3f"),
            "text":text,
            "language":_language(row.get("language",language)),
            "confidence":_confidence(row.get("confidence")),
            "words":normalized_words if words is not None else None,
            "word_timing_status":"observed" if normalized_words else "unknown",
        })
        prev_end=end
    return segments


def _srt_stamp(groups):
    h,m,s,ms=(int(x) for x in groups)
    if m>=60 or s>=60 or h>10:
        raise ASRError("invalid_srt_timestamp")
    value=(((h*60)+m)*60+s)*1000+ms
    if value>600_000:
        raise ASRError("invalid_srt_timestamp")
    return format(Decimal(value)/1000,".3f")


def read_sidecar(sidecar:Sidecar, *, duration):
    if not isinstance(sidecar,Sidecar):
        raise ASRError("unverified_sidecar")
    try:
        content=sidecar.content.decode("utf-8-sig","strict").replace("\r\n","\n")
    except UnicodeError as exc:
        raise ASRError("invalid_sidecar_encoding") from exc
    if sidecar.kind=="txt":
        content=_text(content)
        return {"origin":"sidecar_txt","segments":[],
                "untimed_text":content,"timing_origin":"unknown",
                "timing_reliability":"untimed"}
    blocks=[b.strip() for b in re.split(r"\n\s*\n",content.strip()) if b.strip()]
    if not 1<=len(blocks)<=MAX_SEGMENTS:
        raise ASRError("invalid_srt_blocks")
    rows=[]
    for block in blocks:
        lines=block.splitlines()
        if len(lines)>=3 and lines[0].isdigit():
            lines=lines[1:]
        if len(lines)<2:
            raise ASRError("invalid_srt_block")
        m=SRT_TIME.fullmatch(lines[0].strip())
        if m is None:
            raise ASRError("invalid_srt_timestamp")
        a=_srt_stamp(m.groups()[:4]);b=_srt_stamp(m.groups()[4:])
        text=_text("\n".join(lines[1:]))
        rows.append({"start_sec":a,"end_sec":b,"text":text,
                     "language":UNKNOWN,"confidence":None,"words":None})
    normalized=_normalize_segments(rows,duration=duration,origin="video_local",
                                   language=UNKNOWN)
    return {"origin":"sidecar_srt","segments":normalized,"untimed_text":None,
            "timing_origin":"video_local","timing_reliability":"segment_observed"}


def _empty(audio,config,reason,*,status="unavailable",speech_status="unknown"):
    return _build(audio,config,source="none",status=status,speech_status=speech_status,
                  language=UNKNOWN,lang_source="unknown",
                  timing_origin="unknown",timing="none",segments=[],
                  untimed_text=None,upstream=None,model=None,reason=reason)


def _build(audio,config,*,source,status,speech_status,language,lang_source,
           timing_origin,timing,segments,untimed_text,upstream,model,reason):
    if source not in ("none","psi_asr","psi_srt","sidecar_srt","sidecar_txt","local_faster_whisper"):
        raise ASRError("invalid_asr_source")
    if status not in ("partial","unavailable") or speech_status not in (
        "unknown","speech_detected","verified_silent","no_audio_stream"):
        raise ASRError("invalid_asr_status")
    if source in ("sidecar_txt","sidecar_srt","psi_srt") and speech_status=="speech_detected":
        raise ASRError("captions_not_speech_proof")
    if source=="none" and (segments or untimed_text):
        raise ASRError("invalid_empty_asr")
    if timing not in ("none","untimed","segment_observed","word_observed"):
        raise ASRError("invalid_asr_timing")
    if timing=="word_observed" and not segments:
        raise ASRError("invalid_asr_timing")
    if status=="unavailable" and (segments or untimed_text):
        raise ASRError("unavailable_with_content")
    result={
        "schema_version":ASR_SCHEMA,"method_version":ASR_METHOD,
        "source_media_sha256":audio.source_sha256,
        "source_pcm_sha256":sha256(audio.pcm).hexdigest() if audio.pcm else None,
        "source":source,"status":status,"reason_code":reason,
        "speech_status":speech_status,
        "language":_language(language),
        "language_origin":lang_source,
        "time_origin":timing_origin,
        "timing_reliability":timing,
        "complete_speech_coverage":False,
        "word_alignment_complete":timing=="word_observed" and all(
            seg["word_timing_status"]=="observed" for seg in segments),
        "segments":segments,"untimed_text":untimed_text,
        "model":model,"upstream":upstream,
        "config":config.wire(),
    }
    return {**result,"observation_sha256":canonical_sha256(result)}


def transcribe(audio:DecodedAudio,*,config=None,psi=None,sidecar=None,backend=None,
               vad=None,verified_source=True,psi_verify_current=None,
               video_duration_sec=None):
    """Select one authoritative source, fail closed; fallback is explicit.

    GA-MOD-08 only passes a checked normalized PSI transcript. The caller
    must retain matching PSI binding/rights/versions; this function checks
    SHA and canonical shape but cannot authenticate arbitrary invented dicts.
    """
    cfg=ASRConfig() if config is None else config
    if not isinstance(cfg,ASRConfig) or not isinstance(audio,DecodedAudio):
        raise ASRError("invalid_asr_input")
    if verified_source is not True:
        raise ASRError("unverified_media_lineage")
    if vad is not None:
        if type(vad) is not dict or vad.get("source_media_sha256")!=audio.source_sha256:
            raise ASRError("vad_lineage_mismatch")
    if psi is not None:
        if cfg.source_policy in ("prefer_verified","psi_only"):
            if not callable(psi_verify_current) or psi_verify_current(psi) is not True:
                raise ASRError("psi_current_rights_unverified")
        if type(psi) is not dict or psi.get("schema_version")!="ga-psi-transcript-reuse/v1" or (
            psi.get("media_sha256")!=audio.source_sha256 or
            psi.get("complete_speech_coverage") is not False or
            psi.get("verified_no_speech") is not False or
            psi.get("status") not in ("partial","unavailable") or
            type(psi.get("segments")) is not list):
            raise ASRError("unverified_psi_transcript")
    if sidecar is not None:
        if not isinstance(sidecar,Sidecar) or sidecar.media_sha256!=audio.source_sha256:
            raise ASRError("sidecar_media_hash_mismatch")
    audio_absent=audio.probe.state=="absent"
    digital_silence=bool(audio.pcm) and not any(audio.pcm)
    duration=format(Decimal(audio.sample_count)/Decimal(16000),".3f")
    if video_duration_sec is not None:
        video_duration_sec=_second(video_duration_sec,allow_zero=False)
    # Video-local sidecar/PSI cues have an independent clock origin.
    # Never validate them against decoded AUDIO sample count.
    if ((psi is not None and cfg.source_policy in ("prefer_verified","psi_only"))
        or (sidecar is not None and cfg.source_policy in ("prefer_verified","sidecar_only"))):
        if video_duration_sec is None:
            raise ASRError("video_duration_required")
    # Source order is explicit: one (already rights-checked) PSI transcript,
    # then sidecar, then local faster-whisper only when policy permits.
    if cfg.source_policy in ("prefer_verified","psi_only") and psi is not None:
        if psi["status"]=="partial" and psi["segments"]:
            source_types={s.get("origin") for s in psi["segments"]}
            if len(source_types)!=1 or not source_types <= {"asr","srt"}:
                raise ASRError("invalid_psi_segment_source")
            kind=source_types.pop()
            if kind=="asr" and (audio_absent or digital_silence):
                raise ASRError("psi_audio_contradiction")
            rows=[{**s,"words":None} for s in psi["segments"]]
            segments=_normalize_segments(rows,duration=video_duration_sec,
                                         origin="psi_source_local_ms",language=psi.get("language",UNKNOWN))
            origin="psi_asr" if kind=="asr" else "psi_srt"
            return _build(audio,cfg,source=origin,status="partial",
                speech_status=("speech_detected" if kind=="asr" else
                               "no_audio_stream" if audio_absent else
                               "verified_silent" if digital_silence else "unknown"),
                language=psi.get("language",UNKNOWN),lang_source="psi",
                timing_origin="psi_source_local_ms",timing="segment_observed",
                segments=segments,untimed_text=None,upstream=psi["upstream"],
                model=None,reason="verified_psi_segments_reused")
    if cfg.source_policy in ("prefer_verified","sidecar_only") and sidecar is not None:
        side=read_sidecar(sidecar,duration=video_duration_sec)
        return _build(audio,cfg,source=side["origin"],status="partial",
            speech_status=("no_audio_stream" if audio_absent else
                           "verified_silent" if digital_silence else "unknown"),language=UNKNOWN,lang_source="unknown",
            timing_origin=side["timing_origin"],timing=side["timing_reliability"],
            segments=side["segments"],untimed_text=side["untimed_text"],
            upstream={"sidecar_sha256":sidecar.expected_sha256,"kind":sidecar.kind},
            model=None,reason="sidecar_text_not_verified_as_speech")
    if cfg.source_policy in ("psi_only","sidecar_only"):
        return _empty(audio,cfg,"selected_source_unavailable",
            speech_status="no_audio_stream" if audio_absent else
            "verified_silent" if digital_silence else "unknown")
    if audio_absent:
        return _empty(audio,cfg,"verified_no_audio_stream",speech_status="no_audio_stream")
    if not audio.pcm:
        return _empty(audio,cfg,"pcm_unavailable")
    if digital_silence:
        return _empty(audio,cfg,"verified_digital_silence",speech_status="verified_silent")
    if backend is None:
        return _empty(audio,cfg,"local_asr_backend_not_connected")
    if not isinstance(backend,ASRBackend):
        raise ASRError("invalid_asr_backend")
    raw=backend.transcribe(audio,cfg)
    if type(raw) is not dict or set(raw)!={"language","segments","model_sha256","model_version","engine_version"} or type(raw["segments"]) is not list:
        raise ASRError("invalid_local_asr_output")
    provenance=backend.provenance
    if (type(provenance) is not dict
        or raw["model_sha256"]!=provenance.get("sha256")
        or raw["model_version"]!=provenance.get("model_version")
        or raw["engine_version"]!=provenance.get("engine_version")):
        raise ASRError("asr_model_provenance_mismatch")
    lang=_language(raw["language"])
    if cfg.language!="auto" and lang!=cfg.language:
        raise ASRError("asr_language_mismatch")
    if cfg.language=="auto" and lang==UNKNOWN:
        lang_source="unknown"
    else:
        lang_source="model_detected" if cfg.language=="auto" else "configured"
    rows=[]
    for row in raw["segments"]:
        if type(row) is not dict or not {"start_sec","end_sec","text","words"}<=set(row):
            raise ASRError("invalid_local_asr_segment")
        rows.append({**row,"language":lang})
    segments=_normalize_segments(rows,duration=duration,origin="audio_local",language=lang)
    if not segments:
        return _empty(audio,cfg,"asr_no_observed_speech")
    fully_aligned=cfg.word_timestamps and all(s["word_timing_status"]=="observed" for s in segments)
    return _build(audio,cfg,source="local_faster_whisper",status="partial",
        speech_status="speech_detected",language=lang,lang_source=lang_source,
        timing_origin="audio_stream_local_zero",
        timing="word_observed" if fully_aligned else "segment_observed",
        segments=segments,untimed_text=None,upstream=None,
        model=provenance,reason="local_asr_observed_segments")
