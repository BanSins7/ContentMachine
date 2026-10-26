"""GB-MOD-04: verify the real QDP v1 outbox/preparation from authoritative SQL.

No import of intelligence.qualified_data: it belongs to a separate branch.
The QDP branch currently declares the v1 pointer but a live B dispatcher
handshake is not assumed. We fail closed if any peer table/field is missing.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any
from uuid import UUID
import re

from ..contracts import ContractError, canonical_hash, content_type_for_kind
from ..errors import DependencyUnavailable

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PROJECT = re.compile(r"[0-9a-f]{32}\Z")
_FIELDS = frozenset(("event_id", "project_id", "qdp_run_id", "video_id",
                     "analysis_group", "input_sha256", "schema_version"))
_REQUIRED_DB_TABLES = (
    "qdp_outbox", "qdp_outbox_deliveries", "qdp_runs", "qdp_preparations",
    "qdp_enrichments", "qdp_invalidations", "sources",
    "source_policy_versions", "videos", "media_assets",
)
HANDOFF_VERSION = "qdp-handoff/v1"
QDP_EVENT_VERSION = "qdp-event/v1"


def _uuid(text: object, label: str) -> str:
    if type(text) is not str:
        raise ContractError("invalid_" + label, "Expected canonical UUID")
    try:
        parsed = UUID(text)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ContractError("invalid_" + label, "Expected canonical UUID") from exc
    if str(parsed) != text:
        raise ContractError("invalid_" + label, "UUID must be canonical lower-case")
    return text


def _sha(value: object, label: str) -> str:
    if type(value) is not str or not _SHA.fullmatch(value):
        raise ContractError("invalid_" + label, "Expected 64 lowercase SHA-256 hex characters")
    return value


@dataclass(frozen=True, slots=True)
class QDPHandoff:
    event_id: str
    project_id: str
    qdp_run_id: str
    video_id: int
    analysis_group: str
    input_sha256: str
    schema_version: str

    @classmethod
    def parse(cls, payload: object) -> "QDPHandoff":
        if type(payload) is not dict or set(payload) != _FIELDS:
            raise ContractError("invalid_qdp_payload", "Expected exact QDP v1 pointer fields")
        _uuid(payload["event_id"], "event_id")
        _uuid(payload["qdp_run_id"], "qdp_run_id")
        if type(payload["project_id"]) is not str or not _PROJECT.fullmatch(payload["project_id"]):
            raise ContractError("invalid_project_id", "Expected canonical project ID")
        if type(payload["video_id"]) is not int or payload["video_id"] < 1:
            raise ContractError("invalid_video_id", "Expected D1 canonical video ID")
        if payload["analysis_group"] != "B":
            raise ContractError("invalid_group", "Group B accepts only group B events")
        _sha(payload["input_sha256"], "input_sha256")
        if payload["schema_version"] != HANDOFF_VERSION:
            raise ContractError("unsupported_qdp_schema", "Unsupported QDP handoff version")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class VerifiedPreparation:
    """Validated *current QDP intake*, not Group A/visual/LLM readiness."""
    project_id: str
    event_id: str
    qdp_run_id: str
    video_id: int
    source_id: str
    content_kind: str
    content_type: str
    input_sha256: str
    event_sha256: str
    source_policy_version_id: int
    media_asset_id: int
    media_sha256: str


def _one_mapping(cursor):
    row = cursor.fetchone()
    if row is None:
        return None
    if isinstance(row, Mapping):
        return dict(row)
    return dict(zip((d.name for d in cursor.description), row))


def require_qdp_schema(conn) -> None:
    """No peer schema => unavailable, never a generated fallback."""
    rows = conn.execute(
        "SELECT name FROM unnest(%s::text[]) AS name "
        "WHERE to_regclass('public.' || name) IS NULL",
        (list(_REQUIRED_DB_TABLES),)
    ).fetchall()
    if rows:
        raise DependencyUnavailable("qdp", "qdp_tables_missing")


def read_current_preparation(conn, handoff: QDPHandoff, announced_event_sha256: str) -> VerifiedPreparation:
    """Authoritative QDP outbox + B preparation + D1 permissions read.

    Verifies exact project/video/run/source/policy/media lineage. This verifies
    database snapshots, NOT the bytes of a media file, nor completed A streams.
    """
    _sha(announced_event_sha256, "event_sha256")
    require_qdp_schema(conn)
    cursor = conn.execute(
        """SELECT o.payload AS event_payload, o.payload_sha256 AS event_sha,
                  o.event_type, o.schema_version AS event_version,
                  d.state AS delivery_state,
                  q.status AS qdp_state, q.cancel_requested,
                  p.state AS preparation_state, p.input_sha256, p.inputs,
                  e.source_id, e.source_policy_version_id, e.media_asset_id,
                  e.metadata, e.metadata_id, e.expires_at AS enrichment_expires_at,
                  s.status AS source_state, s.can_analyze_media,
                  sp.is_active AS policy_active, sp.permissions AS policy_permissions,
                  v.content_kind, m.sha256 AS media_sha256,
                  m.video_id AS media_video_id, m.source_id AS media_source_id,
                  m.permission AS media_permission, m.expires_at AS media_expires_at
           FROM qdp_outbox o
           JOIN qdp_outbox_deliveries d ON d.event_id=o.event_id
                AND d.consumer='group_b'
           JOIN qdp_runs q ON q.id=o.run_id AND q.project_id=o.project_id
           JOIN qdp_preparations p ON p.run_id=o.run_id AND p.project_id=o.project_id
                AND p.video_id=o.video_id AND p.analysis_group='B'
           JOIN qdp_enrichments e ON e.run_id=p.run_id AND e.project_id=p.project_id
                AND e.video_id=p.video_id
           JOIN sources s ON s.id=e.source_id
           JOIN source_policy_versions sp ON sp.id=e.source_policy_version_id
                AND sp.source_id=e.source_id
           JOIN videos v ON v.id=o.video_id
           LEFT JOIN media_assets m ON m.id=e.media_asset_id
           WHERE o.event_id=%s::uuid AND o.project_id=%s
             AND o.run_id=%s::uuid AND o.video_id=%s
             AND o.analysis_group='B'
             AND NOT EXISTS (
                 SELECT 1 FROM qdp_invalidations i
                  WHERE i.project_id=o.project_id AND i.run_id=o.run_id
                    AND (i.video_id IS NULL OR i.video_id=o.video_id)
                    AND (i.analysis_group IS NULL OR i.analysis_group='B')
             )""",
        (handoff.event_id, handoff.project_id, handoff.qdp_run_id, handoff.video_id)
    )
    row = _one_mapping(cursor)
    if row is None:
        raise DependencyUnavailable("qdp", "event_or_preparation_unavailable")
    if row["event_type"] != "preparation_ready" or row["event_version"] != QDP_EVENT_VERSION:
        raise DependencyUnavailable("qdp", "unsupported_event_kind")
    if row["delivery_state"] not in ("pending", "leased", "acknowledged"):
        raise DependencyUnavailable("qdp", "delivery_not_current")
    if row["qdp_state"] not in ("running", "completed", "partial") or row["cancel_requested"] is not False:
        raise DependencyUnavailable("qdp", "run_not_current")
    if row["preparation_state"] != "ready":
        raise DependencyUnavailable("qdp", "preparation_blocked")
    if type(row["event_payload"]) is not dict or row["event_payload"] != {
        name: getattr(handoff, name) for name in _FIELDS
    }:
        raise DependencyUnavailable("qdp", "producer_payload_mismatch")
    if canonical_hash(row["event_payload"]) != row["event_sha"] or row["event_sha"] != announced_event_sha256:
        raise DependencyUnavailable("qdp", "event_sha_mismatch")
    if row["input_sha256"] != handoff.input_sha256 or canonical_hash(row["inputs"]) != handoff.input_sha256:
        raise DependencyUnavailable("qdp", "preparation_sha_mismatch")
    if row["source_state"] != "enabled" or row["can_analyze_media"] is not True:
        raise DependencyUnavailable("qdp", "media_rights_revoked")
    if row["policy_active"] is not True or not isinstance(row["policy_permissions"], dict) or (
        row["policy_permissions"].get("can_analyze_media") is not True
    ):
        raise DependencyUnavailable("qdp", "policy_media_rights_revoked")
    if row["enrichment_expires_at"] is not None:
        # SQL clock is authoritative, rather than local worker clock.
        if conn.execute("SELECT %s <= now()", (row["enrichment_expires_at"],)).fetchone()[0]:
            raise DependencyUnavailable("qdp", "enrichment_expired")
    kind = row["content_kind"]
    try:
        content_type = content_type_for_kind(kind)
    except ContractError as exc:
        raise DependencyUnavailable("qdp", "unsupported_content_kind") from exc
    metadata = row["metadata"]
    if not isinstance(metadata, dict) or metadata.get("content_kind") != kind:
        raise DependencyUnavailable("qdp", "metadata_kind_mismatch")
    inputs = row["inputs"]
    if not isinstance(inputs, dict) or inputs.get("result_schema") != "qdp-preparation/v1":
        raise DependencyUnavailable("qdp", "unsupported_preparation_schema")
    source_id = row["source_id"]
    policy_id = row["source_policy_version_id"]
    media_id = row["media_asset_id"]
    media_sha = row["media_sha256"]
    if (type(source_id) is not str or not source_id or type(policy_id) is not int or
        type(media_id) is not int or media_id <= 0 or type(media_sha) is not str or
        not _SHA.fullmatch(media_sha)):
        raise DependencyUnavailable("qdp", "media_or_source_unavailable")
    if not all((
        inputs.get("enrichment_source_id") == source_id,
        inputs.get("source_policy_version_id") == policy_id,
        inputs.get("metadata_id") == row["metadata_id"],
        inputs.get("media_asset_id") == media_id,
        inputs.get("media_sha256") == media_sha,
        row["media_source_id"] == source_id,
        row["media_video_id"] == handoff.video_id,
        row["media_permission"] not in (None, "", "metadata_only"),
    )):
        raise DependencyUnavailable("qdp", "source_media_mapping_mismatch")
    if row["media_expires_at"] is not None:
        if conn.execute("SELECT %s <= now()", (row["media_expires_at"],)).fetchone()[0]:
            raise DependencyUnavailable("qdp", "media_expired")
    # A carousel needs a per-slide evidence manifest, not a lone video asset.
    if content_type == "carousel":
        raise DependencyUnavailable("qdp", "carousel_media_manifest_unavailable")
    if inputs.get("media_kind") != "video":
        raise DependencyUnavailable("qdp", "media_kind_mismatch")
    return VerifiedPreparation(
        handoff.project_id, handoff.event_id, handoff.qdp_run_id,
        handoff.video_id, source_id, kind, content_type,
        handoff.input_sha256, announced_event_sha256, policy_id, media_id, media_sha
    )
