"""Market Intelligence standalone real media observations from copied Group A.

Canonical project-media rights and hash are checked in media_worker. This
module RE-verifies actual bytes against configured roots, runs copied A
technical/frames/OCR, and only then optionally invokes grounded visual
inference with the copied B taxonomy. No Group A/B/C external imports.
"""
from __future__ import annotations
import os
from pathlib import Path
from uuid import uuid4
from contextlib import contextmanager
from threading import BoundedSemaphore
from ._copied.group_a.media_manifest import verify_file
from ._copied.group_a.extractors.frames import sample_frames, SampleConfig
from ._copied.group_a.extractors.ocr import analyze_frame_ocr
from ._copied.group_a.extractors.tesseract_ocr import TesseractAdapter
from ._copied.group_a.extractors.technical import analyze_technical as original_analyze_technical
from intelligence import config

FRAME_BUDGET = 48

def _ocr_backend():
    pin=config.setting("CM_MARKET_TESSERACT_PIN", "").strip()
    binary=config.setting("CM_MARKET_TESSERACT_BINARY", "").strip()
    data=config.setting("CM_MARKET_TESSERACT_DATA", "").strip()
    if not (pin and binary and data):
        return None
    return TesseractAdapter(binary=binary,tessdata_dir=data,expected_sha256=pin)

