"""Transactional Group C -> UAE ingress, plus current certified C7 read port.

GC remains sole scorer and producer. UAE receives an immutable idempotent
reference in the SAME SERIALIZABLE transaction as GC's outbox delivery, then
rechecks original GC24 current rights and QDP at EVERY later read.

A received reference is NOT a qualified unified analytics snapshot.
"""
from __future__ import annotations

from decimal import Decimal
from dataclasses import replace
import re
from collections.abc import Mapping

from intelligence.group_c.analytics_bridge import (
    ANALYTICS_HANDOFF_VERSION, OUTBOX_EVENT_TYPE, PostgreSQLAnalyticsBridge,
    _validate_outbox_payload,
)
from intelligence.group_c.contracts import ContractError, canonical_hash
from intelligence.group_c.d1 import _record
from intelligence.group_c.ports import (
    CapabilityState as GCCapability, IntegrationCapability, GroupCPorts,
)
from intelligence.group_c.retention import _certified, _validate_current
from intelligence.unified_analytics.contracts import AnalyticsBoundaryError
from intelligence.unified_analytics.dto import (
    InputScope, EvidenceRef, EvidenceRead, ReadState,
)
from intelligence.unified_analytics.ports import (
    CapabilityState as UAECapability, PortCapability, PORT_SCHEMAS,
)
from intelligence.unified_analytics.group_c_adapter import (
    GroupCPersistedPins,
)
from intelligence.unified_analytics.qdp_adapter import QDPQualifiedSetReader


HANDOFF_WIRE = "group-c-analytics-handoff/v1"
INGEST_SCHEMA = "uae-group-c-reception/v1"
_RECEIPT_TABLES = (
    "uae_group_c_receptions", "group_c_outbox", "group_c_runs",
    "group_c_calculation_receipts", "calculated_metrics",
    "group_c_score_invalidations", "group_c_erasure_requests",
)
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _fail(code: str) -> None:
    raise ContractError(code, "UAE Group C durable reception denied")


def _available(conn) -> bool:
    if conn is None or not callable(getattr(conn, "execute", None)):
        return False
    try:
        row = conn.execute(
            "SELECT " + ", ".join(
                f"to_regclass('public.{name}') IS NOT NULL"
                for name in _RECEIPT_TABLES
            )
        ).fetchone()
        return row is not None and len(row) == len(_RECEIPT_TABLES) and all(x is True for x in row)
    except Exception:
        return False


