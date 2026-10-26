"""GB-MOD-06: immutable source-first manifest from exact GA + D1 media + A proofs.

No fabricated frames/speech/OCR/slides; no media bytes or personal text in
the emitted manifest. A connected observation catalog is explicitly injected.
Manifest creation is an evidence read, NOT B analysis approval or job start.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from collections.abc import Mapping
from uuid import UUID

from ..adapters.group_a import read_exact_a
from ..contracts import canonical_hash
from ..errors import DependencyUnavailable
from .evidence import (
    EvidenceFailure, MediaByteProof, VerifiedObservation, validate_observations,
    verify_local_bytes,
)

SCHEMA = "group-b-media-evidence/v1"
_MAX_MEDIA = 100
_MAX_EVIDENCE = 2000


def _block(reason: str):
    raise EvidenceFailure(reason)


def _row(cursor):
    result=cursor.fetchone()
    if result is None:
        return None
    if isinstance(result,Mapping):
        return dict(result)
    return dict(zip((item.name for item in cursor.description), result))


def _milliseconds(value) -> int | None:
    if value is None:
        return None
    try:
        decimal=Decimal(str(value))
        ms=decimal*1000
        if not decimal.is_finite() or ms!=ms.to_integral_value() or ms<=0 or ms>86_400_000:
            _block("media_duration_invalid")
        return int(ms)
    except (ValueError,TypeError,InvalidOperation):
        _block("media_duration_invalid")


@dataclass(frozen=True,slots=True)
class MediaEvidenceManifest:
    schema_version: str
    project_id: str
    qdp_run_id: str
    b_run_id: str
    ga_run_id: str
    ga_result_id: int
    ga_result_sha256: str
    qdp_input_sha256: str
    media_manifest_sha256: str
    video_id: int
    source_id: str
    source_policy_version_id: int
    content_kind: str
    assets: tuple[MediaByteProof,...]
    observations: tuple[VerifiedObservation,...]
    manifest_sha256: str

    def payload(self) -> dict:
        """Versioned digest payload, never filesystem paths, ASR/OCR text or bytes."""
        return {
            "schema_version":self.schema_version,
            "project_id":self.project_id,
            "qdp_run_id":self.qdp_run_id,
            "b_run_id":self.b_run_id,
            "ga_run_id":self.ga_run_id,
            "ga_result_id":self.ga_result_id,
            "ga_result_sha256":self.ga_result_sha256,
            "qdp_input_sha256":self.qdp_input_sha256,
            "media_manifest_sha256":self.media_manifest_sha256,
            "video_id":self.video_id,
            "source_id":self.source_id,
            "source_policy_version_id":self.source_policy_version_id,
            "content_kind":self.content_kind,
            "assets":[x.wire() for x in self.assets],
            "observations":[x.wire() for x in self.observations],
            "observed_counts": {
                kind:sum(x.kind==kind for x in self.observations)
                for kind in ("scene","transcript_segment","ocr_span",
                             "sampled_frame","audio_event","slide_ref")
            },
        }

    def wire(self) -> dict:
        return {**self.payload(),"manifest_sha256":self.manifest_sha256}

    def verify_digest(self) -> bool:
        return canonical_hash(self.payload())==self.manifest_sha256


def build_media_evidence_manifest(conn, *, b_run_id: str, ga_result_id: int,
                                  ga_result_sha256: str, media_roots,
                                  observation_catalog,
                                  max_evidence: int = _MAX_EVIDENCE) -> MediaEvidenceManifest:
    """Revalidate pinned A, hash current source media and every A proof file.

    The caller owns an explicit transaction and must enforce project auth.
    This read may be invalidated later; stage07 and paid execution must recheck
    QDP invalidations/source rights and rehash bytes at point of actual use.
    """
    if type(max_evidence) is not int or not 1<=max_evidence<=_MAX_EVIDENCE:
        _block("invalid_evidence_limit")
    pin=read_exact_a(conn,b_run_id=b_run_id,ga_result_id=ga_result_id,
                     expected_sha256=ga_result_sha256)
    try:
        current=_row(conn.execute(
            """SELECT b.project_id,b.video_id,b.source_id,
                      i.qdp_run_id::text AS qdp_run_id,
                      i.preparation_sha256 AS qdp_input_sha256,
                      r.content_kind,r.manifest_sha256,r.source_policy_version_id,
                      r.expected_media_items
                 FROM group_b_runs b
                 JOIN group_b_inbox i ON i.id=b.inbox_id AND i.project_id=b.project_id
                 JOIN ga_runs r ON r.id=%s::uuid AND r.project_id=b.project_id
                      AND r.video_id=b.video_id AND r.source_id=b.source_id
                      AND r.qdp_run_id=i.qdp_run_id
                      AND r.qdp_input_sha256=i.preparation_sha256
                WHERE b.id=%s::uuid""",
            (pin.ga_run_id,b_run_id)
        ))
        rows=conn.execute(
            """SELECT mr.slide_index,mr.media_asset_id,mr.media_kind,
                      mr.asset_sha256,mr.byte_size,m.local_path,m.sha256,
                      m.permission,m.expires_at,m.video_id,m.source_id,m.media_kind,
                      s.status,s.can_analyze_media,
                      p.is_active,p.permissions,
                      COALESCE(va.duration_sec,ci.duration_sec) AS duration_sec
                 FROM ga_media_refs mr
                 JOIN media_assets m ON m.id=mr.media_asset_id
                 JOIN sources s ON s.id=m.source_id
                 JOIN source_policy_versions p ON p.id=%s AND p.source_id=s.id
                 LEFT JOIN video_feature_a_analyses va ON va.id=%s
                 LEFT JOIN carousel_feature_a_items ci
                        ON ci.analysis_id=%s AND ci.slide_index=mr.slide_index
                WHERE mr.run_id=%s::uuid
                ORDER BY mr.slide_index""",
            (pin.source_policy_version_id,
             pin.a_analysis_id if pin.content_type=="video" else None,
             pin.a_analysis_id if pin.content_type=="carousel" else None,
             pin.ga_run_id)
        ).fetchall()
    except Exception:
        raise DependencyUnavailable("media","ga_or_d1_manifest_read_unavailable") from None
    if current is None:
        _block("b_ga_manifest_join_missing")
    if (current["project_id"]!=pin.project_id or current["manifest_sha256"]!=pin.manifest_sha256 or
        current["source_policy_version_id"]!=pin.source_policy_version_id or
        current["expected_media_items"]!=len(pin.media_asset_ids) or
        type(current["qdp_run_id"]) is not str or
        not isinstance(current["content_kind"],str)):
        _block("manifest_lineage_mismatch")
    if len(rows)!=len(pin.media_asset_ids) or not 1<=len(rows)<=_MAX_MEDIA:
        _block("source_media_coverage_incomplete")
    assets=[]
    for index,raw in enumerate(rows,1):
        if len(raw)!=17:
            _block("media_catalog_shape_incompatible")
        (slide,asset_id,media_kind,ga_sha,ga_size,path,d1_sha,
         permission,expiry,video_id,source_id,d1_kind,
         source_state,source_rights,policy_active,permissions,duration)=raw
        if (slide!=index or asset_id!=pin.media_asset_ids[index-1] or
            ga_sha!=pin.media_sha256s[index-1] or ga_sha!=d1_sha or
            type(ga_size) is not int or ga_size<=0 or
            video_id!=current["video_id"] or source_id!=current["source_id"] or
            media_kind!=d1_kind or media_kind not in ("video","image")):
            _block("source_media_identity_mismatch")
        if (source_state!="enabled" or source_rights is not True or
            policy_active is not True or type(permissions) is not dict or
            permissions.get("can_analyze_media") is not True or
            permission in (None,"","metadata_only")):
            _block("media_rights_unavailable")
        if expiry is not None:
            if not isinstance(expiry,datetime) or expiry.tzinfo is None or expiry<=datetime.now(timezone.utc):
                _block("media_expired")
        digest,actual_size=verify_local_bytes(
            path,ga_sha,media_roots,expected_size=ga_size
        )
        assets.append(MediaByteProof(
            asset_id,digest,actual_size,media_kind,slide,
            _milliseconds(duration) if media_kind=="video" else None,
        ))
    if current["content_kind"] in ("video","slideshow_video"):
        if len(assets)!=1 or assets[0].media_kind!="video":
            _block("video_media_type_mismatch")
    elif current["content_kind"]=="photo_carousel":
        if len(assets)<2 or any(x.media_kind!="image" for x in assets):
            _block("carousel_media_type_mismatch")
    elif current["content_kind"]=="mixed_carousel":
        if len(assets)<2 or {x.media_kind for x in assets}!={"image","video"}:
            _block("carousel_media_type_mismatch")
    else:
        _block("unsupported_content_kind")
    if pin.evidence_count!=len(pin.evidence_refs) or not pin.evidence_refs:
        _block("a_observation_proofs_missing")
    observations=validate_observations(
        pin.evidence_refs,observation_catalog,project_id=current["project_id"],
        video_id=current["video_id"],source_id=current["source_id"],
        media=tuple(assets),roots=media_roots,max_evidence=max_evidence,
    )
    params={
        "schema_version":SCHEMA, "project_id":current["project_id"],
        "qdp_run_id":current["qdp_run_id"],"b_run_id":b_run_id,
        "ga_run_id":pin.ga_run_id,"ga_result_id":pin.ga_result_id,
        "ga_result_sha256":pin.result_sha256,
        "qdp_input_sha256":current["qdp_input_sha256"],
        "media_manifest_sha256":pin.manifest_sha256,
        "video_id":current["video_id"],"source_id":current["source_id"],
        "source_policy_version_id":pin.source_policy_version_id,
        "content_kind":current["content_kind"],"assets":tuple(assets),
        "observations":observations,
    }
    unsigned=MediaEvidenceManifest(**params,manifest_sha256="0"*64)
    return MediaEvidenceManifest(**params,manifest_sha256=canonical_hash(unsigned.payload()))
