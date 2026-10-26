"""GC-MOD-05: authoritative read-only D1 facts, project registry and source rights.

No content scoring, provider requests, shadow catalogs or implicit approvals.
These adapters read canonical 001/002/003/004 SQL; C0 strict numeric
qualification is explicitly reserved for GC-MOD-06. All readers deny by
default on missing, changed or cross-scoped evidence.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from .contracts import ContractError
from .ports import CapabilityState, IntegrationCapability, PORT_SCHEMAS

_SOURCES = frozenset({"can_store_metadata", "can_store_metrics", "can_derive_scores"})
_REQUIRED = {
    "d1": {
        "videos": {"id", "platform", "platform_video_id", "creator_id", "c_content_type", "content_kind",
                   "canonical_url", "published_at", "duration_seconds", "language"},
        "creators": {"id", "platform"},
        "metric_snapshots": {
            "id", "video_id", "source_id", "source_policy_version_id",
            "source_metric_definition", "source_metric_version", "observed_at",
            "collected_at", "views", "likes", "comments", "shares", "saves",
            "followers", "observation_time_assumed", "data_quality_status",
            "precision", "expires_at", "snapshot_key",
        },
        "source_video_metadata": {
            "id", "video_id", "source_id", "source_policy_version_id",
            "is_current", "deleted_at", "expires_at", "fetched_at", "published_at",
            "creator_name", "title", "description",
        },
    },
    "registry": {
        "project_registry": {"id"},
        "sre_project_videos": {"project_id", "video_id"},
        "videos": {"id", "creator_id", "platform", "c_content_type", "content_kind"},
        "creators": {"id", "platform", "platform_creator_id", "topic_id", "username",
                     "language", "region"},
        "topics": {"id", "name", "language", "region", "enabled"},
    },
    "source_policy": {
        "sources": {
            "id", "status", "retention_days", "can_store_metadata",
            "can_store_metrics", "can_derive_scores",
        },
        "source_policy_versions": {
            "id", "source_id", "is_active", "permissions", "retention_policy", "reviewed_at",
        },
        "source_video_metadata": {
            "id", "video_id", "source_id", "source_policy_version_id",
            "is_current", "fetched_at", "expires_at", "deleted_at",
        },
        "metric_snapshots": {
            "id", "video_id", "source_id", "source_policy_version_id",
            "data_quality_status", "precision", "observation_time_assumed",
            "expires_at", "observed_at",
        },
    },
}


def _int_id(value: object, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ContractError("invalid_identifier", f"{label} must be positive integer")
    return value


def _source_id(value: object) -> str:
    if type(value) is not str or not value.strip() or len(value) > 250:
        raise ContractError("invalid_source_id", "Exact nonblank D1 source ID required")
    return value


def _utc_time(value: object, label: str) -> datetime:
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() is None):
        raise ContractError("invalid_timestamp", f"Verified timezone-aware {label} required")
    return value


def _record(cursor: Any) -> dict | None:
    row = cursor.fetchone()
    if row is None:
        return None
    if isinstance(row, Mapping):
        return dict(row)
    return dict(zip((col.name for col in cursor.description), row))


def _records(cursor: Any) -> list[dict]:
    rows = cursor.fetchall()
    if not rows:
        return []
    cols = tuple(col.name for col in cursor.description)
    return [dict(row) if isinstance(row, Mapping) else dict(zip(cols, row))
            for row in rows]


def _missing(label: str) -> ContractError:
    return ContractError("d1_evidence_unavailable", "Exact D1 evidence missing: " + label)


def _probe(conn: Any, port: str) -> IntegrationCapability:
    """Required *physical columns* are checked; no peer readiness inferred."""
    try:
        for table, required in _REQUIRED[port].items():
            reg = _record(conn.execute("SELECT to_regclass(%s) AS rel", ("public." + table,)))
            if reg is None or reg.get("rel") is None:
                return IntegrationCapability(
                    port, CapabilityState.UNAVAILABLE,
                    reason_code="missing_" + table,
                )
            rows = _records(conn.execute(
                """SELECT column_name FROM information_schema.columns
                     WHERE table_schema='public' AND table_name=%s""",
                (table,),
            ))
            found = {r["column_name"] for r in rows}
            if not required.issubset(found):
                return IntegrationCapability(
                    port, CapabilityState.INCOMPATIBLE,
                    observed_schema="unknown",
                    reason_code="missing_columns_" + table,
                )
    except Exception:
        return IntegrationCapability(
            port, CapabilityState.UNAVAILABLE, reason_code="d1_schema_probe_failed",
        )
    return IntegrationCapability(
        port, CapabilityState.AVAILABLE, PORT_SCHEMAS[port], "canonical_tables_verified",
    )


class PostgreSQLD1FactsReader:
    """Read canonical videos, metric_snapshots and exact publication metadata."""

    def capability(self, conn: Any) -> IntegrationCapability:
        return _probe(conn, "d1")

    def read_video(self, conn: Any, video_id: int) -> dict:
        _int_id(video_id, "video_id")
        row = _record(conn.execute(
            """SELECT v.id,v.platform,v.platform_video_id,v.creator_id,
                      v.canonical_url,v.content_kind,v.c_content_type,
                      v.published_at,v.duration_seconds,v.language,
                      c.platform AS creator_platform
                 FROM videos v LEFT JOIN creators c ON c.id=v.creator_id
                WHERE v.id=%s""", (video_id,),
        ))
        if row is None:
            raise _missing("videos.id")
        if (row["creator_id"] is not None
                and row["creator_platform"] != row["platform"]):
            raise ContractError("creator_platform_mismatch", "D1 creator/video platform mismatch")
        if row["c_content_type"] not in (None, "video", "carousel"):
            raise ContractError("content_type_incompatible", "Unrecognized C content type")
        # Canonical c_content_type remains NULL if not classified. Do not
        # coerce legacy content_kind or fill missing creator information.
        return row

    def read_snapshot(self, conn: Any, video_id: int, snapshot_id: int) -> dict:
        _int_id(video_id, "video_id")
        _int_id(snapshot_id, "snapshot_id")
        row = _record(conn.execute(
            """SELECT id,video_id,source_id,source_policy_version_id,
                      observed_at,collected_at,views,likes,comments,shares,
                      saves,followers,precision,expires_at,source_metric_definition,
                      source_metric_version,observation_time_assumed,
                      data_quality_status,snapshot_key
                 FROM metric_snapshots WHERE id=%s AND video_id=%s""",
            (snapshot_id, video_id),
        ))
        if row is None:
            raise _missing("metric_snapshots.id/video_id")
        if row["video_id"] != video_id or row["id"] != snapshot_id:
            raise ContractError("d1_lineage_mismatch", "Wrong D1 snapshot identity")
        return row

    def read_previous_snapshots(
        self, conn: Any, video_id: int, source_id: str,
        metric_definition: str, metric_version: str,
        before: datetime, limit: int,
    ) -> list[dict]:
        _int_id(video_id, "video_id")
        _source_id(source_id)
        if (type(metric_definition) is not str or not metric_definition.strip()
                or type(metric_version) is not str or not metric_version.strip()):
            raise ContractError("invalid_metric_definition", "Explicit metric definition and version required")
        _utc_time(before, "before")
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ContractError("invalid_limit", "D1 history bounded to 1..64 rows")
        # No inferred fallback to another source or to a newer metric version.
        # Historical rows are returned INCLUDING retracted/partial evidence:
        # GC-MOD-06 rejects invalid newest observations, rather than choosing
        # older valid rows invisibly.
        return _records(conn.execute(
            """SELECT id,video_id,source_id,source_policy_version_id,
                      observed_at,collected_at,views,likes,comments,shares,
                      saves,followers,precision,expires_at,source_metric_definition,
                      source_metric_version,observation_time_assumed,
                      data_quality_status,snapshot_key
                 FROM metric_snapshots
                WHERE video_id=%s AND source_id=%s
                  AND source_metric_definition=%s AND source_metric_version=%s
                  AND observed_at<%s
                ORDER BY observed_at DESC,id DESC LIMIT %s""",
            (video_id, source_id, metric_definition, metric_version, before, limit),
        ))

    def read_recent_raw_observations(
        self, conn: Any, video_id: int, source_id: str, limit: int,
    ) -> list[dict]:
        """Latest raw source events WITHOUT version/quality/retention filtering.

        C0 needs to see newer retracted, corrected, changed-version or expired
        rows rather than silently reverting to a previous good pair. Runtime
        source eligibility is checked separately before any score use.
        """
        _int_id(video_id, "video_id")
        _source_id(source_id)
        if type(limit) is not int or not 2 <= limit <= 64:
            raise ContractError("invalid_limit", "C0 raw timeline bounded to 2..64")
        return _records(conn.execute(
            """SELECT id,video_id,source_id,source_policy_version_id,
                      observed_at,collected_at,views,likes,comments,shares,
                      saves,followers,precision,expires_at,source_metric_definition,
                      source_metric_version,observation_time_assumed,
                      data_quality_status,snapshot_key
                 FROM metric_snapshots
                WHERE video_id=%s AND source_id=%s
                ORDER BY observed_at DESC,id DESC LIMIT %s""",
            (video_id, source_id, limit),
        ))

    def read_publication(self, conn: Any, video_id: int, metadata_id: int) -> dict:
        _int_id(video_id, "video_id")
        _int_id(metadata_id, "metadata_id")
        row = _record(conn.execute(
            """SELECT id,video_id,source_id,source_policy_version_id,
                      fetched_at,published_at,creator_name,title,description,
                      is_current,deleted_at,expires_at
                 FROM source_video_metadata WHERE id=%s AND video_id=%s""",
            (metadata_id, video_id),
        ))
        if row is None:
            raise _missing("source_video_metadata.id/video_id")
        if (row["id"] != metadata_id or row["video_id"] != video_id
                or row["is_current"] is not True or row["deleted_at"] is not None):
            raise ContractError("publication_not_current", "D1 publication revoked or cross-video")
        now = datetime.now(timezone.utc)
        _utc_time(row["fetched_at"], "fetched_at")
        if row["expires_at"] is not None and _utc_time(row["expires_at"], "expires_at") <= now:
            raise ContractError("publication_expired", "D1 publication retention expired")
        # A missing published_at is allowed to remain None; C6 will block it.
        if row["published_at"] is not None:
            _utc_time(row["published_at"], "published_at")
        return row


class PostgreSQLCreatorTopicRegistry:
    """Explicit project membership, exact creator/topic IDs: no name matching."""

    def capability(self, conn: Any) -> IntegrationCapability:
        return _probe(conn, "registry")

    def read_creator(self, conn: Any, creator_id: int) -> dict:
        _int_id(creator_id, "creator_id")
        row = _record(conn.execute(
            """SELECT id,platform,platform_creator_id,topic_id,username,
                      language,region FROM creators WHERE id=%s""", (creator_id,),
        ))
        if row is None:
            raise _missing("creators.id")
        return row

    def read_topic(self, conn: Any, topic_id: int) -> dict:
        _int_id(topic_id, "topic_id")
        row = _record(conn.execute(
            """SELECT id,name,language,region,enabled
                 FROM topics WHERE id=%s""", (topic_id,),
        ))
        if row is None:
            raise _missing("topics.id")
        return row

    def read_project_video(self, conn: Any, project_id: str, video_id: int) -> dict:
        if type(project_id) is not str or len(project_id) != 32 or any(
            c not in "0123456789abcdef" for c in project_id
        ):
            raise ContractError("invalid_project_id", "Canonical project ID required")
        _int_id(video_id, "video_id")
        row = _record(conn.execute(
            """SELECT pv.project_id,pv.video_id,pv.added_at,v.platform,
                      v.creator_id,v.content_kind,v.c_content_type,
                      c.platform AS creator_platform,c.topic_id
                 FROM sre_project_videos pv
                 JOIN project_registry p ON p.id=pv.project_id
                 JOIN videos v ON v.id=pv.video_id
                 LEFT JOIN creators c ON c.id=v.creator_id
                WHERE pv.project_id=%s AND pv.video_id=%s""",
            (project_id, video_id),
        ))
        if row is None:
            raise ContractError("project_video_unavailable", "Video not explicitly linked to project")
        if row["project_id"] != project_id or row["video_id"] != video_id:
            raise ContractError("d1_lineage_mismatch", "Project link changed")
        if row["creator_id"] is not None and row["creator_platform"] != row["platform"]:
            raise ContractError("creator_platform_mismatch", "Creator and publication platforms disagree")
        return row


class PostgreSQLSourcePolicyReader:
    """Rights/retention gate; current eligibility is not C7 approval."""

    def capability(self, conn: Any) -> IntegrationCapability:
        return _probe(conn, "source_policy")

    def read_effective_policy(self, conn: Any, source_id: str,
                              policy_version_id: int | None) -> dict:
        _source_id(source_id)
        _int_id(policy_version_id, "source_policy_version_id")
        row = _record(conn.execute(
            """SELECT s.id AS source_id,s.status,s.can_store_metadata,
                      s.can_store_metrics,s.can_derive_scores,s.retention_days,
                      p.id AS source_policy_version_id,p.is_active,p.permissions,
                      p.retention_policy,p.reviewed_at
                 FROM sources s JOIN source_policy_versions p ON p.source_id=s.id
                WHERE s.id=%s AND p.id=%s""",
            (source_id, policy_version_id),
        ))
        if row is None:
            raise ContractError("source_policy_unavailable", "Pinned policy version not found")
        if (row["source_id"] != source_id
                or row["source_policy_version_id"] != policy_version_id):
            raise ContractError("source_policy_unavailable", "Policy belongs to different source")
        return row

    def assert_eligible(self, conn: Any, source_id: str, video_id: int,
                        snapshot_ids: Sequence[int], metadata_id: int,
                        cohort_ids: Sequence[int]) -> dict:
        _source_id(source_id)
        _int_id(video_id, "video_id")
        _int_id(metadata_id, "metadata_id")
        if (type(snapshot_ids) not in (tuple, list) or not 1 <= len(snapshot_ids) <= 64
                or any(type(x) is not int or x <= 0 for x in snapshot_ids)
                or len(set(snapshot_ids)) != len(snapshot_ids)):
            raise ContractError("invalid_snapshot_ids", "Exact distinct D1 snapshot IDs required")
        if type(cohort_ids) not in (tuple, list) or any(
            type(c) is not int or c < 1 for c in cohort_ids
        ):
            raise ContractError("invalid_cohort_ids", "Exact cohort IDs required")
        if cohort_ids:
            # GC-MOD-09/10 must verify cohort member rights independently.
            raise ContractError("cohort_approval_unavailable", "Peer cohort rights not yet implemented")
        meta = _record(conn.execute(
            """SELECT id,video_id,source_id,source_policy_version_id,
                      fetched_at,is_current,deleted_at,expires_at
                 FROM source_video_metadata
                WHERE id=%s AND video_id=%s AND source_id=%s""",
            (metadata_id, video_id, source_id),
        ))
        if meta is None or meta["is_current"] is not True or meta["deleted_at"] is not None:
            raise ContractError("metadata_unavailable", "Missing or deleted source evidence")
        policy_id = _int_id(meta["source_policy_version_id"], "source_policy_version_id")
        policy = self.read_effective_policy(conn, source_id, policy_id)
        perms = policy["permissions"]
        if (policy["status"] != "enabled" or policy["is_active"] is not True
                or not all(policy.get(key) is True for key in _SOURCES)
                or type(perms) is not dict
                or any(perms.get(key) is not True for key in _SOURCES)):
            raise ContractError("source_policy_unavailable", "Current rights are not granted")
        fetched = _utc_time(meta["fetched_at"], "metadata fetched_at")
        now = datetime.now(timezone.utc)
        if meta["expires_at"] is not None and _utc_time(meta["expires_at"], "metadata expiry") <= now:
            raise ContractError("metadata_expired", "Metadata expired")
        retention = policy["retention_policy"]
        if type(retention) is not dict:
            raise ContractError("source_policy_unavailable", "Policy retention must be object")
        durations = (policy["retention_days"], retention.get("retention_days"))
        for days in durations:
            if days is not None:
                if type(days) is not int or days < 1:
                    raise ContractError("source_policy_unavailable", "Invalid retention days")
                if fetched + timedelta(days=days) <= now:
                    raise ContractError("source_policy_expired", "Source retention expired")
        snaps = _records(conn.execute(
            """SELECT id,video_id,source_id,source_policy_version_id,
                      observed_at,expires_at,data_quality_status,
                      observation_time_assumed,precision
                 FROM metric_snapshots
                WHERE video_id=%s AND id = ANY(%s::bigint[])""",
            (video_id, list(snapshot_ids)),
        ))
        if len(snaps) != len(snapshot_ids) or {r["id"] for r in snaps} != set(snapshot_ids):
            raise ContractError("metric_snapshot_unavailable", "Exact snapshot set not found")
        for row in snaps:
            if (row["video_id"] != video_id or row["source_id"] != source_id
                    or row["source_policy_version_id"] != policy_id
                    or row["data_quality_status"] != "ok"
                    or row["precision"] != "exact"
                    or row["observation_time_assumed"] is not False):
                raise ContractError("metric_snapshot_ineligible", "D1 observation is not verified")
            _utc_time(row["observed_at"], "observation")
            if row["expires_at"] is not None and _utc_time(row["expires_at"], "metric expiry") <= now:
                raise ContractError("metric_snapshot_expired", "D1 observation expired")
        return {
            "state": "source_eligible", "source_id": source_id,
            "video_id": video_id, "source_policy_version_id": policy_id,
            "metadata_id": metadata_id, "snapshot_ids": tuple(snapshot_ids),
            "cohorts_verified": False, "score_approved": False,
        }


def local_d1_ports():
    """Explicitly inject local DB adapters; capability still probes the schema."""
    from .ports import GroupCPorts
    return GroupCPorts(
        d1=PostgreSQLD1FactsReader(),
        registry=PostgreSQLCreatorTopicRegistry(),
        source_policy=PostgreSQLSourcePolicyReader(),
    )


def read_scoped_d1_bundle(
    conn: Any, project_id: str, video_id: int, snapshot_ids: Sequence[int],
    metadata_id: int, *, ports: Any = None,
) -> dict:
    """Project-scoped D1 evidence and CURRENT source eligibility, not a score.

    No guessed analytics content type or inferred creator/topic/source IDs.
    Denies unknown platform-to-creator, mismatched raw kind, and different
    canonical project/video evidence. C0 arithmetic validation follows later.
    """
    from .ports import GroupCPorts
    adapters = ports if ports is not None else local_d1_ports()
    if not isinstance(adapters, GroupCPorts):
        raise ContractError("invalid_ports", "Expected Group C adapters")
    active = adapters.require_all(("d1", "registry", "source_policy"), conn)
    registry, d1, policy = active["registry"], active["d1"], active["source_policy"]
    link = registry.read_project_video(conn, project_id, video_id)
    video = d1.read_video(conn, video_id)
    if (link["video_id"] != video["id"]
            or link["platform"] != video["platform"]
            or link["creator_id"] != video["creator_id"]
            or link["c_content_type"] != video["c_content_type"]):
        raise ContractError("d1_lineage_mismatch", "Project/video canonical source changed")
    kind = video["c_content_type"]
    if kind not in ("video", "carousel"):
        raise ContractError("content_type_unclassified", "Analytic content type not approved")
    legacy = video["content_kind"]
    mapped = {
        "video": "video", "slideshow_video": "video",
        "photo_carousel": "carousel", "mixed_carousel": "carousel",
    }
    if legacy is not None and (legacy not in mapped or mapped[legacy] != kind):
        raise ContractError("content_kind_incompatible", "Raw and approved publication kinds differ")
    publication = d1.read_publication(conn, video_id, metadata_id)
    source_id = _source_id(publication["source_id"])
    proof = policy.assert_eligible(conn, source_id, video_id, snapshot_ids,
                                   metadata_id, [])
    shots = [d1.read_snapshot(conn, video_id, sid) for sid in snapshot_ids]
    if not shots or any(
        shot["video_id"] != video_id or
        shot["source_id"] != source_id or
        shot["source_policy_version_id"] != proof["source_policy_version_id"]
        for shot in shots
    ):
        raise ContractError("d1_lineage_mismatch", "Pinned snapshot source/policy disagrees")
    if publication["source_policy_version_id"] != proof["source_policy_version_id"]:
        raise ContractError("d1_lineage_mismatch", "Publication policy version disagrees")
    return {
        "status": "source_evidence_verified",
        "score_approved": False,
        "project_id": project_id,
        "video_id": video_id,
        "platform": video["platform"],
        "creator_id": video["creator_id"],
        "content_kind": legacy,
        "c_content_type": kind,
        "source_id": source_id,
        "source_policy_version_id": proof["source_policy_version_id"],
        "metadata": publication,
        "snapshots": tuple(shots),
    }
