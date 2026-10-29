"""GA-MOD-09: explicit, model-pinned offline Silero VAD and interval union.

Only estimates candidate speech activity. Does not transcribe words, measure
WPM, infer languages, or claim calibrated speech confidence / verified
absence from an empty list of probabilities. No torch/PyTorch import at
module import and NEVER torch.hub/download/openrouter.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import math
import re
import sys

from ..contracts_v1 import canonical_sha256
from .audio_decode import DecodedAudio, pcm_int16, SAMPLE_RATE, AudioDecodeError

VAD_SCHEMA = "ga-vad-observation/v1"
VAD_METHOD = "group-a-vad-silero-jit-v1"
WINDOW_SAMPLES = 512
WINDOW_MS = Decimal(WINDOW_SAMPLES) * Decimal(1000) / Decimal(SAMPLE_RATE)
MAX_WINDOWS = 19_000
SHA = re.compile(r"^[a-f0-9]{64}$")


class VADError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__("Group A VAD failed: " + code)


@dataclass(frozen=True, slots=True)
class VADConfig:
    speech_threshold: str = "0.55"
    release_threshold: str = "0.40"
    min_speech_ms: int = 120
    min_silence_ms: int = 160
    pad_ms: int = 64
    merge_gap_ms: int = 120
    device: str = "cpu"          # opt-in cuda, no silent GPU fallback

    def __post_init__(self):
        try:
            high = Decimal(self.speech_threshold)
            low = Decimal(self.release_threshold)
        except Exception as exc:
            raise VADError("invalid_vad_config") from exc
        if (type(self.speech_threshold) is not str
            or type(self.release_threshold) is not str
            or not high.is_finite() or not low.is_finite()
            or not Decimal("0.05") <= low < high <= Decimal("0.99")
            or type(self.min_speech_ms) is not int or not 32 <= self.min_speech_ms <= 3000
            or type(self.min_silence_ms) is not int or not 32 <= self.min_silence_ms <= 2000
            or type(self.pad_ms) is not int or not 0 <= self.pad_ms <= 300
            or type(self.merge_gap_ms) is not int or not 0 <= self.merge_gap_ms <= 1000
            or self.device not in ("cpu", "cuda")):
            raise VADError("invalid_vad_config")

    def wire(self):
        return {
            "speech_threshold": self.speech_threshold,
            "release_threshold": self.release_threshold,
            "min_speech_ms": self.min_speech_ms,
            "min_silence_ms": self.min_silence_ms,
            "pad_ms": self.pad_ms,
            "merge_gap_ms": self.merge_gap_ms,
            "device": self.device,
            "window_samples": WINDOW_SAMPLES,
            "sample_rate": SAMPLE_RATE,
        }


@dataclass(frozen=True, slots=True)
class ModelProvenance:
    name: str
    version: str
    sha256: str
    device: str

    def __post_init__(self):
        if (self.name != "silero_vad"
            or type(self.version) is not str
            or not re.fullmatch(r"silero-vad/[A-Za-z0-9._-]{1,50}",self.version)
            or type(self.sha256) is not str or not SHA.fullmatch(self.sha256)
            or self.device not in ("cpu", "cuda")):
            raise VADError("unverified_vad_model")

    def wire(self):
        return {"name":self.name,"version":self.version,"sha256":self.sha256,"device":self.device}


class LocalSileroJIT:
    """Explicit local torch.jit Silero model with exact SHA and CUDA validation.

    The host obtains/licences the .jit model outside GA. GA refuses any
    missing/changed model; it never contacts the internet or uses torch.hub.
    Silero input contract: 512 mono float32 samples @16 kHz per window.
    Recurrent JIT model states are reset once for each different source.
    """
    def __init__(self, file, *, expected_sha256, version, device="cpu"):
        if type(file) is not str or not Path(file).is_absolute():
            raise VADError("vad_model_path_invalid")
        if type(expected_sha256) is not str or not SHA.fullmatch(expected_sha256):
            raise VADError("unverified_vad_model")
        path=Path(file)
        if path.is_symlink() or not path.is_file():
            raise VADError("vad_model_unavailable")
        if path.stat().st_size > 100 * 1024**2:
            raise VADError("vad_model_size_limit")
        self.provenance=ModelProvenance("silero_vad",version,expected_sha256,device)
        try:
            import torch
        except ImportError as exc:
            raise VADError("torch_not_installed") from exc
        if device=="cuda" and not torch.cuda.is_available():
            raise VADError("cuda_unavailable")
        # Hash and load the same open file descriptor; prevent path-swap
        # between model byte verification and torch.jit deserialization.
        try:
            with path.open("rb") as stream:
                sha=sha256()
                for data in iter(lambda:stream.read(1024 * 1024),b""):
                    sha.update(data)
                if sha.hexdigest()!=expected_sha256:
                    raise VADError("vad_model_hash_mismatch")
                stream.seek(0)
                model=torch.jit.load(stream,map_location=device)
                model.eval()
        except VADError:
            raise
        except Exception as exc:
            raise VADError("invalid_vad_model") from exc
        self._torch,self._model=torch,model

    def probabilities(self, pcm: bytes):
        samples=pcm_int16(pcm)
        try:
            self._model.reset_states()
        except Exception as exc:
            raise VADError("vad_state_reset_failed") from exc
        values=[]
        torch=self._torch
        with torch.inference_mode():
            for start in range(0,len(samples),WINDOW_SAMPLES):
                block=samples[start:start+WINDOW_SAMPLES]
                array_data=[float(x)/32768.0 for x in block]
                if len(array_data)<WINDOW_SAMPLES:
                    array_data.extend([0.0]*(WINDOW_SAMPLES-len(array_data)))
                try:
                    value=self._model(
                        torch.tensor(array_data, dtype=torch.float32,device=self.provenance.device),
                        SAMPLE_RATE,
                    )
                    prob=float(value.item())
                except Exception as exc:
                    raise VADError("vad_inference_failed") from exc
                values.append(prob)
        return values


def _validated_probabilities(values, expected_count):
    if type(values) not in (list, tuple) or len(values)!=expected_count:
        raise VADError("vad_incomplete_window_coverage")
    for p in values:
        if type(p) not in (float, int) or not math.isfinite(p) or not 0 <= p <= 1:
            raise VADError("vad_invalid_probability")
    return [Decimal(str(x)) for x in values]


def _union_spans(spans, *, length_ms, merge_gap_ms=0):
    """Deterministic sorted union; clip padding to actual decoded sample extent."""
    if type(length_ms) is not int or length_ms<0:
        raise VADError("invalid_vad_duration")
    rows=[]
    for a,b in spans:
        if type(a) is not int or type(b) is not int or a<0 or b<a:
            raise VADError("invalid_vad_interval")
        start=max(0,min(length_ms,a))
        end=max(0,min(length_ms,b))
        if end>start:
            rows.append((start,end))
    merged=[]
    for a,b in sorted(rows):
        if merged and a<=merged[-1][1]+merge_gap_ms:
            merged[-1]=(merged[-1][0],max(merged[-1][1],b))
        else:
            merged.append((a,b))
    return merged


def detect_intervals(values, *, total_samples, config):
    """Hysteresis state machine, overlap/gap union, no fabricated timestamps."""
    if not isinstance(config,VADConfig) or type(total_samples) is not int or total_samples<0:
        raise VADError("invalid_vad_input")
    size_ms=(Decimal(total_samples)*1000)/SAMPLE_RATE
    length_ms=int(size_ms)
    p=_validated_probabilities(values,(total_samples+WINDOW_SAMPLES-1)//WINDOW_SAMPLES)
    high,low=Decimal(config.speech_threshold),Decimal(config.release_threshold)
    active=False
    start=0
    silence_start=None
    candidates=[]
    for idx,score in enumerate(p):
        t=int(Decimal(idx)*WINDOW_MS)
        end=min(length_ms,int(Decimal(idx+1)*WINDOW_MS))
        if not active:
            if score>=high:
                active=True;start=t;silence_start=None
        elif score<low:
            if silence_start is None:
                silence_start=t
            if end-silence_start>=config.min_silence_ms:
                if silence_start-start>=config.min_speech_ms:
                    candidates.append((start,silence_start))
                active=False;silence_start=None
        else:
            silence_start=None
    if active:
        until=silence_start if silence_start is not None else length_ms
        if until-start>=config.min_speech_ms:
            candidates.append((start,until))
    padded=[(max(0,a-config.pad_ms),min(length_ms,b+config.pad_ms)) for a,b in candidates]
    return _union_spans(padded,length_ms=length_ms,merge_gap_ms=config.merge_gap_ms)


def observe_vad(audio:DecodedAudio, *, model=None, config=None):
    """No model=> unavailable. Silero probabilities are not a transcript.

    Digitally-zero PCM is a verified silence observation (independent of
    voice model). Other low energy or no VAD positives is NOT speech absence.
    """
    if not isinstance(audio,DecodedAudio):
        raise VADError("decoded_audio_required")
    cfg=config if config is not None else VADConfig()
    if not isinstance(cfg,VADConfig):
        raise VADError("invalid_vad_config")
    if audio.probe.state=="absent":
        state="no_audio_stream";status="unavailable";reason="verified_audio_stream_absent"
        evidence=[];checked=0;model_info=None;verified_no_speech=True
    elif audio.sample_count==0:
        state="unobservable";status="unavailable";reason="decoded_audio_empty"
        evidence=[];checked=0;model_info=None;verified_no_speech=False
    elif not any(pcm_int16(audio.pcm)):
        state="digital_silence";status="complete";reason=None
        evidence=[];checked=0;model_info=None;verified_no_speech=True
    elif model is None:
        state="model_unavailable";status="unavailable";reason="vad_model_not_connected"
        evidence=[];checked=0;model_info=None;verified_no_speech=False
    else:
        provenance=getattr(model,"provenance",None)
        if not isinstance(provenance,ModelProvenance) or provenance.device!=cfg.device:
            raise VADError("vad_provenance_or_device_mismatch")
        if not callable(getattr(model,"probabilities",None)):
            raise VADError("vad_model_interface_missing")
        expected=(audio.sample_count+WINDOW_SAMPLES-1)//WINDOW_SAMPLES
        if expected>MAX_WINDOWS:
            raise VADError("vad_window_limit")
        values=model.probabilities(audio.pcm)
        probabilities=_validated_probabilities(values,expected)
        spans=detect_intervals(values,total_samples=audio.sample_count,config=cfg)
        evidence=[{"start_ms":a,"end_ms":b,
                   "start_sec":f"{Decimal(a)/1000:.3f}",
                   "end_sec":f"{Decimal(b)/1000:.3f}"}
                  for a,b in spans]
        checked=expected
        state="speech_candidates" if evidence else "no_candidates"
        status="partial"
        reason="candidate_speech_not_asr" if evidence else "vad_no_candidates"
        model_info=provenance.wire()
        verified_no_speech=False
    core={
        "schema_version":VAD_SCHEMA,"method_version":VAD_METHOD,
        "source_media_sha256":audio.source_sha256,
        "pcm_sha256":sha256(audio.pcm).hexdigest() if audio.pcm else None,
        "time_origin":"audio_stream_local_zero",
        "state":state,"status":status,"reason_code":reason,
        "verified_no_speech":verified_no_speech,
        "speech_status":"not_run",
        "sampling_rate":SAMPLE_RATE,
        "source_duration_ms":audio.duration_ms,
        "processed_windows":checked,
        "model":model_info,"config":cfg.wire(),
        "intervals":evidence,
        "active_speech_ms":sum(i["end_ms"]-i["start_ms"] for i in evidence),
        "speech_interval_coverage":"candidate_only" if evidence else "unknown",
    }
    return {**core,"observation_sha256":canonical_sha256(core)}
