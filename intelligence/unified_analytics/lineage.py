"""UA-MOD-05 — exact canonical D1/project/source lineage, no LIVE qualification.

This reader is injected with an already-owned DB connection. It never opens a
connection, discovers 'latest' records, reads QDP feature-branch tables, uses
provider calls, computes Group C scores, or grants source rights. The caller
must use a current transactional snapshot with a dict-row connection, and
future UA-MOD-06..13 adapters must verify peer facts, approvals and permissions.

A successful CanonicalLineage means ONLY the pinned IDs exist and agree in
baseline D1/SRE/A/B/C relational identity. qdp_verification is ALWAYS
"not_checked": actual qualified membership is a separate QDP adapter step.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Mapping
from uuid import UUID

from .contracts import (
    AnalyticsBoundaryError, UnifiedAnalyticsInputPointer,
    canonical_hash, content_type_for_kind,
)
from .dto import InputScope

LINEAGE_SCHEMA = "uae-canonical-lineage/v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE = re.compile(r"[A-Za-z0-9._:-]{1,160}\Z")
_VERSION = re.compile(r"[A-Za-z0-9._:/-]{1,160}\Z")


def _fail(code: str, reason: str = "Canonical lineage rejected") -> None:
    raise AnalyticsBoundaryError(code, reason)


def _id(number: object, field: str) -> None:
    if type(number) is not int or number <= 0:
        _fail("invalid_identifier", field)


def _uuid(value: object, field: str) -> None:
    if type(value) is not str:
        _fail("invalid_uuid", field)
    try:
        if str(UUID(value)) != value:
            _fail("invalid_uuid", field)
    except (AttributeError, TypeError, ValueError) as exc:
        raise AnalyticsBoundaryError("invalid_uuid", field) from exc


def _version(value: object, field: str) -> None:
    if type(value) is not str or _VERSION.fullmatch(value) is None:
        _fail("invalid_version", field)


def _utc(as_of: datetime) -> None:
    if (type(as_of) is not datetime or as_of.tzinfo is None
            or as_of.utcoffset() is None or as_of.utcoffset().total_seconds() != 0):
        _fail("invalid_time", "Explicit UTC as_of required")


@dataclass(frozen=True, slots=True)
class CanonicalLineagePins:
    """Strict IDs selected upstream; not a claim that the rows are completed."""

    scope: InputScope
    metadata_id: int
    metric_snapshot_id: int
    sre_run_id: str
    sre_taxonomy_version_id: int
    sre_content_version_id: int
    group_a_analysis_id: int
    group_b_analysis_id: int
    calculated_metric_id: int
    b_taxonomy_version: str
    sre_decision_sha256: str
    schema_version: str = LINEAGE_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.scope, InputScope):
            _fail("invalid_scope", "InputScope required")
        for name in (
            "metadata_id", "metric_snapshot_id", "sre_taxonomy_version_id",
            "sre_content_version_id", "group_a_analysis_id",
            "group_b_analysis_id", "calculated_metric_id",
        ):
            _id(getattr(self, name), name)
        _uuid(self.sre_run_id, "sre_run_id")
        _version(self.b_taxonomy_version, "b_taxonomy_version")
        if (type(self.sre_decision_sha256) is not str
                or _SHA.fullmatch(self.sre_decision_sha256) is None):
            _fail("invalid_sha256", "sre_decision_sha256")
        if self.schema_version != LINEAGE_SCHEMA:
            _fail("unsupported_schema_version", "Unknown lineage contract")

    @classmethod
    def from_pointer(
        cls, pointer: UnifiedAnalyticsInputPointer, *,
        metadata_id: int, metric_snapshot_id: int,
        sre_run_id: str, sre_taxonomy_version_id: int,
        sre_content_version_id: int, b_taxonomy_version: str,
    ) -> "CanonicalLineagePins":
        """Extend existing bootstrap pointer with mandatory *explicit* IDs."""
        if not isinstance(pointer, UnifiedAnalyticsInputPointer):
            _fail("invalid_pointer", "Expected UAE pointer")
        return cls(
            scope=InputScope.from_pointer(pointer),
            metadata_id=metadata_id,
            metric_snapshot_id=metric_snapshot_id,
            sre_run_id=sre_run_id,
            sre_taxonomy_version_id=sre_taxonomy_version_id,
            sre_content_version_id=sre_content_version_id,
            group_a_analysis_id=pointer.group_a_analysis_id,
            group_b_analysis_id=pointer.group_b_analysis_id,
            calculated_metric_id=pointer.calculated_metric_id,
            b_taxonomy_version=b_taxonomy_version,
            sre_decision_sha256=pointer.sre_decision_sha256,
        )

    @property
    def identity_sha256(self) -> str:
        """Fingerprint of ALL pins, not authentication or current rights."""
        return canonical_hash({
            "schema_version": self.schema_version,
            "scope_sha256": self.scope.scope_sha256,
            "metadata_id": self.metadata_id,
            "metric_snapshot_id": self.metric_snapshot_id,
            "sre_run_id": self.sre_run_id,
            "sre_taxonomy_version_id": self.sre_taxonomy_version_id,
            "sre_content_version_id": self.sre_content_version_id,
            "group_a_analysis_id": self.group_a_analysis_id,
            "group_b_analysis_id": self.group_b_analysis_id,
            "calculated_metric_id": self.calculated_metric_id,
            "b_taxonomy_version": self.b_taxonomy_version,
            "sre_decision_sha256": self.sre_decision_sha256,
        })


@dataclass(frozen=True, slots=True)
class CanonicalLineage:
    """Only same-project/source/video/policy exact D1 row identity was linked."""

    pins: CanonicalLineagePins
    as_of: datetime
    a_analysis_version: str
    b_analysis_version: str
    b_taxonomy_version: str
    sre_taxonomy_checksum: str
    sre_content_revision: int
    metric_source_definition: str
    metric_source_version: str
    metric_snapshot_id: int
    calculated_metric_id: int
    score_config_id: int
    lineage_sha256: str
    schema_version: str = LINEAGE_SCHEMA

    @property
    def qdp_verification(self) -> str:
        return "not_checked"

    @property
    def qualified(self) -> bool:
        return False


# Baseline main tables only. Never interpolate user data or dynamically name a
# table from caller input. Strictly parameterized IDs and preselected A table.
D1_IDENTITY_SQL = """
SELECT
 pr.id AS project_id,
 v.id AS video_id, v.content_kind AS content_kind,
 pv.video_id AS member_video_id,
 s.id AS source_id, s.status AS source_status,
 sp.id AS policy_id, sp.source_id AS policy_source_id,
 sp.is_active AS policy_is_active,
 m.id AS metadata_id, m.source_policy_version_id AS metadata_policy_id,
 m.is_current AS metadata_is_current, m.deleted_at AS metadata_deleted_at,
 m.expires_at AS metadata_expires_at,
 ms.id AS snapshot_id, ms.source_id AS snapshot_source_id,
 ms.source_policy_version_id AS snapshot_policy_id,
 ms.video_id AS snapshot_video_id,
 ms.source_metric_definition AS metric_definition,
 ms.source_metric_version AS metric_version,
 w.active_taxonomy_id AS active_taxonomy_id,
 t.id AS taxonomy_id, t.project_id AS taxonomy_project_id,
 t.status AS taxonomy_status, t.snapshot_checksum AS taxonomy_checksum,
 r.id AS sre_run_id, r.project_id AS sre_run_project_id,
 r.taxonomy_version_id AS run_taxonomy_id,
 cv.id AS content_version_id, cv.project_id AS content_project_id,
 cv.video_id AS content_video_id, cv.revision AS content_revision
