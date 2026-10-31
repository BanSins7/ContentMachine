"""All eight copied Group B block contracts exposed in MI, fail closed.

Marketing never depends on QDP or an external Group B service. Labels from
source-verified sampled media are MI-native observations, not signed original
Group B receipts. Each video reports actual per-block coverage and reasons.
Source scope, immutable SHA/PTS index and rights are owned by Marketing.
"""
from __future__ import annotations

from ._copied.group_b.blocks.b1_hook import MECHANISMS
from ._copied.group_b.blocks.b2_format import VIDEO_FORMATS
from ._copied.group_b.blocks.b3_story import ROLES
from ._copied.group_b.blocks.b4_visual import FEATURES as B4_FEATURES
from ._copied.group_b.blocks.b5_editing import KINDS as B5_KINDS
from ._copied.group_b.blocks.b6_subtitles import TYPES as B6_TYPES
from ._copied.group_b.blocks.b7_cta import CTA_TYPES

SCHEMA = "mi-group-b-eight-block-observations/v1"
BLOCKS=tuple("B"+str(i) for i in range(1,9))


def _item(state, *, evidence=None, reason=None, **values):
    return {"state":state,"original_b_receipt_verified":False,
            "evidence_frame_refs":list(evidence or []),
            "reason":reason,**values}


