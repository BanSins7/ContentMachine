"""GA18 deterministic Group A A003 assembly; evidence ≠ invented coverage.

GA18 assembles source-SHA-bound GA06, GA11, GA13, GA14 and GA15 facts,
and requires external verified full-coverage certifications for 'completed'.
No Group B CTA or global mixed-carousel timeline is ever created here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from .contracts_v1 import canonical_sha256
from .validation import (
    SnapshotError, STREAMS, _validate_technical, _validate_speech,
    _validate_text, _validate_audio, _validate_subtitles, _certificate,
    video_scope, utc_measured_at
)

SCHEMA="ga-video-snapshot/v1"
METHOD="group-a-atomic-assembly-v1"


def assemble_video_snapshot(manifest, *, media_asset_id, technical,
                            speech=None, screen_text=None, subtitles=None,
                            audio_events=None, certificates=None,
                            verify_coverage=None, verify_current=None,
                            measured_at=None):
    """Assemble one GA05 video asset; no DB or paid/provider I/O.

    technical is bind_technical(verified GA06 TechnicalMeasurement,
                                asset_sha=manifest_item["sha256"]).
    Other observations are unmodified checksum-verified GA11/13/14/15
    extractor results. Genuine full coverage is independently vouched for by
    verify_coverage(cert) after validating the certificate scope/digest; a
    SHA by itself NEVER authorizes data.
    """
    m,item,scope=video_scope(manifest,media_asset_id)
    if not callable(verify_current) or verify_current(scope) is not True:
        raise SnapshotError("media_rights_unverified")
    duration,scene_rows,technical_data=_validate_technical(item["sha256"],technical)
    speech_data=_validate_speech(speech,asset_sha=item["sha256"],duration=duration)
    text_data=_validate_text(screen_text,asset_sha=item["sha256"],duration=duration)
    audio_data=_validate_audio(audio_events,asset_sha=item["sha256"],duration=duration)
    subtitle_truth,subtitle_sha=_validate_subtitles(subtitles,asset_sha=item["sha256"])
    certs={} if certificates is None else certificates
    if type(certs) is not dict or set(certs)-set(STREAMS):
        raise SnapshotError("invalid_coverage_certificate_set")
    details={"speech":speech_data,"screen_text":text_data,"audio":audio_data}
    statuses={"scene":"complete"}
    coverage={}
    for stream in STREAMS:
        proof=certs.get(stream)
        observation=details[stream]
        if proof is not None and (observation["digest"] is None or
                                  observation["status"]=="unavailable"):
            raise SnapshotError("cannot_certify_missing_evidence")
        certified=_certificate(
            proof,stream=stream,asset_sha=item["sha256"],
            evidence_sha=observation["digest"],verify_coverage=verify_coverage
        )
        if certified and proof["verified_absence"] and observation["rows"]:
            raise SnapshotError("absence_contradicts_positive_events")
        if certified and stream=="speech" and (observation["spoken_words"] is None
                                                 or observation["speech_duration_sec"] is None):
            raise SnapshotError("certified_speech_missing_metrics")
        statuses[stream]="complete" if certified else observation["status"]
        coverage[stream]={
            "status":statuses[stream],
            "verified_complete":certified,
            "observation_sha256":observation["digest"],
            "certificate_sha256":proof["certificate_sha256"] if certified else None,
            "reason_code":None if certified else observation["reason"] or "full_coverage_not_certified",
        }
    coverage["scene"]={
        "status":"complete","verified_complete":True,
        "observation_sha256":technical["observation_sha256"],
        "certificate_sha256":None,"reason_code":None,
    }
    # Subtitle tri-state is independent; a positive embedded stream or a
    # properly audited negative review may be known without dense OCR.
    is_complete=(all(statuses[x]=="complete" for x in ("scene",)+STREAMS)
                 and subtitle_truth in ("true","false"))
    observed_at=utc_measured_at(measured_at or datetime.now(timezone.utc))
    reasons={stream:value["reason_code"] for stream,value in coverage.items()
             if value["reason_code"] is not None}
    if subtitle_truth=="unknown":
        reasons["subtitles"]="subtitle_presence_not_verified"
    core={
        "schema_version":SCHEMA,"method_version":METHOD,
        "project_id":m["project_id"],"qdp_run_id":m["qdp_run_id"],
        "qdp_input_sha256":m["qdp_input_sha256"],"manifest_sha256":m["manifest_sha256"],
        "video_id":m["video_id"],"source_id":m["source_id"],
        "source_policy_version_id":m["source_policy_version_id"],
        "media_asset_id":item["media_asset_id"],
        "source_media_sha256":item["sha256"],
        "content_kind":m["content_kind"],
        "duration_sec":format(duration,".3f"),
        "aspect_ratio":technical_data["aspect_ratio"],
        "analysis_state":"completed" if is_complete else "partial",
        "scene_status":statuses["scene"],"speech_status":statuses["speech"],
        "screen_text_status":statuses["screen_text"],"audio_status":statuses["audio"],
        "has_subtitles":subtitle_truth,
        "spoken_words":speech_data["spoken_words"],
        "speech_duration_sec":speech_data["speech_duration_sec"],
        "cta_start_sec":None,"measured_at":observed_at,
        "coverage":coverage,
        "measurement_methods":{
            "ga18":METHOD,
            "ga_asset_sha256":item["sha256"],  # GA17 read-only selector contract
            "technical_sha256":technical["observation_sha256"],
            "speech_sha256":speech_data["digest"],
            "screen_text_sha256":text_data["digest"],
            "subtitle_sha256":subtitle_sha,
            "audio_events_sha256":audio_data["digest"],
        },
        "unavailable_reasons":reasons,
        "scenes":scene_rows,"speech_segments":speech_data["rows"],
        "screen_text_segments":text_data["rows"],"audio_events":audio_data["rows"],
        "time_origin":"video_asset_local_zero",
        "global_carousel_duration_sec":None,
    }
    if verify_current(scope) is not True:
        raise SnapshotError("media_rights_revoked_during_assembly")
    return {**core,"observation_sha256":canonical_sha256(core)}


def validate_video_snapshot(manifest,raw):
    """Strict replay/cross-version/summary consistency gate before SQL writes.

    A digest cannot authenticate media facts alone. The writer additionally
    requires live QDP+source policy and DB media SHA before commit.
    """
    if type(raw) is not dict or set(raw)!={
        "schema_version","method_version","project_id","qdp_run_id",
        "qdp_input_sha256","manifest_sha256","video_id","source_id",
        "source_policy_version_id","media_asset_id","source_media_sha256",
        "content_kind","duration_sec","aspect_ratio","analysis_state",
        "scene_status","speech_status","screen_text_status","audio_status",
        "has_subtitles","spoken_words","speech_duration_sec","cta_start_sec",
        "measured_at","coverage","measurement_methods","unavailable_reasons",
        "scenes","speech_segments","screen_text_segments","audio_events",
        "time_origin","global_carousel_duration_sec","observation_sha256"
    } or raw.get("schema_version")!=SCHEMA or raw.get("method_version")!=METHOD:
        raise SnapshotError("invalid_video_snapshot_shape")
    m,item,scope=video_scope(manifest,raw["media_asset_id"])
    for key,value in (
        ("project_id",m["project_id"]),("qdp_run_id",m["qdp_run_id"]),
        ("qdp_input_sha256",m["qdp_input_sha256"]),
        ("manifest_sha256",m["manifest_sha256"]),
        ("video_id",m["video_id"]),("source_id",m["source_id"]),
        ("source_policy_version_id",m["source_policy_version_id"]),
        ("source_media_sha256",item["sha256"]),
        ("content_kind",m["content_kind"]),
    ):
        if raw[key]!=value:
            raise SnapshotError("video_snapshot_lineage_mismatch")
    from .validation import fixed, _intervals
    duration=fixed(raw["duration_sec"],positive=True)
    if ("duration_sec" in item and item["duration_sec"]!=raw["duration_sec"]):
        raise SnapshotError("video_manifest_duration_mismatch")
    if type(raw["aspect_ratio"]) is not str or not re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*",raw["aspect_ratio"]):
        raise SnapshotError("video_snapshot_aspect_invalid")
    if raw["cta_start_sec"] is not None or raw["global_carousel_duration_sec"] is not None or raw["time_origin"]!="video_asset_local_zero":
        raise SnapshotError("video_snapshot_fabricated_timeline")
    from datetime import datetime
    try:
        parsed=datetime.fromisoformat(raw["measured_at"].replace("Z","+00:00"))
    except (AttributeError,ValueError) as exc:
        raise SnapshotError("snapshot_timestamp_invalid") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds()!=0:
        raise SnapshotError("snapshot_timestamp_invalid")
    segments={}
    for name,kind in (("scenes","scene"),("speech_segments","speech"),
                      ("screen_text_segments","screen_text"),("audio_events","audio")):
        segments[name]=_intervals(raw[name],duration=duration,kind=kind,
                                  max_rows=300 if kind=="scene" else 12000)
    if not segments["scenes"] or segments["scenes"][0]["start_sec"]!="0.000":
        raise SnapshotError("snapshot_scene_coverage_invalid")
    end="0.000"
    for scene in segments["scenes"]:
        if scene["start_sec"]!=end:
            raise SnapshotError("snapshot_scene_coverage_invalid")
        end=scene["end_sec"]
    if end!=raw["duration_sec"]:
        raise SnapshotError("snapshot_scene_coverage_invalid")
    if (type(raw["spoken_words"]) not in (int,type(None)) or
        (raw["spoken_words"] is not None and raw["spoken_words"]<0)):
        raise SnapshotError("snapshot_spoken_words_invalid")
    if raw["speech_duration_sec"] is not None and fixed(raw["speech_duration_sec"])>duration:
        raise SnapshotError("snapshot_speech_duration_invalid")
    if segments["speech_segments"] and (
        raw["spoken_words"] is None or raw["speech_duration_sec"] is None):
        raise SnapshotError("snapshot_speech_segments_missing_metrics")
    if segments["speech_segments"] and raw["spoken_words"]==0:
        raise SnapshotError("snapshot_spoken_words_contradiction")
    if type(raw["coverage"]) is not dict or set(raw["coverage"])!={"scene","speech","screen_text","audio"}:
        raise SnapshotError("snapshot_coverage_invalid")
    for name in ("scene","speech","screen_text","audio"):
        rec=raw["coverage"][name]
        if type(rec) is not dict or set(rec)!={"status","verified_complete","observation_sha256","certificate_sha256","reason_code"}:
            raise SnapshotError("snapshot_coverage_invalid")
        if rec["status"]!=raw[name+"_status"] or rec["status"] not in ("complete","partial","unavailable"):
            raise SnapshotError("snapshot_stream_state_conflict")
        if rec["verified_complete"] is not (rec["status"]=="complete"):
            raise SnapshotError("snapshot_coverage_conflict")
        if name!="scene" and rec["status"]=="complete" and rec["certificate_sha256"] is None:
            raise SnapshotError("snapshot_certificate_missing")
    if (raw["scene_status"]!="complete"
        or raw["has_subtitles"] not in ("true","false","unknown")
        or type(raw["measurement_methods"]) is not dict
        or raw["measurement_methods"].get("ga_asset_sha256")!=item["sha256"]
        or type(raw["unavailable_reasons"]) is not dict):
        raise SnapshotError("snapshot_metadata_invalid")
    full=all(raw[name+"_status"]=="complete" for name in ("scene","speech","screen_text","audio")) and raw["has_subtitles"]!="unknown"
    if raw["analysis_state"]!=("completed" if full else "partial"):
        raise SnapshotError("snapshot_false_completion")
    if type(raw["observation_sha256"]) is not str or (
        canonical_sha256({k:v for k,v in raw.items() if k!="observation_sha256"})
        != raw["observation_sha256"]
    ):
        raise SnapshotError("video_snapshot_digest_mismatch")
    return dict(raw)
