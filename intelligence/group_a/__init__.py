"""Local Group A stage A1 (FFprobe/FFmpeg geometry, duration and scene timeline)."""
from .contracts import GroupAError, TechnicalMeasurement, Scene, parse_config
from .repository import create_task, task_details, list_tasks, work_once
from .contracts_v1 import (ContractViolation, qdp_handoff_v1, media_manifest_v1,
                           stream_truth_v1, group_a_to_b_v1, ensure_same_lineage)
from .ports import GAPorts, PeerCapability, negotiate
from .config import GASettings, settings_from_environment
from .service import GroupAService, NotReady
from .storage_pg import (migration_snapshot, PostgreSQLGroupAStore,
                         PostgreSQLGroupATaskQueue)
from .handoff import GroupAInboxReceiver, HandoffRejected, validate_event
from .adapters.qdp import PostgresQDPReader, CallbackQDPReader
from .adapters.media_assets import VerifiedMediaCatalog
from .adapters.psi_reuse import (
    PostgresPSIReader, PSIAssetBinding, PSIReuseCache, PSIReuseError,
    ms_to_seconds, seconds_to_ms, normalize_transcript, normalize_vision,
)
from .media_manifest import (MediaVerificationError, VerifiedFile,
                             verify_file, open_verified, build_manifest)
from .extractors.technical import ProbeInfo, TechnicalEvidence, analyze_technical
from .extractors.frames import FrameError, SampleConfig, plan_timestamps, sample_frames
from .extractors.frame_store import FrameStore
from .extractors.audio_decode import (
    AudioDecodeError, AudioProbe, DecodedAudio, decode_audio, parse_audio_probe,
)
from .extractors.vad import (
    VADError, VADConfig, ModelProvenance, LocalSileroJIT,
    detect_intervals, observe_vad,
)
from .extractors.asr import ASRError, ASRConfig, ASRBackend, Sidecar, read_sidecar, transcribe
from .extractors.local_asr import LocalASRProvenance, LocalFasterWhisper
from .extractors.speech_metrics import (
    SpeechMetricsError, SpeechMetricsConfig, PSITimelineBinding,
    measure_speech, count_observed_words, union_intervals,
)
from .extractors.ocr import OCRConfig, OCRError, OCRBackend, OCRCache, analyze_frame_ocr
from .extractors.tesseract_ocr import TesseractAdapter, tesseract_fingerprint
__all__=["GroupAError","TechnicalMeasurement","Scene","parse_config",
         "create_task","task_details","list_tasks","work_once",
         "ContractViolation","qdp_handoff_v1","media_manifest_v1",
         "stream_truth_v1","group_a_to_b_v1","ensure_same_lineage",
         "GAPorts","PeerCapability","negotiate","GASettings",
         "settings_from_environment","GroupAService","NotReady",
         "migration_snapshot","PostgreSQLGroupAStore","PostgreSQLGroupATaskQueue",
         "GroupAInboxReceiver","HandoffRejected","validate_event",
         "PostgresQDPReader","CallbackQDPReader","VerifiedMediaCatalog",
         "MediaVerificationError","VerifiedFile","verify_file",
         "open_verified","build_manifest",
         "ProbeInfo","TechnicalEvidence","analyze_technical",
         "FrameError","SampleConfig","plan_timestamps","sample_frames","FrameStore",
         "PostgresPSIReader","PSIAssetBinding","PSIReuseCache","PSIReuseError",
         "ms_to_seconds","seconds_to_ms","normalize_transcript","normalize_vision",
         "AudioDecodeError","AudioProbe","DecodedAudio","decode_audio",
         "parse_audio_probe","VADError","VADConfig","ModelProvenance",
         "LocalSileroJIT","detect_intervals","observe_vad",
         "ASRError","ASRConfig","ASRBackend","Sidecar","read_sidecar",
         "transcribe","LocalASRProvenance","LocalFasterWhisper",
         "SpeechMetricsError","SpeechMetricsConfig","PSITimelineBinding",
         "measure_speech","count_observed_words","union_intervals",
         "OCRConfig","OCRError","OCRBackend","OCRCache","analyze_frame_ocr",
         "TesseractAdapter","tesseract_fingerprint"]