class UAEGroupCTransactionalSink:
    """GroupCPorts.analytics implementation: writes to UAE receipt in GC tx.

    The Group C dispatcher locks the original outbox, runs GC24 current-rights
    checks, invokes this sink and marks outbox delivered before COMMIT. Neither
    component may ACK via remote RPC or call a second C7 calculation.
    """
    def __init__(self, source_ports: GroupCPorts):
        if not isinstance(source_ports, GroupCPorts):
            _fail("analytics_current_rights_ports_required")
        self._source_ports = source_ports

    def capability(self, conn) -> IntegrationCapability:
        ready = _available(conn)
        return IntegrationCapability(
            port="analytics",
            state=GCCapability.AVAILABLE if ready else GCCapability.UNAVAILABLE,
            observed_schema="group-c-analytics-outbox/v1" if ready else None,
            reason_code="uae_local_durable_reception" if ready
            else "uae_reception_schema_unavailable",
        )

    def enqueue_persisted_result(
        self, conn, project_id: str, calculated_metric_id: int, idempotency_sha256: str,
    ) -> Mapping[str, object]:
        if (type(project_id) is not str or not re.fullmatch("[0-9a-f]{32}", project_id)
                or type(calculated_metric_id) is not int or calculated_metric_id <= 0
                or type(idempotency_sha256) is not str
                or _SHA.fullmatch(idempotency_sha256) is None):
            _fail("analytics_ingest_identity_invalid")
        if not _available(conn):
            _fail("dependency_unavailable")
        # Group C enforces an existing SERIALIZABLE tx; never open a separate
        # connection or commit an ACK before its group_c_outbox state update.
        from intelligence.group_c.analytics_bridge import _require_serializable
        _require_serializable(conn)
        # GC24 independently verifies current source rights, retention,
        # original configuration, frozen peers and explicit invalidations.
        current = PostgreSQLAnalyticsBridge().load_persisted(
            conn, project_id=project_id,
            calculated_metric_id=calculated_metric_id,
            ports=self._source_ports, transaction_managed=True,
        )
        row = _record(conn.execute(
            """SELECT id,project_id,run_id,video_id,calculated_metric_id,
                      event_type,event_key,payload_sha256,payload,state
                 FROM group_c_outbox
                WHERE project_id=%s AND calculated_metric_id=%s
                  AND payload_sha256=%s AND state='pending'
                FOR UPDATE""",
            (project_id,calculated_metric_id,idempotency_sha256),
        ))
        if row is None:
            _fail("analytics_outbox_unavailable")
        # Detect all event field/score/lineage drift against the certified SQL.
        from intelligence.group_c.analytics_bridge import _persisted
        exact = _persisted(conn,project_id=project_id,
                           metric_id=calculated_metric_id,
                           run_id=str(row["run_id"]))
        _validate_outbox_payload(row, exact)
        if (current.project_id != project_id
                or current.calculated_metric_id != calculated_metric_id
                or current.evidence_sha256 != exact.evidence_sha256
                or current.breakout_score != exact.breakout_score
                or str(row["run_id"]) != exact.run_id
                or row["payload_sha256"] != idempotency_sha256):
            _fail("analytics_persisted_score_changed")
        fields = (
            project_id, str(row["id"]), exact.run_id, exact.video_id,
            calculated_metric_id, exact.source_id, exact.source_policy_version_id,
            exact.snapshot_id, exact.previous_snapshot_id,
            exact.published_metadata_id, exact.calculation_config_id,
            exact.evidence_sha256, idempotency_sha256, exact.breakout_score,
        )
        inserted = _record(conn.execute(
            """INSERT INTO uae_group_c_receptions(
                 project_id,outbox_id,run_id,video_id,calculated_metric_id,
                 source_id,source_policy_version_id,snapshot_id,
                 previous_snapshot_id,published_metadata_id,
                 calculation_config_id,evidence_sha256,event_sha256,breakout_score)
               VALUES(%s,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING RETURNING id""", fields,
        ))
        if inserted is None:
            existing = _record(conn.execute(
                """SELECT id,project_id,outbox_id::text AS outbox_id,
                          run_id::text AS run_id,video_id,calculated_metric_id,
                          source_id,source_policy_version_id,snapshot_id,
                          previous_snapshot_id,published_metadata_id,
                          calculation_config_id,evidence_sha256,event_sha256,
                          breakout_score
                   FROM uae_group_c_receptions
                   WHERE project_id=%s AND (outbox_id=%s::uuid OR event_sha256=%s)
                   FOR UPDATE""",
                (project_id,str(row["id"]),idempotency_sha256),
            ))
            expected = (
                "project_id","outbox_id","run_id","video_id","calculated_metric_id",
                "source_id","source_policy_version_id","snapshot_id",
                "previous_snapshot_id","published_metadata_id",
                "calculation_config_id","evidence_sha256","event_sha256","breakout_score",
            )
            if existing is None or any(
                existing.get(field) != value for field,value in zip(expected,fields)
            ):
                _fail("analytics_reception_identity_conflict")
            receipt_id=existing["id"]
        else:
            receipt_id=inserted["id"]
        if type(receipt_id) is not int or receipt_id<=0:
            _fail("analytics_receipt_unavailable")
        # Group C's legacy ACK key calls this analytics_snapshot_id.
        # It is the durable UAE INTAKE receipt id, NOT a qualified snapshot.
        return dict(
            accepted=True, project_id=project_id,
            calculated_metric_id=calculated_metric_id,
            idempotency_sha256=idempotency_sha256,
            analytics_snapshot_id=receipt_id,
        )


