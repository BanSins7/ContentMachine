"""UA-MOD-07: read-only, fail-closed Qualified Set and A/B/C preparation reader.

QDP qualified membership is not a consumer ACK, item ready flag, QDP event,
a media score, or SRE prediction inferred from request JSON. The repository
caller injects an authorized dict-row PostgreSQL connection and transaction.
No QDP tables on standalone main => UNAVAILABLE, never mock production data.

Exact run SHA and each A/B/C preparation SHA are DIFFERENT digests.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID

from .contracts import AnalyticsBoundaryError, canonical_hash
from .dto import EvidenceRead, EvidenceRef, InputScope, ReadState
from .ports import CapabilityState, PortCapability, PORT_SCHEMAS

QDP_PEER_WIRE = "qdp-handoff/v1"
_GROUPS = ("A", "B", "C")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
REQUIRED_RELATIONS = (
    "qdp_runs", "qdp_run_manifests", "qdp_items", "qdp_item_lineage",
    "qdp_enrichments", "qdp_preparations", "qdp_invalidations",
    "qdp_current_base_enrichments", "qdp_group_current_preparations",
    "sre_classification_runs", "sre_classification_results",
    "sre_human_labels", "sre_content_versions",
)
PROBE_SQL = "SELECT " + ", ".join(
    f"to_regclass('public.{name}') AS {name}" for name in REQUIRED_RELATIONS
)
RUN_SQL = """
SELECT r.id AS run_id,r.project_id,r.sre_run_id,
       r.taxonomy_version_id,r.input_sha256,r.status AS run_status,
       r.cancel_requested,r.total_qualified,r.total_review,r.total_excluded,
       m.taxonomy_sha256,m.manifest_sha256,m.selected_sha256,
       m.expected_pairs,m.expected_videos,m.review_watermark,
       m.manifest,
       i.project_id AS item_project_id,i.video_id,i.status AS item_status,
       i.sre_category_keys,i.sre_result_ids,
       s.execution_mode AS sre_execution_mode,s.status AS sre_status,
       s.taxonomy_version_id AS sre_taxonomy_version_id,
       t.status AS taxonomy_status,w.active_taxonomy_id,
       e.source_id,e.source_policy_version_id,e.metadata_id,
       e.media_asset_id,e.metric_snapshot_ids,e.expires_at AS enrichment_expires_at,
       obs.is_current AS metadata_current,obs.deleted_at AS metadata_deleted_at,
       obs.expires_at AS metadata_expires_at,obs.video_id AS metadata_video_id,
       obs.source_id AS metadata_source_id,
       obs.source_policy_version_id AS metadata_policy_id,
       v.content_kind AS content_kind
FROM qdp_runs r
JOIN qdp_run_manifests m ON m.run_id=r.id AND m.project_id=r.project_id
JOIN qdp_items i ON i.run_id=r.id AND i.project_id=r.project_id
JOIN sre_classification_runs s
 ON s.id=r.sre_run_id AND s.project_id=r.project_id
JOIN sre_taxonomy_versions t
 ON t.id=r.taxonomy_version_id AND t.project_id=r.project_id
JOIN sre_workspaces w ON w.project_id=r.project_id
JOIN qdp_current_base_enrichments e
 ON e.run_id=r.id AND e.project_id=r.project_id AND e.video_id=i.video_id
JOIN source_video_metadata obs
 ON obs.id=e.metadata_id AND obs.video_id=i.video_id
 AND obs.source_id=e.source_id AND obs.source_policy_version_id=e.source_policy_version_id
JOIN videos v ON v.id=i.video_id
WHERE r.project_id=%s AND r.id=%s::uuid AND i.video_id=%s
"""
GROUP_SQL = """
SELECT p.analysis_group,p.run_id,p.project_id,p.video_id,p.state,
       p.reason_code,p.input_sha256,p.inputs,p.source_id,
       p.source_policy_version_id,p.metadata_id,p.media_asset_id
FROM qdp_group_current_preparations p
WHERE p.project_id=%s AND p.run_id=%s::uuid AND p.video_id=%s
ORDER BY p.analysis_group
"""
LINEAGE_SQL = """
SELECT l.category_key::text AS category_key,l.sre_result_id,
       l.content_version_id,l.content_sha256,l.evidence_sha256,
       l.model_decision,l.effective_decision,l.human_label_id,
       r.run_id AS original_sre_run,r.project_id AS original_project,
       r.video_id AS result_video,r.category_key::text AS result_category,
       r.content_version_id AS result_content_version,
       r.decision AS result_model_decision,
       h.id AS current_label_id,h.label AS current_human_label
