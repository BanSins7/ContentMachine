"""GC-MOD-04. Durable, fail-closed QDP -> Group C C-preparation consumer.

Accepts *actual* QDP outbox events and CURRENT QDP C preparations only.
This adapter intentionally has no import dependency on a QDP feature branch,
does no score calculation, and NEVER acknowledges upstream inside the
transaction. Its return value is an ACK instruction after the local commit.

Presence of QDP schema is a prerequisite; it does NOT imply that an upstream
producer is already emitting preparation_ready events.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from intelligence.group_c.contracts import ContractError, canonical_hash
from intelligence.group_c.ports import (
    CapabilityState, IntegrationCapability, PORT_SCHEMAS, QDPHandoffNotice,
)

REQUIRED_QDP_RELATIONS = (
    "qdp_outbox", "qdp_group_current_preparations", "qdp_runs",
)
INPUT_SCHEMA = "qdp-preparation/v2"


def _row(cursor) -> dict | None:
    raw = cursor.fetchone()
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        return dict(raw)
    return dict(zip((col.name for col in cursor.description), raw))


def _json_hash(value: object) -> str:
    """QDP sha256 canonical payload format (not Group C's new format)."""
    return canonical_hash(value)


def _positive_id(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ContractError("invalid_preparation", "Invalid " + name)
    return value


def _sha(value: object, name: str) -> str:
    from re import fullmatch
    if type(value) is not str or fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ContractError("invalid_preparation", "Invalid " + name)
    return value


def _timestamp(value: object, name: str) -> datetime:
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() is None):
        raise ContractError("invalid_observation", "Missing timezone-aware " + name)
    return value


def _verify_preparation_payload(row: Mapping[str, Any], notice: QDPHandoffNotice) -> dict:
    """Pure identity/version/hash gate, independent of DB and source rights."""
    if (row.get("project_id") != notice.project_id
            or str(row.get("run_id")) != notice.qdp_run_id
            or row.get("video_id") != notice.video_id
            or row.get("analysis_group") != "C"
            or row.get("state") != "ready"):
        raise ContractError("preparation_not_current", "QDP C preparation not current")
    inputs = row.get("inputs")
    if type(inputs) is not dict:
        raise ContractError("invalid_preparation", "QDP input must be an object")
    digest = _json_hash(inputs)
    if row.get("input_sha256") != notice.input_sha256 or digest != notice.input_sha256:
        raise ContractError("preparation_hash_mismatch", "QDP preparation input SHA changed")
    if inputs.get("result_schema") != INPUT_SCHEMA or inputs.get("group") != "C":
        raise ContractError("incompatible_preparation_schema", "Expected QDP C preparation v2")
    if (inputs.get("enrichment_source_id") != row.get("source_id")
            or inputs.get("source_policy_version_id") != row.get("source_policy_version_id")
            or inputs.get("metadata_id") != row.get("metadata_id")):
        raise ContractError("preparation_source_mismatch", "Source/policy/metadata lineage mismatch")
    source = row.get("source_id")
    if type(source) is not str or not source.strip():
        raise ContractError("invalid_preparation", "Missing exact source")
    for field in ("source_policy_version_id", "metadata_id", "sre_content_version_id",
                  "sre_discovery_id"):
        _positive_id(inputs.get(field), field)
    _sha(inputs.get("sre_content_sha256"), "SRE content SHA")
    _sha(inputs.get("metric_history_sha256"), "history SHA")
    ids = inputs.get("metric_snapshot_ids")
    if type(ids) is not list or len(ids) != 2 or any(
        type(x) is not int or x <= 0 for x in ids
    ) or ids[0] == ids[1]:
        raise ContractError("invalid_metric_pair", "Must pin two distinct D1 snapshots")
    definition = inputs.get("metric_definition")
    if (type(definition) is not dict or set(definition) != {"name", "version"}
            or any(type(v) is not str or not v.strip() for v in definition.values())):
        raise ContractError("invalid_metric_definition", "Expected exact name/version")
    _positive_id(inputs.get("score_config_id"), "score_config_id")
    return inputs


class QDPCurrentPreparationAdapter:
    """Read only a real QDP event, current QDP C view and canonical D1 facts."""

    def capability(self, conn: Any) -> IntegrationCapability:
        try:
            rows = conn.execute(
                """SELECT name, to_regclass('public.' || name) AS relation
                     FROM unnest(%s::text[]) AS t(name)""",
                (list(REQUIRED_QDP_RELATIONS),),
            ).fetchall()
            present = {row[0] if not isinstance(row, Mapping) else row["name"]:
                       row[1] if not isinstance(row, Mapping) else row["relation"]
                       for row in rows}
        except Exception:
            return IntegrationCapability("qdp", CapabilityState.UNAVAILABLE,
                                         reason_code="qdp_schema_probe_failed")
        if any(present.get(name) is None for name in REQUIRED_QDP_RELATIONS):
            return IntegrationCapability("qdp", CapabilityState.UNAVAILABLE,
                                         reason_code="qdp_current_schema_missing")
        return IntegrationCapability("qdp", CapabilityState.AVAILABLE,
                                     PORT_SCHEMAS["qdp"], "qdp_current_view_present")

    def read_exact_preparation(self, conn: Any, notice: QDPHandoffNotice) -> dict:
        """Call inside a transaction. Do not accept caller-supplied QDP rows."""
        if not isinstance(notice, QDPHandoffNotice):
            raise ContractError("invalid_handoff", "Expected validated QDP notice")
        cap = self.capability(conn)
        if cap.state is not CapabilityState.AVAILABLE:
            raise ContractError("dependency_unavailable", cap.reason_code)
        event = _row(conn.execute(
            """SELECT event_id, project_id, run_id, video_id, analysis_group,
                      event_type, schema_version, payload_sha256, payload
                 FROM qdp_outbox WHERE event_id=%s::uuid FOR UPDATE""",
            (notice.event_id,),
        ))
        if event is None:
            raise ContractError("dependency_unavailable", "QDP has not emitted this event")
        if (str(event["event_id"]) != notice.event_id
                or event["project_id"] != notice.project_id
                or str(event["run_id"]) != notice.qdp_run_id
                or event["video_id"] != notice.video_id
                or event["analysis_group"] != "C"
                or event["event_type"] != "preparation_ready"
                or event["schema_version"] != "qdp-event/v1"):
            raise ContractError("event_lineage_mismatch", "QDP event does not match C notice")
        payload = event["payload"]
        if type(payload) is not dict or _json_hash(payload) != event["payload_sha256"]:
            raise ContractError("event_payload_hash_mismatch", "Unverifiable QDP event payload")
        if (payload.get("project_id") != notice.project_id
                or payload.get("run_id") != notice.qdp_run_id
                or payload.get("video_id") != notice.video_id
                or payload.get("analysis_group") != "C"
                or payload.get("input_sha256") != notice.input_sha256):
            raise ContractError("event_payload_mismatch", "QDP outbox payload pin mismatch")
        current = _row(conn.execute(
            """SELECT run_id,project_id,video_id,analysis_group,state,inputs,
                      input_sha256,source_id,source_policy_version_id,metadata_id
                 FROM qdp_group_current_preparations
                WHERE project_id=%s AND run_id=%s::uuid AND video_id=%s
                  AND analysis_group='C'""",
            (notice.project_id, notice.qdp_run_id, notice.video_id),
        ))
        if current is None:
            raise ContractError("preparation_not_current", "No authorized current C preparation")
        inputs = _verify_preparation_payload(current, notice)
        # A current QDP view is necessary, but not sufficient. Recheck exact
        # D1 publication/snapshots and current source permissions in this txn.
        metadata = _row(conn.execute(
            """SELECT id,video_id,source_id,source_policy_version_id,
                      fetched_at,published_at,deleted_at,expires_at
                 FROM source_video_metadata
                WHERE id=%s AND video_id=%s AND source_id=%s
                  AND source_policy_version_id=%s""",
            (inputs["metadata_id"],notice.video_id,current["source_id"],
             inputs["source_policy_version_id"]),
        ))
        if (metadata is None or metadata["deleted_at"] is not None
                or (metadata["expires_at"] is not None
                    and metadata["expires_at"] <= datetime.now(timezone.utc))):
            raise ContractError("publication_unavailable", "QDP publication evidence revoked")
        # Defend against stale/wrongly implemented QDP current views as well:
        # both canonical source and explicit policy version must allow scores.
        rights = _row(conn.execute(
            """SELECT s.status,s.can_store_metadata,s.can_store_metrics,
                      s.can_derive_scores,s.retention_days,
                      p.is_active,p.permissions,p.retention_policy
                 FROM sources s JOIN source_policy_versions p ON p.source_id=s.id
                WHERE s.id=%s AND p.id=%s""",
            (current["source_id"], inputs["source_policy_version_id"]),
        ))
        perms = rights["permissions"] if rights else None
        if (not rights or rights["status"] != "enabled"
                or rights["is_active"] is not True
                or rights["can_store_metadata"] is not True
                or rights["can_store_metrics"] is not True
                or rights["can_derive_scores"] is not True
                or type(perms) is not dict or
                any(perms.get(flag) is not True for flag in (
                    "can_store_metadata", "can_store_metrics", "can_derive_scores"))):
            raise ContractError("source_policy_unavailable", "Current source rights denied")
        published = _timestamp(metadata["published_at"], "published_at")
        # Source and policy retention are independently bounded by fetched_at.
        from datetime import timedelta
        days_from_policy = rights["retention_policy"].get("retention_days")
        if days_from_policy is not None and (
                type(days_from_policy) is not int or days_from_policy < 1):
            raise ContractError("source_policy_unavailable", "Invalid policy retention")
        for days in (rights["retention_days"], days_from_policy):
            if days is not None and metadata["fetched_at"] + timedelta(days=days) <= datetime.now(timezone.utc):
                raise ContractError("source_policy_unavailable", "Source retention expired")
        snapshots = conn.execute(
            """SELECT id,video_id,source_id,source_policy_version_id,
                      observed_at,views,source_metric_definition,
                      source_metric_version,data_quality_status,precision,
                      observation_time_assumed,expires_at
                 FROM metric_snapshots
                WHERE video_id=%s AND id IN (%s,%s)""",
            (notice.video_id,*inputs["metric_snapshot_ids"]),
        )
        rows = snapshots.fetchall()
        cols = [x.name for x in snapshots.description]
        observed = {int(row["id"] if isinstance(row, Mapping) else row[0]):
                    dict(row) if isinstance(row, Mapping) else dict(zip(cols,row))
                    for row in rows}
        if set(observed) != set(inputs["metric_snapshot_ids"]):
            raise ContractError("metric_pair_unavailable", "Exact D1 metric snapshots missing")
        older, newer = (observed[i] for i in inputs["metric_snapshot_ids"])
        times = []
        for snap in (older, newer):
            if (snap["video_id"] != notice.video_id or
                    snap["source_id"] != current["source_id"] or
                    snap["source_policy_version_id"] != inputs["source_policy_version_id"] or
                    snap["data_quality_status"] != "ok" or snap["precision"] != "exact" or
                    snap["observation_time_assumed"] is not False or
                    type(snap["views"]) is not int or snap["views"] < 0 or
                    snap["source_metric_definition"] != inputs["metric_definition"]["name"] or
                    snap["source_metric_version"] != inputs["metric_definition"]["version"] or
                    (snap["expires_at"] is not None and
                     snap["expires_at"] <= datetime.now(timezone.utc))):
                raise ContractError("metric_pair_incompatible", "D1 metric source/rights invalid")
            times.append(_timestamp(snap["observed_at"], "observed_at"))
        if (times[0] < published or times[1] <= times[0]
                or newer["views"] < older["views"] or newer["views"] == 0):
            raise ContractError("metric_pair_incompatible", "Non-increasing measured views/time")
        config = _row(conn.execute(
            """SELECT id FROM metric_calculation_configs
                WHERE id=%s AND is_active AND alpha>0 AND bayesian_k>0""",
            (inputs["score_config_id"],),
        ))
        if config is None:
            raise ContractError("config_unavailable", "QDP score config no longer active")
        return {
            "event_id": notice.event_id, "project_id": notice.project_id,
            "qdp_run_id": notice.qdp_run_id, "video_id": notice.video_id,
            "input_sha256": notice.input_sha256,
            "payload_sha256": event["payload_sha256"],
            "source_id": current["source_id"],
            "source_policy_version_id": inputs["source_policy_version_id"],
            "previous_snapshot_id": older["id"], "snapshot_id": newer["id"],
            "published_metadata_id": metadata["id"],
            "calculation_config_id": config["id"],
            "metric_source_definition": inputs["metric_definition"]["name"],
            "metric_source_version": inputs["metric_definition"]["version"],
        }


class QDPGroupCConsumer:
    """Receives and commits an inbox+run+job before returning an ACK token.

    The dispatcher must send the token to QDP *after* receive() returns.
    No upstream QDP delivery state is mutated here. Conflict returns no ACK.
    """

    def __init__(self, adapter: QDPCurrentPreparationAdapter | None = None):
        self.adapter = adapter or QDPCurrentPreparationAdapter()

    def receive(self, conn: Any, notice: QDPHandoffNotice,
                announced_payload_sha256: str) -> dict:
        if not isinstance(notice, QDPHandoffNotice):
            raise ContractError("invalid_handoff", "Expected strict QDP notice")
        digest = _sha(announced_payload_sha256, "announced payload SHA")
        # ACK is safe only for an actual outermost commit, never a SAVEPOINT
        # inside a caller-owned uncommitted transaction.
        if getattr(conn, "autocommit", None) is not True:
            raise ContractError("transaction_contract_unavailable", "Autocommit connection required")
        # Unavailable capability fails closed without writing any local state.
        if self.adapter.capability(conn).state is not CapabilityState.AVAILABLE:
            raise ContractError("dependency_unavailable", "QDP current preparation unavailable")
        with conn.transaction():
            old = _row(conn.execute(
                """SELECT event_id,project_id,video_id,qdp_run_id,
                          input_sha256,payload_sha256,state
                     FROM group_c_inbox WHERE event_id=%s::uuid FOR UPDATE""",
                (notice.event_id,),
            ))
            if old:
                identity = (
                    old["project_id"] == notice.project_id
                    and old["video_id"] == notice.video_id
                    and str(old["qdp_run_id"]) == notice.qdp_run_id
                    and old["input_sha256"] == notice.input_sha256
                )
                if not identity or old["payload_sha256"] != digest:
                    conn.execute(
                        """INSERT INTO group_c_inbox_conflicts
                           (event_id,observed_payload_sha256,observed_input_sha256,reason_code)
                           VALUES (%s::uuid,%s,%s,%s)
                           ON CONFLICT(event_id,observed_payload_sha256,observed_input_sha256)
                           DO NOTHING""",
                        (notice.event_id,digest,notice.input_sha256,
                         "identity_conflict" if not identity else "payload_conflict"),
                    )
                    return {"ack": False,"replayed": False,"reason_code": "handoff_conflict"}
                current_replay = self.adapter.read_exact_preparation(conn, notice)
                if current_replay["payload_sha256"] != digest:
                    raise ContractError("event_payload_hash_mismatch", "QDP replay digest changed")
                receipt = _row(conn.execute(
                    """SELECT r.id AS run_id,j.id AS job_id
                         FROM group_c_runs r JOIN group_c_jobs j
                           ON j.project_id=r.project_id AND j.run_id=r.id
                        WHERE r.inbox_event_id=%s::uuid AND r.project_id=%s
                          AND r.video_id=%s AND r.qdp_input_sha256=%s
                          AND j.job_kind='score'""",
                    (notice.event_id,notice.project_id,notice.video_id,notice.input_sha256),
                ))
                if old["state"] != "accepted" or receipt is None:
                    raise ContractError("inbox_incomplete", "Existing receipt lacks durable run/job")
                return {"ack": True,"replayed": True, "event_id": notice.event_id,
                        "run_id": str(receipt["run_id"]), "job_id": receipt["job_id"],
                        "receipt_key": str(receipt["run_id"])}
            verified = self.adapter.read_exact_preparation(conn, notice)
            if verified["payload_sha256"] != digest:
                raise ContractError("event_payload_hash_mismatch", "Dispatcher QDP digest differs")
            run_id = str(uuid4())
            idem = canonical_hash({
                "version": "group-c-qdp-intake/v1", "project_id":notice.project_id,
                "event_id":notice.event_id, "run_id":notice.qdp_run_id,
                "video_id":notice.video_id, "preparation_sha256":notice.input_sha256,
            })
            conn.execute(
                """INSERT INTO group_c_inbox
                   (event_id,project_id,video_id,qdp_run_id,analysis_group,
                    producer_schema,input_sha256,payload_sha256,state,accepted_at)
                   VALUES (%s::uuid,%s,%s,%s::uuid,'C','qdp-handoff/v1',
                           %s,%s,'accepted',now())""",
                (notice.event_id,notice.project_id,notice.video_id,notice.qdp_run_id,
                 notice.input_sha256,digest),
            )
            conn.execute(
                """INSERT INTO group_c_runs
                   (id,project_id,video_id,inbox_event_id,qdp_input_sha256,
                    source_id,idempotency_sha256,snapshot_id,previous_snapshot_id,
                    published_metadata_id,calculation_config_id,source_policy_version_id)
                   VALUES (%s::uuid,%s,%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (run_id,notice.project_id,notice.video_id,notice.event_id,
                 notice.input_sha256,verified["source_id"],idem,
                 verified["snapshot_id"],verified["previous_snapshot_id"],
                 verified["published_metadata_id"],verified["calculation_config_id"],
                 verified["source_policy_version_id"]),
            )
            job_id = _row(conn.execute(
                """INSERT INTO group_c_jobs
                   (project_id,run_id,video_id,job_kind,idempotency_sha256)
                   VALUES (%s,%s::uuid,%s,'score',%s) RETURNING id""",
                (notice.project_id,run_id,notice.video_id,idem),
            ))["id"]
        # ACK is returned ONLY after the context manager commits atomically.
        return {"ack": True,"replayed": False,"event_id": notice.event_id,
                "run_id": run_id,"job_id": job_id,"receipt_key":run_id}

    def read_receipt(self, conn: Any, event_id: str) -> dict | None:
        from uuid import UUID
        try:
            if str(UUID(event_id)) != event_id:
                raise ValueError("noncanonical")
        except (TypeError, ValueError, AttributeError) as exc:
            raise ContractError("invalid_event_id", "Expected canonical UUID") from exc
        result = _row(conn.execute(
            """SELECT i.event_id,i.project_id,i.video_id,i.state,
                      r.id AS run_id,j.id AS job_id
                 FROM group_c_inbox i JOIN group_c_runs r
                   ON r.inbox_event_id=i.event_id AND r.project_id=i.project_id
                 JOIN group_c_jobs j ON j.run_id=r.id AND j.project_id=r.project_id
                WHERE i.event_id=%s::uuid AND i.state='accepted'
                  AND j.job_kind='score'""",
            (event_id,),
        ))
        if result is None:
            return None
        return {"event_id":str(result["event_id"]),"project_id":result["project_id"],
                "video_id":result["video_id"],"run_id":str(result["run_id"]),
                "job_id":result["job_id"],"ack":True}
