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
_asr_lock=BoundedSemaphore(1)

class _MIASRLease:
    @contextmanager
    def acquire(self, *, kind, device):
        if kind!="asr" or device!="cpu":
            raise RuntimeError("invalid_asr_resource_request")
        if not _asr_lock.acquire(blocking=False):
            raise RuntimeError("asr_resource_busy")
        try:
            yield
        finally:
            _asr_lock.release()

def _local_asr(source, roots, video_probe=None):
    """Copied GA09/GA10 genuine audio+speech pathway. No model download."""
    from ._copied.group_a.extractors.audio_decode import decode_audio
    from ._copied.group_a.extractors.asr import ASRConfig, transcribe
    from ._copied.group_a.extractors.local_asr import (
        LocalASRProvenance, LocalFasterWhisper)
    if config.setting("CM_MARKET_ASR_ENABLED","")!="1":
        return {"state":"unavailable","reason":"asr_not_enabled","segments":[]}
    audio=decode_audio(source,roots=roots)
    if audio.probe.state=="absent":
        return {"state":"no_audio_stream","segments":[]}
    provenance=LocalASRProvenance(
      model_dir=config.setting("CM_MARKET_ASR_MODEL_DIR","").strip(),
      model_sha256=config.setting("CM_MARKET_ASR_MODEL_SHA256","").strip(),
      model_version=config.setting("CM_MARKET_ASR_MODEL_VERSION","").strip(),
      engine_version=config.setting("CM_MARKET_ASR_ENGINE_VERSION","").strip())
    backend=LocalFasterWhisper(provenance,lease=_MIASRLease())
    result=transcribe(audio,config=ASRConfig(
         source_policy="local_only",device="cpu",word_timestamps=False),
         backend=backend)
    speech_metrics=None
    try:
        from ._copied.group_a.extractors.speech_metrics import measure_speech
        speech_metrics=measure_speech(audio,result,video_probe=video_probe)
    except Exception:
        pass
    return {"state":result["status"],"reason":result.get("reason_code"),
        "speech_metrics":speech_metrics,
        "segments":[{"start_sec":float(s["start_sec"]),
                    "end_sec":float(s["end_sec"]),"text":s["text"][:300]}
                    for s in result.get("segments",[])[:80]],
        "source":result["source"],"observation_sha256":result["observation_sha256"]}


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
        return tech,visual
    frames=[{"ref":f["frame_ref"],"observed_sec":f["observed_sec"],
             "png_sha256":f["png_sha256"]} for f in manifest["frames"]]
    visual.update({"state":"sampled","reason":None,
                   "frame_count":len(frames),"frames":frames,
                   "visual_classification_state":"not_inferred"})
    try:
        speech=_local_asr(source,roots,video_probe=tech.probe)
    except Exception:
        speech={"state":"unavailable","reason":"asr_resource_or_model_unavailable",
                "segments":[]}
    visual["speech"]={"state":speech["state"],
        "reason":speech.get("reason"),"source":speech.get("source"),
        "observation_sha256":speech.get("observation_sha256"),
        "metrics":speech.get("speech_metrics")}
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
        ][:12]
    except Exception:
        visual["ocr_state"]="unavailable"
    # Genuine copied-A14 subtitle packet audit and A15 tri-state classifier.
    try:
        from ._copied.group_a.extractors.subtitle_probe import probe_subtitle_tracks
        from ._copied.group_a.extractors.subtitles import detect_subtitles
        embedded=probe_subtitle_tracks(source,roots=roots)
        visual["group_a_subtitle_probe"]=embedded
        visual["group_a_subtitles"]=detect_subtitles(
            source_media_sha256=sha256,
            verify_current=lambda scope: True,
            embedded_probe=embedded,
            video_duration_sec=float(tech.measurement.duration_sec))
    except Exception as exc:
        visual["group_a_subtitles"]={"state":"unavailable",
           "reason":"source_subtitle_probe_not_usable",
           "error_type":type(exc).__name__}
    # Real Group A screen-text recurrence analysis, only at observed frames.
    try:
        from ._copied.group_a.extractors.text_tracking import track_screen_text
        visual["group_a_text_tracking"]=track_screen_text(
            observation,manifest,verify_current=lambda scope:True)
    except Exception as exc:
        visual["group_a_text_tracking"]={"state":"unavailable",
            "reason":"ocr_or_frame_proof_unavailable",
            "error_type":type(exc).__name__}
    # The Group A frame sampler yields verified PTS instants, not a whole-video
    # visual pass. The media_worker rechecks rights after all these observations.
    visual["group_a_coverage"]={
        "verified_source_sha256":sha256,
        "actual_sampled_frames":len(frames),
        "speech_metrics":"observed" if speech.get("speech_metrics") else "unavailable",
        "full_video_semantic_certification":False,
    }
    try:
        from .replica_vision import classify_grounded
        inferred=(classify_grounded(
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
        from .replica_b_supplement import infer_supplement
        visual["b_extra_result"]=(infer_supplement(
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
