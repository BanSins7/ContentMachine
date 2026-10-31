"""Marketing-native, content-addressed A → B evidence handoff.

A verified local media asset and its per-frame observed PTS are the *only*
media authority. This contract deliberately has no QDP identifiers, runtime
imports, or external Group A/B/C persistence dependencies. The SHA is an
integrity checksum, not a signature or a substitute for the media rights
re-verification performed in media_worker.process().
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re

SCHEMA="mi-native-media-evidence/v1"
HEX=re.compile(r"[a-f0-9]{64}\Z")
PID=re.compile(r"[a-f0-9]{32}\Z")
REF=re.compile(r"ga-frame:f[0-9]{3}-([a-f0-9]{64})\Z")
BLOCKS=tuple("B"+str(n) for n in range(1,9))


def _hash(value):
    return sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,
        separators=(",",":"),allow_nan=False).encode("utf-8")).hexdigest()


def _positive_time(value,duration):
    try:
        d=Decimal(str(value))
    except (ValueError,TypeError,InvalidOperation):
        return False
    return d.is_finite() and 0<=d<duration


def make_receipt(*,project_id:str,video_id:int,source_id:str,
                 source_version_id:str,media_asset_id:str,media_sha256:str,
                 duration_sec,visual:dict,group_b:dict,method:str)->dict:
    """Index the actual GA frame identities and B citations for one video.

    This does not certify that a video was inspected continuously. No video
    or image bytes are repeated in the resulting immutable JSON.
    """
    if type(project_id) is not str or not PID.fullmatch(project_id):
        raise ValueError("mi_project_invalid")
    if type(video_id) is not int or video_id<=0:
        raise ValueError("mi_video_id_invalid")
    if type(media_sha256) is not str or not HEX.fullmatch(media_sha256):
        raise ValueError("mi_media_sha_invalid")
    if any(type(x) is not str or not x for x in (source_id,source_version_id,media_asset_id,method)):
        raise ValueError("mi_asset_identity_invalid")
    try:
        duration=Decimal(str(duration_sec))
    except (InvalidOperation,ValueError,TypeError):
        raise ValueError("mi_duration_invalid") from None
    if not duration.is_finite() or not 0<duration<=600:
        raise ValueError("mi_duration_out_of_bounds")
    if type(visual) is not dict or visual.get("source_media_sha256")!=media_sha256:
        raise ValueError("mi_visual_media_mismatch")
    if type(group_b) is not dict or set(group_b.get("blocks",{}))!=set(BLOCKS):
        raise ValueError("mi_group_b_blocks_missing")
    from .group_a_report import verify_group_a_report
    group_a=visual.get("group_a_report")
    if method in ("mi-abc-audio-expanded-media/v3","mi-abc-b8-native-media/v4"):
        if not verify_group_a_report(group_a,media_sha256):
            raise ValueError("mi_group_a_report_unverified")
    elif group_a is not None and not verify_group_a_report(group_a,media_sha256):
        raise ValueError("mi_group_a_report_unverified")
    from .group_b_report import verify_group_b_report
    b_report=group_b.get("native_report")
    if method=="mi-abc-b8-native-media/v4":
        if not verify_group_b_report(b_report,media_sha256,
               group_a["report_sha256"]):
            raise ValueError("mi_group_b_report_unverified")
    elif b_report is not None:
        if group_a is None or not verify_group_b_report(
                b_report,media_sha256,group_a["report_sha256"]):
            raise ValueError("mi_group_b_report_unverified")
    frames=[]
    seen=set()
    for row in visual.get("frames",[]):
        if type(row) is not dict:
            raise ValueError("mi_frame_row_invalid")
        ref=row.get("ref")
        matched=REF.fullmatch(ref) if type(ref) is str else None
        if (not matched or matched.group(1)!=row.get("png_sha256") or ref in seen
            or not _positive_time(row.get("observed_sec"),duration)):
            raise ValueError("mi_frame_reference_invalid")
        seen.add(ref)
        frames.append({"frame_ref":ref,"png_sha256":row["png_sha256"],
                       "observed_sec":str(row["observed_sec"])})
    if len(frames)>48:
        raise ValueError("mi_frame_limit_exceeded")
    for block in BLOCKS:
        stage=group_b["blocks"][block]
        if type(stage) is not dict or type(stage.get("state")) is not str:
            raise ValueError("mi_b_stage_invalid")
        refs=stage.get("evidence_frame_refs")
        if type(refs) is not list or len(refs)!=len(set(refs)) or any(r not in seen for r in refs):
            raise ValueError("mi_b_uses_unobserved_frame")
    has_source=bool(frames) or bool(visual.get("speech",{}).get("observation_sha256"))
    source_trace={
        "schema_version":SCHEMA,"project_id":project_id,"video_id":video_id,
        "source_id":source_id,"source_version_id":source_version_id,
        "media_asset_id":media_asset_id,"media_sha256":media_sha256,
        "duration_sec":str(duration),"method":method,
        "frames":frames,
        "group_a_report_sha256":group_a.get("report_sha256") if group_a else None,
        "group_b_report_sha256":b_report.get("report_sha256") if b_report else None,
        "asr_observation_sha256":visual.get("speech",{}).get("observation_sha256"),
        "subtitle_observation_sha256":(visual.get("group_a_subtitles") or {}).get("observation_sha256"),
        "text_tracking_sha256":(visual.get("group_a_text_tracking") or {}).get("observation_sha256"),
        "b_states":{b:group_b["blocks"][b]["state"] for b in BLOCKS},
        "coverage_state":"sampled_media_with_traced_observations" if has_source else "technical_only",
        "full_video_visual_coverage":False,
        "full_group_b_certified":False,
    }
    if any(x is not None and (type(x) is not str or not HEX.fullmatch(x))
           for x in (source_trace["group_a_report_sha256"],
                     source_trace["group_b_report_sha256"],
                     source_trace["asr_observation_sha256"],
                     source_trace["subtitle_observation_sha256"],
                     source_trace["text_tracking_sha256"])):
        raise ValueError("mi_observation_digest_invalid")
    return {**source_trace,"receipt_sha256":_hash(source_trace)}


def verify_receipt(receipt:dict,*,media_sha256:str|None=None)->bool:
    if type(receipt) is not dict or receipt.get("schema_version")!=SCHEMA:
        return False
    if media_sha256 is not None and receipt.get("media_sha256")!=media_sha256:
        return False
    checksum=receipt.get("receipt_sha256")
    if type(checksum) is not str or not HEX.fullmatch(checksum):
        return False
    try:
        return checksum==_hash({k:v for k,v in receipt.items() if k!="receipt_sha256"})
    except (TypeError,ValueError,OverflowError):
        return False
