"""GC-MOD-25: transactional Group C → Unified Analytics handoff.

Group C alone owns completed C7 arithmetic. Analytics consumes an exact
persisted certified score ID + immutable source/config/peer/review lineage;
it MUST NOT run a second C7 calculation or independently INSERT C7.

The sink must write a durable record on the same PostgreSQL connection and
transaction (no HTTP/network call). Outbox row is marked delivered ONLY after
a precise, durable idempotent ACK from an actually available Analytics port.
Unavailable/missing peer = no guessed ACK and no delivered transition.

All reads/proofs are scoped to project and current source/peer TTL. An old
complete numeric score is never sufficient for eligibility.
"""
from __future__ import annotations

import json
import re
from contextlib import nullcontext
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping
from uuid import UUID, uuid4

from .contracts import ContractError, canonical_hash
from .d1 import _record, local_d1_ports
from .ports import GroupCPorts
from .retention import _certified, _validate_current

ANALYTICS_HANDOFF_VERSION="group-c-analytics-handoff/v1"
OUTBOX_EVENT_TYPE="group_c.result_ready/v1"
_PROJECT=re.compile(r"[0-9a-f]{32}\Z")
_SHA=re.compile(r"[0-9a-f]{64}\Z")
_STRING_FIELDS=("project_id","run_id","source_id","metric_version",
                "review_request_id","review_decision_id",
                "activation_event_id","evidence_sha256")
_ID_FIELDS=("video_id","calculated_metric_id","snapshot_id",
            "previous_snapshot_id","published_metadata_id",
            "calculation_config_id","source_policy_version_id",
            "creator_freeze_id","prior_freeze_id")
_OPTIONAL_IDS=("niche_freeze_id",)


def _project(value: str) -> str:
    if type(value) is not str or _PROJECT.fullmatch(value) is None:
        raise ContractError("invalid_project_id","Canonical project ID required")
    return value


def _id(value: Any, name: str) -> int:
    if type(value) is not int or value<1:
        raise ContractError("invalid_identifier",name+" must be positive integer")
    return value


def _tx(conn: Any) -> None:
    if getattr(conn,"autocommit",None) is not True:
        raise ContractError("transaction_contract_unavailable",
                            "Dedicated autocommit PostgreSQL connection required")


def _uuid(value: Any) -> str:
    if type(value) is not str:
        raise ContractError("invalid_outbox_id","Canonical lower-case UUID required")
    try:
        x=UUID(value)
    except (AttributeError,TypeError,ValueError) as exc:
        raise ContractError("invalid_outbox_id","Canonical UUID required") from exc
    if str(x)!=value:
        raise ContractError("invalid_outbox_id","Canonical UUID required")
    return value


def _ports(ports: GroupCPorts | None) -> GroupCPorts:
    if ports is None:
        return local_d1_ports()
    if not isinstance(ports,GroupCPorts):
        raise ContractError("invalid_ports","Explicit Group C ports required")
    return ports


def _require_serializable(conn: Any) -> None:
    from psycopg.pq import TransactionStatus
    if conn.info.transaction_status != TransactionStatus.INTRANS:
        raise ContractError("transaction_contract_unavailable",
                            "Must share caller's transaction")
    if conn.execute("SHOW transaction_isolation").fetchone()[0]!="serializable":
        raise ContractError("transaction_contract_unavailable",
                            "Analytics handoff requires SERIALIZABLE transaction")


