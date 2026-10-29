"""UA-MOD-06: fail-closed LIVE SRE current/approved relevance reader.

Only DATABASE-backed SRE LIVE run/item, immutable expected-pair matrix,
historical classification result, active effective human override and current
D1 rights may support a located result. Actual verified LIVE pair decisions
are NOT available in today's SRE M13 migration: the schema supports only
not_evaluated/blocked statuses. Thus this adapter cannot claim a real
positive result until SRE itself implements and deploys authenticated LIVE
pair settlement; it never synthesizes those decisions from offline tables.

The injected connection must be authorized by an upstream owner check and
used in a caller-managed repeatable-read transaction. This module does not
open DB connections, execute writes, send to a model, or qualify a publication.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import UUID

from .contracts import AnalyticsBoundaryError, canonical_hash
from .dto import EvidenceRead, EvidenceRef, InputScope, ReadState
from .lineage import CanonicalLineagePins
from .ports import CapabilityState, PortCapability, PORT_SCHEMAS

SRE_PEER_WIRE = "sre.classifications_ready/v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_HYPOTHESES = 48
REQUIRED_RELATIONS = (
    "sre_live_runs", "sre_live_items", "sre_live_expected_pairs",
    "sre_classification_runs", "sre_classification_results",
    "sre_human_labels", "sre_taxonomy_categories", "sre_taxonomy_versions",
    "sre_workspaces", "sre_readable_content_versions",
    "sre_live_verified_pair_results", "sre_live_human_reviews",
    "sre_live_result_outbox", "sre_m36_live_only_export",
    "sre_live_result_invalidation_intents",
    "qdp_sre_live_events", "qdp_sre_live_tombstones",
    "source_video_metadata", "source_policy_versions", "sources",
)
# Deliberately no JOIN to an unsigned transport event.
SCHEMA_PROBE_SQL = "SELECT " + ", ".join(
    "to_regclass('public." + name + "') AS " + name
    for name in REQUIRED_RELATIONS
)
COLUMN_PROBE_SQL = """
SELECT count(*) AS n FROM information_schema.columns
WHERE table_schema='public'
  AND ((table_name='sre_classification_runs' AND column_name='execution_mode')
    OR (table_name='sre_live_expected_pairs' AND column_name='state')
    OR (table_name='sre_content_versions' AND column_name='content_sha256')
    OR (table_name='sre_live_items' AND column_name='expected_hypothesis_keys')
    OR (table_name='sre_human_labels' AND column_name='superseded_at'))
"""
SRE_RUN_ITEM_SQL = """
SELECT lr.run_id,lr.project_id,lr.execution_mode AS live_execution_mode,
       lr.state AS live_state,lr.cancel_requested,
       lr.taxonomy_version_id,lr.taxonomy_sha256,lr.manifest_sha256,
       lr.expected_items,lr.finished_items,lr.blocked_items,
       lr.created_at AS live_created_at,
       i.id AS item_id,i.video_id,i.state AS item_state,
       i.content_version_id,i.content_sha256,i.source_id,
       i.source_policy_version_id,i.metadata_observation_id,
       i.expected_hypothesis_keys,i.contexts_sha256,i.manifest_sha256 AS item_manifest_sha256,
       c.status AS classifier_state,c.execution_mode AS classifier_execution_mode,
       c.taxonomy_version_id AS classifier_taxonomy_id,
       EXISTS(SELECT 1 FROM sre_live_result_outbox ready
              WHERE ready.project_id=lr.project_id
                AND ready.run_id=lr.run_id AND ready.item_id=i.id
                AND ready.kind='ready') AS has_m36_ready,
       t.status AS taxonomy_state,t.snapshot_checksum,
       w.active_taxonomy_id,
       cv.revision AS content_revision,cv.created_at AS content_created_at,
       cv.expires_at AS content_expires_at,
       s.status AS source_state,s.can_store_metadata,s.can_analyze_media,
       s.retention_days AS source_retention_days,
       p.is_active AS policy_active,p.reviewed_at AS policy_reviewed_at,
       p.permissions AS policy_permissions,p.retention_policy,
       m.id AS metadata_id,m.is_current AS metadata_current,
       m.deleted_at AS metadata_deleted_at,m.expires_at AS metadata_expires_at,
       m.fetched_at AS metadata_fetched_at