FROM project_registry pr
LEFT JOIN videos v ON v.id=%s
LEFT JOIN sre_project_videos pv
  ON pv.project_id=pr.id AND pv.video_id=v.id
LEFT JOIN sources s ON s.id=%s
LEFT JOIN source_policy_versions sp
  ON sp.id=%s AND sp.source_id=s.id
LEFT JOIN source_video_metadata m
  ON m.id=%s AND m.video_id=v.id AND m.source_id=s.id
LEFT JOIN metric_snapshots ms
  ON ms.id=%s AND ms.video_id=v.id
LEFT JOIN sre_workspaces w ON w.project_id=pr.id
LEFT JOIN sre_taxonomy_versions t
  ON t.id=%s AND t.project_id=pr.id
LEFT JOIN sre_classification_runs r
  ON r.id=%s AND r.project_id=pr.id
LEFT JOIN sre_content_versions cv
  ON cv.id=%s AND cv.project_id=pr.id AND cv.video_id=v.id
WHERE pr.id=%s
"""

A_VIDEO_SQL = """
SELECT id, video_id, source_id, source_policy_version_id,
       analysis_version, state, NULL::text AS content_kind
FROM video_feature_a_analyses WHERE id=%s
"""
A_CAROUSEL_SQL = """
SELECT id, video_id, source_id, source_policy_version_id,
       analysis_version, state, content_kind
