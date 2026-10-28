"""UA-MOD-25: independently signed UAE -> PIE research manifest boundary.

PIE's read-only branch accepts 'uae-to-pie/v1', a byte-level canonical
manifest + separately authenticated, nonce-bound receipt. We implement
the *producer* side only. No discovery, model inference, publisher, API,
self-signed defaults, arbitrary unqualified rows, or assumed RTB evidence.

All data is exact persisted UAE/D1/A/B/C, reauthorized per snapshot within
ONE fresh REPEATABLE READ transaction. This never promotes a historical
partial result to qualified. A cryptographic receipt is HMAC-SHA256 using
an EXTERNALLY injected secret/key ID and is distinct from any data digest.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import re
from typing import Any, Mapping
from uuid import UUID

from .contracts import AnalyticsBoundaryError, PROJECT_RE, SHA256_RE, canonical_hash
from .descriptive_aggregation import MIN_SAMPLE, MAX_AGGREGATE, QUALIFIED_METRIC_SQL, _compatibility, _number
from .readside_visibility import VisibilityPins, VisibleSnapshot, VisibleSnapshotReader, CLOCK_SQL, _utc
from .group_a_adapter import A_VIDEO_SQL, A_CAROUSEL_SQL
from .typed_segmentation import GROUP_B_DIMENSIONS_SQL, _typed_values
from .cohort_comparison import AUTHOR_TIME_SQL
from .project_ranked_views import (
    ProjectRankedRunStore, RUN_READ_ID_SQL, _has_match, SORT_MODES,
)

PIE_MANIFEST_WIRE="uae-to-pie/v1"
PIE_DATASET_WIRE="pie-row-dataset/v1"
PIE_BUILD_VERSION="uae-pie-research-builder/v1"
PIE_ENGINE_VERSION="uae-project-ranked-run/v1"
PIE_SELECTION_RULE="explicit-current-qualified-pins/v1"
PIE_RECEIPT_ISSUER="uae.project.research"
MAX_SIGNED_SECONDS=300
_TOKEN=re.compile(r"[A-Za-z0-9._:/-]{1,160}\Z")
_VERSION=re.compile(r"[A-Za-z0-9._:/-]{1,160}\Z")
_SCORE=re.compile(r"-?(?:0|[1-9][0-9]{0,20})(?:\.[0-9]{1,18})?\Z")
_RECEIPT_FIELDS=(
    "project_id","source_id","source_policy_version_id",
    "producer_version","producer_run_id","manifest_sha256",
    "dataset_sha256","challenge","issued_at","expires_at",
    "issuer","key_id",
)


def _deny(code:str)->None:
    raise AnalyticsBoundaryError(code,"UAE to PIE handoff denied: "+code)


def _one(db:Any,sql:str,params:tuple)->dict | None:
    rows=db.execute(sql,params).fetchall()
    if type(rows) is not list or len(rows)>1 or any(
        not isinstance(r,Mapping) for r in rows
    ):
        _deny("pie_row_shape_invalid")
    return dict(rows[0]) if rows else None


def _json(obj:dict)->bytes:
    if type(obj) is not dict:
        _deny("pie_object_invalid")
    try:
        return json.dumps(obj,sort_keys=True,ensure_ascii=False,
                          separators=(",",":"),allow_nan=False).encode("utf-8")
    except (TypeError,ValueError,OverflowError) as exc:
        raise AnalyticsBoundaryError(
            "pie_json_invalid","PIE research manifest is not canonical JSON",
        ) from exc


def _iso(dt:Any)->str:
    t=_utc(dt)
    if t is None:
        _deny("pie_timestamp_invalid")
    return t.isoformat(timespec="microseconds")


def _uuid(s:Any)->bool:
    if type(s) is not str:return False
    try:return str(UUID(s))==s
    except (ValueError,TypeError,AttributeError):return False


def _sha(s:Any)->bool:
    return type(s) is str and SHA256_RE.fullmatch(s) is not None


def _token(s:Any)->bool:
    return type(s) is str and _TOKEN.fullmatch(s) is not None


def _pk(s:Any)->bool:
    return type(s) is int and 0<s<2**63


def _canon_decimal(value:Decimal)->str:
    if type(value) is not Decimal or not value.is_finite():
        _deny("pie_score_invalid")
    # Group C's NUMERIC(30,12) is exact; do NOT round/truncate or normalize.
    canonical=format(value,"f")
    if _SCORE.fullmatch(canonical) is None:
        _deny("pie_score_outside_wire")
    return canonical


@dataclass(frozen=True,slots=True)
class SignedResearchReceipt:
    project_id:str
    source_id:str
    source_policy_version_id:int
    producer_version:str
    producer_run_id:str
    manifest_sha256:str
    dataset_sha256:str
    challenge:str
    issued_at:str
    expires_at:str
    issuer:str
    key_id:str
    signature:str

    def __post_init__(self)->None:
        if (type(self.project_id) is not str
            or PROJECT_RE.fullmatch(self.project_id) is None
            or not _token(self.source_id)
            or not _pk(self.source_policy_version_id)
            or not _uuid(self.producer_run_id)
            or not all(_sha(getattr(self,k)) for k in (
                "manifest_sha256","dataset_sha256","challenge","signature"))
            or not all(_token(getattr(self,k)) for k in (
                "producer_version","issuer","key_id"))):
            _deny("pie_receipt_invalid")
        try:
            a=datetime.fromisoformat(self.issued_at)
            b=datetime.fromisoformat(self.expires_at)
        except (ValueError,TypeError) as exc:
            raise AnalyticsBoundaryError("pie_receipt_time_invalid",
                                         "Signed receipt time invalid") from exc
        if (_utc(a) is None or _utc(b) is None or b<=a
            or b-a>timedelta(seconds=MAX_SIGNED_SECONDS)):
            _deny("pie_receipt_time_invalid")

    def signed_fields(self)->dict:
        return {key:getattr(self,key) for key in _RECEIPT_FIELDS}


@dataclass(frozen=True,slots=True)
class SignedResearchExport:
    """Neutral transport envelope; PIE integration maps to its OWN classes.

    SHA-256 digests and a signed receipt do not independently certify live
    rights. PIE must run its separate rights and per-row provenance gates.
    """
    raw_manifest:bytes
    receipt:SignedResearchReceipt
    manifest_sha256:str
    dataset_sha256:str
    sample_size:int
    verified_live:bool=False
    ready_for_analysis:bool=False

    def __post_init__(self)->None:
        if (type(self.raw_manifest) is not bytes
            or len(self.raw_manifest)>64*1024*1024
            or not isinstance(self.receipt,SignedResearchReceipt)
            or self.manifest_sha256!=canonical_hash(json.loads(
                self.raw_manifest.decode("utf-8")))
            or self.receipt.manifest_sha256!=self.manifest_sha256
            or self.receipt.dataset_sha256!=self.dataset_sha256
            or type(self.sample_size) is not int
            or not MIN_SAMPLE<=self.sample_size<=MAX_AGGREGATE
            or self.verified_live is not False
            or self.ready_for_analysis is not False):
            _deny("pie_export_invalid")


class ReceiptSigner:
    """Explicit in-process key, no default key, no self-generated issuer.

    The receiving PIE verifier must be INDEPENDENTLY configured with the
    matching trust root, key rotation, replay prevention and project scope.
    """
    def __init__(self,secret:bytes,*,key_id:str,issuer:str=PIE_RECEIPT_ISSUER):
        if (type(secret) is not bytes or len(secret)<32
            or not _token(key_id) or not _token(issuer)):
            _deny("pie_signing_capability_unavailable")
        self._secret=secret
        self.key_id=key_id
        self.issuer=issuer

    def sign(self,claims:dict)->SignedResearchReceipt:
        if type(claims) is not dict or set(claims)!=set(_RECEIPT_FIELDS):
            _deny("pie_receipt_fields_invalid")
        signed=_json(claims)
        signature=hmac.new(self._secret,signed,hashlib.sha256).hexdigest()
        return SignedResearchReceipt(**claims,signature=signature)


def _row_version_info(facts:dict,p:VisibilityPins)->dict:
    c=p.candidate
    meta=_compatibility(facts,p)
    if meta is None:
        _deny("pie_metric_incompatible")
    # Versions must be explicit PIE-version-safe tokens.
    versions=dict(
        group_a_version=c.group_a.analysis_version,
        group_b_version=c.group_b.analysis_version,
        group_b_taxonomy_version=c.group_b.taxonomy_version,
        sre_taxonomy_version=str(c.lineage.sre_taxonomy_version_id),
        creator_cohort_version=meta["creator_cohort_version"],
        prior_cohort_version=meta["prior_cohort_version"],
        niche_cohort_version=meta["niche_cohort_version"],
    )
    if any(not _VERSION.fullmatch(v) for v in versions.values() if v is not None):
        _deny("pie_version_invalid")
    return versions


def _a_features(db:Any,p:VisibilityPins)->dict | None:
    c=p.candidate
    scope=c.scope
    is_video=scope.content_kind in ("video","slideshow_video")
    fact=_one(db,A_VIDEO_SQL if is_video else A_CAROUSEL_SQL,
              (c.group_a.analysis_id,))
    if fact is None or any(fact.get(k)!=v for k,v in (
        ("id",c.group_a.analysis_id),
        ("video_id",scope.video_id),
        ("source_id",scope.source_id),
        ("source_policy_version_id",scope.source_policy_version_id),
        ("analysis_version",c.group_a.analysis_version),
        ("state","completed"),
    )):
        return None
    if is_video:
        if (type(fact.get("scene_count")) is not int
            or fact["scene_count"]<0
            or fact.get("has_subtitles") not in ("true","false","unknown")):
            return None
        return {
            "scene_count":fact["scene_count"],
            "has_subtitles":fact["has_subtitles"],
        }
    slides=fact.get("slide_count")
    if type(slides) is not int or not 1<=slides<=100:
        return None
    if type(fact.get("has_audio")) is not bool:
        return None
    return {"slide_count":slides,"has_audio":fact["has_audio"]}


def _b_features(db:Any,p:VisibilityPins)->dict | None:
    c=p.candidate
    scope=c.scope
    g=c.group_b
    rec=_one(db,GROUP_B_DIMENSIONS_SQL,(
        g.b_run_id,scope.project_id,scope.video_id,scope.source_id,
        scope.source_policy_version_id,g.b_analysis_id,g.b_analysis_id,
    ))
    if rec is None or any(rec.get(k)!=v for k,v in (
        ("analysis_id",g.b_analysis_id),("video_id",scope.video_id),
        ("source_id",scope.source_id),
        ("source_policy_version_id",scope.source_policy_version_id),
        ("analysis_version",g.analysis_version),
        ("taxonomy_version",g.taxonomy_version),
    )):
        return None
    # Never infer absent taxonomy features. A missing-but-inapplicable
    # dimension is explicitly None or an empty list, not fabricated "none".
    kinds=(
        ("hook_mechanism","hook_mechanisms"),
        ("story_role","story_roles"),
        ("cta_type","cta_types"),
        ("video_format","video_format"),
        ("editing_type","editing_type"),
        ("subtitle_type","subtitle_type"),
        ("topic_id","topic_id"),
    )
    result={}
    for dimension,column in kinds:
        values=_typed_values(rec,dimension,scope.content_kind,g.taxonomy_version)
        if dimension in ("hook_mechanism","story_role","cta_type"):
            result[column]=list(values)
        else:
            result[column]=values[0] if values else None
    return result


def _source_rows(db:Any,p:VisibilityPins)->tuple[dict,dict,dict] | None:
    scope=p.candidate.scope
    metric=_one(db,QUALIFIED_METRIC_SQL,
                (scope.project_id,p.snapshot_id,scope.video_id))
    meta=_one(db,AUTHOR_TIME_SQL,
              (scope.project_id,p.snapshot_id,scope.video_id))
    # Use the exact immutable UAE payload digest, not a made-up signature.
    snapshot=_one(db,"""SELECT pl.payload_sha256,sn.project_id,sn.id,
       sn.video_id,sn.status
       FROM uae_snapshot_payloads pl
       JOIN uae_unified_snapshots sn
         ON sn.project_id=pl.project_id AND sn.id=pl.snapshot_id
       WHERE pl.project_id=%s AND pl.snapshot_id=%s
         AND sn.video_id=%s AND sn.status='qualified'""",
          (scope.project_id,p.snapshot_id,scope.video_id))
    if not metric or not meta or not snapshot:
        return None
    if (_compatibility(metric,p) is None
        or snapshot.get("project_id")!=scope.project_id
        or snapshot.get("id")!=p.snapshot_id
        or snapshot.get("video_id")!=scope.video_id
        or not _sha(snapshot.get("payload_sha256"))
        or meta.get("metadata_id")!=p.candidate.group_c.metadata_id
        or meta.get("calculated_metric_id")!=p.candidate.group_c.calculated_metric_id
        or meta.get("metric_snapshot_id")!=p.candidate.group_c.current_snapshot_id
        or meta.get("source_id")!=scope.source_id
        or meta.get("source_policy_version_id")!=scope.source_policy_version_id
        or meta.get("is_current") is not True):
        return None
    return metric,meta,snapshot


class PIEResearchExportReader:
    """One-time authenticated producer export of exact pinned observed rows.

    Non-exported/denied member => NOTHING: no partial manifest, scores,
    exclusions or sample counts. Explicit population is only the qualified
    selected pins; no guessed project-wide missingness/excluded video IDs.
    """

    def __init__(self,conn:Any,signer:ReceiptSigner|None):
        if (conn is None or not callable(getattr(conn,"execute",None))
            or not callable(getattr(conn,"transaction",None))
            or not isinstance(signer,ReceiptSigner)):
            _deny("pie_export_capability_unavailable")
        self._conn=conn
        self._signer=signer

    def export(self,run_id:str,pins:tuple[VisibilityPins,...],challenge:str
               )->SignedResearchExport | None:
        if (not _uuid(run_id)
            or not _sha(challenge)
            or type(pins) is not tuple
            or not MIN_SAMPLE<=len(pins)<=MAX_AGGREGATE
            or any(not isinstance(p,VisibilityPins) for p in pins)
            or len({p.snapshot_id for p in pins})!=len(pins)
            or len({p.candidate.scope.video_id for p in pins})!=len(pins)
            or len({p.project_id for p in pins})!=1
            or len({p.candidate.scope.source_id for p in pins})!=1
            or len({p.candidate.scope.source_policy_version_id for p in pins})!=1):
            _deny("pie_export_request_invalid")
        if getattr(getattr(self._conn,"info",None),"transaction_status",1)!=0:
            _deny("pie_fresh_transaction_required")
        db=self._conn
        store=ProjectRankedRunStore(db)
        try:
            with db.transaction():
                db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                clock=db.execute(CLOCK_SQL).fetchone()
                if not isinstance(clock,Mapping):
                    _deny("pie_clock_invalid")
                now=_utc(clock.get("db_now"))
                if now is None:
                    _deny("pie_clock_invalid")
                # Every member CURRENT rights before ANY stored scores,
                # A/B details, source author/time or old manifest lookup.
                vis=VisibleSnapshotReader(db)
                for p in pins:
                    x=vis._read(db,p,now)
                    if (type(x) is not VisibleSnapshot
                        or x.project_id!=p.project_id
                        or x.snapshot_id!=p.snapshot_id
                        or x.video_id!=p.candidate.scope.video_id):
                        return None
                stored=_one(db,RUN_READ_ID_SQL,(pins[0].project_id,run_id))
                if stored is None or stored.get("sort_mode") not in SORT_MODES:
                    return None
                # Reverify exact current run stats and every pin/score.
                checked=store._verify_all(db,pins,stored["sort_mode"],now)
                if checked is None or not _has_match(
                    stored,checked,stored.get("request_key"),
                    stored.get("run_revision"),
                ):
                    return None
                version=None
                out=[]
                sorted_pins=sorted(pins,key=lambda p:p.candidate.scope.video_id)
                for p in sorted_pins:
                    data=_source_rows(db,p)
                    if data is None:
                        return None
                    m,a,snap=data
                    scores=_number(m.get("breakout_score"))
                    if scores is None:
                        return None
                    observed=_utc(a.get("observed_at"))
                    published=_utc(a.get("published_at"))
                    if (observed is None or published is None
                        or not published<=observed<=now):
                        return None
                    versions=_row_version_info(m,p)
                    if version is None:
                        version=versions
                    elif version!=versions:
                        return None
                    af=_a_features(db,p)
                    bf=_b_features(db,p)
                    if af is None or bf is None:
                        return None
                    scope=p.candidate.scope
                    cp=p.candidate
                    creator=a.get("creator_id")
                    if not _pk(creator):
                        return None
                    score=_canon_decimal(scores)
                    views=_number(m.get("views"))
                    if (views is None or views<0
                        or views!=views.to_integral_value()):
                        return None
                    # These two source-backed digests describe the selected
                    # persisted *facts*, not external producer signatures.
                    raw_sha=canonical_hash({
                        "id":cp.group_c.current_snapshot_id,
                        "video":scope.video_id,"views":str(views),
                        "observed_at":_iso(observed),
                        "metric_version":m["source_metric_version"],
                    })
                    calculated_sha=canonical_hash({
                        "id":cp.group_c.calculated_metric_id,
                        "video":scope.video_id,
                        "config":cp.group_c.config_id,
                        "score":score,
                    })
                    a_sha=cp.group_a.result_sha256
                    b_sha=canonical_hash({
                        "run":cp.group_b.b_run_id,
                        "analysis":cp.group_b.b_analysis_id,
                        "blocks":list(cp.group_b.block_evidence_sha256s),
                    })
                    if not _sha(a_sha):
                        return None
                    # PIE requires exact row fields. Unknown producer
                    # features are omitted/None rather than invented.
                    row={
                        "video_id":scope.video_id,
                        "uae_snapshot_id":p.snapshot_id,
                        "uae_snapshot_sha256":snap["payload_sha256"],
                        "project_id":scope.project_id,
                        "source_id":scope.source_id,
                        "source_policy_version_id":scope.source_policy_version_id,
                        "creator_key":"creator:"+str(creator),
                        "platform":m["platform"],
                        "content_kind":scope.content_kind,
                        "observed_at":_iso(observed),
                        "published_at":_iso(published),
                        "qdp_run_id":scope.qdp_run_id,
                        "qdp_preparation_sha256":scope.qdp_input_sha256,
                        "group_a_analysis_id":cp.group_a.analysis_id,
                        "group_b_analysis_id":cp.group_b.b_analysis_id,
                        "metric_snapshot_id":cp.group_c.current_snapshot_id,
                        "calculated_metric_id":cp.group_c.calculated_metric_id,
                        "sre_decision_sha256":cp.lineage.sre_decision_sha256,
                        "group_a_sha256":a_sha,
                        "group_b_sha256":b_sha,
                        "metric_snapshot_sha256":raw_sha,
                        "calculated_metric_sha256":calculated_sha,
                        "features_a":af,"features_b":bf,
                        "features_sha256":canonical_hash({"a":af,"b":bf}),
                        "status":"qualified","score_status":"complete",
                        "breakout_score":score,
                        **versions,
                    }
                    out.append(row)
                if not out or version is None:
                    return None
                scope=sorted_pins[0].candidate.scope
                if any(x["source_id"]!=scope.source_id or
                       x["source_policy_version_id"]!=scope.source_policy_version_id
                       for x in out):
                    return None
                pop_hash=canonical_hash({
                    "wire":PIE_SELECTION_RULE,
                    "scope":scope.scope_sha256,
                    "pins":[(p.candidate.scope.video_id,p.snapshot_id)
                            for p in sorted_pins],
                })
                # Selection rules ONLY cover the already pinned/qualified
                # sample. Never fake full project exclusions or missingness.
                manifest={
                    "schema_version":PIE_MANIFEST_WIRE,
                    "producer":{
                        "engine_version":PIE_ENGINE_VERSION,
                        "run_id":run_id,
                        "dataset_build_version":PIE_BUILD_VERSION,
                    },
                    "scope":{
                        "project_id":scope.project_id,
                        "source_id":scope.source_id,
                        "source_policy_version_id":scope.source_policy_version_id,
                        "calculation_config_id":sorted_pins[0].candidate.group_c.config_id,
                        "source_metric_definition":"platform_post_views",
                        "source_metric_version":out[0]["group_b_version"] if False
                           else m["source_metric_version"],
                        "as_of":_iso(now),
                    },
                    "versions":version,
                    "selection":{
                        "rule_version":PIE_SELECTION_RULE,
                        "population_sha256":pop_hash,
                        "scanned_count":len(out),
                        "included_count":len(out),
                        "excluded_count":0,
                        "exclusions":[],
                    },
                    "observations":out,
                }
                dataset_sha=canonical_hash({
                    "schema_version":PIE_DATASET_WIRE,
                    "observations":out,
                })
                manifest_sha=canonical_hash(manifest)
                fields=dict(
                    project_id=scope.project_id,
                    source_id=scope.source_id,
                    source_policy_version_id=scope.source_policy_version_id,
                    producer_version=PIE_ENGINE_VERSION,
                    producer_run_id=run_id,manifest_sha256=manifest_sha,
                    dataset_sha256=dataset_sha,challenge=challenge,
                    issued_at=_iso(now),
                    expires_at=_iso(now+timedelta(seconds=MAX_SIGNED_SECONDS)),
                    issuer=self._signer.issuer,key_id=self._signer.key_id,
                )
                receipt=self._signer.sign(fields)
                return SignedResearchExport(
                    _json(manifest),receipt,manifest_sha,dataset_sha,
                    len(out),
                )
        except AnalyticsBoundaryError:
            raise
        except Exception as exc:
            raise AnalyticsBoundaryError(
                "pie_export_source_unavailable",
                "UAE-to-PIE source read unavailable",
            ) from exc


__all__=(
    "PIE_MANIFEST_WIRE","PIE_DATASET_WIRE","PIE_BUILD_VERSION",
    "PIE_ENGINE_VERSION","PIE_SELECTION_RULE","MAX_SIGNED_SECONDS",
    "SignedResearchReceipt","SignedResearchExport","ReceiptSigner",
    "PIEResearchExportReader",
)
