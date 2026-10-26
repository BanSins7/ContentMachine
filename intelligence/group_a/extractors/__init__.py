"""Offline-only, bounded Group A extractors; no QDP or paid provider calls."""
from .technical import (
    ProbeInfo, TechnicalEvidence, probe_details, inspect_probe,
    parse_cuts, measure, analyze_technical,
)

__all__ = [
    "ProbeInfo", "TechnicalEvidence", "probe_details", "inspect_probe",
    "parse_cuts", "measure", "analyze_technical",
]

from .frames import (
    FRAME_SCHEMA, FRAME_METHOD, SampleConfig, FrameError,
    plan_timestamps, sample_frames,
)
from .frame_store import FrameStore
__all__ += [
    "FRAME_SCHEMA","FRAME_METHOD","SampleConfig","FrameError",
    "plan_timestamps","sample_frames","FrameStore",
]

from .audio_decode import (
    AudioDecodeError, AudioProbe, DecodedAudio, parse_audio_probe, decode_audio,
)
from .vad import (
    VADError, VADConfig, ModelProvenance, LocalSileroJIT,
    detect_intervals, observe_vad,
)
__all__ += [
    "AudioDecodeError", "AudioProbe", "DecodedAudio", "parse_audio_probe",
    "decode_audio", "VADError", "VADConfig", "ModelProvenance",
    "LocalSileroJIT", "detect_intervals", "observe_vad",
]

from .asr import ASRError, ASRConfig, ASRBackend, Sidecar, read_sidecar, transcribe
from .local_asr import LocalASRProvenance, LocalFasterWhisper
__all__ += ["ASRError","ASRConfig","ASRBackend","Sidecar","read_sidecar",
            "transcribe","LocalASRProvenance","LocalFasterWhisper"]

from .speech_metrics import (
    SpeechMetricsError, SpeechMetricsConfig, PSITimelineBinding,
    measure_speech, count_observed_words, union_intervals,
)
__all__ += ["SpeechMetricsError","SpeechMetricsConfig","PSITimelineBinding",
            "measure_speech","count_observed_words","union_intervals"]