class UAEReceivedGroupCReader:
    """A UAEPorts.group_c reader over current GC24 + exact delivered receipt.

    This reader never re-scores C7 or trusts the ingestion ACK as freshness.
    QDP and Group C are rechecked in one consistent read-only transaction.
    An unavailable source, revised policy, revoked peer or mismatched receipt
    yields MISSING/UNAVAILABLE and prevents UAE qualification.
    """

    def __init__(
        self, conn, pins: GroupCPersistedPins,
        qdp: QDPQualifiedSetReader, source_ports: GroupCPorts,
    ):
        if not isinstance(pins,GroupCPersistedPins) or not isinstance(qdp,QDPQualifiedSetReader):
            raise AnalyticsBoundaryError("c_integration_pins_missing","Exact UAE C/QDP pins required")
        if not isinstance(source_ports,GroupCPorts):
            raise AnalyticsBoundaryError("c_integration_source_ports_missing","GC current rights required")
        if qdp._conn is not conn or qdp._pins.scope != pins.scope:
            raise AnalyticsBoundaryError("c_integration_qdp_scope_mismatch","Exact QDP scope required")
        self._conn,self._pins,self._qdp,self._source_ports = conn,pins,qdp,source_ports

    def capability(self) -> PortCapability:
        if not _available(self._conn):
            return PortCapability("group_c",UAECapability.UNAVAILABLE,
                                  reason_code="c_reception_unavailable")
        if self._qdp.capability().state is not UAECapability.DECLARED:
            return PortCapability("group_c",UAECapability.UNAVAILABLE,
                                  reason_code="c_qdp_unavailable")
        return PortCapability("group_c",UAECapability.DECLARED,
                              PORT_SCHEMAS["group_c"],HANDOFF_WIRE)

    def read_persisted_metric(self, scope: InputScope) -> EvidenceRead:
        if not isinstance(scope,InputScope) or scope!=self._pins.scope:
            raise AnalyticsBoundaryError("c_scope_mismatch","Exact scope required")
        if self.capability().state is not UAECapability.DECLARED:
            return EvidenceRead("group_c",scope,ReadState.UNAVAILABLE,
                                reason_code="c_reception_unavailable")
        try:
            if getattr(self._conn,"autocommit",None) is not True:
                raise AnalyticsBoundaryError("c_fresh_transaction_required","Independent read required")
            with self._conn.transaction():
                self._conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                if self._qdp.read_qualified_membership(scope).state is not ReadState.LOCATED:
                    return EvidenceRead("group_c",scope,ReadState.MISSING,
                                        reason_code="c_qdp_membership_unverified")
                p=self._pins
                row=_record(self._conn.execute(
                    """SELECT recv.id,recv.outbox_id::text AS outbox_id,
                              recv.run_id::text AS run_id,recv.video_id,
                              recv.calculated_metric_id,recv.source_id,
                              recv.source_policy_version_id,recv.snapshot_id,
                              recv.previous_snapshot_id,recv.published_metadata_id,
                              recv.calculation_config_id,recv.evidence_sha256,
                              recv.event_sha256,recv.breakout_score,
                              o.event_type,o.payload_sha256,o.payload,o.state AS event_state,
                              gr.state AS run_state,gr.qdp_input_sha256,
                              gr.source_id AS run_source_id,
                              gr.source_policy_version_id AS run_policy_id,
                              inbox.qdp_run_id::text AS qdp_run_id,
                              inbox.state AS inbox_state
                       FROM uae_group_c_receptions recv
                       JOIN group_c_outbox o ON o.id=recv.outbox_id
                       JOIN group_c_runs gr
                         ON gr.id=recv.run_id AND gr.project_id=recv.project_id
                          AND gr.video_id=recv.video_id
                       JOIN group_c_inbox inbox ON inbox.event_id=gr.inbox_event_id
                       WHERE recv.project_id=%s AND recv.run_id=%s::uuid
                         AND recv.calculated_metric_id=%s
                       ORDER BY recv.id DESC LIMIT 1""",
                    (scope.project_id,p.c_run_id,p.calculated_metric_id),
                ))
                if row is None:
                    return EvidenceRead("group_c",scope,ReadState.MISSING,
                                        reason_code="c_reception_not_delivered")
                if (row["run_id"] != p.c_run_id
                        or row["video_id"] != scope.video_id
                        or row["source_id"] != scope.source_id
                        or row["run_source_id"] != scope.source_id
                        or row["source_policy_version_id"] != scope.source_policy_version_id
                        or row["run_policy_id"] != scope.source_policy_version_id
                        or row["calculated_metric_id"] != p.calculated_metric_id
                        or row["snapshot_id"] != p.current_snapshot_id
                        or row["previous_snapshot_id"] != p.previous_snapshot_id
                        or row["published_metadata_id"] != p.metadata_id
                        or row["calculation_config_id"] != p.config_id
                        or row["qdp_run_id"] != scope.qdp_run_id
                        or row["qdp_input_sha256"] != p.qdp_c_preparation_sha256
                        or row["inbox_state"] != "accepted"
                        or row["run_state"] != "completed"
                        or row["event_type"] != OUTBOX_EVENT_TYPE
                        or row["event_state"] != "delivered"
                        or row["event_sha256"] != row["payload_sha256"]
                        or canonical_hash(row["payload"]) != row["event_sha256"]):
                    return EvidenceRead("group_c",scope,ReadState.MISSING,
                                        reason_code="c_reception_lineage_changed")
                certified=_certified(self._conn,project_id=scope.project_id,
                                     calculated_metric_id=p.calculated_metric_id)
                _validate_current(self._conn,certified,self._source_ports)
                if (certified["evidence_sha256"] != row["evidence_sha256"]
                        or certified["breakout_score"] != row["breakout_score"]
                        or certified["snapshot_id"] != row["snapshot_id"]
                        or certified["previous_snapshot_id"] != row["previous_snapshot_id"]
                        or certified["published_metadata_id"] != row["published_metadata_id"]
                        or certified["calculation_config_id"] != row["calculation_config_id"]
                        or certified["source_id"] != scope.source_id
                        or certified["source_policy_version_id"] != scope.source_policy_version_id):
                    return EvidenceRead("group_c",scope,ReadState.MISSING,
                                        reason_code="c_reception_current_mismatch")
                return EvidenceRead(
                    "group_c",scope,ReadState.LOCATED,
                    reference=EvidenceRef(
                        scope=scope,port="group_c",
                        record_key="c7:"+str(row["calculated_metric_id"]),
                        producer_schema=HANDOFF_WIRE,
                        producer_version=INGEST_SCHEMA,
                        payload_sha256=row["event_sha256"],
                    ),
                )
        except ContractError:
            return EvidenceRead("group_c",scope,ReadState.MISSING,
                                reason_code="c_current_source_denied")
        except AnalyticsBoundaryError:
            raise
        except Exception:
            return EvidenceRead("group_c",scope,ReadState.UNAVAILABLE,
                                reason_code="c_reception_read_unavailable")


__all__ = ["HANDOFF_WIRE","INGEST_SCHEMA",
           "UAEGroupCTransactionalSink","UAEReceivedGroupCReader"]