@dataclass(frozen=True)
class PersistedC7Handoff:
    """A read-only copy of *actual* stored C7 columns, not math inputs."""
    project_id: str
    run_id: str
    video_id: int
    calculated_metric_id: int
    snapshot_id: int
    previous_snapshot_id: int
    published_metadata_id: int
    calculation_config_id: int
    source_id: str
    source_policy_version_id: int
    metric_definition: str
    metric_version: str
    creator_cohort_id: int
    prior_cohort_id: int
    niche_cohort_id: int | None
    creator_freeze_id: int
    prior_freeze_id: int
    niche_freeze_id: int | None
    review_request_id: str
    review_decision_id: str
    activation_event_id: str
    evidence_sha256: str
    breakout_score: Decimal
    score_creator_component: Decimal
    score_growth_component: Decimal
    score_engagement_component: Decimal
    score_freshness_component: Decimal
    version: str = ANALYTICS_HANDOFF_VERSION

    def __post_init__(self):
        _project(self.project_id)
        _uuid(self.run_id)
        for key in (
            "video_id","calculated_metric_id","snapshot_id",
            "previous_snapshot_id","published_metadata_id","calculation_config_id",
            "source_policy_version_id","creator_cohort_id","prior_cohort_id",
            "creator_freeze_id","prior_freeze_id",
        ):
            _id(getattr(self,key),key)
        for key in ("niche_cohort_id","niche_freeze_id"):
            value=getattr(self,key)
            if value is not None:
                _id(value,key)
        if (type(self.evidence_sha256) is not str
                or _SHA.fullmatch(self.evidence_sha256) is None
                or self.metric_definition!="platform_post_views"):
            raise ContractError("analytics_lineage_mismatch","Certified source/metric version required")
        for key in ("breakout_score","score_creator_component",
                    "score_growth_component","score_engagement_component",
                    "score_freshness_component"):
            value=getattr(self,key)
            if type(value) is not Decimal or not value.is_finite():
                raise ContractError("analytics_score_unverified",
                                    "Actual SQL exact Decimal C7 is required")
        if self.breakout_score != sum((
            self.score_creator_component,self.score_growth_component,
            self.score_engagement_component,self.score_freshness_component,
        ),Decimal(0)):
            raise ContractError("analytics_score_unverified",
                                "Stored score must match four persisted components")


@dataclass(frozen=True)
class AnalyticsOutboxReceipt:
    project_id: str
    calculated_metric_id: int
    outbox_id: str
    event_key: str
    payload_sha256: str
    replayed: bool
    state: str = "pending"
    version: str = ANALYTICS_HANDOFF_VERSION


@dataclass(frozen=True)
class AnalyticsDeliveryReceipt:
    project_id: str
    calculated_metric_id: int
    outbox_id: str
    analytics_snapshot_id: int | None
    state: str
    reason_code: str | None = None
    version: str = ANALYTICS_HANDOFF_VERSION


def _persisted(conn: Any, *, project_id: str, metric_id: int,
               run_id: str | None = None) -> PersistedC7Handoff:
    row=_certified(conn,project_id=project_id,calculated_metric_id=metric_id)
    # A replay may complete the same immutable C7 from a second valid QDP
    # run. An outbox event must remain pinned to ITS own completed run, not
    # whichever completed run happens to sort most recently for the score.
    if run_id is not None:
        _uuid(run_id)
        bound=_record(conn.execute(
            """SELECT id FROM group_c_runs WHERE project_id=%s
                 AND id=%s::uuid AND video_id=%s
                 AND calculated_metric_id=%s AND state='completed'""",
            (project_id,run_id,row["video_id"],metric_id),
        ))
        if bound is None:
            raise ContractError("analytics_lineage_mismatch",
                                "Outbox run does not own exact certified complete C7")
    score=_record(conn.execute(
        """SELECT c.score_creator_component,c.score_growth_component,
                  c.score_engagement_component,c.score_freshness_component,
                  c.creator_cohort_id,c.prior_cohort_id,c.niche_cohort_id,
                  r.review_decision_id,r.activation_event_id
             FROM calculated_metrics c
             JOIN group_c_calculation_receipts r
               ON r.calculated_metric_id=c.id
            WHERE c.id=%s AND r.project_id=%s AND c.post_id=%s""",
        (metric_id,project_id,row["video_id"]),
    ))
    if score is None or row["breakout_score"] is None:
        raise ContractError("analytics_score_unverified","Original complete C7 missing")
    return PersistedC7Handoff(
        project_id=project_id,
        run_id=run_id if run_id is not None else str(row["completed_run_id"]),
        video_id=row["video_id"],calculated_metric_id=metric_id,
        snapshot_id=row["snapshot_id"],
        previous_snapshot_id=row["previous_snapshot_id"],
        published_metadata_id=row["published_metadata_id"],
        calculation_config_id=row["calculation_config_id"],
        source_id=row["source_id"],
        source_policy_version_id=row["source_policy_version_id"],
        metric_definition=row["source_metric_definition"],
        metric_version=row["source_metric_version"],
        creator_cohort_id=score["creator_cohort_id"],
        prior_cohort_id=score["prior_cohort_id"],
        niche_cohort_id=score["niche_cohort_id"],
        creator_freeze_id=row["creator_freeze_id"],
        prior_freeze_id=row["prior_freeze_id"],
        niche_freeze_id=row["niche_freeze_id"],
        review_request_id=str(row["review_request_id"]),
        review_decision_id=str(score["review_decision_id"]),
        activation_event_id=str(score["activation_event_id"]),
        evidence_sha256=row["evidence_sha256"],
        breakout_score=row["breakout_score"],
        score_creator_component=score["score_creator_component"],
        score_growth_component=score["score_growth_component"],
        score_engagement_component=score["score_engagement_component"],
        score_freshness_component=score["score_freshness_component"],
    )


