"""FIX-07: exact SRE S3 snapshot → D1 metadata observation and discovery.

QDP never picks the most recent observation of a canonical video. Every
qualified video's accepted metadata must match its immutable SRE content
version, source ID, policy version, observation ID, and project discovery.

Missing peer S3 columns/association = explicit BLOCKED (not silent fallback).
No paid calls, ingestion, metadata updates or media downloads.
"""
from __future__ import annotations

from collections.abc import Mapping

from .contracts import QDPError


def _single(values, label):
    if type(values) is not list or len(values) != 1:
        raise QDPError("sre_source_unpinned",
                       "Expected one pinned SRE " + label, 409)
    value = values[0]
    if label.endswith("_id") or label in ("policy", "observation", "discovery"):
        if type(value) is not int or value <= 0:
            raise QDPError("sre_source_unpinned", "Invalid " + label, 409)
    elif type(value) is not str or not value:
        raise QDPError("sre_source_unpinned", "Invalid source", 409)
    return value


def pinned_source_identity(pinned: Mapping) -> dict:
    """Pure identity validator. Used before any database query or fallback."""
    if not isinstance(pinned, Mapping):
        raise QDPError("sre_source_unpinned", "Missing source snapshot", 409)
    return {
        "content_version_id": _single([pinned.get("content_version_id")],
                                      "content_version_id"),
        "source_id": _single(pinned.get("source_ids"), "source"),
        "source_policy_version_id": _single(
            pinned.get("source_policy_version_ids"), "policy"),
        "metadata_observation_id": _single(
            pinned.get("metadata_observation_ids"), "observation"),
        "discovery_id": _single(pinned.get("discovery_ids"), "discovery"),
        "content_sha256": pinned.get("content_sha256"),
    }


def read_exact_metadata(conn, project: str, video_id: int,
                        pinned: Mapping) -> tuple[dict | None, str | None]:
    """Return exact authorized metadata or a stable reason code for Worker.

    The SRE peer S3 migration owns metadata_observation_id and
    sre_project_discoveries. Their absence blocks downstream readiness.
    """
    try:
        identity = pinned_source_identity(pinned)
    except QDPError:
        return None, "sre_source_unpinned"
    if (type(identity["content_sha256"]) is not str or
            len(identity["content_sha256"]) != 64):
        return None, "sre_source_unpinned"
    # QDP v1 may deploy before independent SRE S3 migration. Don't run SQL
    # that references nonexistent peer columns/tables.
    capability = conn.execute(
        """SELECT
          EXISTS (
            SELECT 1 FROM pg_attribute
            WHERE attrelid=to_regclass('sre_content_versions')
              AND attname='metadata_observation_id' AND NOT attisdropped
          ) AS observation_column,
          (to_regclass('sre_project_discoveries') IS NOT NULL) AS discovery_table"""
    ).fetchone()
    if not capability or not all(capability.values()):
        return None, "sre_metadata_contract_unavailable"
    cid, sid, policy, obs, did = (
        identity["content_version_id"], identity["source_id"],
        identity["source_policy_version_id"],
        identity["metadata_observation_id"], identity["discovery_id"])
    # A currently authorized SRE view row is mandatory. It may be absent
    # because source was disabled, policy revoked, SRE content expired, etc.
    content = conn.execute(
        """SELECT c.id,c.source_id,c.source_policy_version_id,
                  c.metadata_observation_id,c.provenance,c.content_sha256,c.content
           FROM sre_content_versions c
           JOIN sre_readable_content_versions readable
             ON readable.id=c.id AND readable.project_id=c.project_id
            AND readable.video_id=c.video_id
           WHERE c.id=%s AND c.project_id=%s AND c.video_id=%s""",
        (cid, project, video_id)).fetchone()
    if not isinstance(content, Mapping) or not content:
        return None, "sre_content_unreadable"
    if (content["source_id"] != sid or
            content["source_policy_version_id"] != policy or
            content["metadata_observation_id"] != obs or
            content["content_sha256"] != identity["content_sha256"] or
            not isinstance(content["provenance"], dict) or
            content["provenance"].get("discovery_id") != did):
        return None, "sre_metadata_lineage_mismatch"
    # SRE's link is project-scoped. A same-video discovery in another
    # project or a discovery from another source must never be accepted.
    linked = conn.execute(
        """SELECT d.id
           FROM sre_project_discoveries pd
           JOIN discoveries d ON d.id=pd.discovery_id
          WHERE pd.project_id=%s AND pd.video_id=%s
            AND pd.discovery_id=%s AND d.video_id=%s AND d.source_id=%s""",
        (project,video_id,did,video_id,sid)).fetchone()
    if linked is None:
        return None, "sre_discovery_lineage_missing"
    metadata = conn.execute(
        """SELECT m.id AS metadata_id,m.video_id,m.source_id,
                  m.source_policy_version_id,m.title,m.description,
                  m.creator_name,m.published_at,m.duration_seconds,m.fetched_at,
                  m.expires_at,v.platform,v.platform_video_id,v.canonical_url,
                  v.content_kind,s.can_store_metrics,s.can_derive_scores,
                  s.can_analyze_media,s.retention_days,p.permissions,p.retention_policy
           FROM source_video_metadata m
           JOIN videos v ON v.id=m.video_id
           JOIN sources s ON s.id=m.source_id
           JOIN source_policy_versions p
             ON p.id=m.source_policy_version_id AND p.source_id=s.id
           WHERE m.id=%s AND m.video_id=%s AND m.source_id=%s
             AND m.source_policy_version_id=%s
             AND m.is_current AND m.deleted_at IS NULL
             AND s.status='enabled' AND s.can_store_metadata
             AND p.is_active
             AND p.permissions->>'can_store_metadata'='true'
             AND (m.expires_at IS NULL OR m.expires_at>now())
             AND (s.retention_days IS NULL OR
                  m.fetched_at+s.retention_days*interval '1 day'>now())
             AND (p.retention_policy->>'retention_days' IS NULL OR
                  m.fetched_at+(p.retention_policy->>'retention_days')::integer
                  * interval '1 day'>now())""",
        (obs,video_id,sid,policy)).fetchone()
    if metadata is None:
        return None, "pinned_metadata_unavailable_or_revoked"
    # SRE S3 stores the title/description from this exact observation.
    # A mutable D1 row edited in-place cannot silently change output.
    original = content["content"]
    if (not isinstance(original, dict) or
            any(original.get(k) != metadata[k] for k in ("title", "description")
                if k in original)):
        return None, "pinned_metadata_content_changed"
    result = dict(metadata)
    result["sre_content_version_id"] = cid
    result["sre_content_sha256"] = identity["content_sha256"]
    result["metadata_observation_id"] = obs
    result["discovery_id"] = did
    result["provenance_schema"] = "qdp-exact-metadata/v1"
    return result, None
