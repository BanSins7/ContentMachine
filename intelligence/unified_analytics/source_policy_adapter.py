"""UA-MOD-11: authoritative current source rights, retention and revocation.

The actual source/policy, membership, pinned publication metadata, raw C0
observations and media assets are checked through one injected connection.
A source's TRUE permission is necessary but never sufficient: the matching
reviewed policy permission must ALSO be explicit TRUE. No fallback, no
implicit infinity for a missing derived retention policy, no cached grants.

All returned digests are evidence bindings, not authentication signatures.
SourcePermissionGuard (below) rechecks before AND after a peer read to reject
a changed/expired source. SQL snapshot-consistent transactions are required.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from .contracts import AnalyticsBoundaryError, canonical_hash
from .dto import EvidenceRead, EvidenceRef, InputScope, ReadState
from .ports import CapabilityState, PortCapability, PORT_SCHEMAS, UAEPorts

RIGHTS_VERSION = "uae-source-rights/v1"
RIGHTS = (
    "can_store_metadata","can_analyze_media",
    "can_store_metrics","can_derive_scores",
)
TABLES = ("sources","source_policy_versions","source_video_metadata",
          "metric_snapshots","media_assets","sre_project_videos")
PROBE_SQL = "SELECT " + ", ".join(
    f"to_regclass('public.{name}') AS {name}" for name in TABLES
)
SOURCE_SQL = """
SELECT s.id AS source_id,s.status AS source_status,s.updated_at AS source_updated_at,
       s.can_store_metadata,s.can_analyze_media,s.can_store_metrics,
       s.can_derive_scores,s.retention_days,
       p.id AS policy_id,p.source_id AS policy_source_id,
       p.is_active,p.permissions,p.retention_policy,p.reviewed_at,
       (SELECT count(*) FROM source_policy_versions newer
        WHERE newer.source_id=s.id AND newer.is_active) AS active_policy_count,
       EXISTS (SELECT 1 FROM sre_project_videos spv
        WHERE spv.project_id=%s AND spv.video_id=%s) AS project_link
FROM sources s
JOIN source_policy_versions p ON p.source_id=s.id AND p.id=%s
WHERE s.id=%s
"""
METADATA_SQL = """
SELECT id,video_id,source_id,source_policy_version_id,
       fetched_at,published_at,is_current,deleted_at,expires_at
FROM source_video_metadata WHERE id=%s AND video_id=%s
"""
METRICS_SQL = """
SELECT id,video_id,source_id,source_policy_version_id,
       source_metric_definition,source_metric_version,
       data_quality_status,precision,observation_time_assumed,
       observed_at,collected_at,expires_at
FROM metric_snapshots
WHERE video_id=%s AND id=ANY(%s::bigint[])
ORDER BY id
"""
MEDIA_SQL = """
SELECT id,video_id,source_id,media_kind,sha256,permission,
       created_at,expires_at
FROM media_assets
WHERE video_id=%s AND id=ANY(%s::bigint[])
ORDER BY id
"""
INVALIDATED_SQL = """
SELECT EXISTS(
    SELECT 1 FROM uae_snapshot_invalidations inv
    WHERE inv.project_id=%s AND inv.snapshot_id=%s
) AS invalidated
"""
SNAPSHOT_IDENTITY_SQL = """
SELECT sn.id,sn.project_id,sn.video_id,sn.source_id,
       sn.source_policy_version_id,sn.qdp_run_id::text AS qdp_run_id,
       sn.qdp_input_sha256,sn.content_kind,sn.status
