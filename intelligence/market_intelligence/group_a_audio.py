"""Marketing-owned full audio execution using *copied* Group A algorithms.

Decodes a SHA-verified MP4 exactly once. Independently runs local VAD, ASR,
speech metrics and sound-event analysis. Audio stays in RAM; emitted results
contain only timestamped, provenance-hashed observations. Optional local model
files must be preinstalled and explicitly pinned; no downloads or QDP.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import json
import threading

from intelligence import config
from ._copied.group_a.extractors.audio_decode import decode_audio
from ._copied.group_a.extractors.vad import VADConfig, LocalSileroJIT, observe_vad
from ._copied.group_a.extractors.asr import ASRConfig, transcribe
from ._copied.group_a.extractors.local_asr import LocalASRProvenance, LocalFasterWhisper
from ._copied.group_a.extractors.speech_metrics import measure_speech
from ._copied.group_a.extractors.audio_events import AudioEventConfig, observe_audio_events
from ._copied.group_a.extractors.onnx_audio_events import LocalONNXAudioClassifier

MODEL_LOCK=threading.Lock()


class _CPUModelLease:
    @contextmanager
    def acquire(self, *, kind, device):
        if kind not in ("asr","audio_events") or device!="cpu":
            raise ValueError("invalid_cpu_model_lease")
        with MODEL_LOCK:
            yield


def _setting(key):
    return config.setting(key,"").strip()


def _reason(exc):
    # A stable error code without raw paths, API keys or model exceptions.
    return getattr(exc,"code",None) or getattr(exc,"reason",None) or type(exc).__name__


def _unavailable(stage,reason):
    return {"state":"unavailable","stage":stage,"reason":reason}


def _vad_model():
    if _setting("CM_MARKET_VAD_ENABLED")!="1":
        return None
    path=_setting("CM_MARKET_VAD_MODEL_PATH")
    sha=_setting("CM_MARKET_VAD_MODEL_SHA256")
    version=_setting("CM_MARKET_VAD_MODEL_VERSION")
    if not all((path,sha,version)):
        raise ValueError("vad_model_provenance_not_configured")
    return LocalSileroJIT(path,expected_sha256=sha,version=version,device="cpu")


def _asr_model():
    if _setting("CM_MARKET_ASR_ENABLED")!="1":
        return None
    fields={k:_setting(v) for k,v in {
        "model_dir":"CM_MARKET_ASR_MODEL_DIR",
        "model_sha256":"CM_MARKET_ASR_MODEL_SHA256",
        "model_version":"CM_MARKET_ASR_MODEL_VERSION",
        "engine_version":"CM_MARKET_ASR_ENGINE_VERSION",
    }.items()}
    if not all(fields.values()):
        raise ValueError("asr_model_provenance_not_configured")
    return LocalFasterWhisper(LocalASRProvenance(**fields),lease=_CPUModelLease())


def _events_model():
    if _setting("CM_MARKET_AUDIO_EVENTS_ENABLED")!="1":
        return None
    if _setting("CM_MARKET_AUDIO_EVENTS_LICENSE_AUTHORIZED")!="1":
        raise ValueError("audio_event_model_license_not_authorized")
    try:
        groups=json.loads(_setting("CM_MARKET_AUDIO_EVENTS_LABELMAP_JSON"))
    except (TypeError,ValueError):
        raise ValueError("audio_event_labelmap_invalid") from None
    return LocalONNXAudioClassifier(
        model_path=_setting("CM_MARKET_AUDIO_EVENTS_MODEL_PATH"),
        expected_sha256=_setting("CM_MARKET_AUDIO_EVENTS_MODEL_SHA256"),
        model_version=_setting("CM_MARKET_AUDIO_EVENTS_MODEL_VERSION"),
        engine=_setting("CM_MARKET_AUDIO_EVENTS_ENGINE"),
        label_groups=groups,
        input_layout=_setting("CM_MARKET_AUDIO_EVENTS_INPUT_LAYOUT") or "batch_samples",
        lease=_CPUModelLease(),
        expected_labelmap_sha256=_setting("CM_MARKET_AUDIO_EVENTS_LABELMAP_SHA256"),
        verify_license=lambda claim: (
            claim.get("license_authorization")=="host_verified"
            and claim.get("model_sha256")==_setting("CM_MARKET_AUDIO_EVENTS_MODEL_SHA256")
            and _setting("CM_MARKET_AUDIO_EVENTS_LICENSE_AUTHORIZED")=="1"
        ),
    )


def inspect_audio(source, *, roots, video_probe, verify_current, decoder=None,
                  vad_model_factory=None, asr_model_factory=None,
                  audio_events_model_factory=None):
    """Run every Group A audio algorithm; a failure in one does not hide others.

    verify_current is a Marketing host callback for this source's exact
    authorized media/version; SHA strings alone never grant permissions.
    Injectable model factories are for offline tests, not network fallbacks.
    """
    if not callable(verify_current) or verify_current(
        {"source_media_sha256":source.sha256,"scope":"mi_audio"}) is not True:
        raise ValueError("mi_audio_current_rights_unverified")
    result={"schema_version":"mi-audio-analysis/v1",
            "source_media_sha256":source.sha256,
            "decode":_unavailable("decode","not_started"),
            "vad":_unavailable("vad","no_audio"),
            "asr":_unavailable("asr","no_audio"),
            "speech_metrics":_unavailable("speech_metrics","no_audio"),
            "audio_events":_unavailable("audio_events","no_audio"),
            "transcript_segments":[],
            "errors":[]}
    try:
        audio=(decoder or decode_audio)(source,roots=roots)
        result["decode"]=audio.wire()
        if audio.source_sha256!=source.sha256:
            raise ValueError("decoded_audio_source_sha_mismatch")
    except Exception as exc:
        result["decode"]=_unavailable("decode",_reason(exc))
        result["errors"].append({"stage":"decode","reason":_reason(exc)})
        return result

    try:
        model=(vad_model_factory or _vad_model)()
        # VAD is useful even when the ASR backend is not available.
        with MODEL_LOCK:
            vad=observe_vad(audio,model=model,config=VADConfig())
        result["vad"]=vad
    except Exception as exc:
        vad=None
        result["vad"]=_unavailable("vad",_reason(exc))
        result["errors"].append({"stage":"vad","reason":_reason(exc)})

    try:
        backend=(asr_model_factory or _asr_model)()
        asr=transcribe(audio,config=ASRConfig(
            source_policy="local_only",device="cpu",word_timestamps=False),
            backend=backend, vad=vad,video_duration_sec=video_probe.duration)
        result["asr"]={k:v for k,v in asr.items() if k!="segments"}
        result["transcript_segments"]=asr["segments"]
    except Exception as exc:
        asr=None
        result["asr"]=_unavailable("asr",_reason(exc))
        result["errors"].append({"stage":"asr","reason":_reason(exc)})

    if asr is not None:
        try:
            result["speech_metrics"]=measure_speech(
                audio,asr,video_probe=video_probe,vad=vad)
        except Exception as exc:
            result["speech_metrics"]=_unavailable("speech_metrics",_reason(exc))
            result["errors"].append({"stage":"speech_metrics","reason":_reason(exc)})

    try:
        model=(audio_events_model_factory or _events_model)()
        if verify_current({"source_media_sha256":source.sha256,"scope":"mi_audio_events"}) is not True:
            raise ValueError("mi_audio_event_current_rights_unverified")
        result["audio_events"]=observe_audio_events(
            audio,classifier=model,config=AudioEventConfig(),
            video_probe=video_probe,
            verify_current=lambda context: (
                context.get("source_media_sha256")==source.sha256 and
                verify_current({"source_media_sha256":source.sha256,"scope":"mi_audio_events"}) is True
            ),
            verify_classifier=lambda claim: (
                claim.get("license_authorization")=="host_verified" and
                claim.get("model_sha256")==_setting("CM_MARKET_AUDIO_EVENTS_MODEL_SHA256") and
                _setting("CM_MARKET_AUDIO_EVENTS_LICENSE_AUTHORIZED")=="1"
            ))
    except Exception as exc:
        result["audio_events"]=_unavailable("audio_events",_reason(exc))
        result["errors"].append({"stage":"audio_events","reason":_reason(exc)})
    return result
