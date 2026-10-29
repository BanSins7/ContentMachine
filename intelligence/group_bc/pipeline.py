"""Fail-closed, transactional B + C fan-in for one canonical D1 publication.

QDP fans out to B (content features) and C (performance). Neither module
consumes the other's fabricated inputs. This bridge joins *verified durable*
B and C results by project/video/source/QDP run/policy, then offers a pinned
composite to a trusted downstream adapter. It never computes C7 or drives
paid B analysis.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from intelligence.group_b.integration.analytics import read_uae_handoff
from intelligence.group_b.integration.analytics_contract import HandoffDecision
from intelligence.group_c.analytics_bridge import (
    PersistedC7Handoff, PostgreSQLAnalyticsBridge,
)

VERSION = "group-bc-qualified-join/v1"
PROJECT = re.compile(r"[0-9a-f]{32}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")


class JointBlocked(ValueError):
    """No durable success or downstream ACK is permitted."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _uuid(value: str) -> bool:
    if type(value) is not str:
        return False
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def _hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _record(cursor) -> dict | None:
    row = cursor.fetchone()
    if row is None:
        return None
    if isinstance(row, Mapping):
        return dict(row)
    return dict(zip((col.name for col in cursor.description), row))


@dataclass(frozen=True, slots=True)
class ExactPair:
    project_id: str
    video_id: int
    source_id: str
    qdp_run_id: str
    b_run_id: str
    c_run_id: str
    b_analysis_id: int
    c_metric_id: int
    source_policy_version_id: int
    b_preparation_sha256: str
    c_preparation_sha256: str

    def __post_init__(self):
        if not PROJECT.fullmatch(self.project_id):
            raise JointBlocked("invalid_project")
        if (type(self.video_id) is not int or self.video_id <= 0 or
            type(self.b_analysis_id) is not int or self.b_analysis_id <= 0 or
            type(self.c_metric_id) is not int or self.c_metric_id <= 0 or
            type(self.source_policy_version_id) is not int or
            self.source_policy_version_id <= 0):
            raise JointBlocked("invalid_identity")
        if type(self.source_id) is not str or not self.source_id.strip():
            raise JointBlocked("invalid_source")
        if not all(_uuid(x) for x in
                   (self.qdp_run_id, self.b_run_id, self.c_run_id)):
            raise JointBlocked("invalid_run")
        if not all(type(x) is str and SHA.fullmatch(x) for x in
                   (self.b_preparation_sha256, self.c_preparation_sha256)):
            raise JointBlocked("invalid_preparation_digest")


def read_exact_pair(conn: Any, *, project_id: str,
                    b_run_id: str, c_run_id: str) -> ExactPair:
    """Only joins completed/accepted records pinned to the same QDP run.

    B and C are independent QDP consumer groups: their preparation digests
    MUST NOT be assumed equal. The shared QDP run, canonical source, video
    and policy revision MUST match.
    """
    if not PROJECT.fullmatch(project_id) or not _uuid(b_run_id) or not _uuid(c_run_id):
        raise JointBlocked("invalid_scope")
    row = _record(conn.execute(
        """SELECT br.project_id,br.video_id,br.source_id,
                  bi.qdp_run_id::text AS qdp_run_id,
                  br.id::text AS b_run_id,cr.id::text AS c_run_id,
                  br.result_analysis_id AS b_analysis_id,
                  cr.calculated_metric_id AS c_metric_id,
                  br.source_policy_version_id,
                  bi.preparation_sha256 AS b_preparation_sha256,
                  ci.input_sha256 AS c_preparation_sha256
             FROM group_b_runs br
             JOIN group_b_inbox bi
               ON bi.project_id=br.project_id AND bi.id=br.inbox_id
              AND bi.video_id=br.video_id AND bi.source_id=br.source_id
             JOIN group_c_runs cr
               ON cr.project_id=br.project_id AND cr.video_id=br.video_id
              AND cr.source_id=br.source_id
              AND cr.source_policy_version_id=br.source_policy_version_id
             JOIN group_c_inbox ci
               ON ci.event_id=cr.inbox_event_id AND ci.project_id=cr.project_id
              AND ci.video_id=cr.video_id
              AND ci.input_sha256=cr.qdp_input_sha256
              AND ci.qdp_run_id=bi.qdp_run_id
            WHERE br.project_id=%s AND br.id=%s::uuid AND cr.id=%s::uuid
              AND br.state='completed' AND cr.state='completed'
              AND bi.status='accepted' AND ci.state='accepted'
              AND br.result_analysis_id IS NOT NULL
              AND cr.calculated_metric_id IS NOT NULL
              AND br.source_policy_version_id IS NOT NULL
            FOR SHARE OF br,bi,cr,ci""",
        (project_id, b_run_id, c_run_id),
    ))
    if row is None:
        raise JointBlocked("exact_completed_pair_unavailable")
    return ExactPair(**row)


