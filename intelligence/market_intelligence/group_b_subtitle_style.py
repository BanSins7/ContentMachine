"""B6 measured OCR geometry over actual A-frame observations.

This is NOT a subtitle detector or a certification of full-video scan.
Ocular text position and approximate bounding box height are measured on
sampled SHA-verified frame refs. Font weight/color/continuous animation are
unknown without a separately evidenced original-media style inspector.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from statistics import median
from ._copied.group_b.blocks.b6_subtitles import STYLES

VERSION="mi-b6-observed-ocr-geometry/v1"

def subtitle_style_candidates(visual:dict)->dict:
    frames=visual.get("frames") or []
    ocr=visual.get("group_a_ocr") or {}
    known={f.get("ref") for f in frames if isinstance(f,dict)}
    if ocr.get("schema_version")!="ga-frame-ocr/v1" or ocr.get("source_media_sha256")!=visual.get("source_media_sha256"):
        return {"schema_version":VERSION,"state":"unavailable",
                "reason":"no_sha_matched_ocr","observations":[]}
    candidates=[]
    for frame in ocr.get("frames",[]):
        if frame.get("frame_ref") not in known or frame.get("state") not in ("text_observed","partial_language"):
            continue
        width,height=frame.get("width"),frame.get("height")
        if type(width) is not int or type(height) is not int or width<=0 or height<=0:
            continue
        words=frame.get("words") or []
        if not words:continue
        positions=[]
        heights=[]
        texts=[]
        for word in words:
            box=word.get("bbox") or {}
            if any(type(box.get(k)) is not int for k in ("x","y","width","height")):
                continue
            if not 0<=box["y"]<height or box["height"]<=0 or box["y"]+box["height"]>height:
                continue
            positions.append(Decimal(box["y"]+box["height"]/2)/Decimal(height))
            heights.append(Decimal(box["height"])/Decimal(height))
            texts.append(str(word.get("text",""))[:80])
        if not positions:continue
        y=median(positions)
        h=median(heights)
        position=("top" if y<Decimal("0.33") else
                  "bottom" if y>Decimal("0.66") else "center")
        size=("small" if h<Decimal("0.035") else
              "large" if h>Decimal("0.085") else "medium")
        candidates.append({
            "frame_ref":frame["frame_ref"],
            "observed_sec":str(frame["observed_sec"]),
            "position":position,"relative_text_height":str(round(h,4)),
            "font_size_proxy":size,"ocr_word_count":len(texts),
            "text_sample":" ".join(texts)[:240],
            "has_subtitle_identity_proof":False})
    positions={item["position"] for item in candidates}
    sizes={item["font_size_proxy"] for item in candidates}
    return {
        "schema_version":VERSION,
        "state":"sampled_geometry_observed" if candidates else "unavailable",
        "reason":None if candidates else "ocr_words_not_observed",
        "observations":candidates,
        "dominant_position":next(iter(positions)) if len(positions)==1 else None,
        "dominant_size_proxy":next(iter(sizes)) if len(sizes)==1 else None,
        "font_style":None,"text_color":None,"highlight_color":None,
        "background_style":None,"display_mode":None,
        "subtitle_type_certified":False,
        "limitations":["text_not_verified_as_subtitle",
                       "sampled_frames_not_full_video",
                       "font_height_proxy_not_actual_font_size",
                       "no_video_wide_negative_claim"]
    }