def _event_payload(h: PersistedC7Handoff) -> dict:
    return {
        "schema_version":OUTBOX_EVENT_TYPE,
        "handoff_version":ANALYTICS_HANDOFF_VERSION,
        "project_id":h.project_id,"run_id":h.run_id,
        "video_id":h.video_id,
        "calculated_metric_id":h.calculated_metric_id,
        "source_id":h.source_id,
        "source_policy_version_id":h.source_policy_version_id,
        "metric_definition":h.metric_definition,
        "metric_version":h.metric_version,
        "snapshot_id":h.snapshot_id,
        "previous_snapshot_id":h.previous_snapshot_id,
        "published_metadata_id":h.published_metadata_id,
        "calculation_config_id":h.calculation_config_id,
        "creator_cohort_id":h.creator_cohort_id,
        "prior_cohort_id":h.prior_cohort_id,
        "niche_cohort_id":h.niche_cohort_id,
        "creator_freeze_id":h.creator_freeze_id,
        "prior_freeze_id":h.prior_freeze_id,
        "niche_freeze_id":h.niche_freeze_id,
        "review_request_id":h.review_request_id,
        "review_decision_id":h.review_decision_id,
        "activation_event_id":h.activation_event_id,
        "evidence_sha256":h.evidence_sha256,
        "breakout_score":str(h.breakout_score),
        "score_creator_component":str(h.score_creator_component),
        "score_growth_component":str(h.score_growth_component),
        "score_engagement_component":str(h.score_engagement_component),
        "score_freshness_component":str(h.score_freshness_component),
    }


def _validate_outbox_payload(row: Mapping[str,Any],h: PersistedC7Handoff) -> None:
    """Pin existing GC22 revision events OR new baseline events to same C7.

    The outbox body is hashed immutably, and must match the current actual
    certified score identity and original SQL numeric value. GC22 had an
    older revision event schema without all component numbers; the sink reads
    these only from persisted C7 via _persisted, never from event projections.
    """
    payload=row.get("payload")
    if (type(payload) is not dict
            or canonical_hash(payload)!=row.get("payload_sha256")
            or row.get("event_type")!=OUTBOX_EVENT_TYPE
            or row.get("project_id")!=h.project_id
            or str(row.get("run_id"))!=h.run_id
            or row.get("video_id")!=h.video_id
            or row.get("calculated_metric_id")!=h.calculated_metric_id):
        raise ContractError("analytics_outbox_conflict","Outbox identity/hash differs")
    expected=_event_payload(h)
    required=("project_id","run_id","video_id","calculated_metric_id",
              "source_id","source_policy_version_id","metric_definition",
              "metric_version","snapshot_id","previous_snapshot_id",
              "published_metadata_id","calculation_config_id",
              "creator_freeze_id","prior_freeze_id","niche_freeze_id",
              "review_request_id","review_decision_id","activation_event_id",
              "evidence_sha256","breakout_score")
    for key in required:
        if payload.get(key)!=expected[key]:
            raise ContractError("analytics_outbox_conflict",
                                "Outbox event does not match certified C7: "+key)
    for optional in ("creator_cohort_id","prior_cohort_id","niche_cohort_id",
                     "score_creator_component","score_growth_component",
                     "score_engagement_component","score_freshness_component"):
        if optional in payload and payload[optional]!=expected[optional]:
            raise ContractError("analytics_outbox_conflict",
                                "Outbox optional C7 data differs: "+optional)
    if payload.get("schema_version")!=OUTBOX_EVENT_TYPE:
        raise ContractError("analytics_outbox_conflict","Unsupported outbox event schema")