def certified_payload(pair: ExactPair, b: HandoffDecision,
                      c: PersistedC7Handoff) -> dict:
    """Pure final boundary validation; does NOT authenticate source itself.

    Production must obtain B via read_uae_handoff and C via
    PostgreSQLAnalyticsBridge.load_persisted in the same SQL transaction.
    """
    if not isinstance(b, HandoffDecision) or not b.verify_digest():
        raise JointBlocked("group_b_fresh_candidate_unavailable")
    if not isinstance(c, PersistedC7Handoff):
        raise JointBlocked("group_c_certified_score_unavailable")
    data = b.payload
    if not isinstance(data, dict):
        raise JointBlocked("group_b_candidate_missing")
    if not (b.project_id == pair.project_id and
            b.run_id == pair.b_run_id and
            b.analysis_id == pair.b_analysis_id and
            data.get("project_id") == pair.project_id and
            data.get("run_id") == pair.b_run_id and
            data.get("analysis_id") == pair.b_analysis_id and
            data.get("video_id") == pair.video_id and
            data.get("source_id") == pair.source_id and
            data.get("qdp_run_id") == pair.qdp_run_id and
            data.get("qdp_preparation_sha256") == pair.b_preparation_sha256 and
            data.get("source_policy_version_id") == pair.source_policy_version_id and
            data.get("uae_qualification_status") == "not_evaluated" and
            data.get("group_c_qualification_status") == "not_evaluated"):
        raise JointBlocked("group_b_exact_lineage_mismatch")
    if not (c.project_id == pair.project_id and
            c.run_id == pair.c_run_id and
            c.video_id == pair.video_id and
            c.calculated_metric_id == pair.c_metric_id and
            c.source_id == pair.source_id and
            c.source_policy_version_id == pair.source_policy_version_id):
        raise JointBlocked("group_c_exact_lineage_mismatch")
    if type(c.evidence_sha256) is not str or not SHA.fullmatch(c.evidence_sha256):
        raise JointBlocked("group_c_evidence_invalid")
    if not (type(data.get("schema_version")) is str and
            c.version == "group-c-analytics-handoff/v1"):
        raise JointBlocked("peer_schema_incompatible")

    # No duplicated C7 calculation and no unapproved transfer of B raw text.
    # A downstream adapter must fetch both original certified versions by ID.
    return {
        "schema_version": VERSION,
        "project_id": pair.project_id,
        "video_id": pair.video_id,
        "source_id": pair.source_id,
        "qdp_run_id": pair.qdp_run_id,
        "source_policy_version_id": pair.source_policy_version_id,
        "group_b": {
            "run_id": pair.b_run_id,
            "analysis_id": pair.b_analysis_id,
            "handoff_version": data["schema_version"],
            "handoff_sha256": b.payload_sha256,
            "preparation_sha256": pair.b_preparation_sha256,
        },
        "group_c": {
            "run_id": pair.c_run_id,
            "calculated_metric_id": pair.c_metric_id,
            "handoff_version": c.version,
            "evidence_sha256": c.evidence_sha256,
            "preparation_sha256": pair.c_preparation_sha256,
            "snapshot_id": c.snapshot_id,
            "previous_snapshot_id": c.previous_snapshot_id,
            "published_metadata_id": c.published_metadata_id,
            "calculation_config_id": c.calculation_config_id,
            "metric_definition": c.metric_definition,
            "metric_version": c.metric_version,
        },
        # The joint event is a candidate for a future UAE acceptance gate,
        # not evidence that UAE has accepted/qualified either group.
        "analytics_qualification": "not_evaluated",
    }


@dataclass(frozen=True, slots=True)
class JointReceipt:
    receipt_id: str
    project_id: str
    b_run_id: str
    c_run_id: str
    payload_sha256: str
    newly_created: bool
    state: str


