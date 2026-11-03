"""GB-MOD-12: source-first, deterministic B5 editing from verified SHOT intervals.

Never infer hard cuts from semantic 'scene' chapters. A trusted GA shot
catalog must explicitly attest complete shot-boundary segmentation for the
exact already byte-verified Group B evidence manifest. No video decoding,
network, model, provider cost, or guessed 0-cuts on unknown coverage.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from collections.abc import Mapping

from ..contracts import canonical_hash
from ..context.manifest import MediaEvidenceManifest, SCHEMA as MEDIA_VERSION
from ..context.evidence import VerifiedObservation
from ..errors import GroupBError
from ..worker import LocalResult
from ..orchestrator import JobClaim

VERSION = "group-b-b5-editing/v1"
SHOT_TIMELINE = "group-a-shot-timeline/v1"
QUANT = Decimal("0.0001")
MAX_SHOTS = 2000
MAX_DURATION_MS = 86_400_000
KINDS = ("single_take", "slow_cuts", "medium_cuts", "fast_cuts")


class B5MeasurementUnavailable(GroupBError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("b5_unavailable", "B5: " + reason)


def _fail(reason):
    raise B5MeasurementUnavailable(reason)


def _nat(value) -> bool:
    return type(value) is int and value >= 0


def _positive(value) -> bool:
    return type(value) is int and value > 0


def _sha(value) -> bool:
    return type(value) is str and len(value)==64 and all(x in "0123456789abcdef" for x in value)


@dataclass(frozen=True, slots=True)
class VerifiedShotTimeline:
    manifest: MediaEvidenceManifest
    scenes: tuple[VerifiedObservation, ...]
    duration_ms: int
    proof_sha256: str


@dataclass(frozen=True, slots=True)
class B5Metrics:
    editing_type: str
    cut_count: int
    cuts_per_minute: Decimal
    avg_shot_duration: Decimal
    cuts_first_5_sec: int

    def db_fields(self) -> dict:
        """Exactly the four quantitative fields + type of original SQL 005."""
        return {
            "editing_type": self.editing_type,
            "cut_count": self.cut_count,
            "cuts_per_minute": self.cuts_per_minute,
            "avg_shot_duration": self.avg_shot_duration,
            "cuts_first_5_sec": self.cuts_first_5_sec,
        }

    def wire(self) -> dict:
        return {
            "editing_type": self.editing_type,
            "cut_count": self.cut_count,
            "cuts_per_minute": format(self.cuts_per_minute, ".4f"),
            "avg_shot_duration": format(self.avg_shot_duration, ".4f"),
            "cuts_first_5_sec": self.cuts_first_5_sec,
        }


def read_shot_timeline(manifest: MediaEvidenceManifest, catalog) -> VerifiedShotTimeline:
    """Demand a trusted, *completed* shot interval attestation from GA.

    Contract:
    catalog.resolve_shot_timeline(manifest) -> dict EXACT keys:
      schema_version, manifest_sha256, ga_result_sha256, media_asset_id,
      media_sha256, duration_ms, segmentation_kind, status, shot_refs,
      timeline_sha256.
    timeline_sha256 hashes the remaining keys. The injected catalog is an
    internal trusted adapter, NOT a user-supplied JSON attachment or prompt.
    Every ref must point to an independently byte-verified A scene record.
    """
    if (not isinstance(manifest, MediaEvidenceManifest) or
        manifest.schema_version != MEDIA_VERSION or not manifest.verify_digest()):
        _fail("manifest_unverified")
    if manifest.content_kind not in ("video","slideshow_video"):
        _fail("video_only")
    if (len(manifest.assets)!=1 or manifest.assets[0].media_kind!="video"
        or not _positive(manifest.assets[0].duration_ms)
        or manifest.assets[0].duration_ms>MAX_DURATION_MS):
        _fail("measured_duration_unavailable")
    resolve=getattr(catalog,"resolve_shot_timeline",None)
    if not callable(resolve):
        _fail("shot_timeline_catalog_unavailable")
    try:
        row=resolve(manifest)
    except Exception:
        _fail("shot_timeline_catalog_unavailable")
    keys={
        "schema_version","manifest_sha256","ga_result_sha256",
        "media_asset_id","media_sha256","duration_ms",
        "segmentation_kind","status","shot_refs","timeline_sha256",
    }
    if type(row) is not dict or set(row)!=keys:
        _fail("shot_timeline_contract_mismatch")
    if (row["schema_version"]!=SHOT_TIMELINE or
        row["manifest_sha256"]!=manifest.manifest_sha256 or
        row["ga_result_sha256"]!=manifest.ga_result_sha256 or
        row["media_asset_id"]!=manifest.assets[0].asset_id or
        row["media_sha256"]!=manifest.assets[0].source_sha256 or
        row["duration_ms"]!=manifest.assets[0].duration_ms or
        row["segmentation_kind"]!="shot_boundaries" or
        row["status"]!="complete"):
        _fail("shot_timeline_not_complete_or_wrong_source")
    refs=row["shot_refs"]
    if (type(refs) is not list or not 1<=len(refs)<=MAX_SHOTS or
        any(not _sha(x) for x in refs) or len(set(refs))!=len(refs)):
        _fail("shot_reference_set_invalid")
    if not _sha(row["timeline_sha256"]):
        _fail("shot_timeline_digest_invalid")
    try:
        calculated=canonical_hash({k:v for k,v in row.items() if k!="timeline_sha256"})
    except (TypeError,ValueError):
        _fail("shot_timeline_contract_mismatch")
    if row["timeline_sha256"]!=calculated:
        _fail("shot_timeline_digest_mismatch")
    observed=[x for x in manifest.observations if x.kind=="scene"]
    if len(observed)!=len(refs):
        _fail("shot_reference_coverage_incomplete")
    indexed={x.ref_sha256:x for x in observed}
    if len(indexed)!=len(refs) or set(indexed)!=set(refs):
        _fail("shot_reference_coverage_incomplete")
    shots=tuple(indexed[x] for x in refs)
    duration=row["duration_ms"]
    previous=0
    for index,shot in enumerate(shots):
        if (not isinstance(shot,VerifiedObservation) or
            shot.timebase!="video_absolute" or
            shot.media_asset_id!=manifest.assets[0].asset_id or
            shot.asset_sha256!=manifest.assets[0].source_sha256 or
            not _nat(shot.start_ms) or not _positive(shot.end_ms) or
            shot.start_ms!=previous or shot.end_ms<=shot.start_ms or
            shot.end_ms>duration):
            _fail("shot_intervals_incomplete_or_overlapping")
        previous=shot.end_ms
    if previous!=duration:
        _fail("shot_intervals_incomplete_or_overlapping")
    return VerifiedShotTimeline(manifest,shots,duration,row["timeline_sha256"])


def analyze_b5(timeline: VerifiedShotTimeline) -> B5Metrics:
    if not isinstance(timeline,VerifiedShotTimeline):
        _fail("timeline_unverified")
    # Never accept caller-constructed timeline as sufficient for execution:
    # B5EditingExecutor always resolves it from the current trusted catalog.
    duration=timeline.duration_ms
    n=len(timeline.scenes)
    if not n or not _positive(duration):
        _fail("shot_measurements_missing")
    cut_count=n-1
    cut_points=tuple(shot.start_ms for shot in timeline.scenes[1:])
    per_minute=(Decimal(cut_count)*Decimal(60000)/Decimal(duration)).quantize(
        QUANT,rounding=ROUND_HALF_UP)
    average=(Decimal(duration)/(Decimal(n)*Decimal(1000))).quantize(
        QUANT,rounding=ROUND_HALF_UP)
    if average<=0:
        _fail("shot_duration_rounds_to_zero")
    if per_minute>=30:
        editing_type="fast_cuts"
    elif per_minute>=10:
        editing_type="medium_cuts"
    elif cut_count>0 and per_minute>0:
        editing_type="slow_cuts"
    elif cut_count==0:
        editing_type="single_take"
    else:
        _fail("cut_rate_rounds_to_zero")
    metrics=B5Metrics(
        editing_type,cut_count,per_minute,average,
        sum(0<point<5000 for point in cut_points)
    )
    if metrics.editing_type not in KINDS or metrics.cuts_first_5_sec>metrics.cut_count:
        _fail("b5_sql_contract_failed")
    return metrics


def _output(timeline: VerifiedShotTimeline) -> dict:
    m=timeline.manifest
    return {
        "schema_version":VERSION,
        "project_id":m.project_id,
        "b_run_id":m.b_run_id,
        "video_id":m.video_id,
        "ga_result_sha256":m.ga_result_sha256,
        "input_evidence_sha256":m.manifest_sha256,
        "shot_timeline_sha256":timeline.proof_sha256,
        "measurement_kind":"verified_shot_boundaries",
        "coverage":"full_video_duration",
        "metrics":analyze_b5(timeline).wire(),
    }


def _match(claim: JobClaim, manifest: MediaEvidenceManifest) -> bool:
    return (
        isinstance(claim,JobClaim) and isinstance(manifest,MediaEvidenceManifest)
        and claim.kind=="local" and claim.block=="B5"
        and claim.project_id==manifest.project_id
        and claim.run_id==manifest.b_run_id
        and claim.evidence_sha256==manifest.manifest_sha256
        and manifest.verify_digest()
    )


class B5EditingExecutor:
    """Explicit trusted A shot catalog required; no paid providers imported."""
    def execute_local(self,claim: JobClaim,manifest: MediaEvidenceManifest,catalog) -> LocalResult:
        if not _match(claim,manifest):
            _fail("job_manifest_identity_mismatch")
        timeline=read_shot_timeline(manifest,catalog)
        output=_output(timeline)
        return LocalResult("B5",manifest.manifest_sha256,output,canonical_hash(output))


class B5EditingVerifier:
    """Recompute independently, never trust a self-certified JSON + SHA."""
    def verify_local_result(self,claim: JobClaim,result: LocalResult,
                            manifest: MediaEvidenceManifest,catalog) -> bool:
        if (not _match(claim,manifest) or not isinstance(result,LocalResult)
            or result.block!="B5" or result.input_evidence_sha256!=manifest.manifest_sha256
            or not result.verify_digest()):
            return False
        try:
            truth=read_shot_timeline(manifest,catalog)
            return result.output==_output(truth)
        except B5MeasurementUnavailable:
            return False
