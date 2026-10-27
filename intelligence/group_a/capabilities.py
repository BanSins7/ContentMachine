"""Bootstrap capability boundary for a standalone Group A checkout.

GA-MOD-02: A1 remains usable for local, offline technical measurement.  No
QDP inbox/contract adapter is installed yet: do not silently claim that the
legacy QDP database tables exist or that background ingestion is ready.
GA-MOD-04 must replace this explicit deny-by-default handoff.
"""
from __future__ import annotations

import shutil
from .contracts import GroupAError
from .service import GroupAService
from .config import GASettings, settings_from_environment

CAPABILITY_SCHEMA = "group-a-capabilities/v1"
QDP_CONTRACT = "qdp-handoff/v1"
BOOTSTRAP_REASON = "qdp_handoff_not_integrated"


def live_handoff_ready() -> bool:
    """Never activate legacy QDP execution just because tables are present."""
    return False


def capability_snapshot() -> dict:
    available = all(shutil.which(name) for name in ("ffmpeg", "ffprobe"))
    try:
        settings = settings_from_environment()
        config_error = None
    except ValueError:
        # Config is invalid: never activate a service or expose raw env values.
        settings = GASettings()
        config_error = "invalid_feature_flags"
    plan = GroupAService(settings=settings).capabilities()
    if config_error is not None:
        plan["reason_codes"].insert(0, config_error)
    return {
        "ok": True,
        "schema_version": CAPABILITY_SCHEMA,
        "module_stage": "GA-MOD-15",
        "audio_events": {
            "contract": "ga-audio-events/v1",
            "method": "group-a-audio-events-local-v1",
            "state": "explicit_offline_only",
            "model": "no_bundled_weights_optional_licensed_pinned_onnx",
            "classifier_engines": ["yamnet_onnx", "panns_onnx"],
            "cpu_only": True,
            "license_authorization_required": True,
            "source": "ga09_verified_decoded_pcm",
            "allowed_events": ["music", "sound_effect", "other"],
            "presence_false_only_for": "verified_no_track_or_digital_silence",
            "multi_label_speech_overlap": True,
            "full_video_acoustic_coverage": False,
            "class_probability_calibrated": False,
            "a003_sql_writer": False,
            "automatic_worker": False,
        },
        "subtitle_calibration": {
            "method": "group-a-subtitle-ocr-asr-rule-v1",
            "mode": "preselected_threshold_holdout_en_ru",
            "precision_gate": "stratified_wilson_lower_95_percent",
            "requires_authoritative_human_gold": True,
            "gold_corpus_bundled": False,
            "production_calibration_verified": False,
            "offline_only": True,
        },
        "subtitle_detection": {
            "contract": "ga-subtitle-presence/v1",
            "method_version": "group-a-subtitle-truth-v1",
            "state": "explicit_offline_only",
            "a003_field": "has_subtitles",
            "values": ["true","false","unknown"],
            "positive_evidence": ["verified_embedded_subtitle_packets",
                                   "verified_timed_srt",
                                   "externally_calibrated_ocr_asr"],
            "negative_evidence": "authenticated_dense_full_video_review_only",
            "sparse_ocr_false": False,
            "subtitle_style_ownership": "group_b",
            "automatic_worker": False,
            "a003_sql_write": False,
        },
        "screen_text_tracking": {
            "contract": "ga-screen-text-tracking/v1",
            "method_version": "group-a-screen-text-tracking-v1",
            "state": "explicit_offline_only",
            "evidence": "ga12_ocr_ga07_sampled_instants",
            "reappearing_occurrences": "gap_sensitive_distinct",
            "temporal_coverage": "unknown",
            "continuous_intervals_verified": False,
            "first_text_true_onset": "unknown",
            "a003_text_writer": "blocked_pending_continuity_proof_ga18",
            "automatic_worker": False,
        },
        "frame_ocr": {
            "contract": "ga-frame-ocr/v1",
            "method_version": "group-a-local-frame-ocr-v1",
            "state": "explicit_offline_only",
            "available_engines": ["tesseract_cpu"],
            "paddleocr": "not_installed_no_silent_fallback",
            "tessdata_policy": "pinned_tesseract_binary_and_eng_rus_models_sha256",
            "coverage": "sparse_frames_only",
            "ocr_temporal_coverage": "unknown",
            "cpu_threads": "config_limited_1_to_4",
            "gpu": "not_claimed_for_tesseract",
            "automatic_worker": False,
            "a003_text_writer": False,
        },
        "speech_metrics": {
            "contract": "ga-speech-metrics/v1",
            "method_version": "group-a-speech-metrics-v1",
            "state": "explicit_offline_only",
            "transcript_policy": "asr_audible_only",
            "word_policy": "unicode_nfc_alnum_apostrophe_v1",
            "active_duration": "asr_union_or_correlated_vad_candidates",
            "video_coverage": "requires_proven_audio_or_psi_video_clock",
            "complete_speech_coverage": False,
            "a003_direct_write": False,
            "automatic_worker": False,
        },
        "asr": {
            "contract": "ga-asr-transcript/v1",
            "method_version": "group-a-asr-pipeline-v1",
            "state": "explicit_offline_only",
            "engine": "faster-whisper",
            "weights": "host_local_inventory_sha256",
            "resource_lease": "required_for_local_asr",
            "model_autodownload": False,
            "word_alignment": "only_if_observed",
            "source_priority": ["verified_psi", "verified_srt_or_txt", "local_asr"],
            "timebases": ["audio_stream_local_zero", "video_local", "psi_source_local_ms"],
            "automatic_worker": False,
        },
        "audio_decode": {
            "contract": "ga-audio-decode/v1",
            "state": "offline_only",
            "sample_rate": 16000,
            "channel_count": 1,
            "source_proof": "ga05_verified_local_media",
            "distinguish_no_track_and_digital_silence": True,
            "automatic_worker": False,
        },
        "vad": {
            "contract": "ga-vad-observation/v1",
            "method_version": "group-a-vad-silero-jit-v1",
            "state": "optional_model_not_installed_by_default",
            "model_policy": "explicit_local_jit_sha256_cpu_or_cuda",
            "auto_download": False,
            "asr_or_word_counts": False,
            "verified_no_speech_from_no_candidates": False,
        },
        "psi_reuse": {
            "transcript_peer_version": "transcript/v1",
            "vision_peer_version": "vision-prepare/v1",
            "vision_port_version": "visual-preparation/v1",
            "state": "opt_in_unwired",
            "cache_mode": "source_hash_and_current_rights_fenced",
            "auto_paid_ai": False,
            "full_ocr_claim": False,
        },
        "frame_sampling": {
            "contract": "group-a-frame-manifest/v1",
            "state": "offline_only",
            "coverage": "sparse_observations",
            "ocr_coverage": "unknown",
            "storage": "atomic_local_files_not_sql",
            "automatic_worker": False,
        },
        "technical_extraction": {
            "method_version": "group-a-ffmpeg-technical-v2",
            "state": "offline_only",
            "strict_roots_required": True,
            "a003_default_state": "partial",
            "automated_worker": False,
        },
        "media_input": {
            "contract": "ga-media-manifest/v1",
            "state": "opt_in_unwired",
            "verification": "sha256_size_magic_fd_roots",
            "allow_network_acquisition": False,
        },
        "qdp_intake": {
            "schema_version": "qdp.preparation_ready/v1",
            "state": "opt_in_unwired",
            "ack_schema_version": "group-a-receipt/v1",
            "automatic_ingest": False,
        },
        "adapter_readiness": plan,
        "qdp_handoff": {
            "contract": QDP_CONTRACT,
            "state": "unavailable",
            "reason_code": BOOTSTRAP_REASON,
        },
        "runtime": {
            "state": "disabled",
            "reason_code": BOOTSTRAP_REASON,
        },
        "technical_a1": {
            "state": "tools_detected" if available else "tools_unavailable",
            "mode": "local_only",
            "result_scope": "duration_aspect_and_technical_scenes_only",
        },
    }


def require_live_handoff() -> None:
    """Gate network-facing API and automatic workers during bootstrap."""
    raise GroupAError(
        BOOTSTRAP_REASON,
        "Group A requires a versioned QDP handoff; standalone bootstrap is offline-only",
        503,
    )