def analyze_media(local:Path, sha256:str, *,roots:list[str],project_id:str,
                  srt_rows:list[dict],technical=None,
                  allow_provider:bool=False) -> tuple[object,dict]:
    """Perform actual copied-A algorithms, optionally real inference.

    Returns copied-A TechnicalEvidence and an MI creative observation.
    If visual sampling/OCR/provider is unavailable, the output explicitly
    records UNKNOWN; no fabricated hook, CTA or visual claim.
    """
    source=verify_file(str(local),media_kind="video",sha256=sha256,roots=roots)
    tech=technical if technical is not None else original_analyze_technical(
        local,strict=True,roots=roots)
    visual={"state":"unavailable","reason":"frame_sampling_unavailable",
            "frame_count":0,"frames":[],"screen_text":[],"ocr_state":"unavailable",
            "source_media_sha256":sha256,"visual_classification_state":"unknown"}
    # Dense-enough bounded sampling relative to duration, without an
    # unnecessary 48 independent FFmpeg seeks for a 2–3 second publication.
    sample_budget=min(FRAME_BUDGET,max(12,int(float(tech.measurement.duration_sec)*2)))
    try:
        manifest,blobs=sample_frames(source,tech.probe,roots=roots,
            scenes=tech.measurement.scenes,config=SampleConfig(
               budget=sample_budget,max_edge=512,early_focus=4))
    except Exception:
        # A failure to sample *video* frames must not suppress independent
        # verified audio, subtitle and ASR probes of the same media.
        manifest={"frames":[]}
        blobs={}
    frames=[{"ref":f["frame_ref"],"observed_sec":f["observed_sec"],
             "png_sha256":f["png_sha256"]} for f in manifest["frames"]]
    visual.update({"state":"sampled" if frames else "unavailable",
                   "reason":None if frames else "frame_sampling_unavailable",
                   "frame_count":len(frames),"frames":frames,
                   "visual_classification_state":"not_inferred"})
    from .group_a_audio import inspect_audio
    suite=inspect_audio(source,roots=roots,video_probe=tech.probe,
        verify_current=lambda ctx: (
            type(ctx) is dict and ctx.get("source_media_sha256")==sha256
        ))
    asr=suite.get("asr") or {}
    speech={
        "state":asr.get("status","unavailable"),
        "reason":asr.get("reason_code") or asr.get("reason"),
        "source":asr.get("source"),
        "observation_sha256":asr.get("observation_sha256"),
        "speech_metrics":suite.get("speech_metrics"),
        "segments":suite.get("transcript_segments",[]),
    }
    visual["group_a_audio"]=suite
    visual["speech"]={"state":speech["state"],
        "reason":speech.get("reason"),"source":speech.get("source"),
        "observation_sha256":speech.get("observation_sha256"),
        "metrics":speech.get("speech_metrics")}
    # Do not discard later ASR utterances, as the original <=80 cap did.
    visual["asr_segments"]=speech.get("segments",[])
    backend=None
    try:
        backend=_ocr_backend()
        from ._copied.group_a.extractors.ocr import OCRConfig
        observation=analyze_frame_ocr(manifest,blobs,backend=backend,
            config=OCRConfig(frame_budget=sample_budget,
                             batch_timeout_sec=900),
            project_id=project_id,run_id=str(uuid4()))
        visual["ocr_state"]=observation.get("state","unavailable")
        visual["screen_text"]=[
          {"frame_ref":row.get("frame_ref"),"observed_sec":row.get("observed_sec"),
           "words":[w.get("text","")[:160] for w in row.get("words",[])][:40]}
          for row in observation.get("frames",[]) if row.get("words")
        ][:48]
    except Exception:
        visual["ocr_state"]="unavailable"
    # Track screen text in the original observed-frame coordinate system.
    tracking=None
    try:
        if "observation" not in locals():
            raise ValueError("ocr_observation_unavailable")
        from ._copied.group_a.extractors.text_tracking import track_screen_text
        tracking=track_screen_text(observation,manifest,
            verify_current=lambda ctx: (
                ctx.get("source_media_sha256")==sha256
                and ctx.get("frame_manifest_sha256")==manifest.get("manifest_sha256")
            ))
        visual["group_a_text_tracking"]=tracking
    except Exception as exc:
        visual["group_a_text_tracking"]={"status":"unavailable",
            "reason":getattr(exc,"code",type(exc).__name__)}
    # Subtitles: embedded stream packet evidence + optional observed
    # ASR/OCR alignment, without inferring absence from sparse frames.
    from ._copied.group_a.extractors.subtitle_probe import probe_subtitle_tracks
    from ._copied.group_a.extractors.subtitles import detect_subtitles
    try:
        embedded=probe_subtitle_tracks(source,roots=roots)
        visual["group_a_subtitle_probe"]=embedded
    except Exception as exc:
        embedded=None
        visual["group_a_subtitle_probe"]={"state":"unavailable",
            "reason":getattr(exc,"code",type(exc).__name__)}
    try:
        # ASR transcript and speech metrics share the same decoded audio SHA.
        # A caption sidecar is NOT independent proof of audible speech.
        asr_full=dict(asr)
        if asr_full.get("schema_version")=="ga-asr-transcript/v1":
            asr_full["segments"]=suite.get("transcript_segments",[])
        else:
            asr_full=None
        metric=suite.get("speech_metrics")
        if (asr_full is None or asr_full.get("source")!="local_faster_whisper"
            or not isinstance(tracking,dict) or tracking.get("status")!="partial"
            or not isinstance(metric,dict) or metric.get("schema_version") is None):
            metric=None
        visual["group_a_subtitles"]=detect_subtitles(
            source_media_sha256=sha256,
            verify_current=lambda ctx: (
                type(ctx) is dict and ctx.get("source_media_sha256")==sha256
            ),
            embedded_probe=embedded,transcript=asr_full,
            tracking=tracking,speech_metrics=metric,
            video_duration_sec=str(tech.measurement.duration_sec))
    except Exception as exc:
        visual["group_a_subtitles"]={"status":"unavailable",
           "reason":getattr(exc,"code",type(exc).__name__)}
    # The Group A frame sampler yields verified PTS instants, not a whole-video
    # visual pass. The media_worker rechecks rights after all these observations.
    visual["group_a_coverage"]={
        "verified_source_sha256":sha256,
        "actual_sampled_frames":len(frames),
        "requested_frame_budget":sample_budget,
        "speech_metrics":"observed" if (suite.get("speech_metrics") or {}).get("schema_version") else "unavailable",
        "vad_status":(suite.get("vad") or {}).get("status","unavailable"),
        "audio_event_state":(suite.get("audio_events") or {}).get("state","unavailable"),
        "full_video_semantic_certification":False,
    }
    try:
        from .replica_b_timeline import classify_timeline
        inferred=(classify_timeline(
           frames=manifest["frames"],png_blobs=blobs,
           transcript=speech.get("segments",[]) or srt_rows,
           duration_sec=float(tech.measurement.duration_sec))
           if allow_provider else
           {"state":"unavailable","reason":"project_source_llm_rights_denied"})
    except Exception:
        inferred={"state":"unavailable","reason":"visual_provider_or_evidence_unavailable"}
    visual["visual_classification_state"]=inferred.get("state","unavailable")
    visual["model_result"]=inferred
    try:
        from .replica_b_supplement import infer_supplement_timeline
        visual["b_extra_result"]=(infer_supplement_timeline(
            frames=manifest["frames"],png_blobs=blobs,
            transcript=speech.get("segments",[]) or srt_rows)
            if allow_provider else
            {"state":"unavailable","reason":"project_source_llm_rights_denied"})
    except Exception as exc:
        visual["b_extra_result"]={"state":"unavailable",
            "reason":"supplemental_multimodal_failed",
            "error_type":type(exc).__name__}
    # Persist only SHA/frame references and observed text, not frame bytes.
    return tech,visual
