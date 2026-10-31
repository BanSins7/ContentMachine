"""MI Group B B1–B8 native evidence validation and immutable report.

Original copied-B taxonomies are reused. QDP-coupled original persistence
validators are replaced by Marketing-native A hash, frame/time/rights contracts.
No model self-attestation proves that unseen video frames were classified.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from .contracts import digest
from .group_a_report import verify_group_a_report
from ._copied.group_b.blocks.b1_hook import MECHANISMS
from ._copied.group_b.blocks.b2_format import VIDEO_FORMATS
from ._copied.group_b.blocks.b3_story import ROLES
from ._copied.group_b.blocks.b4_visual import FEATURES
from ._copied.group_b.blocks.b7_cta import CTA_TYPES

VERSION="mi-group-b-native-report/v1"
SHA=re.compile(r"[a-f0-9]{64}\Z")
BLOCKS=tuple("B"+str(i) for i in range(1,9))
MAX_BLOCK_EVIDENCE=48


def _valid_time(t,duration):
    try:v=Decimal(str(t))
    except (InvalidOperation,TypeError,ValueError):
        raise ValueError("b_invalid_time") from None
    if not v.is_finite() or v<0 or v>duration:
        raise ValueError("b_time_outside_video")
    return v


def build_group_b_report(*,source_sha256,group_a,visual,group_b,duration_sec):
    if type(source_sha256) is not str or SHA.fullmatch(source_sha256) is None:
        raise ValueError("b_source_digest_invalid")
    if not verify_group_a_report(group_a,source_sha256):
        raise ValueError("b_group_a_report_unverified")
    if type(group_b) is not dict or set(group_b.get("blocks",{}))!=set(BLOCKS):
        raise ValueError("b_all_eight_blocks_required")
    try:duration=Decimal(str(duration_sec))
    except (TypeError,ValueError,InvalidOperation):
        raise ValueError("b_duration_invalid") from None
    if not duration.is_finite() or not 0<duration<=600:
        raise ValueError("b_duration_invalid")
    if str(group_a["duration_sec"])!=str(duration):
        raise ValueError("b_group_a_clock_mismatch")
    frames=visual.get("frames") or []
    frame_at={}
    for f in frames:
        if type(f) is not dict or type(f.get("ref")) is not str or f["ref"] in frame_at:
            raise ValueError("b_source_frames_invalid")
        frame_at[f["ref"]]=_valid_time(f.get("observed_sec"),duration)
    validated={}
    for name in BLOCKS:
        stage=group_b["blocks"][name]
        if type(stage) is not dict or type(stage.get("state")) is not str:
            raise ValueError("b_stage_invalid")
        refs=stage.get("evidence_frame_refs",[])
        if type(refs) is not list or len(refs)>MAX_BLOCK_EVIDENCE or len(refs)!=len(set(refs)):
            raise ValueError("b_stage_evidence_invalid")
        if any(type(ref) is not str or ref not in frame_at for ref in refs):
            raise ValueError("b_stage_unobserved_frame")
        if stage.get("original_b_receipt_verified") is True:
            raise ValueError("b_cross_pipeline_certification_disallowed")
        validated[name]={"state":stage["state"],"evidence_frame_refs":refs,
                         "reason":stage.get("reason"),"has_observations":bool(refs)}
    b1=group_b["blocks"]["B1"]
    if any(x not in MECHANISMS for x in b1.get("hook_mechanisms",[])):
        raise ValueError("b1_taxonomy_invalid")
    b2=group_b["blocks"]["B2"]
    if b2.get("format_family") is not None and b2["format_family"] not in VIDEO_FORMATS:
        raise ValueError("b2_taxonomy_invalid")
    if any(role not in ROLES for role in group_b["blocks"]["B3"].get("story_roles",[])):
        raise ValueError("b3_taxonomy_invalid")
    for attr,val in group_b["blocks"]["B4"].get("visual_features",{}).items():
        if attr not in FEATURES or val not in ("present","absent","unknown"):
            raise ValueError("b4_assessment_invalid")
        if val=="absent":
            # Sparse sampled frames may not establish a whole-video negative.
            raise ValueError("b4_negative_without_dense_review")
    for name,segments in (
        ("B3",group_b["blocks"]["B3"].get("narrative_segments",[])),
        ("B7",group_b["blocks"]["B7"].get("cta_occurrences",[]))):
        if type(segments) is not list or len(segments)>2000:
            raise ValueError("b_timeline_invalid")
        for seg in segments:
            if type(seg) is not dict:
                raise ValueError("b_segment_invalid")
            start=_valid_time(seg.get("start_sec"),duration)
            end=_valid_time(seg.get("end_sec"),duration)
            if end<start:raise ValueError("b_segment_reverse_time")
            cited=seg.get("evidence_frame_refs") or []
            if (type(cited) is not list or len(cited)!=len(set(cited)) or
                any(type(x) is not str or x not in frame_at or
                    not start<=frame_at[x]<=end for x in cited)):
                raise ValueError("b_segment_unobserved_time_ref")
            if name=="B3" and any(x not in ROLES for x in seg.get("roles",[])):
                raise ValueError("b3_segment_taxonomy_invalid")
            if name=="B7" and seg.get("cta_type") not in CTA_TYPES:
                raise ValueError("b7_segment_taxonomy_invalid")
    b6=group_b["blocks"]["B6"]
    geometry=(b6.get("ocr_style_candidates") or {}).get("observations",[])
    if len(geometry)>48 or any(x.get("frame_ref") not in frame_at or
                               _valid_time(x.get("observed_sec"),duration)!=frame_at[x["frame_ref"]]
                               for x in geometry):
        raise ValueError("b6_unverified_ocr_geometry")
    b8=group_b["blocks"]["B8"]
    mapping=b8.get("taxonomy_match") or {}
    if mapping.get("state")=="catalog_matched":
        if (mapping.get("mi_approved_topic_match") is not True
            or any(type(mapping.get(x)) is not int or mapping[x]<=0
                   for x in ("category_id","topic_id"))
            or type(mapping.get("catalog_sha256")) is not str or
               SHA.fullmatch(mapping["catalog_sha256"]) is None):
            raise ValueError("b8_taxonomy_lineage_invalid")
    # A full original B receipt still requires independently proven coverage
    # and approved semantics. Do not claim it from model proposals.
    report={
        "schema_version":VERSION,"source_media_sha256":source_sha256,
        "group_a_report_sha256":group_a["report_sha256"],
        "duration_sec":str(duration),
        "blocks":validated,
        "b8_catalog_sha256":mapping.get("catalog_sha256"),
        "b8_topic_id":mapping.get("topic_id"),
        "model_observations_partial":sum(
            1 for stage in validated.values() if stage["state"] not in ("unavailable",)),
        "complete_original_blocks":[],
        "original_full_group_b_certified":False,
        "unobserved_video_intervals":True,
        "no_qdp_dependencies":True,
        "state":"partial" if any(v["state"]=="unavailable" for v in validated.values())
            else "sampled_observations_only"
    }
    return {**report,"report_sha256":digest(report)}


def verify_group_b_report(report,source_sha256,group_a_sha):
    if (type(report) is not dict or report.get("schema_version")!=VERSION
        or report.get("source_media_sha256")!=source_sha256
        or report.get("group_a_report_sha256")!=group_a_sha
        or report.get("original_full_group_b_certified") is not False
        or report.get("no_qdp_dependencies") is not True):
        return False
    sha=report.get("report_sha256")
    if type(sha) is not str or SHA.fullmatch(sha) is None:
        return False
    try:
        return sha==digest({k:v for k,v in report.items() if k!="report_sha256"})
    except (TypeError,ValueError,OverflowError):
        return False
