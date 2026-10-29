"""GA18 fail-closed video observation normalization and source/clock gates.

Pure data validation: no SQL, file/network access, worker dispatch or inference.
GA10/11/13/14/15 observations are *partial* unless a separately authorized
continuous-coverage certificate proves otherwise.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re

from .contracts import TechnicalMeasurement
from .contracts_v1 import canonical_sha256, media_manifest_v1

SHA=re.compile(r"^[a-f0-9]{64}$")
TIME=re.compile(r"^(0|[1-9][0-9]*)\.[0-9]{3}$")
PCT=re.compile(r"^(?:[0-9]|[1-9][0-9]|100)\.[0-9]{2}$")
STREAMS=("speech","screen_text","audio")
SCHEMAS={
    "speech":"ga-speech-metrics/v1",
    "screen_text":"ga-screen-text-tracking/v1",
    "audio":"ga-audio-events/v1",
    "subtitles":"ga-subtitle-presence/v1",
}
STATUS={"complete","partial","unavailable"}
EVENTS={"music","sound_effect","other"}


class SnapshotError(ValueError):
    def __init__(self, code):
        self.code=code
        super().__init__("GA18 snapshot rejected: "+code)


def fixed(raw, *, positive=False):
    if type(raw) is not str or TIME.fullmatch(raw) is None:
        raise SnapshotError("noncanonical_video_clock")
    value=Decimal(raw)
    if value>Decimal("600.000") or (value<=0 if positive else value<0):
        raise SnapshotError("invalid_video_clock")
    return value


def _digest(raw, *, schema, asset_sha):
    if type(raw) is not dict or raw.get("schema_version")!=schema:
        raise SnapshotError("incompatible_stream_schema")
    if raw.get("source_media_sha256")!=asset_sha:
        raise SnapshotError("cross_asset_stream")
    fingerprint=raw.get("observation_sha256")
    if type(fingerprint) is not str or SHA.fullmatch(fingerprint) is None or (
        canonical_sha256({k:v for k,v in raw.items() if k!="observation_sha256"})!=fingerprint
    ):
        raise SnapshotError("stream_observation_digest_mismatch")
    return fingerprint


def _intervals(rows, *, duration, kind, max_rows=12000):
    if type(rows) is not list or len(rows)>max_rows:
        raise SnapshotError("invalid_segment_set")
    normalized=[]
    previous=None
    seen=set()
    for row in rows:
        if type(row) is not dict or set(row)!=(
            {"scene_index","start_sec","end_sec"} if kind=="scene" else
            {"start_sec","end_sec","spoken_text","method_name","confidence_pct"} if kind=="speech" else
            {"start_sec","end_sec","screen_text","method_name","confidence_pct"} if kind=="screen_text" else
            {"start_sec","end_sec","event_type","label","method_name","confidence_pct"}
        ):
            raise SnapshotError("invalid_segment_shape")
        start=fixed(row["start_sec"])
        end=fixed(row["end_sec"],positive=True)
        if start>=end or end>duration:
            raise SnapshotError("segment_outside_video")
        key=(start,end)
        if previous is not None and key<previous:
            raise SnapshotError("noncanonical_segment_order")
        previous=key
        unique=(kind,tuple(sorted((k,str(v)) for k,v in row.items())))
        if unique in seen:
            raise SnapshotError("duplicate_measured_segment")
        seen.add(unique)
        if kind=="scene":
            if type(row["scene_index"]) is not int or row["scene_index"]!=len(normalized)+1:
                raise SnapshotError("scene_index_invalid")
        elif kind=="audio":
            if row["event_type"] not in EVENTS or type(row["event_type"]) is not str:
                raise SnapshotError("audio_event_kind_invalid")
        else:
            field="spoken_text" if kind=="speech" else "screen_text"
            if type(row[field]) is not str or len(row[field])>10000:
                raise SnapshotError("segment_text_invalid")
        if kind!="scene":
            if row["method_name"] is not None and (type(row["method_name"]) is not str or len(row["method_name"])>120):
                raise SnapshotError("segment_method_invalid")
            conf=row["confidence_pct"]
            if conf is not None and (type(conf) is not str or PCT.fullmatch(conf) is None):
                raise SnapshotError("segment_confidence_invalid")
            if kind=="audio" and row["label"] is not None and (type(row["label"]) is not str or len(row["label"])>200):
                raise SnapshotError("audio_event_label_invalid")
        normalized.append(dict(row))
    return normalized


def _certificate(cert, *, stream, asset_sha, evidence_sha, verify_coverage):
    """A third-party host must independently authorize an audited full scope.

    A checksum is *integrity*, NOT an authenticated human review signature.
    No GA11/13/15 stage-local 'partial' may upgrade itself by merely setting
    flags in its own output. The host owns a stronger full-coverage producer.
    """
    if cert is None:
        return False
    if not callable(verify_coverage):
        raise SnapshotError("coverage_verifier_required")
    if type(cert) is not dict or set(cert)!={
        "schema_version","stream","source_media_sha256","observation_sha256",
        "full_video_coverage","scope","verified_absence","method",
        "certificate_sha256"
    } or cert["schema_version"]!="ga-stream-coverage/v1":
        raise SnapshotError("coverage_certificate_invalid")
    if (cert["stream"]!=stream or cert["source_media_sha256"]!=asset_sha
        or cert["observation_sha256"]!=evidence_sha
        or cert["full_video_coverage"] is not True
        or cert["scope"]!="whole_verified_video_asset"
        or type(cert["verified_absence"]) is not bool
        or type(cert["method"]) is not str or not 1<=len(cert["method"])<=120):
        raise SnapshotError("coverage_certificate_scope_mismatch")
    if canonical_sha256({k:v for k,v in cert.items() if k!="certificate_sha256"})!=cert["certificate_sha256"]:
        raise SnapshotError("coverage_certificate_digest_mismatch")
    if verify_coverage(dict(cert)) is not True:
        raise SnapshotError("coverage_authorization_denied")
    return True


def _choose_evidence(asset_sha, name, raw):
    return _digest(raw,schema=SCHEMAS[name],asset_sha=asset_sha) if raw is not None else None


def bind_technical(measurement, *, asset_sha):
    if not isinstance(measurement,TechnicalMeasurement) or type(asset_sha) is not str or not SHA.fullmatch(asset_sha):
        raise SnapshotError("invalid_technical_evidence")
    raw=measurement.to_dict()
    # Decimal('0') from GA06 is semantically zero; A003 snapshots require
    # canonical millisecond precision, not the source's Decimal formatting.
    raw["duration_sec"]=format(measurement.duration_sec,".3f")
    raw["scenes"]=[
        {"scene_index":scene.index,"start_sec":format(scene.start_sec,".3f"),
         "end_sec":format(scene.end_sec,".3f")}
        for scene in measurement.scenes
    ]
    core={"schema_version":"ga18-technical-bound/v1",
          "source_media_sha256":asset_sha,"measurement":raw}
    return {**core,"observation_sha256":canonical_sha256(core)}


def _validate_technical(asset_sha, technical):
    if type(technical) is not dict or set(technical)!={
        "schema_version","source_media_sha256","measurement","observation_sha256"
    } or technical["schema_version"]!="ga18-technical-bound/v1":
        raise SnapshotError("invalid_technical_envelope")
    if technical["source_media_sha256"]!=asset_sha or (
        canonical_sha256({k:v for k,v in technical.items() if k!="observation_sha256"})
        != technical["observation_sha256"]):
        raise SnapshotError("technical_digest_mismatch")
    m=technical["measurement"]
    if type(m) is not dict or set(m)!={
        "schema_version","method_version","duration_sec","aspect_ratio","scenes",
        "scene_status","speech_status","screen_text_status","audio_status",
        "has_subtitles","spoken_words","cta_start_sec"
    } or m["schema_version"]!="group-a-measurement/v1" or (
        m["scene_status"]!="complete" or
        any(m[x]!="not_run" for x in ("speech_status","screen_text_status","audio_status"))
        or m["has_subtitles"]!="unknown" or m["spoken_words"] is not None
        or m["cta_start_sec"] is not None
    ):
        raise SnapshotError("technical_contains_fabricated_truth")
    duration=fixed(m["duration_sec"],positive=True)
    if type(m["aspect_ratio"]) is not str or re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*",m["aspect_ratio"]) is None:
        raise SnapshotError("technical_aspect_ratio_invalid")
    scenes=_intervals(m["scenes"],duration=duration,kind="scene",max_rows=300)
    if not scenes or fixed(scenes[0]["start_sec"])!=0:
        raise SnapshotError("technical_scene_coverage_invalid")
    previous=Decimal(0)
    for scene in scenes:
        if fixed(scene["start_sec"])!=previous:
            raise SnapshotError("technical_scene_coverage_invalid")
        previous=fixed(scene["end_sec"])
    if previous!=duration:
        raise SnapshotError("technical_scene_coverage_invalid")
    return duration,scenes,m


def _validate_speech(raw,*,asset_sha,duration):
    sha=_choose_evidence(asset_sha,"speech",raw)
    if raw is None:
        return {"status":"unavailable","spoken_words":None,"speech_duration_sec":None,"rows":[],"digest":None,"asr_sha256":None,"reason":"speech_not_run"}
    asr_sha=raw.get("asr_observation_sha256")
    if type(asr_sha) is not str or SHA.fullmatch(asr_sha) is None:
        raise SnapshotError("speech_upstream_transcript_digest_missing")
    if raw.get("transcript_source") not in (
        "none","local_faster_whisper","psi_asr","psi_srt",
        "sidecar_srt","sidecar_txt"
    ):
        raise SnapshotError("speech_transcript_source_invalid")
    proj=raw.get("a003_speech_projection")
    if type(proj) is not dict or set(proj)!={"speech_status","spoken_words","speech_duration_sec"}:
        raise SnapshotError("speech_projection_invalid")
    if proj["speech_status"] not in ("partial","unavailable"):
        raise SnapshotError("speech_stage_overclaims_coverage")
    if proj["spoken_words"]!=raw.get("spoken_words") or proj["speech_duration_sec"]!=raw.get("speech_duration_sec"):
        raise SnapshotError("speech_projection_tampered")
    words=proj["spoken_words"]
    if words is not None and (type(words) is not int or words<0):
        raise SnapshotError("speech_words_invalid")
    active=proj["speech_duration_sec"]
    if active is not None and fixed(active)>duration:
        raise SnapshotError("speech_duration_outside_video")
    if raw.get("video_duration_sec") not in (None,format(duration,".3f")):
        raise SnapshotError("speech_video_duration_mismatch")
    rows=_intervals(raw.get("a003_speech_segments_projection"),duration=duration,kind="speech")
    if rows and raw.get("transcript_source") not in (
        "local_faster_whisper","psi_asr"
    ):
        raise SnapshotError("captions_not_audible_speech_proof")
    if rows and (raw.get("video_duration_sec")!=format(duration,".3f")
                 or raw.get("clock_status") in ("unknown",None)
                 or raw.get("video_clock_offset_sec") is None):
        raise SnapshotError("speech_timebase_unverified")
    if rows and (words is None or active is None):
        raise SnapshotError("speech_timed_rows_without_metrics")
    if words==0 and rows:
        raise SnapshotError("speech_zero_with_positive_segments")
    return {"status":proj["speech_status"],"spoken_words":words,
            "speech_duration_sec":active,"rows":rows,"digest":sha,
            "asr_sha256":asr_sha,
            "reason":None if proj["speech_status"]=="partial" else "speech_evidence_unavailable"}


def _validate_text(raw,*,asset_sha,duration):
    sha=_choose_evidence(asset_sha,"screen_text",raw)
    if raw is None:
        return {"status":"unavailable","rows":[],"digest":None,"reason":"screen_text_not_run"}
    if raw.get("sampling_truth")!="sparse_instants_only" or raw.get("continuous_intervals_verified") is not False:
        raise SnapshotError("screen_text_coverage_contract_invalid")
    if type(raw.get("tracks")) is not list:
        raise SnapshotError("screen_text_tracks_contract_invalid")
    counted=raw.get("sampled_screen_word_count")
    if counted is not None and (type(counted) is not int or counted<0):
        raise SnapshotError("screen_text_sampled_count_invalid")
    if fixed(raw.get("source_duration_sec"),positive=True)!=duration:
        raise SnapshotError("screen_text_duration_mismatch")
    rows=_intervals(raw.get("a003_screen_text_projection"),duration=duration,kind="screen_text")
    if rows:
        raise SnapshotError("sparse_ocr_cannot_prove_intervals")
    status=raw.get("status")
    if status not in ("partial","unavailable"):
        raise SnapshotError("screen_text_stage_overclaims_coverage")
    return {"status":status,"rows":[],"digest":sha,
            "reason":None if status=="partial" else "screen_text_evidence_unavailable"}


def _validate_audio(raw,*,asset_sha,duration):
    sha=_choose_evidence(asset_sha,"audio",raw)
    if raw is None:
        return {"status":"unavailable","rows":[],"digest":None,"reason":"audio_not_run"}
    state=raw.get("state")
    if state not in ("classified_partial","partial_failure","unavailable","no_audio_stream","digital_silence","classifier_unavailable"):
        raise SnapshotError("audio_state_unexpected")
    rows=_intervals(raw.get("a003_audio_events_projection"),duration=duration,kind="audio")
    if rows and (raw.get("a003_mapping_state")!="verified_audio_video_timebase"
                 or raw.get("video_clock_state") in (None,"unknown")
                 or raw.get("video_duration_sec")!=format(duration,".3f")):
        raise SnapshotError("audio_video_timebase_unverified")
    if state in ("unavailable","no_audio_stream","digital_silence","classifier_unavailable") and rows:
        raise SnapshotError("audio_absence_contradicts_events")
    if state in ("no_audio_stream","digital_silence") and raw.get("audio_absence_verified") is not True:
        raise SnapshotError("audio_absence_unverified")
    return {"status":"partial" if state in ("classified_partial","partial_failure","no_audio_stream","digital_silence")
            else "unavailable","rows":rows,"digest":sha,
            "reason":None if state in ("classified_partial","no_audio_stream","digital_silence") else
                ("audio_windows_failed" if state=="partial_failure" else "audio_evidence_unavailable")}


def _validate_subtitles(raw,*,asset_sha):
    sha=_choose_evidence(asset_sha,"subtitles",raw)
    if raw is None:
        return "unknown",None
    if raw.get("a003_has_subtitles_projection")!=raw.get("has_subtitles") or (
        raw["has_subtitles"] not in ("true","false","unknown")):
        raise SnapshotError("subtitle_truth_invalid")
    if raw["has_subtitles"]=="false" and raw.get("proof_kind")!="certified_dense_negative_review":
        raise SnapshotError("subtitle_false_without_dense_review")
    return raw["has_subtitles"],sha


def video_scope(manifest,asset_id):
    try:
        m=media_manifest_v1(manifest)
    except (ValueError,TypeError) as exc:
        raise SnapshotError("invalid_media_manifest") from exc
    if m["content_kind"] not in ("video","slideshow_video","mixed_carousel"):
        raise SnapshotError("non_video_media_kind")
    if type(asset_id) is not int or asset_id<=0:
        raise SnapshotError("invalid_video_asset_id")
    matches=[x for x in m["items"] if x["media_asset_id"]==asset_id and x["kind"]=="video"]
    if len(matches)!=1:
        raise SnapshotError("video_asset_not_in_manifest")
    item=matches[0]
    return m,item,{
        "project_id":m["project_id"],"qdp_run_id":m["qdp_run_id"],
        "qdp_input_sha256":m["qdp_input_sha256"],"video_id":m["video_id"],
        "source_id":m["source_id"],"source_policy_version_id":m["source_policy_version_id"],
        "manifest_sha256":m["manifest_sha256"],"media_asset_id":asset_id,
        "asset_sha256":item["sha256"],
    }


def utc_measured_at(value):
    if not isinstance(value,datetime) or value.utcoffset() is None:
        raise SnapshotError("measured_at_requires_utc")
    if value.utcoffset().total_seconds()!=0:
        raise SnapshotError("measured_at_requires_utc")
    return value.isoformat(timespec="milliseconds").replace("+00:00","Z")