FROM sre_live_runs lr
JOIN sre_live_items i ON i.run_id=lr.run_id AND i.project_id=lr.project_id
LEFT JOIN sre_classification_runs c ON c.id=lr.run_id
  AND c.project_id=lr.project_id
JOIN sre_workspaces w ON w.project_id=lr.project_id
JOIN sre_taxonomy_versions t ON t.id=lr.taxonomy_version_id
  AND t.project_id=lr.project_id
JOIN sre_readable_content_versions cv ON cv.id=i.content_version_id
  AND cv.project_id=i.project_id AND cv.video_id=i.video_id
  AND cv.source_id=i.source_id
  AND cv.source_policy_version_id=i.source_policy_version_id
  AND cv.content_sha256=i.content_sha256
JOIN sources s ON s.id=i.source_id
JOIN source_policy_versions p ON p.id=i.source_policy_version_id
  AND p.source_id=i.source_id
JOIN source_video_metadata m ON m.id=i.metadata_observation_id
  AND m.video_id=i.video_id AND m.source_id=i.source_id
  AND m.source_policy_version_id=i.source_policy_version_id
WHERE lr.run_id=%s AND lr.project_id=%s AND i.video_id=%s
  AND i.content_version_id=%s
"""
SRE_RESULTS_SQL = """
SELECT pair.category_key::text AS category_key,pair.state AS pair_state,
       res.id AS result_id,res.decision AS model_decision,
       res.content_version_id AS result_content_version_id,
       res.taxonomy_version_id AS result_taxonomy_id,
       res.evidence AS result_evidence,res.stage_trace AS result_trace,
       cat.review_status AS category_review_status,
       lab.label AS human_label,lab.id AS human_label_id,
       lab.created_at AS human_created_at
FROM sre_live_expected_pairs pair
JOIN sre_live_items i ON i.id=pair.item_id
  AND i.run_id=pair.run_id AND i.project_id=pair.project_id
LEFT JOIN sre_classification_results res ON res.run_id=pair.run_id
  AND res.project_id=pair.project_id AND res.video_id=i.video_id
  AND res.category_key=pair.category_key
LEFT JOIN sre_taxonomy_categories cat
  ON cat.project_id=pair.project_id
  AND cat.taxonomy_version_id=%s AND cat.category_key=pair.category_key
LEFT JOIN sre_human_labels lab ON lab.project_id=pair.project_id
  AND lab.video_id=i.video_id AND lab.taxonomy_version_id=%s
  AND lab.category_key=pair.category_key AND lab.superseded_at IS NULL
WHERE pair.project_id=%s AND pair.run_id=%s AND pair.item_id=%s
ORDER BY pair.category_key
"""

SRE_MODERN_RESULTS_SQL = """
SELECT pair.category_key::text AS category_key,
       valid.original_model_decision AS model_decision,
       valid.human_decision AS human_decision,
       valid.review_id::text AS review_id,
       verified.result_id::text AS result_id,
       verified.decision AS verified_model_decision,
       verified.verified_chunk_sha256,verified.verified_at,
       valid.verified_chunk_sha256 AS m36_evidence_sha256,
       latest.review_id::text AS latest_review_id,
       latest.human_decision AS latest_human_decision,
       latest.reviewed_at AS human_created_at,
       cat.review_status AS category_review_status,
       event.event_id::text AS ready_event_id,
       event.event_sha256 AS event_sha256
FROM sre_live_expected_pairs pair
JOIN sre_m36_live_only_export valid
  ON valid.project_id=pair.project_id AND valid.run_id=pair.run_id
  AND valid.item_id=pair.item_id AND valid.category_key=pair.category_key
JOIN sre_live_result_outbox event
  ON event.event_id=valid.event_id AND event.project_id=valid.project_id
  AND event.run_id=valid.run_id AND event.item_id=valid.item_id
  AND event.kind='ready' AND event.state='acked'
JOIN qdp_sre_live_events q
  ON q.event_id=event.event_id AND q.project_id=event.project_id
  AND q.sre_run_id=event.run_id AND q.item_id=event.item_id
  AND q.kind='ready' AND q.event_sha256=event.event_sha256
JOIN sre_live_verified_pair_results verified
  ON verified.project_id=pair.project_id AND verified.run_id=pair.run_id
  AND verified.item_id=pair.item_id AND verified.category_key=pair.category_key
  AND verified.receipt_id=valid.receipt_id
