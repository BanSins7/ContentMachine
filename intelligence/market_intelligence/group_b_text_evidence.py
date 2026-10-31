"""Marketing B1/B7 verbatim text provenance over actual copied-A OCR/ASR.

Semantic labels from a model are never treated as a transcript by themselves.
Identical observed phrases are linked to SHA-pinned original-frame OCR or
GA11-audio/video synchronized speech; downloader-generated .srt is NOT speech.
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

VERSION="mi-b-text-verbatim-evidence/v1"

def _norm(text):
    if type(text) is not str:return ""
    return " ".join(unicodedata.normalize("NFKC",text).casefold().split())


def _range(row):
    try:
        return Decimal(str(row["start_sec"])),Decimal(str(row["end_sec"]))
    except (InvalidOperation,ValueError,KeyError,TypeError):
        return None,None


def match_text(text,visual,*,start_sec=0,end_sec=600,allowed_refs=None):
    wanted=_norm(text)
    if not wanted or len(wanted)>700:
        return {"state":"unverified","reason":"text_missing_or_too_long",
                "matched_sources":[],"evidence_frame_refs":[]}
    a,b=Decimal(str(start_sec)),Decimal(str(end_sec))
    if not a.is_finite() or not b.is_finite() or a<0 or a>b or b>600:
        raise ValueError("mi_b_text_time_invalid")
    allowed=set(allowed_refs or [f["ref"] for f in visual.get("frames",[])])
    matched=[]
    frames=[]
    ocr=visual.get("group_a_ocr") or {}
    if ocr.get("schema_version")=="ga-frame-ocr/v1" and ocr.get("source_media_sha256")==visual.get("source_media_sha256"):
        for row in ocr.get("frames",[]):
            ref=row.get("frame_ref")
            if ref not in allowed:continue
            try:sec=Decimal(str(row.get("observed_sec")))
            except (InvalidOperation,ValueError,TypeError):continue
            if not a<=sec<=b:continue
            observed=_norm(" ".join(str(w.get("text","")) for w in row.get("words",[])))
            if wanted in observed:
                matched.append({"kind":"ocr_sampled_frame","start_sec":str(sec),
                                "end_sec":str(sec),"ref":ref})
                if ref not in frames:frames.append(ref)
    # ASR segment start/end are in VIDEO time only after GA11 proves clock.
    speech=visual.get("speech") or {}
    if speech.get("video_time_verified") is True:
        for row in visual.get("asr_segments",[]):
            start,end=_range(row)
            if start is None or end is None or end<a or start>b:continue
            if wanted in _norm(row.get("text","")):
                matched.append({"kind":"verified_ga11_speech",
                               "start_sec":str(start),"end_sec":str(end)})
    return {
        "schema_version":VERSION,
        "state":"verbatim_observed" if matched else "model_proposal_only",
        "reason":None if matched else "no_matching_ga_ocr_or_aligned_asr",
        "matched_sources":matched[:20],
        "evidence_frame_refs":frames,
        "full_video_completeness":False,
    }