class GroupBCPipeline:
    """Synchronous, caller-driven B+C fan-in with durable delivery fencing."""

    def __init__(self, c_bridge: PostgreSQLAnalyticsBridge | None = None):
        self.c_bridge = c_bridge or PostgreSQLAnalyticsBridge()

    def _verified(self, conn, pair, *, b_current, b_compatibility,
                  b_verifier, c_ports):
        if b_current is None or b_compatibility is None or b_verifier is None or c_ports is None:
            raise JointBlocked("trusted_peer_ports_missing")
        b = read_uae_handoff(
            conn, project_id=pair.project_id, run_id=pair.b_run_id,
            current=b_current, compatibility=b_compatibility,
            verifier=b_verifier,
        )
        if not b.verify_digest():
            raise JointBlocked("group_b_verification_blocked")
        c = self.c_bridge.load_persisted(
            conn, project_id=pair.project_id,
            calculated_metric_id=pair.c_metric_id, ports=c_ports,
            transaction_managed=True,
        )
        return b, c, certified_payload(pair, b, c)

    def prepare(self, conn, *, project_id: str, b_run_id: str, c_run_id: str,
                b_current, b_compatibility, b_verifier, c_ports) -> JointReceipt:
        """One serializable transaction: reread both producers and enqueue.

        No event is accepted based on client supplied completion flags.
        Same exact pair is idempotent; same pair/new payload is a conflict.
        """
        if not callable(getattr(conn, "transaction", None)):
            raise JointBlocked("transaction_required")
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            pair = read_exact_pair(conn, project_id=project_id,
                                   b_run_id=b_run_id, c_run_id=c_run_id)
            _, _, payload = self._verified(
                conn, pair, b_current=b_current,
                b_compatibility=b_compatibility, b_verifier=b_verifier,
                c_ports=c_ports,
            )
            digest = _hash(payload)
            created = _record(conn.execute(
                """INSERT INTO group_bc_joint_receipts(
                     project_id,video_id,source_id,qdp_run_id,
                     b_run_id,c_run_id,b_analysis_id,c_metric_id,
                     source_policy_version_id,b_preparation_sha256,
                     c_preparation_sha256,b_handoff_sha256,c_evidence_sha256,
                     payload_sha256,payload)
                   VALUES (%s,%s,%s,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,
                           %s,%s,%s,%s,%s,%s::jsonb)
                   ON CONFLICT(project_id,b_run_id,c_run_id) DO NOTHING
                   RETURNING id::text AS receipt_id,state""",
                (pair.project_id, pair.video_id, pair.source_id,
                 pair.qdp_run_id, pair.b_run_id, pair.c_run_id,
                 pair.b_analysis_id, pair.c_metric_id,
                 pair.source_policy_version_id,
                 pair.b_preparation_sha256, pair.c_preparation_sha256,
                 payload["group_b"]["handoff_sha256"],
                 payload["group_c"]["evidence_sha256"],
                 digest, json.dumps(payload, sort_keys=True)),
            ))
            if created:
                return JointReceipt(created["receipt_id"], project_id,
                                    b_run_id, c_run_id, digest, True, created["state"])
            existing = _record(conn.execute(
                """SELECT id::text AS receipt_id,payload_sha256,state
                   FROM group_bc_joint_receipts
                  WHERE project_id=%s AND b_run_id=%s::uuid AND c_run_id=%s::uuid
                  FOR UPDATE""", (project_id, b_run_id, c_run_id),
            ))
            if not existing or existing["payload_sha256"] != digest:
                raise JointBlocked("joint_version_conflict")
            return JointReceipt(existing["receipt_id"], project_id,
                                b_run_id, c_run_id, digest, False, existing["state"])

    def deliver_one(self, conn, *, project_id: str, b_current_provider,
                    b_compatibility, b_verifier, c_ports, sink,
                    receipt_id: str | None = None) -> JointReceipt | None:
        """One queued composite -> actual durable downstream consumer.

        sink.persist_joint(conn, receipt_id, payload, b_handoff, c_handoff)
        must write a durable same-transaction row. A returned ACK alone is
        insufficient: sink.verify_durable_ack must re-read it from *conn*.
        Any error rolls back the delivery state and keeps retry possible.
        """
        if ((receipt_id is not None and not _uuid(receipt_id)) or
            not PROJECT.fullmatch(project_id) or
            not callable(getattr(conn, "transaction", None)) or
            not callable(b_current_provider) or
            not callable(getattr(sink, "persist_joint", None)) or
            not callable(getattr(sink, "verify_durable_ack", None))):
            raise JointBlocked("delivery_ports_unavailable")
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            row = _record(conn.execute(
                """SELECT id::text AS receipt_id,b_run_id::text,c_run_id::text,
                          payload_sha256
                   FROM group_bc_joint_receipts
                  WHERE project_id=%s AND state='queued'
                    AND (%s::uuid IS NULL OR id=%s::uuid)
                  ORDER BY created_at,id
                  LIMIT 1 FOR UPDATE SKIP LOCKED""",
                (project_id, receipt_id, receipt_id),
            ))
            if row is None:
                return None
            pair = read_exact_pair(conn, project_id=project_id,
                                   b_run_id=row["b_run_id"], c_run_id=row["c_run_id"])
            b_current = b_current_provider(conn, pair)
            b, c, payload = self._verified(
                conn, pair, b_current=b_current,
                b_compatibility=b_compatibility, b_verifier=b_verifier, c_ports=c_ports,
            )
            if _hash(payload) != row["payload_sha256"]:
                raise JointBlocked("joint_payload_became_stale")
            ack = sink.persist_joint(
                conn, receipt_id=row["receipt_id"], payload=payload,
                b_handoff=b, c_handoff=c,
            )
            if (not isinstance(ack, Mapping) or
                ack.get("receipt_id") != row["receipt_id"] or
                ack.get("payload_sha256") != row["payload_sha256"] or
                sink.verify_durable_ack(
                    conn, receipt_id=row["receipt_id"],
                    payload_sha256=row["payload_sha256"]) is not True):
                raise JointBlocked("consumer_durable_ack_missing")
            result = conn.execute(
                """UPDATE group_bc_joint_receipts
                      SET state='delivered',delivered_at=now()
                    WHERE id=%s::uuid AND project_id=%s AND state='queued'""",
                (row["receipt_id"], project_id),
            )
            if result.rowcount != 1:
                raise JointBlocked("delivery_fence_lost")
            return JointReceipt(row["receipt_id"], project_id,
                                row["b_run_id"], row["c_run_id"],
                                row["payload_sha256"], False, "delivered")
