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
        if certified and not observation["rows"] and not proof["verified_absence"]:
            raise SnapshotError("completed_zero_requires_verified_absence")
        if (certified and stream=="speech" and
            proof["verified_absence"] and observation["spoken_words"]!=0):
            raise SnapshotError("speech_absence_contradicts_measured_words")
        if (certified and stream=="screen_text" and screen_text is not None
            and (screen_text.get("tracks") or
                 (screen_text.get("sampled_screen_word_count") or 0)>0)):
            # GA13 sparse instants do not locate continuous onset/offset.
            # A different independently measured dense stream is needed to
            # create honest completed text intervals and first_text_sec.
            raise SnapshotError("positive_sparse_ocr_requires_dense_producer")
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
        "coverage_certificates":dict(certs),
        "measurement_methods":{
            "ga18":METHOD,
            "ga_asset_sha256":item["sha256"],  # GA17 read-only selector contract
            "technical_sha256":technical["observation_sha256"],
            "speech_sha256":speech_data["digest"],
            "asr_sha256":speech_data["asr_sha256"],
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
        "measured_at","coverage","coverage_certificates","measurement_methods","unavailable_reasons",
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
    if type(raw["coverage_certificates"]) is not dict or set(raw["coverage_certificates"])-set(STREAMS):
        raise SnapshotError("snapshot_certificates_invalid")
    for name in ("scene","speech","screen_text","audio"):
        rec=raw["coverage"][name]
        if type(rec) is not dict or set(rec)!={"status","verified_complete","observation_sha256","certificate_sha256","reason_code"}:
            raise SnapshotError("snapshot_coverage_invalid")
        if rec["status"]!=raw[name+"_status"] or rec["status"] not in ("complete","partial","unavailable"):
            raise SnapshotError("snapshot_stream_state_conflict")
        if rec["verified_complete"] is not (rec["status"]=="complete"):
            raise SnapshotError("snapshot_coverage_conflict")
        field={
            "scene":"technical_sha256","speech":"speech_sha256",
            "screen_text":"screen_text_sha256","audio":"audio_events_sha256"
        }[name]
        if (type(raw["measurement_methods"]) is not dict
            or rec["observation_sha256"]!=raw["measurement_methods"].get(field)):
            raise SnapshotError("snapshot_coverage_source_digest_mismatch")
        if name!="scene":
            certificate=raw["coverage_certificates"].get(name)
            if rec["status"]=="complete" and (
                type(certificate) is not dict or
                certificate.get("certificate_sha256")!=rec["certificate_sha256"]
            ):
                raise SnapshotError("snapshot_certificate_missing")
            if rec["status"]!="complete" and certificate is not None:
                raise SnapshotError("snapshot_unused_certificate")
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


def rebind_prepared_mixed(manifest,prepared,*,read_a003,verify_current,
                          audio_proofs=None):
    """O(slides) pure metadata relink of *already decoded* GA17 OCR rows.

    Invoked inside the GA18 A003+A004 transaction after assigning A003 IDs.
    No image decode, FFmpeg, OCR or model inference inside a DB transaction.
    The input may have explicit missing A003, but must be GA17-valid.
    """
    from copy import deepcopy
    from .extractors.mixed_carousel import (
        validate_mixed_observation, _bind_video, _audio_proof, _summary
    )
    from .extractors.carousel import _scope
    from .contracts_v1 import media_manifest_v1
    before=validate_mixed_observation(manifest,prepared)
    m=media_manifest_v1(manifest)
    if not callable(read_a003) or not callable(verify_current):
        raise SnapshotError("mixed_rebind_ports_missing")
    audio_proofs={} if audio_proofs is None else audio_proofs
    video_assets={i["media_asset_id"] for i in m["items"] if i["kind"]=="video"}
    if type(audio_proofs) is not dict or any(
        type(key) is not int or key not in video_assets for key in audio_proofs
    ):
        raise SnapshotError("mixed_rebind_audio_map_invalid")
    updated=deepcopy(before)
    audio_states=[]
    for item,output in zip(m["items"],updated["items"]):
        if verify_current(_scope(m,item)) is not True:
            raise SnapshotError("media_rights_unverified")
        if item["kind"]=="video":
            raw=read_a003(m,item)
            bound=None if raw is None else _bind_video(m,item,raw)
            output["video_a_analysis_id"]=bound["analysis_id"] if bound else None
            output["video_binding_sha256"]=bound["binding_sha256"] if bound else None
            output["video_timeline"]=bound["timeline"] if bound else []
            output["duration_sec"]=bound["duration_sec"] if bound else None
            output["aspect_ratio"]=bound["aspect_ratio"] if bound else None
            output["reason_code"]=(
                None if bound and bound["state"]=="completed" else
                "a003_analysis_partial" if bound else "a003_analysis_missing"
            )
            audio_states.append(_audio_proof(item,audio_proofs))
        if verify_current(_scope(m,item)) is not True:
            raise SnapshotError("media_rights_revoked_during_rebind")
    updated.update(_summary(updated["items"],audio_states))
    updated["observation_sha256"]=canonical_sha256({
        k:v for k,v in updated.items() if k!="observation_sha256"
    })
    return validate_mixed_observation(m,updated)
