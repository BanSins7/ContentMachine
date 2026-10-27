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

from .ocr import OCRConfig, OCRError, OCRBackend, OCRCache, analyze_frame_ocr
from .tesseract_ocr import TesseractAdapter, tesseract_fingerprint
__all__ += ["OCRConfig","OCRError","OCRBackend","OCRCache","analyze_frame_ocr",
            "TesseractAdapter","tesseract_fingerprint"]

from .text_tracking import TextTrackingError, TrackConfig, track_screen_text
__all__ += ["TextTrackingError","TrackConfig","track_screen_text"]

from .subtitles import (
    SubtitleError, SubtitleConfig, SubtitleCalibration, DenseNegativeReview,
    detect_subtitles,
)
from .subtitle_probe import probe_subtitle_tracks, parse_subtitle_probe
__all__ += ["SubtitleError","SubtitleConfig","SubtitleCalibration",
            "DenseNegativeReview","detect_subtitles","probe_subtitle_tracks",
            "parse_subtitle_probe"]

from .subtitle_calibration import GoldSubtitleCase, evaluate_heldout_subtitle_rule
__all__ += ["GoldSubtitleCase","evaluate_heldout_subtitle_rule"]

from .audio_events import AudioEventError, AudioEventConfig, AudioEventClassifier, observe_audio_events
from .onnx_audio_events import LocalONNXAudioClassifier, pinned_labelmap
__all__ += ["AudioEventError","AudioEventConfig","AudioEventClassifier",
            "observe_audio_events","LocalONNXAudioClassifier","pinned_labelmap"]

from .carousel import CarouselError, extract_photo_carousel, validate_photo_observation
from .carousel_writer import persist_photo_carousel
__all__ += ["CarouselError", "extract_photo_carousel", "validate_photo_observation", "persist_photo_carousel"]

from .mixed_carousel import (
    extract_mixed_carousel, validate_mixed_observation, A003_BINDING,
)
from .mixed_carousel_writer import persist_mixed_carousel
__all__ += ["extract_mixed_carousel", "validate_mixed_observation",
            "persist_mixed_carousel", "A003_BINDING"]
