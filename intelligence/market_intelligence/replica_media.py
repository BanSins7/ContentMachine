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
from ._copied.group_a.media_manifest import verify_file
from ._copied.group_a.extractors.frames import sample_frames, SampleConfig
from ._copied.group_a.extractors.ocr import analyze_frame_ocr
from ._copied.group_a.extractors.tesseract_ocr import TesseractAdapter
from ._copied.group_a.extractors.technical import analyze_technical as original_analyze_technical
from intelligence import config

FRAME_BUDGET = 12

def _ocr_backend():
    pin=config.setting("CM_MARKET_TESSERACT_PIN", "").strip()
    binary=config.setting("CM_MARKET_TESSERACT_BINARY", "").strip()
    data=config.setting("CM_MARKET_TESSERACT_DATA", "").strip()
    if not (pin and binary and data):
        return None
    return TesseractAdapter(binary=binary,tessdata_dir=data,expected_sha256=pin)

def analyze_media(local:Path, sha256:str, *,roots:list[str],project_id:str,
                  srt_rows:list[dict],technical=None) -> tuple[object,dict]:
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
    try:
        manifest,blobs=sample_frames(source,tech.probe,roots=roots,
            scenes=tech.measurement.scenes,config=SampleConfig(
               budget=FRAME_BUDGET,max_edge=512,early_focus=4))
    except Exception:
        return tech,visual
    frames=[{"ref":f["frame_ref"],"observed_sec":f["observed_sec"],
             "png_sha256":f["png_sha256"]} for f in manifest["frames"]]
    visual.update({"state":"sampled","reason":None,
                   "frame_count":len(frames),"frames":frames,
                   "visual_classification_state":"not_inferred"})
    backend=None
    try:
        backend=_ocr_backend()
        observation=analyze_frame_ocr(manifest,blobs,backend=backend,
            project_id=project_id,run_id=str(uuid4()))
        visual["ocr_state"]=observation.get("state","unavailable")
        visual["screen_text"]=[
          {"frame_ref":row.get("frame_ref"),"observed_sec":row.get("observed_sec"),
           "words":[w.get("text","")[:160] for w in row.get("words",[])][:40]}
          for row in observation.get("frames",[]) if row.get("words")
        ][:12]
    except Exception:
        visual["ocr_state"]="unavailable"
    try:
        from .replica_vision import classify_grounded
        inferred=classify_grounded(
           frames=manifest["frames"],png_blobs=blobs,
           transcript=srt_rows,duration_sec=float(tech.measurement.duration_sec))
    except Exception:
        inferred={"state":"unavailable","reason":"visual_provider_or_evidence_unavailable"}
    visual["visual_classification_state"]=inferred.get("state","unavailable")
    visual["model_result"]=inferred
    # Persist only SHA/frame references and observed text, not frame bytes.
    return tech,visual
