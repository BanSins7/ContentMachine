"""Pure read-only export of frozen Marketing A/B carousel and video evidence.

No model calls, downloads, provider requests, SQL, QDP or implicit re-analysis.
A 100-post export uses the immutable selection and a verified, frozen snapshot.
It clearly separates OCR-verbatim text, model proposals and unavailable
per-slide scene descriptions. It may safely join a pre-existing local C report.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json


SCHEMA="marketing-pinned-100-creative-evidence/v1"
BLOCKS=tuple(f"B{i}" for i in range(1,9))


def _dict(value):
    return value if isinstance(value,dict) else {}


def _list(value):
    return value if isinstance(value,list) else []


def _unique_posts(rows):
    return (isinstance(rows,list) and len(rows)==100
            and len({r.get("video_id") for r in rows if isinstance(r,dict)})==100)


def _join_c(c_data,posts,project_id,batch_id,selection_sha):
    if c_data is None:
        return {}
    if not isinstance(c_data,dict):
        raise ValueError("creative_export_invalid_existing_c_report")
    if (c_data.get("project_id")!=project_id
        or c_data.get("batch_id")!=batch_id
        or c_data.get("pinned_selection_sha256")!=selection_sha
        or not _unique_posts(c_data.get("posts"))):
        raise ValueError("creative_export_existing_c_report_scope_mismatch")
    rows={r["video_id"]:r for r in c_data["posts"]}
    for p in posts:
        item=rows.get(p["video_id"])
        if (item is None or item.get("url")!=p["url"]
            or item.get("platform_native_id")!=p["native_id"]
            or item.get("views")!=p["views"]):
            raise ValueError("creative_export_existing_c_report_identity_mismatch")
    return rows


def _carousel_slides(ga):
    slides=_list(ga.get("slide_observations"))
    output=[]
    for ordinal,slide in enumerate(slides,1):
        if not isinstance(slide,dict) or slide.get("slide_index")!=ordinal:
            raise ValueError("creative_export_corrupted_slide_order")
        ocr=_dict(slide.get("ocr"))
        output.append({
            "slide_index":ordinal,
            "source_ref":slide.get("ref"),
            "source_png_sha256":slide.get("image_png_sha256"),
            "image_width":slide.get("image_width"),
            "image_height":slide.get("image_height"),
            "ocr_state":ocr.get("state","unavailable"),
            "ocr_unavailable_reason":ocr.get("reason"),
            "ocr_words":_list(ocr.get("words")),
            "ocr_resized_for_analysis":ocr.get("ocr_resized"),
            "visual_scene_description":None,
            "visual_scene_description_status":"not_persisted_per_slide",
        })
    return output


def make_export(*,active,selection,selection_sha,frozen,c_data=None):
    """A full immutable 100-row report, with no invented recognition results."""
    if not isinstance(active,dict) or not isinstance(selection,dict) or not isinstance(frozen,dict):
        raise ValueError("creative_export_invalid_inputs")
    videos=active.get("videos")
    posts=selection.get("posts")
    if (not _unique_posts(videos) or not isinstance(posts,list) or len(posts)!=100
        or active.get("selection_sha256")!=selection_sha
        or not isinstance(selection.get("profile_snapshot_id"),str)
        or not isinstance(active.get("batch_id"),int)
        or not isinstance(active.get("project_id"),str)):
        raise ValueError("creative_export_pinned_scope_invalid")
    frozen_id=active.get("state",{}).get("group_c_snapshot_id")
    evidence=_dict(frozen.get("snapshot")).get("video_evidence")
    if (not frozen_id or frozen.get("snapshot_id")!=frozen_id
        or not _unique_posts(evidence)):
        raise ValueError("creative_export_frozen_evidence_not_exact_100")
    by_id={r["video_id"]:r for r in evidence}
    if set(by_id)!={v["video_id"] for v in videos}:
        raise ValueError("creative_export_frozen_ids_not_pinned")
    for post,video in zip(posts,videos):
        if (not isinstance(post,dict) or not isinstance(video,dict)
            or post.get("native_id")!=video.get("native_id")
            or post.get("url")!=video.get("url")
            or video.get("state")!="analysis_partial"
            or type(post.get("views")) is not int):
            raise ValueError("creative_export_changed_post_or_media_state")
        row=by_id[video["video_id"]]
        if (row.get("url")!=post["url"] or row.get("platform")!="tiktok"
            or row.get("mi_analysis_id") is None):
            raise ValueError("creative_export_frozen_analysis_identity_mismatch")
    c_rows=_join_c(c_data,[{**p,"video_id":v["video_id"]}
                          for p,v in zip(posts,videos)],
                   active["project_id"],active["batch_id"],selection_sha)

    counts=Counter()
    output=[]
    for post,video in zip(posts,videos):
        e=by_id[video["video_id"]]
        kind=post.get("content_kind")
        if kind not in ("video","photo_carousel") or e.get("content_kind")!=kind:
            raise ValueError("creative_export_kind_mismatch")
        ga=_dict(e.get("group_a"))
        gb=_dict(e.get("group_b"))
        blocks=_dict(gb.get("blocks"))
        if set(blocks)!=set(BLOCKS):
            raise ValueError("creative_export_missing_eight_b_blocks")
        b1,b2,b3,b4,b6,b7=( _dict(blocks.get(k)) for k in
                             ("B1","B2","B3","B4","B6","B7") )
        creative_state=e.get("creative_state","unavailable")
        slides=_carousel_slides(ga) if kind=="photo_carousel" else []
        if kind=="photo_carousel":
            if len(slides)<2 or ga.get("slide_count")!=len(slides):
                raise ValueError("creative_export_carousel_slide_count_mismatch")
        frame_info=_dict(e.get("visual_evidence"))
        frames=_list(frame_info.get("frames")) if kind=="video" else []
        ref_to_slide={s["source_ref"]:s["slide_index"] for s in slides}
        refs=_list(b7.get("evidence_slide_refs")) if kind=="photo_carousel" else _list(b7.get("evidence_frame_refs"))
        hook_ref_key="evidence_slide_refs" if kind=="photo_carousel" else "evidence_frame_refs"
        c=_dict(c_rows.get(video["video_id"]))
        c1=_dict(c.get("C1"))
        c4=_dict(c.get("C4"))
        result={
            "video_id":video["video_id"],
            "native_id":post["native_id"],
            "url":post["url"],
            "content_kind":kind,
            "source_profile_snapshot_id":selection["profile_snapshot_id"],
            "source_selection_sha256":selection_sha,
            "source_analysis_id":str(e["mi_analysis_id"]),
            "source_analysis_state":creative_state,
            "views_original_profile_snapshot":post["views"],
            "C1_diagnostic":c1 if c1 else None,
            "C4_diagnostic":c4 if c4 else None,
            "published_at":c.get("publication_age",{}).get("published_at")
                             if isinstance(c.get("publication_age"),dict) else e.get("published_at"),
            "hook":{
                "state":b1.get("state","unavailable"),
                "mechanisms":_list(b1.get("hook_mechanisms")),
                "text":b1.get("hook_text"),
                "text_verbatim_ocr_verified":(
                    b1.get("hook_text_verbatim_ocr_verified") is True
                    if kind=="photo_carousel"
                    else _dict(b1.get("verbatim_text_evidence")).get("state")=="verbatim_observed"),
                "visual_description":(b1.get("hook_visual") if kind=="photo_carousel"
                                      else b1.get("visual_hook")),
                "source_evidence_refs":_list(b1.get(hook_ref_key)),
                "hook_slide_index":b1.get("hook_slide_index") if kind=="photo_carousel" else None,
                "hook_window_observed":b1.get("hook_window_observed") if kind=="video" else None,
                "explanation":"model_output_not_reconstruction_instructions",
            },
            "format":{
                "state":b2.get("state","unavailable"),
                "carousel_format":b2.get("carousel_format") if kind=="photo_carousel" else None,
                "video_format_family":b2.get("format_family") if kind=="video" else None,
            },
            "narrative":{
                "state":b3.get("state","unavailable"),
                "story_roles":_list(b3.get("story_roles")),
                "reading_axis":"ordered_slides" if kind=="photo_carousel" else "observed_video_frames",
                "narrative_segments":_list(b3.get("narrative_segments")) if kind=="video" else None,
            },
            "visual":{
                "state":b4.get("state","unavailable"),
                "features":_dict(b4.get("visual_features")),
                "subjects":_list(e.get("visual_subjects")),
                "all_slide_scene_descriptions_available":False,
                "limitations":"No detailed subject/pose/composition per slide was persisted by this classifier.",
            },
            "cta":{
                "state":b7.get("state","unavailable"),
                "type":b7.get("cta_type"),
                "text":b7.get("cta_text"),
                "text_verbatim_ocr_verified":(
                    b7.get("cta_text_verbatim_ocr_verified") is True
                    if kind=="photo_carousel"
                    else _dict(b7.get("verbatim_text_evidence")).get("state")=="verbatim_observed"),
                "source_evidence_refs":refs,
                "candidate_slide_indices":sorted({ref_to_slide[x] for x in refs if x in ref_to_slide}),
                "cta_occurrences":_list(b7.get("cta_occurrences")) if kind=="video" else None,
            },
            "slides":slides if kind=="photo_carousel" else None,
            "video_sampled_frames":[
                {"ref":f.get("ref"),"observed_sec":f.get("observed_sec"),
                 "png_sha256":f.get("png_sha256")} for f in frames if isinstance(f,dict)
            ] if kind=="video" else None,
            "video_ocr_observation":frame_info.get("group_a_ocr") if kind=="video" else None,
            "video_text_tracking":frame_info.get("group_a_text_tracking") if kind=="video" else None,
            "group_a_report_sha256":ga.get("report_sha256"),
            "group_b_report_sha256":gb.get("report_sha256"),
            "group_b_block_states":{k:_dict(blocks[k]).get("state","unavailable") for k in BLOCKS},
            "B6_text_observation":b6 if kind=="photo_carousel" else {
                "state":b6.get("state"),"ocr_style_candidates":b6.get("ocr_style_candidates")},
            "model_reconstruction_ready":False,
            "official_full_group_b_certified":False,
        }
        output.append(result)
        counts[kind]+=1
        if result["hook"]["text"]:counts["hook_text_present"]+=1
        if result["hook"]["text_verbatim_ocr_verified"]:counts["hook_text_ocr_verified"]+=1
        if result["hook"]["visual_description"]:counts["hook_visual_description_present"]+=1
        if result["cta"]["type"]:counts["cta_type_present"]+=1
        if result["cta"]["text"]:counts["cta_text_present"]+=1
        if result["cta"]["text_verbatim_ocr_verified"]:counts["cta_text_ocr_verified"]+=1
        if kind=="photo_carousel":
            counts["carousel_slides"]+=len(slides)
            counts["carousel_ocr_observed_slides"]+=sum(
                s["ocr_state"]!="unavailable" and bool(s["ocr_words"]) for s in slides)
        if c1:counts["C1_diagnostic_present"]+=1
        if c4:counts["C4_diagnostic_present"]+=1
    if counts["video"]!=50 or counts["photo_carousel"]!=50:
        raise ValueError("creative_export_not_exact_50_video_50_carousel")
    return {
        "schema_version":SCHEMA,
        "scope":"exact_immutable_top100_tiktok_marketing_batch",
        "project_id":active["project_id"],
        "batch_id":active["batch_id"],
        "profile_snapshot_id":selection["profile_snapshot_id"],
        "pinned_selection_sha256":selection_sha,
        "frozen_market_snapshot_id":frozen_id,
        "frozen_market_snapshot_sha256":frozen.get("payload_sha256"),
        "source_report":"frozen_market_intelligence_snapshot_verified_read_only",
        "C1_C4_from_saved_local_diagnostic":bool(c_rows),
        "counts":dict(counts),
        "limitations":[
            "hook_and_cta_recognition_are_model_proposals_unless_OCR_verified",
            "the_model_did_not_persist_detailed_scene_descriptions_for_every_slide",
            "model_not_available_or_source_rights_can_leave_hook_cta_unknown",
            "slide_OCR_is_bounded_and_may_miss_stylized_or_small_text",
            "video_analysis_uses_sampled_frames_not_continuous_inspection",
            "no_original_B_full_certification_or_C0_C7_certification",
            "single_profile_popularity_is_not_causal_virality_proof",
            "no_provider_or_LLM_called_for_this_export",
        ],
        "public_metrics_snapshot_time_verified":False,
        "new_batches":0,
        "provider_requests":0,
        "model_requests":0,
        "database_writes":0,
        "posts":output,
    }


def export_sha(report):
    return sha256(json.dumps(report,ensure_ascii=False,sort_keys=True,
                             separators=(",",":")).encode("utf-8")).hexdigest()
