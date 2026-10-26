"""GA-MOD-08: read-only PSI transcript/vision reuse with explicit D1 lineage.

PSI source_version_id is a UUID and is NEVER Discovery videos.id (BIGINT).
The caller must independently verify a project-scoped GA media identity AND
provide an explicit PSI source/version mapping. No PSI code import, network,
provider, ASR, OCR or Sonnet invocation on cache miss.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from uuid import UUID

from ..contracts_v1 import canonical_sha256
from ..ports import negotiate, unavailable, STATE_AVAILABLE, PeerCapability

HEX=re.compile(r"^[a-f0-9]{64}$")
PROJECT=re.compile(r"^[a-f0-9]{32}$")
PSI_TRANSCRIPT="transcript/v1"
PSI_VISION="vision-prepare/v1"  # actual PSI output, not GA port name
GA_VISION="visual-preparation/v1"
MAX_DURATION_MS=600_000
MAX_SEGMENTS=12_000
MAX_FRAMES=8


class PSIReuseError(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__("PSI reuse rejected: "+code)


def _uuid(value):
    if type(value) is not str:
        raise PSIReuseError("invalid_psi_uuid")
    try:
        if str(UUID(value))!=value:
            raise ValueError()
    except (ValueError,AttributeError):
        raise PSIReuseError("invalid_psi_uuid") from None
    return value


def _hash(value,code="invalid_hash"):
    if type(value) is not str or not HEX.fullmatch(value):
        raise PSIReuseError(code)
    return value


def _ms(value,*,can_zero=True):
    if type(value) is not int or value<0 or (not can_zero and value==0) or value>MAX_DURATION_MS:
        raise PSIReuseError("invalid_psi_time")
    return value


def ms_to_seconds(ms):
    """Lossless PSI integer ms -> GA fixed-width seconds."""
    return format(Decimal(_ms(ms))/1000,".3f")


def seconds_to_ms(seconds):
    if type(seconds) is not str or not re.fullmatch(r"^(?:0|[1-9][0-9]*)\.[0-9]{3}$",seconds):
        raise PSIReuseError("invalid_ga_seconds")
    v=Decimal(seconds)*1000
    if v>MAX_DURATION_MS:
        raise PSIReuseError("invalid_ga_seconds")
    return int(v)


@dataclass(frozen=True,slots=True)
class PSIAssetBinding:
    project_id: str
    video_id: int               # Discovery D1 BIGINT, never PSI UUID
    media_sha256: str
    media_byte_size: int
    psi_source_id: str          # PSI UUID
    psi_source_version_id: str  # distinct PSI UUID
    psi_acquisition_job_id: str # PSI UUID, source of media provenance

    def __post_init__(self):
        if type(self.project_id) is not str or not PROJECT.fullmatch(self.project_id):
            raise PSIReuseError("invalid_project")
        if type(self.video_id) is not int or self.video_id<=0:
            raise PSIReuseError("invalid_discovery_video_id")
        _hash(self.media_sha256)
        if type(self.media_byte_size) is not int or not 1<=self.media_byte_size<=2*1024**3:
            raise PSIReuseError("invalid_media_size")
        for item in (self.psi_source_id,self.psi_source_version_id,self.psi_acquisition_job_id):
            _uuid(item)
        if self.psi_source_id==self.psi_source_version_id:
            raise PSIReuseError("psi_identity_conflation")

    def lineage(self):
        return {
            "project_id":self.project_id,"discovery_video_id":self.video_id,
            "source_media_sha256":self.media_sha256,
            "source_byte_size":self.media_byte_size,
            "psi_source_id":self.psi_source_id,
            "psi_source_version_id":self.psi_source_version_id,
            "psi_acquisition_job_id":self.psi_acquisition_job_id,
        }


def _envelope(record, binding, expected_schema, *, expected_config_sha256=None,
              expected_model_version=None):
    """Validate peer output provenance and immutable DB result digest.

    A record is produced by an authoritative PSI read-only reader. It still
    cannot grant D1 rights or remap a PSI source to a Discovery video.
    """
    if type(record) is not dict or set(record)!={
        "project_id","source_id","source_version_id","acquisition_job_id",
        "output_id","schema_version","output","output_sha256",
        "config_sha256","job_state","rights_ok"
    }:
        raise PSIReuseError("invalid_psi_record")
    if (record["project_id"]!=binding.project_id
        or record["source_id"]!=binding.psi_source_id
        or record["source_version_id"]!=binding.psi_source_version_id
        or record["acquisition_job_id"]!=binding.psi_acquisition_job_id):
        raise PSIReuseError("psi_lineage_mismatch")
    _uuid(record["output_id"])
    if record["schema_version"]!=expected_schema or record["job_state"] not in ("completed","partial"):
        raise PSIReuseError("psi_schema_or_state_mismatch")
    if record["rights_ok"] is not True:
        raise PSIReuseError("psi_rights_revoked")
    _hash(record["output_sha256"])
    if record["config_sha256"] is not None:
        _hash(record["config_sha256"])
    if expected_config_sha256 is not None and record["config_sha256"]!=_hash(expected_config_sha256):
        raise PSIReuseError("psi_config_mismatch")
    output=record["output"]
    if type(output) is not dict or output.get("schema_version")!=expected_schema:
        raise PSIReuseError("psi_output_schema_mismatch")
    try:
        digest=canonical_sha256(output)
    except ValueError as exc:
        raise PSIReuseError("invalid_psi_output") from exc
    if digest!=record["output_sha256"]:
        raise PSIReuseError("psi_output_digest_mismatch")
    if output.get("video_sha256")!=binding.media_sha256:
        raise PSIReuseError("psi_media_hash_mismatch")
    if expected_model_version is not None and output.get("model_version")!=expected_model_version:
        raise PSIReuseError("psi_model_version_mismatch")
    return output


def _text(value):
    if type(value) is not str or not value.strip() or len(value)>1400:
        raise PSIReuseError("invalid_psi_text")
    if any(ord(c)<32 and c not in "\t\n" for c in value):
        raise PSIReuseError("invalid_psi_text")
    return value


def normalize_transcript(record, binding, *, expected_config_sha256=None,
                         expected_model_version=None):
    output=_envelope(record,binding,PSI_TRANSCRIPT,
        expected_config_sha256=expected_config_sha256,
        expected_model_version=expected_model_version)
    if (output.get("source") not in ("srt","asr","none")
        or output.get("processing_status") not in ("completed","partial","unavailable")
        or output.get("speech_status") not in ("speech_detected","no_speech","asr_error","unknown")
        or type(output.get("normalizer_version")) is not str
        or not output["normalizer_version"].startswith("transcript-normalizer/")):
        raise PSIReuseError("psi_transcript_metadata_invalid")
    segs=output.get("segments")
    if type(segs) is not list or len(segs)>MAX_SEGMENTS:
        raise PSIReuseError("psi_segments_invalid")
    timing=output.get("timing_coverage")
    if type(timing) is not dict:
        raise PSIReuseError("psi_timing_missing")
    duration=timing.get("duration_ms")
    if duration is not None:
        _ms(duration,can_zero=False)
    observed=[]
    last_end=0
    for seg in segs:
        if type(seg) is not dict or not {"start_ms","end_ms","text","source","language"}<=set(seg):
            raise PSIReuseError("psi_segment_invalid")
        a=_ms(seg["start_ms"]);b=_ms(seg["end_ms"],can_zero=False)
        if a<last_end or b<=a or duration is None or b>duration+500:
            raise PSIReuseError("psi_segment_timing_invalid")
        if seg["source"]!=output["source"] or seg["source"] not in ("srt","asr"):
            raise PSIReuseError("psi_segment_source_mismatch")
        if type(seg["language"]) is not str or len(seg["language"])>35:
            raise PSIReuseError("psi_language_invalid")
        row={"start_sec":ms_to_seconds(a),"end_sec":ms_to_seconds(b),
             "text":_text(seg["text"]),"language":seg["language"],
             "origin":seg["source"],
             "confidence":None}
        if "confidence" in seg:
            confidence=seg["confidence"]
            if type(confidence) not in (int,float) or not 0<=confidence<=1:
                raise PSIReuseError("psi_confidence_invalid")
            row["confidence"]=str(Decimal(str(confidence)))
        observed.append(row);last_end=b
    if output.get("segment_count")!=len(segs):
        raise PSIReuseError("psi_segment_count_mismatch")
    if output["source"]=="none" and observed:
        raise PSIReuseError("psi_segment_source_mismatch")
    if output["speech_status"]=="speech_detected" and not observed:
        raise PSIReuseError("psi_speech_not_evidenced")
    # PSI status 'completed' means its normalization job ended; it DOES NOT
    # certify full Group A speech observation or a verified no-speech claim.
    status="partial" if observed else "unavailable"
    return {
        "schema_version":"ga-psi-transcript-reuse/v1",
        "source":"psi","status":status,
        "segments":observed,
        "language":output.get("detected_language") if type(output.get("detected_language")) is str else "unknown",
        "timing_origin":"psi_source_local_ms",
        "timed_coverage":"sparse_intervals" if observed else "unknown",
        "complete_speech_coverage":False,
        "verified_no_speech":False,
        "evidence_count":len(observed) if observed else None,
        "upstream":{"output_id":record["output_id"],"output_sha256":record["output_sha256"],
          "normalizer_version":output["normalizer_version"],
          "model":output.get("model"),"model_version":output.get("model_version"),
          "config_sha256":record["config_sha256"]},
        "media_sha256":binding.media_sha256,
    }


def normalize_vision(record,binding,*,expected_config_sha256=None):
    output=_envelope(record,binding,PSI_VISION,expected_config_sha256=expected_config_sha256)
    if (output.get("trust_level")!="local_frame_sample"
        or output.get("status") not in ("completed","partial")
        or type(output.get("frame_sampling")) is not dict
        or output["frame_sampling"].get("all_frames_analyzed") is not False):
        raise PSIReuseError("psi_visual_coverage_untrusted")
    frames=output.get("frames")
    if type(frames) is not list or not 1<=len(frames)<=MAX_FRAMES:
        raise PSIReuseError("psi_vision_frames_invalid")
    duration=_ms(output.get("video_duration_ms"),can_zero=False)
    spans=[]
    frame_ids=set()
    for f in frames:
        if type(f) is not dict or not {"frame_id","time_ms","sha256","byte_size","ocr","time_precision"}<=set(f):
            raise PSIReuseError("psi_frame_invalid")
        if (type(f["frame_id"]) is not str or not re.fullmatch(r"f[0-9]{3}",f["frame_id"])
            or f["frame_id"] in frame_ids or f["time_precision"]!="sample_only"):
            raise PSIReuseError("psi_frame_invalid")
        frame_ids.add(f["frame_id"])
        _hash(f["sha256"])
        if type(f["byte_size"]) is not int or f["byte_size"]<=0 or f["byte_size"]>180_000:
            raise PSIReuseError("psi_frame_invalid")
        t=_ms(f["time_ms"])
        if t>=duration:
            raise PSIReuseError("psi_frame_timing_invalid")
        o=f["ocr"]
        if type(o) is not dict or o.get("coverage_kind")!="single_frame":
            raise PSIReuseError("psi_ocr_coverage_invalid")
        if o.get("status") not in ("ok","no_text_detected","skipped","failed","unavailable"):
            raise PSIReuseError("psi_ocr_status_invalid")
        if o["status"]=="ok" and type(o.get("text")) is str and o["text"].strip():
            if o.get("confidence") is not None:
                raise PSIReuseError("psi_uncalibrated_confidence")
            start=_ms(o.get("start_ms"));end=_ms(o.get("end_ms"),can_zero=False)
            if start!=t or end>min(t+40,duration) or end<=start:
                raise PSIReuseError("psi_frame_timing_invalid")
            spans.append({
                "frame_id":f["frame_id"],"frame_sha256":f["sha256"],
                "observed_sec":ms_to_seconds(t),
                "sample_end_sec":ms_to_seconds(end),
                "text":_text(o["text"]),
                "confidence":None,"coverage":"single_frame_only",
            })
    return {
        "schema_version":"ga-psi-vision-reuse/v1","source":"psi",
        "status":"partial" if spans else "unavailable",
        "ocr_spans":spans,"evidence_count":len(spans) if spans else None,
        "sampled_frames":len(frames),
        "visual_temporal_coverage":"unknown",
        "ocr_temporal_coverage":"unknown",
        "scene_coverage":"unknown",
        "upstream":{"output_id":record["output_id"],"output_sha256":record["output_sha256"],
                    "config_sha256":record["config_sha256"],
                    "engine_versions":output.get("engine_versions")},
        "media_sha256":binding.media_sha256,
    }


class PostgresPSIReader:
    """Read-only public SQL PSI reader. No inferred GA↔PSI identity mapping.

    The host supplies a genuinely authenticated PSIAssetBinding. The read
    verifies current PSI source rights, linked acquisition media SHA/size,
    the output's immutable digest, and the PSI job stage/state.
    """

    RELATIONS=("project_sources","project_source_versions","project_media_assets",
               "source_extraction_jobs","source_processing_outputs",
               "project_video_acquisition_runs","project_transcript_runs","project_vision_runs",
               "videos","sre_project_videos","media_assets","sources",
               "source_policy_versions")

    def __init__(self,conn,binding:PSIAssetBinding,*,kind,observed_version):
        if not isinstance(binding,PSIAssetBinding) or kind not in ("transcript","vision"):
            raise TypeError("Binding and transcript/vision kind required")
        self.conn=conn;self.binding=binding;self.kind=kind
        self.observed_version=observed_version

    def capability(self):
        port="transcript" if self.kind=="transcript" else "vision"
        required=PSI_TRANSCRIPT if port=="transcript" else PSI_VISION
        if self.observed_version!=required:
            return unavailable(port,"psi_version_unnegotiated") if self.observed_version is None else negotiate(port,self.observed_version)
        try:
            for name in self.RELATIONS:
                row=self.conn.execute("SELECT to_regclass(%s)",(name,)).fetchone()
                if not row or row[0] is None:
                    return unavailable(port,"psi_schema_missing")
        except Exception:
            return unavailable(port,"psi_probe_failed")
        # PSI's historical vision wire is 'vision-prepare/v1'; after strict
        # normalization the GA VisionReader port is 'visual-preparation/v1'.
        return negotiate(port,"transcript/v1" if port=="transcript" else GA_VISION)

    def read_verified(self,media_sha256,*,expected_config_sha256=None,
                      expected_model_version=None):
        if self.capability().state!=STATE_AVAILABLE:
            return None
        if media_sha256!=self.binding.media_sha256:
            raise PSIReuseError("psi_media_hash_mismatch")
        b=self.binding
        # Explicit PSI UUID source-version mapping is NOT sufficient without
        # current Discovery project-video/byte lineage. Never infer D1 video
        # identity from a PSI source UUID or from shared URL/native IDs.
        current_d1=self.conn.execute(
            """SELECT EXISTS(
                 SELECT 1 FROM videos v
                 JOIN sre_project_videos pv ON pv.video_id=v.id
                 JOIN media_assets ma ON ma.video_id=v.id
                 JOIN sources ds ON ds.id=ma.source_id
                 JOIN source_policy_versions dp
                   ON dp.source_id=ds.id
                 WHERE pv.project_id=%s AND v.id=%s AND ma.sha256=%s
                   AND ma.permission='analysis_allowed'
                   AND (ma.expires_at IS NULL OR ma.expires_at>now())
                   AND ds.status='enabled' AND ds.can_analyze_media=true
                   AND dp.is_active=true
                   AND dp.permissions->>'can_analyze_media'='true'
               )""",
            (b.project_id,b.video_id,b.media_sha256),
        ).fetchone()
        if not current_d1 or current_d1[0] is not True:
            return None
        kind="transcript" if self.kind=="transcript" else "visual_scenes"
        version=PSI_TRANSCRIPT if self.kind=="transcript" else PSI_VISION
        stage="asr" if self.kind=="transcript" else "vision"
        # PSI read consistency is transaction-owned by the caller; the host
        # must run this in its own project-scoped read-only transaction.
        rows=self.conn.execute(
            """SELECT o.id::text,o.output,o.output_sha256,j.state,
                      ps.can_analyze,ps.tombstoned_at,sv.id::text,
                      ac.video_sha256,asset.content_sha256,asset.byte_size,
                      ac.job_id::text,ac.result_source_version_id::text,
                      sv.policy_snapshot,asset.rights_snapshot,
                      coalesce(tr.config_sha256,vr.config_sha256),
                      vr.mode,ac.cancel_requested
               FROM source_processing_outputs o
               JOIN source_extraction_jobs j
                 ON j.id=o.extraction_job_id AND j.project_id=o.project_id
                  AND j.source_id=o.source_id AND j.source_version_id=o.source_version_id
               JOIN project_sources ps
                 ON ps.id=o.source_id AND ps.project_id=o.project_id
               JOIN project_source_versions sv
                 ON sv.id=o.source_version_id AND sv.source_id=o.source_id
                  AND sv.project_id=o.project_id
               JOIN project_video_acquisition_runs ac
                 ON ac.project_id=o.project_id AND ac.source_id=o.source_id
                  AND ac.result_source_version_id=o.source_version_id
               JOIN project_media_assets asset
                 ON asset.project_id=ac.project_id AND asset.id=ac.video_asset_id
                  AND asset.source_version_id=ac.result_source_version_id
               LEFT JOIN project_transcript_runs tr
                 ON tr.project_id=o.project_id AND tr.job_id=j.id
                  AND tr.source_id=o.source_id AND tr.video_source_version_id=o.source_version_id
                  AND tr.mode IN ('reuse_sidecar','local_asr')
               LEFT JOIN project_vision_runs vr
                 ON vr.project_id=o.project_id AND vr.job_id=j.id
                  AND vr.source_id=o.source_id AND vr.video_source_version_id=o.source_version_id
                  AND vr.mode='prepare'
               WHERE o.project_id=%s AND o.source_id=%s::uuid
                 AND o.source_version_id=%s::uuid AND o.output_type=%s
                 AND o.schema_version=%s AND j.stage=%s
                 AND ac.job_id=%s::uuid AND ac.video_sha256=%s
               ORDER BY o.created_at DESC LIMIT 4
               FOR SHARE OF o,j,ps,sv,ac,asset""",
            (b.project_id,b.psi_source_id,b.psi_source_version_id,kind,
             version,stage,b.psi_acquisition_job_id,b.media_sha256),
        ).fetchall()
        candidates=[]
        for row in rows:
            (oid,output,digest,state,allowed,tombstoned,source_version,
             acquired_sha,asset_sha,asset_bytes,acq_job,acq_version,
             version_rights,asset_rights,run_config,vision_mode,cancelled)=row
            if not (allowed is True and tombstoned is None and cancelled is False
                and isinstance(version_rights,dict)
                and version_rights.get("can_analyze") is True
                and isinstance(asset_rights,dict)
                and asset_rights.get("can_analyze") is True
                and isinstance(run_config,str)
                and (self.kind!="vision" or vision_mode=="prepare")
                and acquired_sha==asset_sha==b.media_sha256
                and asset_bytes==b.media_byte_size
                and source_version==acq_version==b.psi_source_version_id
                and acq_job==b.psi_acquisition_job_id):
                continue
            record={"project_id":b.project_id,"source_id":b.psi_source_id,
                    "source_version_id":source_version,
                    "acquisition_job_id":acq_job,"output_id":oid,
                    "schema_version":version,"output":output,
                    "output_sha256":digest,"config_sha256":run_config,
                    "job_state":state,"rights_ok":True}
            # Source-specific model/config metadata is held in the immutable
            # output; SQL cache pins will add precise run config in stage 18.
            try:
                normalized=(normalize_transcript(record,b,
                               expected_config_sha256=expected_config_sha256,
                               expected_model_version=expected_model_version)
                            if self.kind=="transcript" else
                            normalize_vision(record,b,
                               expected_config_sha256=expected_config_sha256))
            except PSIReuseError:
                continue
            candidates.append(normalized)
        return candidates[0] if candidates else None


class PSIReuseCache:
    """Versioned ephemeral memo with mandatory current reauthorization.

    A cache key always includes explicit D1 & PSI lineage, output/model/config
    digests and requested peer version. Never return a cached result before
    calling verify_current(): rights/retention must be checked again.
    """

    def __init__(self,max_items=128):
        if type(max_items) is not int or not 1<=max_items<=1024:
            raise ValueError("Invalid cache size")
        self.max_items=max_items;self._memo={}

    def get(self,binding,*,kind,output_sha256,config_sha256,model_version,
            observed_version,verify_current):
        if not isinstance(binding,PSIAssetBinding) or not callable(verify_current):
            raise PSIReuseError("invalid_cache_request")
        k=self._key(binding,kind,output_sha256,config_sha256,model_version,observed_version)
        if verify_current(binding) is not True:
            self._memo.pop(k,None)
            raise PSIReuseError("psi_rights_revoked")
        value=self._memo.get(k)
        return None if value is None else json.loads(json.dumps(value))

    def put(self,binding,normalized,*,kind,output_sha256,config_sha256,
            model_version,observed_version):
        if not isinstance(binding,PSIAssetBinding) or type(normalized) is not dict:
            raise PSIReuseError("invalid_cache_request")
        if normalized.get("media_sha256")!=binding.media_sha256:
            raise PSIReuseError("psi_media_hash_mismatch")
        expected_schema=("ga-psi-transcript-reuse/v1" if kind=="transcript"
                         else "ga-psi-vision-reuse/v1")
        peer_version=PSI_TRANSCRIPT if kind=="transcript" else PSI_VISION
        upstream=normalized.get("upstream")
        if (normalized.get("schema_version")!=expected_schema
            or normalized.get("source")!="psi"
            or normalized.get("status") not in ("partial","unavailable")
            or type(upstream) is not dict
            or upstream.get("output_sha256")!=output_sha256
            or upstream.get("config_sha256")!=config_sha256
            or observed_version!=peer_version
            or (kind=="vision" and
                (normalized.get("ocr_temporal_coverage")!="unknown"
                 or normalized.get("visual_temporal_coverage")!="unknown"))
            or (kind=="transcript" and
                (normalized.get("complete_speech_coverage") is not False
                 or normalized.get("verified_no_speech") is not False))):
            raise PSIReuseError("untrusted_cache_payload")
        k=self._key(binding,kind,output_sha256,config_sha256,model_version,observed_version)
        if len(self._memo)>=self.max_items:
            self._memo.pop(next(iter(self._memo)))
        self._memo[k]=json.loads(json.dumps(normalized))

    @staticmethod
    def _key(binding,kind,output_sha256,config_sha256,model_version,observed_version):
        _hash(output_sha256)
        if config_sha256 is not None:_hash(config_sha256)
        if kind not in ("transcript","vision") or type(observed_version) is not str:
            raise PSIReuseError("invalid_cache_request")
        if model_version is not None and type(model_version) is not str:
            raise PSIReuseError("invalid_cache_request")
        return canonical_sha256({
            "lineage":binding.lineage(),"kind":kind,
            "output_sha256":output_sha256,"config_sha256":config_sha256,
            "model_version":model_version,"observed_version":observed_version,
        })