JOIN LATERAL (
  SELECT h.review_id,h.human_decision,h.reviewed_at
  FROM sre_live_human_reviews h
  WHERE h.project_id=pair.project_id AND h.run_id=pair.run_id
    AND h.item_id=pair.item_id AND h.category_key=pair.category_key
  ORDER BY h.revision DESC LIMIT 1
) latest ON TRUE
JOIN sre_taxonomy_categories cat
  ON cat.project_id=pair.project_id
  AND cat.taxonomy_version_id=%s AND cat.category_key=pair.category_key
WHERE pair.project_id=%s AND pair.run_id=%s::uuid AND pair.item_id=%s
  AND NOT EXISTS (
    SELECT 1 FROM qdp_sre_live_tombstones tomb
    WHERE tomb.project_id=event.project_id
      AND tomb.prior_ready_event_id=event.event_id
  )
  AND NOT EXISTS (
    SELECT 1 FROM sre_live_result_invalidation_intents rev
    WHERE rev.project_id=event.project_id
      AND rev.ready_event_id=event.event_id
  )
ORDER BY pair.category_key
"""

def _effective_modern_decisions(rows: list[dict[str, Any]], keys: list[str],
                                targets: tuple[str, ...], as_of: datetime,
                                pinned_digest: str) -> tuple[str | None, str | None]:
    """Require original M30, latest M32, and M36/QDP acknowledged evidence."""
    if len(rows) != len(keys):
        return "incomplete_live_m30_m32_coverage", None
    output=[]
    for key,row in zip(keys,rows):
        if row.get("category_key")!=key:
            return "incomplete_live_m30_m32_coverage", None
        if row.get("category_review_status")!="approved":
            return "unapproved_live_category", None
        if (not _uuid(row.get("result_id")) or
            not _uuid(row.get("review_id")) or
            not _uuid(row.get("ready_event_id")) or
            not _sha(row.get("event_sha256")) or
            not _sha(row.get("verified_chunk_sha256")) or
            row.get("m36_evidence_sha256")!=row.get("verified_chunk_sha256") or
            row.get("latest_review_id")!=row.get("review_id") or
            _at(row.get("human_created_at")) is None or
            _at(row["human_created_at"])>as_of or
            _at(row.get("verified_at")) is None or
            _at(row["verified_at"])>as_of):
            return "live_source_or_human_review_superseded", None
        raw=row.get("model_decision")
        effective=row.get("human_decision")
        if (raw not in ("match","no_match","uncertain") or
            row.get("verified_model_decision")!=raw or
            row.get("latest_human_decision")!=effective or
            effective not in ("match","no_match","uncertain")):
            return "live_verdict_drift", None
        if effective=="uncertain":
            return "uncertain_live_verdict", None
        if key in targets and effective!="match":
            return "target_not_approved_relevant", None
        output.append([key,effective])
    digest=canonical_hash({"effective_decisions":output})
    if digest!=pinned_digest:
        return "sre_decision_digest_mismatch", None
    return None,digest

SRE_STALENESS_SQL = """
SELECT
 EXISTS(
   SELECT 1 FROM sre_content_versions newer
   WHERE newer.project_id=%s AND newer.video_id=%s
     AND newer.revision>%s
 ) AS newer_content,
 EXISTS(
   SELECT 1 FROM sre_live_items newer_item
   JOIN sre_live_runs newer_run
     ON newer_run.run_id=newer_item.run_id AND newer_run.project_id=newer_item.project_id
   WHERE newer_item.project_id=%s AND newer_item.video_id=%s
     AND newer_item.run_id<>%s AND newer_run.state='completed'
     AND newer_run.created_at>%s
 ) AS newer_live_run