def assemble_b1_b8(*, visual:dict, inferred:dict, measurements:object,
                   transcript:list[dict], extras:dict|None=None)->dict:
    if type(visual) is not dict or type(inferred) is not dict:
        raise ValueError("invalid_b_source")
    extras=extras or {}
    authenticated=inferred.get("state")=="model_observed_partial"
    refs=inferred.get("evidence_frame_refs") if authenticated else []
    observed={x["ref"] for x in visual.get("frames",[]) if type(x) is dict and x.get("ref")}
    if type(refs) is not list or any(type(ref) is not str or ref not in observed for ref in refs):
        raise ValueError("unverified_b_frame_references")
    model_state="model_observed_partial" if authenticated else "unavailable"
    missing="missing_cited_multimodal_response"
    blocks={}
    blocks["B1"]=_item(model_state,evidence=refs,reason=None if authenticated else missing,
        hook_mechanisms=[x for x in inferred.get("hook_mechanisms",[]) if x in MECHANISMS] if authenticated else [],
        hook_text=inferred.get("hook_text") if authenticated else None,
        visual_hook=inferred.get("hook_visual_description") if authenticated else None)
    blocks["B2"]=_item(model_state,evidence=refs,reason=None if authenticated else missing,
        format_family=(inferred.get("format_family") if inferred.get("format_family") in VIDEO_FORMATS else None)
                    if authenticated else None)
    timeline=inferred.get("timeline_windows",[]) if authenticated else []
    story_segments=[{
        "start_sec":window["start_sec"],"end_sec":window["end_sec"],
        "roles":[role for role in window.get("story_roles",[]) if role in ROLES],
        "evidence_frame_refs":window.get("evidence_frame_refs",[])}
        for window in timeline if window.get("state")=="model_observed_partial"]
    blocks["B3"]=_item(model_state,evidence=refs,reason=None if authenticated else missing,
        story_roles=[x for x in inferred.get("story_roles",[]) if x in ROLES] if authenticated else [],
        narrative_segments=story_segments,
        story_boundaries_verified=False)
    features={field:"unknown" for field in B4_FEATURES}
    additional=extras if extras.get("state")=="model_observed_partial" else {}
    for field in B4_FEATURES:
        if additional.get(field)=="present":
            features[field]="present"
    extras_refs=additional.get("evidence_frame_refs") or []
    if (type(extras_refs) is not list or
        any(type(ref) is not str or ref not in observed for ref in extras_refs)):
        raise ValueError("unverified_b4_evidence_refs")
    if any(value=="present" for value in features.values()) and not extras_refs:
        raise ValueError("uncited_b4_positive")
    blocks["B4"]=_item("model_observed_partial" if additional else "unavailable",
        evidence=extras_refs,reason=None if additional else "whole_video_visual_pass_unavailable",
        visual_features=features,ai_generated=None,
        whole_video_absence_authenticated=False)
    scenes=getattr(getattr(measurements,"measurement",None),"scenes",None)
    duration=getattr(getattr(measurements,"measurement",None),"duration_sec",None)
    # The copied A FFmpeg detector measures the whole decoded timeline.
    # Original B5 requires exact physical shot boundaries, which an
    # FFmpeg scene threshold can miss. Preserve the quantitative diagnostic.
    pace=None
    if scenes and duration and float(duration)>0:
        from decimal import Decimal, ROUND_HALF_UP
        raw_cuts=len(scenes)-1
        d=Decimal(str(duration))
        rate=(Decimal(raw_cuts)*Decimal(60)/d).quantize(
            Decimal("0.0001"),rounding=ROUND_HALF_UP)
        avg=(d/Decimal(len(scenes))).quantize(
            Decimal("0.0001"),rounding=ROUND_HALF_UP)
        pace={
            "detected_scene_cuts":raw_cuts,
            "detected_cuts_per_minute":str(rate),
            "mean_detected_scene_duration_sec":str(avg),
            "detected_cuts_first_5_sec":sum(0<Decimal(str(sc.start_sec))<5
                                              for sc in scenes[1:]),
            "ffmpeg_scene_threshold_not_complete_shot_catalog":True
        }
    blocks["B5"]=_item("measured_ffmpeg_scene_change_proxy" if pace else "unavailable",
        reason="thresholded_scenes_are_not_exact_shot_boundaries",
        scene_change_metrics=pace,
        video_duration_sec=str(duration) if duration is not None else None,
        certified_editing_type=None,certified_cut_count=None,
        supported_editing_types=list(B5_KINDS))
    subtitle=visual.get("group_a_subtitles") or {}
    raw_presence=subtitle.get("has_subtitles") if type(subtitle) is dict else None
    has=(True if raw_presence=="true" else
         False if raw_presence=="false" and
             subtitle.get("proof_kind")=="certified_dense_negative_review" else None)
    subtitle_state=("group_a_original_subtitle_evidence" if has is True else "unavailable")
    blocks["B6"]=_item(subtitle_state,
        reason="style_or_full_coverage_not_authenticated",
        embedded_subtitle_present=has if type(has) is bool else None,
        subtitle_type="unknown",supported_types=list(B6_TYPES),
        subtitle_evidence_sha256=subtitle.get("observation_sha256") if type(subtitle) is dict else None)
    cta_occurrences=[{"start_sec":window["start_sec"],
        "end_sec":window["end_sec"],
        "cta_type":window.get("cta_type"),
        "cta_text":window.get("cta_text"),
        "evidence_frame_refs":window.get("evidence_frame_refs",[])}
        for window in timeline if window.get("state")=="model_observed_partial"
            and window.get("cta_type") in CTA_TYPES]
    blocks["B7"]=_item(model_state,evidence=refs,
        reason=None if authenticated else missing,
        cta_type=(inferred.get("cta_type") if inferred.get("cta_type") in CTA_TYPES else None)
                 if authenticated else None,
        cta_text=inferred.get("cta_text") if authenticated else None,
        cta_occurrences=cta_occurrences,
        absence_whole_video_verified=False)
    topic=additional.get("topic_hint")
    blocks["B8"]=_item("observed_topic_hint_unapproved" if topic else "unavailable",
        evidence=extras_refs if topic else [],
        reason="no_signed_topic_taxonomy_snapshot",
        topic_hint=topic if type(topic) is str else None,
        approved_topic_id=None)
    return {"schema_version":SCHEMA,"blocks":blocks,
        "attempted_blocks":list(BLOCKS),
        "complete_original_blocks":[],
        "state":"partial_unverified",
        "limitations":["mi_native_evidence_and_per_block_coverage_required",
                       "b5_full_shot_catalog_required",
                       "b4_b7_full_publication_review_required",
                       "b8_approved_taxonomy_required"],
        "no_synthetic_certification":True}