FROM qdp_item_lineage l
JOIN qdp_runs qr ON qr.id=l.run_id AND qr.project_id=l.project_id
JOIN sre_classification_results r ON r.id=l.sre_result_id
LEFT JOIN sre_human_labels h ON h.project_id=l.project_id
  AND h.video_id=l.video_id AND h.taxonomy_version_id=qr.taxonomy_version_id
  AND h.category_key=l.category_key AND h.superseded_at IS NULL
WHERE l.project_id=%s AND l.run_id=%s::uuid AND l.video_id=%s
ORDER BY l.category_key
"""
INVALIDATION_SQL = """
SELECT EXISTS(
 SELECT 1 FROM qdp_invalidations x
 WHERE x.project_id=%s AND x.run_id=%s::uuid
 AND (x.video_id IS NULL OR x.video_id=%s)
) AS is_invalidated
"""


def _error(code: str) -> None:
    raise AnalyticsBoundaryError(code, "QDP input unavailable or unverified: " + code)


def _sha(value: object) -> bool:
    return type(value) is str and bool(_SHA.fullmatch(value))


def _uuid(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        return str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _positive(value: object) -> bool:
    return type(value) is int and value > 0


def _utc(value: object) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _qdp_hash(obj: object) -> str:
    """Exact QDP contracts.sha256 JSON format, without accepting Python objects."""
    try:
        b = json.dumps(obj, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise AnalyticsBoundaryError("qdp_payload_invalid", "JSON facts required") from exc
    return hashlib.sha256(b).hexdigest()


def _db(conn: Any, sql: str, args: tuple = ()) -> Any:
    try:
        return conn.execute(sql, args)
    except Exception as exc:
        raise AnalyticsBoundaryError("qdp_read_failed", "QDP database read failed") from exc


def _one(conn: Any, sql: str, args: tuple = ()) -> dict | None:
    row = _db(conn, sql, args).fetchone()
    if row is None:
        return None
    if not isinstance(row, Mapping):
        _error("qdp_db_row_invalid")
    return dict(row)


def _many(conn: Any, sql: str, args: tuple) -> list[dict]:
    try:
        rows = _db(conn, sql, args).fetchall()
    except AnalyticsBoundaryError:
        raise
    except Exception as exc:
        raise AnalyticsBoundaryError("qdp_read_failed", "QDP database read failed") from exc
    if type(rows) is not list or any(not isinstance(row, Mapping) for row in rows):
        _error("qdp_db_row_invalid")
    return [dict(row) for row in rows]


@dataclass(frozen=True, slots=True)
class QDPQualifiedPins:
    """The run/input scope and all THREE independent A/B/C preparation digests."""
    scope: InputScope
    sre_run_id: str
    sre_taxonomy_version_id: int
    taxonomy_sha256: str
    qdp_manifest_sha256: str
    qdp_selected_sha256: str
    sre_content_version_id: int
    sre_content_sha256: str
    metadata_id: int
    preparation_shas: tuple[str, str, str]
    approved_category_keys: tuple[str, ...]
    sre_result_ids: tuple[int, ...]
    group_c_snapshot_ids: tuple[int, int]
    group_c_config_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.scope, InputScope):
            _error("qdp_scope_invalid")
        if not _uuid(self.sre_run_id):
            _error("qdp_sre_run_id_invalid")
        for n in ("sre_taxonomy_version_id", "sre_content_version_id",
                  "metadata_id", "group_c_config_id"):
            if not _positive(getattr(self, n)):
                _error("qdp_identifier_invalid")
        for n in ("taxonomy_sha256", "qdp_manifest_sha256",
                  "qdp_selected_sha256", "sre_content_sha256"):
            if not _sha(getattr(self, n)):
                _error("qdp_digest_invalid")
        if type(self.preparation_shas) is not tuple or len(self.preparation_shas) != 3 or any(
            not _sha(v) for v in self.preparation_shas
        ):
            _error("qdp_preparation_hashes_invalid")
        keys = self.approved_category_keys
        if (type(keys) is not tuple or not keys or len(keys) > 48
                or any(not _uuid(k) for k in keys)
                or tuple(sorted(set(keys))) != keys):
            _error("qdp_category_keys_invalid")
        ids = self.sre_result_ids
        if (type(ids) is not tuple or len(ids) != len(keys)
                or any(not _positive(v) for v in ids)
                or tuple(sorted(set(ids))) != ids):
            _error("qdp_result_ids_invalid")
        pair = self.group_c_snapshot_ids
        if (type(pair) is not tuple or len(pair) != 2
                or any(not _positive(v) for v in pair)
                or pair[0] == pair[1]):
            _error("qdp_score_snapshot_pins_invalid")

    @property
    def binding_sha256(self) -> str:
        return canonical_hash({
            "scope": self.scope.scope_sha256,
            "sre_run": self.sre_run_id,
            "sre_taxonomy": self.sre_taxonomy_version_id,
            "taxonomy_digest": self.taxonomy_sha256,
            "run_manifest": self.qdp_manifest_sha256,
            "selection": self.qdp_selected_sha256,
            "sre_content_version_id": self.sre_content_version_id,
            "sre_content_sha256": self.sre_content_sha256,
            "metadata_id": self.metadata_id,
            "A_B_C_input_sha256": list(self.preparation_shas),
            "approved_categories": list(self.approved_category_keys),
            "sre_result_ids": list(self.sre_result_ids),
            "metric_pair_ids": list(self.group_c_snapshot_ids),
            "score_config_id": self.group_c_config_id,
        })


class QDPQualifiedSetReader:
    """QDPReader protocol, no peer import, writes, or synthetic fallback."""

    def __init__(self, conn: Any, pins: QDPQualifiedPins, *, as_of: datetime):
        if not isinstance(pins, QDPQualifiedPins):
            _error("qdp_pins_invalid")
        now = _utc(as_of)
        if now is None or as_of.utcoffset().total_seconds() != 0:
            _error("qdp_as_of_invalid")
        self._conn = conn
        self._pins = pins
        self._as_of = now

    def capability(self) -> PortCapability:
        if self._conn is None or not callable(getattr(self._conn, "execute", None)):
            return PortCapability("qdp", CapabilityState.UNAVAILABLE,
                                  reason_code="not_connected")
        try:
            observed = _one(self._conn, PROBE_SQL)
        except AnalyticsBoundaryError:
            return PortCapability("qdp", CapabilityState.UNAVAILABLE,
                                  reason_code="probe_failed")
        if observed is None or any(observed.get(n) is None for n in REQUIRED_RELATIONS):
            return PortCapability("qdp", CapabilityState.UNAVAILABLE,
                                  reason_code="qdp_schema_missing")
        return PortCapability("qdp", CapabilityState.DECLARED,
                              PORT_SCHEMAS["qdp"], QDP_PEER_WIRE)

    @staticmethod
    def _missing(scope: InputScope, code: str) -> EvidenceRead:
        return EvidenceRead("qdp", scope, ReadState.MISSING, reason_code=code)

    def read_qualified_membership(self, scope: InputScope) -> EvidenceRead:
        p = self._pins
        if not isinstance(scope, InputScope) or scope != p.scope:
            _error("qdp_scope_mismatch")
        capability = self.capability()
        if capability.state is not CapabilityState.DECLARED:
            return EvidenceRead("qdp", scope, ReadState.UNAVAILABLE,
                                reason_code=capability.reason_code)
        s = scope
        run = _one(self._conn, RUN_SQL, (s.project_id, s.qdp_run_id, s.video_id))
        if run is None:
            return self._missing(s, "qdp_qualified_item_not_found")
        if (str(run.get("run_id")) != s.qdp_run_id or
                run.get("project_id") != s.project_id or
                run.get("item_project_id") != s.project_id or
                run.get("video_id") != s.video_id or
                run.get("source_id") != s.source_id or
                run.get("source_policy_version_id") != s.source_policy_version_id or
                run.get("content_kind") != s.content_kind or
                run.get("metadata_id") != p.metadata_id or
                run.get("metadata_video_id") != s.video_id or
                run.get("metadata_source_id") != s.source_id or
                run.get("metadata_policy_id") != s.source_policy_version_id):
            return self._missing(s, "qdp_source_or_project_mismatch")
        if (run.get("run_status") != "completed" or
                run.get("cancel_requested") is not False or
                run.get("item_status") != "ready" or
                not _positive(run.get("total_qualified")) or
                str(run.get("sre_run_id")) != p.sre_run_id or
                run.get("sre_execution_mode") != "live" or
                run.get("sre_status") != "completed" or
                run.get("sre_taxonomy_version_id") != p.sre_taxonomy_version_id or
                run.get("taxonomy_version_id") != p.sre_taxonomy_version_id or
                run.get("taxonomy_status") != "approved" or
                run.get("active_taxonomy_id") != p.sre_taxonomy_version_id):
            return self._missing(s, "qdp_not_qualified_or_sre_not_live")
        if (run.get("input_sha256") != s.qdp_input_sha256 or
                run.get("taxonomy_sha256") != p.taxonomy_sha256 or
                run.get("selected_sha256") != p.qdp_selected_sha256 or
                run.get("manifest_sha256") != p.qdp_manifest_sha256):
            return self._missing(s, "qdp_manifest_or_run_sha_mismatch")
        manifest = run.get("manifest")
        if type(manifest) is not dict or _qdp_hash(manifest) != p.qdp_manifest_sha256:
            return self._missing(s, "qdp_manifest_tampered")
        if (manifest.get("schema_version") != "qdp-run-manifest/v1" or
                manifest.get("project_id") != s.project_id or
                manifest.get("sre_run_id") != p.sre_run_id or
                manifest.get("taxonomy_version_id") != p.sre_taxonomy_version_id or
                manifest.get("taxonomy_sha256") != p.taxonomy_sha256 or
                manifest.get("selection_sha256") != p.qdp_selected_sha256 or
                manifest.get("expected_videos") != run.get("expected_videos") or
                manifest.get("expected_pairs") != run.get("expected_pairs")):
            return self._missing(s, "qdp_manifest_inconsistent")
        pinned_video = manifest.get("by_video")
        if type(pinned_video) is not dict:
            return self._missing(s, "qdp_manifest_video_missing")
        video = pinned_video.get(str(s.video_id))
        if (type(video) is not dict or video.get("status") != "qualified" or
                video.get("content_version_id") != p.sre_content_version_id or
                video.get("content_sha256") != p.sre_content_sha256 or
                video.get("category_keys") != list(p.approved_category_keys) or
                video.get("result_ids") != list(p.sre_result_ids) or
                video.get("source_ids") != [s.source_id] or
                video.get("source_policy_version_ids") !=
                [s.source_policy_version_id] or
                video.get("metadata_observation_ids") != [p.metadata_id]):
            return self._missing(s, "qdp_qualified_video_pin_mismatch")
        categories = run.get("sre_category_keys")
        ids = run.get("sre_result_ids")
        if (categories != list(p.approved_category_keys) or
                ids != list(p.sre_result_ids) or
                not run.get("metadata_current") is True or
                run.get("metadata_deleted_at") is not None):
            return self._missing(s, "qdp_item_membership_mismatch")
        for k in ("metadata_expires_at", "enrichment_expires_at"):
            date = run.get(k)
            if date is not None and (_utc(date) is None or _utc(date) <= self._as_of):
                return self._missing(s, "qdp_source_expired")
        invalidated = _one(self._conn, INVALIDATION_SQL,
                           (s.project_id, s.qdp_run_id, s.video_id))
        if invalidated is None or invalidated.get("is_invalidated") is not False:
            return self._missing(s, "qdp_input_invalidated")
        groups = _many(self._conn, GROUP_SQL, (s.project_id, s.qdp_run_id, s.video_id))
        if len(groups) != 3 or [x.get("analysis_group") for x in groups] != list(_GROUPS):
            return self._missing(s, "qdp_current_abc_preparations_missing")
        for idx, row in enumerate(groups):
            group = _GROUPS[idx]
            facts = row.get("inputs")
            if (row.get("run_id") is None or
                    str(row["run_id"]) != s.qdp_run_id or
                    row.get("project_id") != s.project_id or
                    row.get("video_id") != s.video_id or
                    row.get("source_id") != s.source_id or
                    row.get("source_policy_version_id") != s.source_policy_version_id or
                    row.get("metadata_id") != p.metadata_id or
                    row.get("state") != "ready" or row.get("reason_code") is not None or
                    type(facts) is not dict or
                    not _sha(row.get("input_sha256")) or
                    _qdp_hash(facts) != row.get("input_sha256") or
                    row.get("input_sha256") != p.preparation_shas[idx]):
                return self._missing(s, "qdp_group_input_mismatch")
            if (facts.get("result_schema") != "qdp-preparation/v2" or
                    facts.get("group") != group or
                    facts.get("enrichment_source_id") != s.source_id or
                    facts.get("source_policy_version_id") != s.source_policy_version_id or
                    facts.get("metadata_id") != p.metadata_id or
                    facts.get("sre_content_version_id") != p.sre_content_version_id or
                    facts.get("sre_content_sha256") != p.sre_content_sha256 or
                    not _positive(facts.get("sre_discovery_id"))):
                return self._missing(s, "qdp_preparation_lineage_mismatch")
            if group in ("A", "B"):
                if (not _positive(facts.get("media_asset_id")) or
                        row.get("media_asset_id") != facts.get("media_asset_id") or
                        not _sha(facts.get("media_sha256")) or
                        facts.get("media_kind") != "video" or
                        not _positive(facts.get("media_size_bytes"))):
                    return self._missing(s, "qdp_media_unverified")
            else:
                if (facts.get("metric_snapshot_ids") != list(p.group_c_snapshot_ids) or
                        facts.get("score_config_id") != p.group_c_config_id or
                        not _sha(facts.get("metric_history_sha256")) or
                        type(facts.get("metric_definition")) is not dict or
                        set(facts["metric_definition"]) != {"name","version"} or
                        not all(type(v) is str and v for v in facts["metric_definition"].values())):
                    return self._missing(s, "qdp_metric_history_unverified")
        lineage = _many(self._conn, LINEAGE_SQL, (s.project_id, s.qdp_run_id, s.video_id))
        if not lineage or len(lineage) > 48:
            return self._missing(s, "qdp_sre_lineage_missing")
        positives: list[tuple[str, int]] = []
        seen: set[str] = set()
        for row in lineage:
            key = row.get("category_key")
            if not _uuid(key) or key in seen:
                return self._missing(s, "qdp_sre_lineage_duplicate")
            seen.add(key)
            if (str(row.get("original_sre_run")) != p.sre_run_id or
                    row.get("original_project") != s.project_id or
                    row.get("result_video") != s.video_id or
                    row.get("result_category") != key or
                    row.get("result_content_version") != p.sre_content_version_id or
                    row.get("content_version_id") != p.sre_content_version_id or
                    row.get("content_sha256") != p.sre_content_sha256 or
                    not _sha(row.get("evidence_sha256")) or
                    not _positive(row.get("sre_result_id")) or
                    row.get("model_decision") != row.get("result_model_decision")):
                return self._missing(s, "qdp_sre_lineage_mismatch")
            effective = row.get("effective_decision")
            if effective not in ("match","no_match","uncertain","not_evaluated"):
                return self._missing(s, "qdp_sre_lineage_unknown")
            # Current human label may have changed AFTER this QDP snapshot.
            h_id = row.get("current_label_id")
            if h_id is None:
                if row.get("human_label_id") is not None:
                    return self._missing(s, "qdp_human_review_superseded")
                current_effective = row.get("model_decision")
            else:
                if row.get("human_label_id") != h_id:
                    return self._missing(s, "qdp_human_review_superseded")
                current_effective = row.get("current_human_label")
            if current_effective != effective:
                return self._missing(s, "qdp_human_review_superseded")
            if effective == "match":
                positives.append((key,row["sre_result_id"]))
        if (tuple(k for k,_ in positives) != p.approved_category_keys or
                tuple(sorted(v for _,v in positives)) != p.sre_result_ids):
            return self._missing(s, "qdp_qualified_set_changed")
        digest = canonical_hash({
            "version": "uae-qdp-membership/v1",
            "pins": p.binding_sha256,
            "scope": s.scope_sha256,
            "manifest_sha256": p.qdp_manifest_sha256,
            "preparations": list(p.preparation_shas),
            "lineage_rows": [(r["category_key"], r["sre_result_id"], r["effective_decision"],
                             r.get("human_label_id"), r["evidence_sha256"]) for r in lineage],
        })
        return EvidenceRead("qdp", s, ReadState.LOCATED, reference=EvidenceRef(
            scope=s, port="qdp",
            record_key="qdp:" + s.qdp_run_id + ":" + str(s.video_id),
            producer_schema=QDP_PEER_WIRE, producer_version="qualified-v1",
            payload_sha256=digest,
        ))


__all__ = (
    "QDP_PEER_WIRE", "QDPQualifiedPins", "QDPQualifiedSetReader",
    "REQUIRED_RELATIONS", "PROBE_SQL", "RUN_SQL", "GROUP_SQL",
    "LINEAGE_SQL", "INVALIDATION_SQL",
)
