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
from .extractors.text_tracking import TextTrackingError, TrackConfig, track_screen_text
from .extractors.subtitles import (
    SubtitleError, SubtitleConfig, SubtitleCalibration, DenseNegativeReview,
    detect_subtitles,
)
from .extractors.subtitle_probe import probe_subtitle_tracks, parse_subtitle_probe
from .extractors.subtitle_calibration import GoldSubtitleCase, evaluate_heldout_subtitle_rule
from .extractors.audio_events import AudioEventError, AudioEventConfig, AudioEventClassifier, observe_audio_events
from .extractors.onnx_audio_events import LocalONNXAudioClassifier, pinned_labelmap
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
         "TesseractAdapter","tesseract_fingerprint",
         "TextTrackingError","TrackConfig","track_screen_text",
         "SubtitleError","SubtitleConfig","SubtitleCalibration","DenseNegativeReview",
         "detect_subtitles","probe_subtitle_tracks","parse_subtitle_probe",
         "GoldSubtitleCase","evaluate_heldout_subtitle_rule",
         "AudioEventError","AudioEventConfig","AudioEventClassifier",
         "observe_audio_events","LocalONNXAudioClassifier","pinned_labelmap"]

from .extractors.carousel import CarouselError, extract_photo_carousel, validate_photo_observation
from .extractors.carousel_writer import persist_photo_carousel
__all__ += ["CarouselError", "extract_photo_carousel", "validate_photo_observation", "persist_photo_carousel"]

from .adapters.a003_slide import PinnedA003SlideReader
from .extractors.mixed_carousel import (
    extract_mixed_carousel, validate_mixed_observation, A003_BINDING,
)
from .extractors.mixed_carousel_writer import persist_mixed_carousel
__all__ += ["PinnedA003SlideReader", "extract_mixed_carousel",
            "validate_mixed_observation", "persist_mixed_carousel",
            "A003_BINDING"]

from .validation import SnapshotError, bind_technical
from .assembly import (
    assemble_video_snapshot, validate_video_snapshot, rebind_prepared_mixed,
)
from .snapshot_repository import persist_group_a_snapshot
__all__ += ["SnapshotError", "bind_technical", "assemble_video_snapshot",
            "validate_video_snapshot", "rebind_prepared_mixed",
            "persist_group_a_snapshot"]

from .adapters.group_b import (
    GroupBHandoffError, prepare_group_b_result, validate_prepared_handoff,
)
from .adapters.group_b_writer import persist_group_b_handoff
__all__ += ["GroupBHandoffError","prepare_group_b_result",
            "validate_prepared_handoff","persist_group_b_handoff"]

from .cost_resources import ResourceBudget, Stage, RuntimeDenied, plan_for_kind
from .task_queue import (
    StepClaim, install_run_dag, claim_next_step, finish_step,
    heartbeat_step, recover_worker_leases, lease_is_live,
    pause_run, reuse_verified_cache,
)
from .invalidation import invalidate_run, cancel_run, read_current_result
from .delivery import (
    DeliveryClaim, claim_outbox, acknowledge_delivery, fail_delivery,
    recover_outbox, deliver_once,
)
from .worker import LocalGAOrchestrator, LocalStepOutput, run_bounded_local_command
__all__ += [
    "ResourceBudget","Stage","RuntimeDenied","plan_for_kind","StepClaim",
    "install_run_dag","claim_next_step","finish_step","heartbeat_step",
    "recover_worker_leases","lease_is_live","pause_run",
    "reuse_verified_cache","invalidate_run","cancel_run",
    "read_current_result","DeliveryClaim","claim_outbox",
    "acknowledge_delivery","fail_delivery","recover_outbox","deliver_once",
    "LocalGAOrchestrator","LocalStepOutput","run_bounded_local_command",
]