"""


def _reject(code: str) -> None:
    raise AnalyticsBoundaryError(code, "SRE LIVE relevance was not verified: " + code)


def _uuid(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        return str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _sha(value: object) -> bool:
    return type(value) is str and bool(_SHA.fullmatch(value))


def _at(value: object) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _dictrow(cursor: Any) -> dict[str, Any] | None:
    value = cursor.fetchone()
    if value is None:
        return None
    if not isinstance(value, Mapping):
        _reject("invalid_sre_db_row")
    return dict(value)


def _db(conn: Any, sql: str, args: tuple[Any, ...] = ()) -> Any:
    try:
        return conn.execute(sql, args)
    except Exception as exc:
        raise AnalyticsBoundaryError(
            "sre_dependency_read_failed", "SRE database read failed"
        ) from exc


@dataclass(frozen=True, slots=True)
class SREApprovedRelevancePins:
    lineage: CanonicalLineagePins
    taxonomy_sha256: str
    content_sha256: str
    contexts_sha256: str
    target_category_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.lineage, CanonicalLineagePins):
            _reject("invalid_sre_lineage")
        for name in ("taxonomy_sha256", "content_sha256", "contexts_sha256"):
            if not _sha(getattr(self, name)):
                _reject("invalid_sre_digest")
        keys = self.target_category_keys
        if (type(keys) is not tuple or not 1 <= len(keys) <= MAX_HYPOTHESES
                or any(not _uuid(x) for x in keys)
                or list(keys) != sorted(set(keys))):
            _reject("invalid_sre_targets")


def _effective_decisions(rows: list[dict[str, Any]], keys: list[str],
                         targets: tuple[str, ...], as_of: datetime,
                         expected_content_id: int, expected_tax_id: int,
                         pinned_digest: str) -> tuple[str | None, str | None]:
    """(reason, digest) of all LIVE terminal pair decisions + human overrides."""
    if len(rows) != len(keys):
        return "incomplete_coverage", None
    final = []
    for key, row in zip(keys, rows):
        if row.get("category_key") != key:
            return "incomplete_coverage", None
        # Crucial current peer status: M13 can ONLY emit not_evaluated/blocked,
        # and no output from res/human may elevate a nonterminal LIVE pair.
        state = row.get("pair_state")
        if state not in ("match", "no_match", "uncertain"):
            return "unsettled_live_evidence", None
        if (row.get("category_review_status") != "approved"
                or not isinstance(row.get("result_id"), int)
                or type(row.get("result_content_version_id")) is not int
                or row["result_content_version_id"] != expected_content_id
                or row.get("result_taxonomy_id") != expected_tax_id):
            return "unapproved_or_unpinned_result", None
        raw = row.get("model_decision")
        if raw not in ("match", "no_match", "uncertain") or raw != state:
            return "result_pair_mismatch", None
        if raw == "match":
            evidence = row.get("result_evidence")
            if type(evidence) is not list or not evidence:
                return "missing_source_evidence", None
        reviewer = row.get("human_label")
        if reviewer is not None:
            if reviewer not in ("match", "no_match", "uncertain") or (
                    type(row.get("human_label_id")) is not int or
                    _at(row.get("human_created_at")) is None or
                    _at(row["human_created_at"]) > as_of):
                return "invalid_human_review", None
        effective = reviewer if reviewer is not None else raw
        if effective == "uncertain":
            return "uncertain_result", None
        if key in targets and effective != "match":
            return "not_approved_relevant", None
        # A human-reversed no_match->match also requires source-backed evidence.
        if effective == "match" and (
                type(row.get("result_evidence")) is not list
                or not row["result_evidence"]):
            return "missing_source_evidence", None
        final.append([key, effective])
    digest = canonical_hash({"effective_decisions": final})
    if digest != pinned_digest:
        return "sre_decision_digest_mismatch", None
    return None, digest


class SREApprovedRelevanceReader:
    """Injected SREReader protocol implementation; no auto peer import."""

    def __init__(self, conn: Any, pins: SREApprovedRelevancePins, *,
                 as_of: datetime):
        if not isinstance(pins, SREApprovedRelevancePins):
            _reject("invalid_sre_pins")
        stamp = _at(as_of)
        if stamp is None or as_of.utcoffset().total_seconds() != 0:
            _reject("invalid_sre_as_of")
        self._conn = conn
        self._pins = pins
        self._as_of = stamp

    def capability(self) -> PortCapability:
        if self._conn is None or not callable(getattr(self._conn, "execute", None)):
            return PortCapability("sre", CapabilityState.UNAVAILABLE,
                                  reason_code="not_connected")
        try:
            existing = _dictrow(_db(self._conn, SCHEMA_PROBE_SQL))
            if existing is None or any(
                existing.get(name) is None for name in REQUIRED_RELATIONS
            ):
                return PortCapability("sre", CapabilityState.UNAVAILABLE,
                                      reason_code="live_schema_missing")
            checked = _dictrow(_db(self._conn, COLUMN_PROBE_SQL))
            if checked is None or checked.get("n") != 5:
                return PortCapability("sre", CapabilityState.INCOMPATIBLE,
                                      reason_code="live_schema_incompatible")
        except AnalyticsBoundaryError:
            return PortCapability("sre", CapabilityState.UNAVAILABLE,
                                  reason_code="probe_failed")
        return PortCapability("sre", CapabilityState.DECLARED,
                              PORT_SCHEMAS["sre"], SRE_PEER_WIRE)

    def read_current_relevance(self, scope: InputScope) -> EvidenceRead:
        if not isinstance(scope, InputScope) or scope != self._pins.lineage.scope:
            _reject("sre_scope_mismatch")
        cap = self.capability()
        if cap.state is not CapabilityState.DECLARED:
            return EvidenceRead("sre", scope, ReadState.UNAVAILABLE,
                                reason_code=cap.reason_code)
        p = self._pins
        lineage = p.lineage
        s = lineage.scope
        current = _dictrow(_db(self._conn, SRE_RUN_ITEM_SQL, (
            lineage.sre_run_id, s.project_id, s.video_id, lineage.sre_content_version_id,
        )))
        if current is None:
            return self._missing(scope, "no_readable_live_item")
        if (
            str(current.get("run_id")) != lineage.sre_run_id
            or current.get("project_id") != s.project_id
            or current.get("video_id") != s.video_id
            or current.get("content_version_id") != lineage.sre_content_version_id
            or current.get("source_id") != s.source_id
            or current.get("source_policy_version_id") != s.source_policy_version_id
            or current.get("metadata_observation_id") != lineage.metadata_id
            or current.get("metadata_id") != lineage.metadata_id
        ):
            return self._missing(scope, "live_lineage_mismatch")
        if (
            _at(current.get("live_created_at")) is None
            or _at(current["live_created_at"]) > self._as_of
            or _at(current.get("content_created_at")) is None
            or _at(current["content_created_at"]) > self._as_of
        ):
            return self._missing(scope, "future_dated_live_record")
        if (
            current.get("live_execution_mode") != "live"
            or (current.get("classifier_execution_mode") is not None
                and current.get("classifier_execution_mode") != "live")
            or current.get("live_state") != "completed"
            or (current.get("classifier_state") is not None
                and current.get("classifier_state") != "completed")
            or current.get("item_state") != "completed"
            or current.get("cancel_requested") is not False
            or type(current.get("expected_items")) is not int
            or current["expected_items"] <= 0
            or current.get("finished_items") != current["expected_items"]
            or current.get("blocked_items") != 0
        ):
            return self._missing(scope, "no_completed_live_run")
        if (
            current.get("taxonomy_version_id") != lineage.sre_taxonomy_version_id
            or (current.get("classifier_taxonomy_id") is not None
                and current.get("classifier_taxonomy_id") != lineage.sre_taxonomy_version_id)
            or current.get("active_taxonomy_id") != lineage.sre_taxonomy_version_id
            or current.get("taxonomy_state") != "approved"
            or current.get("taxonomy_sha256") != p.taxonomy_sha256
            or current.get("snapshot_checksum") != p.taxonomy_sha256
        ):
            return self._missing(scope, "stale_or_unapproved_taxonomy")
        if (
            current.get("content_sha256") != p.content_sha256
            or current.get("contexts_sha256") != p.contexts_sha256
            or not _sha(current.get("manifest_sha256"))
            or current.get("item_manifest_sha256") != current["manifest_sha256"]
            or not isinstance(current.get("content_revision"), int)
            or current["content_revision"] <= 0
        ):
            return self._missing(scope, "content_or_context_mismatch")
        if not self._rights(current):
            return self._missing(scope, "sre_rights_expired_or_revoked")
        keys = current.get("expected_hypothesis_keys")
        if (type(keys) is not list or not 1 <= len(keys) <= MAX_HYPOTHESES
                or any(not _uuid(k) for k in keys)
                or keys != sorted(set(keys))
                or not set(p.target_category_keys).issubset(keys)):
            return self._missing(scope, "hypothesis_coverage_missing")

        stale = _dictrow(_db(self._conn, SRE_STALENESS_SQL, (
            s.project_id, s.video_id, current["content_revision"],
            s.project_id, s.video_id, lineage.sre_run_id,
            current["live_created_at"],
        )))
        if stale is None or stale.get("newer_content") is not False or (
                stale.get("newer_live_run") is not False):
            return self._missing(scope, "superseded_live_relevance")

        try:
            modern=(current.get("classifier_state") is None
                    or current.get("has_m36_ready") is True)
            query=SRE_MODERN_RESULTS_SQL if modern else SRE_RESULTS_SQL
            arguments=(
                (lineage.sre_taxonomy_version_id,s.project_id,
                 lineage.sre_run_id,current["item_id"])
                if modern else
                (lineage.sre_taxonomy_version_id,lineage.sre_taxonomy_version_id,
                 s.project_id,lineage.sre_run_id,current["item_id"])
            )
            rows = _db(self._conn, query, arguments).fetchall()
            if type(rows) is not list or any(not isinstance(r, Mapping) for r in rows):
                _reject("invalid_sre_db_row")
            parsed = [dict(row) for row in rows]
        except AnalyticsBoundaryError:
            raise
        except Exception as exc:
            raise AnalyticsBoundaryError("sre_dependency_read_failed",
                                         "SRE result read failed") from exc
        if modern:
            failure,effective_hash=_effective_modern_decisions(
                parsed,keys,p.target_category_keys,self._as_of,
                lineage.sre_decision_sha256,
            )
        else:
            failure,effective_hash=_effective_decisions(
                parsed, keys, p.target_category_keys, self._as_of,
                lineage.sre_content_version_id, lineage.sre_taxonomy_version_id,
                lineage.sre_decision_sha256,
            )
        if failure:
            return self._missing(scope, failure)
        # A peer-owned LIVE settlement receipt is still needed in production:
        # pair.state MUST be terminal (currently impossible under M13).
        payload_digest = canonical_hash({
            "run_id": lineage.sre_run_id,
            "item_id": current["item_id"],
            "content_sha256": p.content_sha256,
            "taxonomy_sha256": p.taxonomy_sha256,
            "contexts_sha256": p.contexts_sha256,
            "decisions_sha256": effective_hash,
            "target_category_keys": list(p.target_category_keys),
            "as_of": self._as_of.isoformat(),
        })
        ref = EvidenceRef(
            scope=scope, port="sre",
            record_key="sre:" + lineage.sre_run_id + ":" + str(s.video_id),
            producer_schema=SRE_PEER_WIRE, producer_version="live-v1",
            payload_sha256=payload_digest,
        )
        return EvidenceRead("sre", scope, ReadState.LOCATED, reference=ref)

    def _rights(self, row: Mapping[str, Any]) -> bool:
        p = self._pins
        now = self._as_of
        policy = row.get("policy_permissions")
        retention = row.get("retention_policy")
        if not isinstance(policy, dict) or not isinstance(retention, dict):
            return False
        if (
            row.get("source_state") != "enabled"
            or row.get("can_store_metadata") is not True
            or row.get("policy_active") is not True
            or _at(row.get("policy_reviewed_at")) is None
            or policy.get("can_store_metadata") is not True
            or policy.get("can_semantic_classify") is not True
            or row.get("metadata_current") is not True
            or row.get("metadata_deleted_at") is not None
        ):
            return False
        if (row.get("can_analyze_media") is not True
                or policy.get("can_analyze_media") is not True):
            # UAE requires actual A/B media analysis for all supported kinds.
            return False
        base_time = _at(row.get("content_created_at"))
        metadata_time = _at(row.get("metadata_fetched_at"))
        if (base_time is None or metadata_time is None
                or base_time > now or metadata_time > now
                or _at(row.get("policy_reviewed_at")) > now):
            return False
        for field in ("content_expires_at", "metadata_expires_at"):
            raw = row.get(field)
            if raw is not None and (_at(raw) is None or _at(raw) <= now):
                return False
        for days, basis in (
            (row.get("source_retention_days"), base_time),
            (retention.get("retention_days"), base_time),
        ):
            if days is not None and (
                type(days) is not int or not 1 <= days <= 36500
                or basis + timedelta(days=days) <= now
            ):
                return False
        return True

    @staticmethod
    def _missing(scope: InputScope, reason: str) -> EvidenceRead:
        return EvidenceRead("sre", scope, ReadState.MISSING, reason_code=reason)


__all__ = (
    "SRE_PEER_WIRE", "SREApprovedRelevancePins",
    "SREApprovedRelevanceReader", "REQUIRED_RELATIONS",
    "SCHEMA_PROBE_SQL", "COLUMN_PROBE_SQL", "SRE_RUN_ITEM_SQL",
    "SRE_RESULTS_SQL", "SRE_MODERN_RESULTS_SQL", "SRE_STALENESS_SQL",
)