class PostgreSQLAnalyticsBridge:
    """No background scheduler or network. Client drives one transactional step."""

    def enqueue_baseline(
        self,conn:Any,*,project_id:str,calculated_metric_id:int,
        run_id:str|None=None,
        transaction_managed:bool=False,
    ) -> AnalyticsOutboxReceipt:
        """Publish original C7 into same committed transaction as GC21 score.

        GC22 revised scores already create their own event; caller must not
        create a second one. Only complete runs and certified C7 are accepted.
        """
        _tx(conn)
        _project(project_id)
        _id(calculated_metric_id,"calculated_metric_id")
        if type(transaction_managed) is not bool:
            raise ContractError("invalid_transaction_mode","Expected bool")
        if transaction_managed:
            _require_serializable(conn)
        with (nullcontext() if transaction_managed else conn.transaction()):
            if not transaction_managed:
                conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            h=_persisted(conn,project_id=project_id,
                         metric_id=calculated_metric_id,run_id=run_id)
            existing=_record(conn.execute(
                """SELECT id,event_key,payload_sha256,payload,event_type,
                          run_id,project_id,video_id,calculated_metric_id,state
                     FROM group_c_outbox
                    WHERE project_id=%s AND run_id=%s::uuid
                      AND calculated_metric_id=%s AND event_type=%s""",
                (project_id,h.run_id,calculated_metric_id,OUTBOX_EVENT_TYPE),
            ))
            if existing is not None:
                _validate_outbox_payload(existing,h)
                return AnalyticsOutboxReceipt(
                    project_id=project_id,calculated_metric_id=calculated_metric_id,
                    outbox_id=str(existing["id"]),event_key=existing["event_key"],
                    payload_sha256=existing["payload_sha256"],replayed=True,
                    state=existing["state"],
                )
            # Never invent a baseline from an actual planned GC22 revision:
            # that revision already enqueues a stronger old→new lineage event.
            if conn.execute(
                "SELECT EXISTS(SELECT 1 FROM group_c_revisions "
                "WHERE project_id=%s AND run_id=%s::uuid)",
                (project_id,h.run_id),
            ).fetchone()[0]:
                raise ContractError("analytics_revision_event_required",
                                    "GC22 revision must use its exact revision outbox")
            payload=_event_payload(h)
            digest=canonical_hash(payload)
            event_key="c7-initial:"+h.run_id
            record=_record(conn.execute(
                """INSERT INTO group_c_outbox (
                     project_id,run_id,video_id,calculated_metric_id,
                     event_type,event_key,payload_sha256,payload)
                   VALUES(%s,%s::uuid,%s,%s,%s,%s,%s,%s::jsonb)
                   ON CONFLICT DO NOTHING RETURNING id""",
                (project_id,h.run_id,h.video_id,calculated_metric_id,
                 OUTBOX_EVENT_TYPE,event_key,digest,
                 json.dumps(payload,sort_keys=True,separators=(",",":"))),
            ))
            if record is None:
                # Idempotent concurrent call must prove EXACT same event.
                old=_record(conn.execute(
                    """SELECT id,event_key,payload_sha256,payload,event_type,
                              run_id,project_id,video_id,calculated_metric_id,state
                         FROM group_c_outbox
                        WHERE project_id=%s AND run_id=%s::uuid
                          AND calculated_metric_id=%s AND event_type=%s""",
                    (project_id,h.run_id,calculated_metric_id,OUTBOX_EVENT_TYPE),
                ))
                if old is None:
                    raise ContractError("analytics_outbox_conflict",
                                        "Conflicting event key/identity")
                _validate_outbox_payload(old,h)
                if old["event_key"]!=event_key:
                    raise ContractError("analytics_outbox_conflict","Baseline event key changed")
                return AnalyticsOutboxReceipt(
                    project_id=project_id,calculated_metric_id=calculated_metric_id,
                    outbox_id=str(old["id"]),event_key=event_key,
                    payload_sha256=old["payload_sha256"],replayed=True,
                    state=old["state"],
                )
            return AnalyticsOutboxReceipt(
                project_id=project_id,calculated_metric_id=calculated_metric_id,
                outbox_id=str(record["id"]),event_key=event_key,
                payload_sha256=digest,replayed=False,
            )

    def load_persisted(
        self,conn:Any,*,project_id:str,calculated_metric_id:int,
        ports:GroupCPorts|None=None,
        transaction_managed:bool=False,
    ) -> PersistedC7Handoff:
        """Safe Analytics-scoped read of certified SQL numbers, NEVER C7 math."""
        _tx(conn)
        _project(project_id)
        _id(calculated_metric_id,"calculated_metric_id")
        adapters=_ports(ports)
        if type(transaction_managed) is not bool:
            raise ContractError("invalid_transaction_mode","Expected bool")
        if transaction_managed:
            _require_serializable(conn)
        with (nullcontext() if transaction_managed else conn.transaction()):
            if not transaction_managed:
                conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE, READ ONLY")
            h=_persisted(conn,project_id=project_id,metric_id=calculated_metric_id)
            row=_certified(conn,project_id=project_id,calculated_metric_id=calculated_metric_id)
            _validate_current(conn,row,adapters)
            return h

    def deliver_one(
        self,conn:Any,*,project_id:str,
        ports:GroupCPorts|None,
    ) -> AnalyticsDeliveryReceipt | None:
        """One atomic DB-to-DB handoff, never an external HTTP request.

        The sink must INSERT durable Analytics-side reception using *conn*.
        ACK may only be returned if it has done so in the SAME transaction.
        SKIP LOCKED fences parallel dispatchers; source rights checked again
        after acquiring the outbox lock, immediately before sink invocation.
        """
        _tx(conn)
        _project(project_id)
        adapters=_ports(ports)
        if adapters.analytics is None:
            raise ContractError("dependency_unavailable","Analytics adapter not connected")
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            # Check dependency BEFORE touching an outbox row.
            analytics=adapters.require("analytics",conn)
            row=_record(conn.execute(
                """SELECT id,project_id,run_id,video_id,calculated_metric_id,
                          event_type,event_key,payload_sha256,payload,state
                   FROM group_c_outbox
                  WHERE project_id=%s AND state='pending'
                  ORDER BY created_at,id
                  LIMIT 1 FOR UPDATE SKIP LOCKED""",
                (project_id,),
            ))
            if row is None:
                return None
            metric_id=row["calculated_metric_id"]
            h=_persisted(conn,project_id=project_id,metric_id=metric_id,
                         run_id=str(row["run_id"]))
            _validate_outbox_payload(row,h)
            cert=_certified(conn,project_id=project_id,calculated_metric_id=metric_id)
            try:
                _validate_current(conn,cert,adapters)
            except ContractError as exc:
                # Source revoked/expired/superseded, so never emit score.
                # Mark queued event non-dispatchable without changing payload.
                conn.execute(
                    "UPDATE group_c_outbox SET state='failed',"
                    "delivery_attempts=delivery_attempts+1 WHERE id=%s",
                    (row["id"],),
                )
                return AnalyticsDeliveryReceipt(
                    project_id=project_id,calculated_metric_id=metric_id,
                    outbox_id=str(row["id"]),analytics_snapshot_id=None,
                    state="blocked",reason_code=exc.code,
                )
            # Existing GroupCPorts.analytics protocol requires a DB-local
            # idempotent sink. A port claiming AVAILABLE but doing a remote
            # call would violate this shared-transaction integration contract.
            ack=analytics.enqueue_persisted_result(
                conn,project_id,metric_id,row["payload_sha256"],
            )
            if not isinstance(ack,Mapping):
                raise ContractError("analytics_ack_invalid","Typed durable Analytics ACK required")
            if (ack.get("accepted") is not True
                    or ack.get("project_id")!=project_id
                    or ack.get("calculated_metric_id")!=metric_id
                    or ack.get("idempotency_sha256")!=row["payload_sha256"]
                    or type(ack.get("analytics_snapshot_id")) is not int
                    or ack["analytics_snapshot_id"]<=0):
                raise ContractError("analytics_ack_invalid",
                                    "Analytics must ACK exact persisted C7/lineage and durable ID")
            updated=conn.execute(
                """UPDATE group_c_outbox SET state='delivered',
                     delivered_at=clock_timestamp(),
                     delivery_attempts=delivery_attempts+1
                   WHERE id=%s AND project_id=%s AND state='pending'
                   RETURNING id""",(row["id"],project_id),
            ).fetchone()
            if updated is None:
                raise ContractError("analytics_outbox_conflict",
                                    "Outbox was mutated before commit")
            return AnalyticsDeliveryReceipt(
                project_id=project_id,calculated_metric_id=metric_id,
                outbox_id=str(row["id"]),
                analytics_snapshot_id=ack["analytics_snapshot_id"],
                state="delivered",
            )