FROM uae_unified_snapshots sn
WHERE sn.id=%s AND sn.project_id=%s
"""


def _fail(code: str) -> None:
    raise AnalyticsBoundaryError(code, "Current source rights rejected: "+code)


def _one(conn: Any, query: str, args: tuple = ()) -> dict | None:
    try:
        value=conn.execute(query,args).fetchone()
    except Exception as exc:
        raise AnalyticsBoundaryError("source_read_failed",
                                     "Current source DB read failed") from exc
    if value is None:
        return None
    if not isinstance(value,Mapping):
        _fail("source_db_shape_invalid")
    return dict(value)


def _many(conn: Any, query: str, args: tuple) -> list[dict]:
    try:
        values=conn.execute(query,args).fetchall()
    except Exception as exc:
        raise AnalyticsBoundaryError("source_read_failed",
                                     "Current source DB read failed") from exc
    if type(values) is not list or any(not isinstance(x,Mapping) for x in values):
        _fail("source_db_shape_invalid")
    return [dict(v) for v in values]


def _time(value: Any) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        return None
    try: return value.astimezone(timezone.utc)
    except (ValueError,OverflowError): return None


def _positive(value: Any) -> bool:
    return type(value) is int and value>0


@dataclass(frozen=True,slots=True)
class SourceRightsPins:
    scope: InputScope
    metadata_id: int
    metric_snapshot_ids: tuple[int, ...]
    media_asset_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scope,InputScope) or not _positive(self.metadata_id):
            _fail("source_scope_pins_invalid")
        for values in (self.metric_snapshot_ids,self.media_asset_ids):
            if (type(values) is not tuple or not values or len(values)>100
                or any(not _positive(x) for x in values) or
                len(set(values))!=len(values)):
                _fail("source_evidence_pins_invalid")
        if (self.scope.content_kind in ("video","slideshow_video")
            and len(self.media_asset_ids)!=1):
            _fail("source_video_media_pins_invalid")
        if (self.scope.content_kind in ("photo_carousel","mixed_carousel")
            and len(self.media_asset_ids)<2):
            _fail("source_carousel_media_pins_invalid")


@dataclass(frozen=True,slots=True)
class CurrentSourceRights:
    """Rights decision; never exposes source payload or production approval."""
    scope: InputScope
    metadata_id: int
    metric_snapshot_ids: tuple[int, ...]
    media_asset_ids: tuple[int, ...]
    as_of: datetime
    expires_at: datetime
    derived_retention_days: int
    evidence_sha256: str
    schema_version: str = RIGHTS_VERSION

    def __post_init__(self) -> None:
        if (not isinstance(self.scope,InputScope) or
            _time(self.as_of) is None or _time(self.expires_at) is None or
            not self.as_of<self.expires_at or
            type(self.derived_retention_days) is not int or
            not 1<=self.derived_retention_days<=365 or
            type(self.evidence_sha256) is not str or
            len(self.evidence_sha256)!=64):
            _fail("source_rights_result_invalid")


class SourcePolicyCurrentReader:
    """Implements UAE SourcePolicyReader, with independently pinned raw inputs."""

    def __init__(self,conn: Any,pins: SourceRightsPins,*,as_of: datetime):
        if not isinstance(pins,SourceRightsPins):
            _fail("source_pins_invalid")
        clock=_time(as_of)
        if clock is None or as_of.utcoffset().total_seconds()!=0:
            _fail("source_as_of_invalid")
        self._conn,self._pins,self._as_of=conn,pins,clock

    def capability(self) -> PortCapability:
        if self._conn is None or not callable(getattr(self._conn,"execute",None)):
            return PortCapability("source_policy",CapabilityState.UNAVAILABLE,
                                  reason_code="not_connected")
        try: schema=_one(self._conn,PROBE_SQL)
        except AnalyticsBoundaryError:
            return PortCapability("source_policy",CapabilityState.UNAVAILABLE,
                                  reason_code="source_schema_probe_failed")
        if schema is None or any(schema.get(t) is None for t in TABLES):
            return PortCapability("source_policy",CapabilityState.UNAVAILABLE,
                                  reason_code="source_schema_missing")
        return PortCapability("source_policy",CapabilityState.DECLARED,
                              PORT_SCHEMAS["source_policy"],RIGHTS_VERSION)

    def check_current_rights(self,scope:InputScope) -> CurrentSourceRights:
        if not isinstance(scope,InputScope) or scope!=self._pins.scope:
            _fail("source_scope_mismatch")
        if self.capability().state is not CapabilityState.DECLARED:
            _fail("source_schema_unavailable")
        pins=self._pins
        row=_one(self._conn,SOURCE_SQL,(
            scope.project_id,scope.video_id,
            scope.source_policy_version_id,scope.source_id
        ))
        if row is None:
            _fail("source_policy_missing")
        if row.get("project_link") is not True:
            _fail("source_project_link_revoked")
        if (row.get("source_id")!=scope.source_id or
            row.get("policy_id")!=scope.source_policy_version_id or
            row.get("policy_source_id")!=scope.source_id or
            row.get("source_status")!="enabled" or
            row.get("is_active") is not True or
            row.get("active_policy_count")!=1):
            _fail("source_policy_revoked_or_superseded")
        reviewed=_time(row.get("reviewed_at"))
        updated=_time(row.get("source_updated_at"))
        if reviewed is None or reviewed>self._as_of or updated is None or updated>self._as_of:
            _fail("source_policy_unreviewed")
        policy=row.get("permissions")
        if type(policy) is not dict or any(
            row.get(right) is not True or policy.get(right) is not True
            for right in RIGHTS
        ):
            _fail("source_permission_intersection_denied")
        source_days=row.get("retention_days")
        options=row.get("retention_policy")
        if (not _positive(source_days) or type(options) is not dict
            or not _positive(options.get("derived_retention_days"))
            or options["derived_retention_days"]>365):
            _fail("source_derived_retention_unapproved")
        days=min(source_days,options["derived_retention_days"])
        try: duration=timedelta(days=days)
        except (ValueError,OverflowError): _fail("source_retention_invalid")
        ends=[]
        metadata=_one(self._conn,METADATA_SQL,(pins.metadata_id,scope.video_id))
        if metadata is None or (
            metadata.get("id")!=pins.metadata_id or
            metadata.get("video_id")!=scope.video_id or
            metadata.get("source_id")!=scope.source_id or
            metadata.get("source_policy_version_id")!=scope.source_policy_version_id or
            metadata.get("is_current") is not True or
            metadata.get("deleted_at") is not None
        ):
            _fail("source_metadata_revoked")
        start=_time(metadata.get("fetched_at"))
        published=metadata.get("published_at")
        if (start is None or start>self._as_of or
            published is not None and (_time(published) is None or
                                       _time(published)>self._as_of)):
            _fail("source_metadata_time_invalid")
        self._expires(ends,start,metadata.get("expires_at"),duration)
        metrics=_many(self._conn,METRICS_SQL,(
            scope.video_id,list(pins.metric_snapshot_ids)
        ))
        if (len(metrics)!=len(pins.metric_snapshot_ids) or
            {x.get("id") for x in metrics}!=set(pins.metric_snapshot_ids)):
            _fail("source_metric_evidence_missing")
        for m in metrics:
            if (m.get("video_id")!=scope.video_id or
                m.get("source_id")!=scope.source_id or
                m.get("source_policy_version_id")!=scope.source_policy_version_id or
                m.get("data_quality_status")!="ok" or
                m.get("precision")!="exact" or
                m.get("observation_time_assumed") is not False or
                m.get("source_metric_definition")!="platform_post_views" or
                type(m.get("source_metric_version")) is not str or
                not m["source_metric_version"]):
                _fail("source_metric_invalid_or_revoked")
            observed=_time(m.get("observed_at"))
            collected=_time(m.get("collected_at"))
            if (observed is None or collected is None or
                observed>collected or collected>self._as_of):
                _fail("source_metric_time_invalid")
            self._expires(ends,observed,m.get("expires_at"),duration)
        media=_many(self._conn,MEDIA_SQL,(
            scope.video_id,list(pins.media_asset_ids)
        ))
        if (len(media)!=len(pins.media_asset_ids) or
            {x.get("id") for x in media}!=set(pins.media_asset_ids)):
            _fail("source_media_evidence_missing")
        kinds=[]
        for asset in media:
            if (asset.get("video_id")!=scope.video_id or
                asset.get("source_id")!=scope.source_id or
                asset.get("permission") not in ("local_verified","licensed",
                                                "owned","commercial","verified") or
                type(asset.get("sha256")) is not str or
                len(asset["sha256"])!=64 or
                any(ch not in "0123456789abcdef" for ch in asset["sha256"])):
                _fail("source_media_revoked")
            kind=asset.get("media_kind")
            if kind not in ("video","image"):
                _fail("source_media_kind_invalid")
            kinds.append(kind)
            created=_time(asset.get("created_at"))
            if created is None or created>self._as_of:
                _fail("source_media_time_invalid")
            self._expires(ends,created,asset.get("expires_at"),duration)
        if (scope.content_kind in ("video","slideshow_video") and kinds!=["video"] or
            scope.content_kind=="photo_carousel" and any(k!="image" for k in kinds) or
            scope.content_kind=="mixed_carousel" and not (
                "image" in kinds and "video" in kinds)):
            _fail("source_media_kind_mismatch")
        expiry=min(ends)
        if expiry<=self._as_of:
            _fail("source_retention_expired")
        digest=canonical_hash({
            "version":RIGHTS_VERSION,"scope":scope.scope_sha256,
            "policy_id":scope.source_policy_version_id,
            "reviewed_at":reviewed.isoformat(),
            "source_updated_at":updated.isoformat(),
            "days":days,"metadata_id":pins.metadata_id,
            "metric_ids":list(pins.metric_snapshot_ids),
            "media_ids":list(pins.media_asset_ids),
            "media_sha":[m["sha256"] for m in media],
            "expires_at":expiry.isoformat(),
            "as_of":self._as_of.isoformat(),
        })
        return CurrentSourceRights(
            scope=scope,metadata_id=pins.metadata_id,
            metric_snapshot_ids=pins.metric_snapshot_ids,
            media_asset_ids=pins.media_asset_ids,
            as_of=self._as_of,expires_at=expiry,
            derived_retention_days=days,evidence_sha256=digest,
        )

    @staticmethod
    def _expires(values:list[datetime],start:datetime,
                 stored:Any,maximum:timedelta) -> None:
        try: end=start+maximum
        except (OverflowError,ValueError): _fail("source_retention_invalid")
        if stored is not None:
            stored_at=_time(stored)
            if stored_at is None or stored_at<start:
                _fail("source_expiry_invalid")
            end=min(end,stored_at)
        values.append(end)

    def read_current_policy(self,scope:InputScope) -> EvidenceRead:
        if not isinstance(scope,InputScope) or scope!=self._pins.scope:
            _fail("source_scope_mismatch")
        if self.capability().state is not CapabilityState.DECLARED:
            return EvidenceRead("source_policy",scope,ReadState.UNAVAILABLE,
                                reason_code="source_schema_unavailable")
        try: rights=self.check_current_rights(scope)
        except AnalyticsBoundaryError as exc:
            if exc.code=="source_read_failed":
                return EvidenceRead("source_policy",scope,ReadState.UNAVAILABLE,
                                    reason_code=exc.code)
            return EvidenceRead("source_policy",scope,ReadState.MISSING,
                                reason_code=exc.code)
        return EvidenceRead("source_policy",scope,ReadState.LOCATED,
                            reference=EvidenceRef(
                                scope=scope,port="source_policy",
                                record_key="policy:"+str(scope.source_policy_version_id),
                                producer_schema=RIGHTS_VERSION,
                                producer_version="current-v1",
                                payload_sha256=rights.evidence_sha256,
                            ))


class GuardedUAEReads:
    """Mandatory facade for future intake/read handlers.

    Rechecks rights for every read, before and after obtaining peer facts.
    Permission expiry / revocation means no result including partial facts.
    Cannot turn a missing peer into LOCATED. Call the facade under one
    caller-owned transaction; direct UAEPorts.read remains a low-level
    *untrusted evidence* interface and MUST NOT be exposed as HTTP read API.
    """

    def __init__(self, ports:UAEPorts,rights:SourcePolicyCurrentReader):
        if not isinstance(ports,UAEPorts) or not isinstance(rights,SourcePolicyCurrentReader):
            _fail("source_guard_dependencies_invalid")
        if ports.source_policy is not rights:
            _fail("source_guard_policy_port_mismatch")
        self._ports,self._rights=ports,rights

    def read(self,port:str,scope:InputScope) -> EvidenceRead:
        if port=="patterns" or port not in (
            "d1","source_policy","sre","qdp","group_a","group_b","group_c"
        ):
            _fail("source_guard_port_invalid")
        before=self._rights.read_current_policy(scope)
        if before.state is not ReadState.LOCATED:
            return EvidenceRead(port,scope,before.state,
                                reason_code=before.reason_code)
        if port=="source_policy":
            return before
        result=self._ports.read(port,scope)
        after=self._rights.read_current_policy(scope)
        if after.state is not ReadState.LOCATED:
            return EvidenceRead(port,scope,after.state,
                                reason_code=after.reason_code)
        if after.reference.lineage_sha256!=before.reference.lineage_sha256:
            return EvidenceRead(port,scope,ReadState.MISSING,
                                reason_code="source_rights_changed_during_read")
        return result

    def authorize_snapshot(self,scope:InputScope,snapshot_id:int) -> CurrentSourceRights:
        if not _positive(snapshot_id) or not isinstance(scope,InputScope):
            _fail("source_snapshot_identity_invalid")
        # Check the immutable snapshot identity and revocation ledger inside
        # the same transaction as rights + downstream *actual* read.
        r=_one(self._rights._conn,SNAPSHOT_IDENTITY_SQL,
               (snapshot_id,scope.project_id))
        if r is None or any(r.get(key)!=expected for key,expected in (
            ("id",snapshot_id),("project_id",scope.project_id),
            ("video_id",scope.video_id),("source_id",scope.source_id),
            ("source_policy_version_id",scope.source_policy_version_id),
            ("qdp_run_id",scope.qdp_run_id),
            ("qdp_input_sha256",scope.qdp_input_sha256),
            ("content_kind",scope.content_kind),
        )):
            _fail("source_snapshot_scope_invalid")
        invalidated=_one(self._rights._conn,INVALIDATED_SQL,
                         (scope.project_id,snapshot_id))
        if invalidated is None or invalidated.get("invalidated") is not False:
            _fail("source_snapshot_invalidated")
        return self._rights.check_current_rights(scope)


__all__ = (
    "RIGHTS_VERSION","RIGHTS","SourceRightsPins","CurrentSourceRights",
    "SourcePolicyCurrentReader","GuardedUAEReads","PROBE_SQL",
    "SOURCE_SQL","METADATA_SQL","METRICS_SQL","MEDIA_SQL",
    "SNAPSHOT_IDENTITY_SQL","INVALIDATED_SQL",
)
