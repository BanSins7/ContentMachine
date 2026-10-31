"""Run copied-B taxonomy multimodal inference across every A-sampled window.

Each request sees at most eight SHA-proven PNGs and returns cited labels.
The merged result is *not* a whole-publication original B receipt.
Provider failures are explicit and do not silently pass as successful frames.
"""
from __future__ import annotations

from collections import Counter
from .replica_vision import classify_grounded, MAX_INPUT_FRAMES
from ._copied.group_b.blocks.b1_hook import MECHANISMS
from ._copied.group_b.blocks.b2_format import VIDEO_FORMATS
from ._copied.group_b.blocks.b3_story import ROLES
from ._copied.group_b.blocks.b7_cta import CTA_TYPES

MAX_TIMELINE_FRAMES=48

def _unique(values, limit):
    found=[]
    for x in values:
        if x not in found:
            found.append(x)
            if len(found)>=limit:
                break
    return found


def classify_timeline(*, frames:list[dict],png_blobs:dict,
                      transcript:list[dict],duration_sec:float,
                      requester=None) -> dict:
    if not frames:
        return {"state":"unavailable","reason":"no_verified_frames"}
    if len(frames)>MAX_TIMELINE_FRAMES:
        return {"state":"unavailable","reason":"timeline_frame_cap_exceeded"}
    successful=[]
    windows=[]
    for index in range(0,len(frames),MAX_INPUT_FRAMES):
        frame_chunk=frames[index:index+MAX_INPUT_FRAMES]
        chunk_start=float(frame_chunk[0]["observed_sec"])
        chunk_end=(float(frame_chunk[-1]["observed_sec"])
                   if index+MAX_INPUT_FRAMES>=len(frames) else
                   float(frames[index+MAX_INPUT_FRAMES]["observed_sec"]))
        chunk_text=[x for x in transcript if isinstance(x,dict)
                    and type(x.get("start_sec")) in (float,int)
                    and chunk_start-0.1<=x["start_sec"]<=chunk_end+0.1]
        try:
            result=classify_grounded(frames=frame_chunk,png_blobs=png_blobs,
                  transcript=chunk_text,duration_sec=duration_sec,requester=requester)
        except Exception as exc:
            result={"state":"unavailable","reason":"model_or_validation_error",
                    "error_type":type(exc).__name__}
        windows.append({"start_sec":chunk_start,"end_sec":chunk_end,
                        "sampled_frame_count":len(frame_chunk),
                        "state":result.get("state"),
                        "reason":result.get("reason"),
                        "provider_response_id":result.get("provider_response_id"),
                        "model":result.get("model"),
                        "evidence_frame_refs":result.get("evidence_frame_refs",[]),
                        "story_roles":result.get("story_roles",[]) if result.get("state")=="model_observed_partial" else [],
                        "hook_mechanisms":result.get("hook_mechanisms",[]) if result.get("state")=="model_observed_partial" else [],
                        "cta_type":result.get("cta_type") if result.get("state")=="model_observed_partial" else None,
                        "cta_text":result.get("cta_text") if result.get("state")=="model_observed_partial" else None,
                        "visual_subjects":result.get("visual_subjects",[]) if result.get("state")=="model_observed_partial" else []})
        if result.get("state")=="model_observed_partial":
            successful.append((index,result))
        elif result.get("reason") in ("multimodal_model_not_configured","visual_provider_disabled"):
            break
    if not successful:
        return {"state":"unavailable","reason":"no_successful_window_inference",
                "timeline_windows":windows}
    # A missing early window cannot become a fabricated hook based on a
    # later scene. Model classifications are tied to observed video instants.
    early=[(i,r) for i,r in successful
           if float(frames[i]["observed_sec"])<=5.0]
    selected=early[0][1] if early else None
    last=successful[-1][1]
    formats=[r.get("format_family") for _,r in successful
             if r.get("format_family") in VIDEO_FORMATS]
    family=(Counter(formats).most_common(1)[0][0] if formats else None)
    mechanics=_unique((v for _,r in early
        for v in r.get("hook_mechanisms",[]) if v in MECHANISMS),10)
    roles=_unique((v for _,r in successful
        for v in r.get("story_roles",[]) if v in ROLES),12)
    subjects=_unique((v for _,r in successful
        for v in r.get("visual_subjects",[]) if type(v) is str),12)
    refs=_unique((v for _,r in successful
        for v in r.get("evidence_frame_refs",[])),MAX_TIMELINE_FRAMES)
    ctas=[r for _,r in successful if r.get("cta_type") in CTA_TYPES]
    cta=ctas[-1] if ctas else last
    return {
        "state":"model_observed_partial",
        "format_family":family,
        "hook_mechanisms":mechanics,
        "hook_text":selected.get("hook_text") if selected else None,
        "hook_visual_description":selected.get("hook_visual_description") if selected else None,
        "hook_window_observed":bool(early),
        "story_roles":roles,
        "cta_type":cta.get("cta_type"),
        "cta_text":cta.get("cta_text"),
        "visual_subjects":subjects,
        "evidence_frame_refs":refs,
        "model":(selected or last).get("model"),
        "provider_response_id":(selected or last).get("provider_response_id"),
        "timeline_windows":windows,
        "successful_windows":len(successful),
        "requested_windows":len(windows),
        "fully_sampled_window_inference":len(successful)==len(windows),
        "limitations":["sampled_frames_not_all_video_frames",
                        "uncertified_original_B1_B8_receipts"],
    }