FROM carousel_feature_a_analyses WHERE id=%s
"""
B_ANALYSIS_SQL = """
SELECT id, video_id, source_id, source_policy_version_id,
       video_a_analysis_id, carousel_a_analysis_id, content_type,
       analysis_version, taxonomy_version, state, needs_review
FROM content_b_analyses WHERE id=%s
"""
C_METRIC_SQL = """
SELECT cm.id, cm.post_id, cm.snapshot_id, cm.calculation_config_id,
       cm.score_status, cm.score_creator_component,
       ms.source_id, ms.source_policy_version_id,
       ms.source_metric_definition, ms.source_metric_version
FROM calculated_metrics cm
JOIN metric_snapshots ms ON ms.id=cm.snapshot_id AND ms.video_id=cm.post_id
WHERE cm.id=%s
"""


def _row(conn: Any, sql: str, args: tuple[Any, ...]) -> dict[str, Any] | None:
    """An injected psycopg dict_row connection or compatible test double."""
    try:
        cursor = conn.execute(sql, args)
        raw = cursor.fetchone()
    except Exception as exc:
        raise AnalyticsBoundaryError(
            "lineage_read_unavailable", "Canonical D1 read failed"
        ) from exc
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        _fail("invalid_db_row", "Use psycopg dict_row for canonical D1 reads")
    return dict(raw)


def resolve_canonical_lineage(
    conn: Any, pins: CanonicalLineagePins, *, as_of: datetime,
) -> CanonicalLineage:
    """Reject cross-project, missing, superseded or unpinned D1 identities.

    Precondition: the caller controls an authorized, repeatable-read DB
    transaction and supplies as_of from its clock. This pure DB reader has NO
    QDP tables on main and cannot mark a result as qualified.
    """
    if not isinstance(pins, CanonicalLineagePins):
        _fail("invalid_pins", "CanonicalLineagePins required")
    _utc(as_of)
    if conn is None or not callable(getattr(conn, "execute", None)):
        _fail("dependency_unavailable", "Inject an authoritative D1 connection")

    s = pins.scope
    base = _row(conn, D1_IDENTITY_SQL, (
        s.video_id, s.source_id, s.source_policy_version_id,
        pins.metadata_id, pins.metric_snapshot_id,
        pins.sre_taxonomy_version_id, pins.sre_run_id,
        pins.sre_content_version_id, s.project_id,
    ))
    if base is None:
        _fail("project_not_found")
    if base.get("project_id") != s.project_id or base.get("video_id") != s.video_id:
        _fail("video_project_identity_mismatch")
    if base.get("member_video_id") != s.video_id:
        _fail("not_in_project")
    if base.get("content_kind") != s.content_kind:
        _fail("content_kind_mismatch")
    if (base.get("source_id") != s.source_id
            or base.get("policy_id") != s.source_policy_version_id
            or base.get("policy_source_id") != s.source_id):
        _fail("source_policy_lineage_mismatch")
    if base.get("source_status") != "enabled" or base.get("policy_is_active") is not True:
        _fail("source_policy_inactive")
    if (base.get("metadata_id") != pins.metadata_id
            or base.get("metadata_policy_id") != s.source_policy_version_id
            or base.get("metadata_is_current") is not True
            or base.get("metadata_deleted_at") is not None):
        _fail("source_metadata_lineage_mismatch")
    expires_at = base.get("metadata_expires_at")
    if expires_at is not None:
        if not isinstance(expires_at, datetime) or expires_at.tzinfo is None:
            _fail("invalid_db_row", "Metadata expiry must be timezone-aware")
        if expires_at <= as_of:
            _fail("source_metadata_expired")
    if (base.get("snapshot_id") != pins.metric_snapshot_id
            or base.get("snapshot_video_id") != s.video_id
            or base.get("snapshot_source_id") != s.source_id
            or base.get("snapshot_policy_id") != s.source_policy_version_id):
        _fail("metric_lineage_mismatch")
    if not all(_VERSION.fullmatch(base.get(k) or "") for k in (
        "metric_definition", "metric_version"
    )):
        _fail("metric_version_unknown")
    if (base.get("taxonomy_id") != pins.sre_taxonomy_version_id
            or base.get("taxonomy_project_id") != s.project_id
            or base.get("active_taxonomy_id") != pins.sre_taxonomy_version_id
            or base.get("taxonomy_status") != "approved"):
        _fail("sre_taxonomy_not_current")
    checksum = base.get("taxonomy_checksum")
    if type(checksum) is not str or _SHA.fullmatch(checksum) is None:
        _fail("sre_taxonomy_fingerprint_missing")
    if (str(base.get("sre_run_id")) != pins.sre_run_id
            or base.get("sre_run_project_id") != s.project_id
            or base.get("run_taxonomy_id") != pins.sre_taxonomy_version_id):
        _fail("sre_run_lineage_mismatch")
    if (base.get("content_version_id") != pins.sre_content_version_id
            or base.get("content_project_id") != s.project_id
            or base.get("content_video_id") != s.video_id
            or not _positive(base.get("content_revision"))):
        _fail("sre_content_version_mismatch")

    video = s.content_kind in ("video", "slideshow_video")
    a = _row(conn, A_VIDEO_SQL if video else A_CAROUSEL_SQL, (pins.group_a_analysis_id,))
    if a is None or not _consistent_source_policy(
        a, s, id_key="id", expected_id=pins.group_a_analysis_id
    ):
        _fail("a_lineage_mismatch")
    if not video and a.get("content_kind") != s.content_kind:
        _fail("a_carousel_kind_mismatch")
    if not _text_version(a.get("analysis_version")):
        _fail("a_version_unknown")

    b = _row(conn, B_ANALYSIS_SQL, (pins.group_b_analysis_id,))
    if b is None or not _consistent_source_policy(
        b, s, id_key="id", expected_id=pins.group_b_analysis_id
    ):
        _fail("b_lineage_mismatch")
    b_parent = "video_a_analysis_id" if video else "carousel_a_analysis_id"
    other = "carousel_a_analysis_id" if video else "video_a_analysis_id"
    if (b.get("content_type") != content_type_for_kind(s.content_kind)
            or b.get(b_parent) != pins.group_a_analysis_id
            or b.get(other) is not None):
        _fail("a_b_lineage_mismatch")
    if not _text_version(b.get("analysis_version")):
        _fail("b_version_unknown")
    if b.get("taxonomy_version") != pins.b_taxonomy_version:
        _fail("b_taxonomy_version_mismatch")

    c = _row(conn, C_METRIC_SQL, (pins.calculated_metric_id,))
    if c is None or (
        c.get("id") != pins.calculated_metric_id
        or c.get("post_id") != s.video_id
        or c.get("snapshot_id") != pins.metric_snapshot_id
        or c.get("source_id") != s.source_id
        or c.get("source_policy_version_id") != s.source_policy_version_id
        or c.get("source_metric_definition") != base.get("metric_definition")
        or c.get("source_metric_version") != base.get("metric_version")
    ):
        _fail("c_metric_lineage_mismatch")
    if not _positive(c.get("calculation_config_id")):
        _fail("c_config_reference_missing")

    # No claims about SRE LIVE mode, QDP qualified membership, A/B coverage,
    # C approval, media rights, content hashes, source expiry or score quality.
    lineage_hash = canonical_hash({
        "schema_version": LINEAGE_SCHEMA,
        "pins_sha256": pins.identity_sha256,
        "as_of": as_of.isoformat(),
        "a_analysis_version": a["analysis_version"],
        "b_analysis_version": b["analysis_version"],
        "b_taxonomy_version": b["taxonomy_version"],
        "sre_taxonomy_checksum": checksum,
        "sre_content_revision": base["content_revision"],
        "metric_definition": base["metric_definition"],
        "metric_version": base["metric_version"],
        "score_config_id": c["calculation_config_id"],
        "qdp_verification": "not_checked",
    })
    return CanonicalLineage(
        pins=pins, as_of=as_of,
        a_analysis_version=a["analysis_version"],
        b_analysis_version=b["analysis_version"],
        b_taxonomy_version=b["taxonomy_version"],
        sre_taxonomy_checksum=checksum,
        sre_content_revision=base["content_revision"],
        metric_source_definition=base["metric_definition"],
        metric_source_version=base["metric_version"],
        metric_snapshot_id=pins.metric_snapshot_id,
        calculated_metric_id=pins.calculated_metric_id,
        score_config_id=c["calculation_config_id"],
        lineage_sha256=lineage_hash,
    )


def _positive(value: Any) -> bool:
    return type(value) is int and value > 0


def _text_version(value: Any) -> bool:
    return type(value) is str and bool(_VERSION.fullmatch(value))


def _consistent_source_policy(
    row: Mapping[str, Any], scope: InputScope, *, id_key: str, expected_id: int,
) -> bool:
    return (
        row.get(id_key) == expected_id
        and row.get("video_id") == scope.video_id
        and row.get("source_id") == scope.source_id
        and row.get("source_policy_version_id") == scope.source_policy_version_id
    )
