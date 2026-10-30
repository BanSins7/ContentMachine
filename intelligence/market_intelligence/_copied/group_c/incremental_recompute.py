"""GC-MOD-22: audit-backed incremental C7 recompute, no historical score rewrite.

A new version is never invented. Each revision requires a NEW, already accepted
GC04 QDP run and its original queued score job, an existing certified complete
score, a current signed review/activation and real D1 source observations.
The GC21 score worker consumes that run; after atomic GC20 completion it also
enqueues the versioned Analytics outbox event within the SAME transaction.
Delivery belongs to GC25. The old complete score remains unchanged.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from .c0 import validate_c0_pair, validate_c0_snapshot
from .c7_persistence import _reviewed
from .contracts import ContractError, canonical_hash
from .d1 import _record, local_d1_ports, read_scoped_d1_bundle
from .ports import GroupCPorts
from .publication_time import validate_publication_time

RECOMPUTE_VERSION = "group-c-incremental-recompute/v1"
OUTBOX_VERSION = "group-c.result_ready/v1"
_KINDS = frozenset({
    "new_snapshot", "config_changed", "cohort_changed", "publication_correction",
})


@dataclass(frozen=True)
class RevisionPlan:
    revision_id: str
    project_id: str
    run_id: str
    video_id: int
    previous_calculated_metric_id: int
    revision_kind: str
    evidence_sha256: str
    score_job_id: int
    replayed: bool = False
    schema_version: str = RECOMPUTE_VERSION
    status: str = "revision_planned_unpersisted_score"

    @property
    def is_score_complete(self) -> bool:
        return False


@dataclass(frozen=True)
class RevisionOutbox:
    revision_id: str
    run_id: str
    calculated_metric_id: int
    previous_calculated_metric_id: int
    outbox_id: str
    event_key: str
    payload_sha256: str
    inserted: bool
    schema_version: str = RECOMPUTE_VERSION
    state: str = "pending_analytics_delivery"


def _uuid(value: Any, name: str) -> str:
    if type(value) is not str:
        raise ContractError("invalid_revision_id", "Canonical UUID required: "+name)
    try:
        ident = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ContractError("invalid_revision_id", "Canonical UUID required: "+name) from exc
    if str(ident) != value:
        raise ContractError("invalid_revision_id", "Canonical lowercase UUID required: "+name)
    return value


def _int(value: Any, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ContractError("invalid_revision_id", name + " must be exact positive integer")
    return value


def determine_revision_kind(before: Mapping[str,Any],
                            after: Mapping[str,Any]) -> str:
    """Classify *documented* lineage changes, never guess a revised metric.

    The ordering captures simultaneous changes: a genuinely new raw snapshot
    supersedes other changes; metadata correction takes priority over cohort
    and config; changed approved cohorts take priority over config-only changes.
    """
    if not isinstance(before,Mapping) or not isinstance(after,Mapping):
        raise ContractError("invalid_revision_evidence", "Structured old/new lineage required")
    mandatory=("project_id","video_id","source_id","snapshot_id",
               "previous_snapshot_id","metadata_id","config_id",
               "creator_cohort_id","prior_cohort_id","niche_cohort_id")
    for key in mandatory:
        if key not in before or key not in after:
            raise ContractError("invalid_revision_evidence", "Explicit lineage: "+key)
    for row in (before,after):
        if (type(row["project_id"]) is not str
                or len(row["project_id"])!=32
                or any(ch not in "0123456789abcdef" for ch in row["project_id"])
                or type(row["source_id"]) is not str or not row["source_id"].strip()):
            raise ContractError("invalid_revision_evidence", "Canonical project/source required")
        for key in ("video_id","snapshot_id","previous_snapshot_id",
                    "metadata_id","config_id","creator_cohort_id","prior_cohort_id"):
            _int(row[key],key)
        if row["niche_cohort_id"] is not None:
            _int(row["niche_cohort_id"],"niche_cohort_id")
        if row["snapshot_id"]==row["previous_snapshot_id"]:
            raise ContractError("revision_history_mismatch","Distinct raw snapshot pair required")
    if any(before[k]!=after[k] for k in ("project_id","video_id","source_id")):
        raise ContractError("revision_identity_mismatch", "Cannot cross projects/videos/source")
    if before["snapshot_id"] != after["snapshot_id"]:
        if after["previous_snapshot_id"] != before["snapshot_id"]:
            raise ContractError("revision_history_mismatch", "New raw observation must continue prior snapshot")
        return "new_snapshot"
    if before["previous_snapshot_id"] != after["previous_snapshot_id"]:
        raise ContractError("revision_history_mismatch","Cannot silently replace original history pair")
    if before["metadata_id"] != after["metadata_id"]:
        return "publication_correction"
    if any(before[k]!=after[k] for k in (
        "creator_cohort_id","prior_cohort_id","niche_cohort_id",
    )):
        return "cohort_changed"
    if before["config_id"] != after["config_id"]:
        return "config_changed"
    raise ContractError("revision_version_required",
                        "No versioned raw/config/cohort/metadata change; cannot overwrite complete C7")


def _version_key(row: Mapping[str,Any]) -> tuple:
    """Authoritative 003 unique NULLS NOT DISTINCT C7 lineage."""
    return tuple(row[k] for k in (
        "snapshot_id","config_id","previous_snapshot_id",
        "creator_cohort_id","niche_cohort_id","prior_cohort_id",
    ))


def _tx(conn: Any) -> None:
    if getattr(conn,"autocommit",None) is not True:
        raise ContractError("transaction_contract_unavailable",
                            "GC22 requires independent autocommit connection")


def _approved_adapter(ports: GroupCPorts | None) -> GroupCPorts:
    if ports is not None and not isinstance(ports,GroupCPorts):
        raise ContractError("invalid_ports","Compatible D1/registry/source_policy ports required")
    return ports if ports is not None else local_d1_ports()


def _certified_previous(conn: Any, project_id: str, video_id: int,
                        metric_id: int) -> dict:
    row = _record(conn.execute(
        """SELECT c.id,c.post_id AS video_id,c.snapshot_id,
                  c.previous_snapshot_id,c.published_metadata_id AS metadata_id,
                  c.calculation_config_id AS config_id,
                  c.creator_cohort_id,c.niche_cohort_id,c.prior_cohort_id,
                  c.score_status,c.calculation_status,
                  a.project_id,a.evidence_sha256,a.review_request_id,
                  a.creator_freeze_id,a.prior_freeze_id,a.niche_freeze_id,
                  s.source_id,s.source_metric_version AS metric_version,
                  s.source_policy_version_id
             FROM calculated_metrics c
             JOIN group_c_calculation_receipts a
               ON a.calculated_metric_id=c.id
             JOIN metric_snapshots s ON s.id=c.snapshot_id
             WHERE c.id=%s AND c.post_id=%s AND a.project_id=%s
               AND a.video_id=%s""",
        (metric_id,video_id,project_id,video_id),
    ))
    if (row is None or row["score_status"]!="complete"
            or row["calculation_status"]!="complete"
            or not isinstance(row["evidence_sha256"],str)
            or len(row["evidence_sha256"])!=64):
        raise ContractError("previous_complete_missing",
                            "Exact certified previous complete C7 is required")
    old_run=_record(conn.execute(
        """SELECT id FROM group_c_runs
            WHERE project_id=%s AND video_id=%s
              AND calculated_metric_id=%s AND state='completed'
            ORDER BY created_at DESC,id DESC LIMIT 1""",
        (project_id,video_id,metric_id),
    ))
    if old_run is None:
        raise ContractError("previous_complete_missing",
                            "Previous score must belong to a completed original Group C run")
    row["completed_run_id"]=str(old_run["id"])
    return row


def _read_revision(conn: Any, project_id: str, run_id: str) -> dict | None:
    return _record(conn.execute(
        """SELECT id,project_id,run_id,video_id,revision,revision_kind,
                  evidence_sha256,evidence,previous_calculated_metric_id
             FROM group_c_revisions WHERE project_id=%s AND run_id=%s::uuid
             ORDER BY revision LIMIT 1""",
        (project_id,run_id),
    ))


def _revision_digest(payload: dict) -> str:
    return canonical_hash(payload)


def validate_revision_record(row: Mapping[str,Any], *,
                             project_id: str, run_id: str,
                             video_id: int) -> dict:
    """Check the original DB revision and its canonical wire evidence.

    A hash is only tamper-evidence, not an authorization. Caller still
    performs current DB/IAM verification at score commit.
    """
    if not isinstance(row,Mapping) or not isinstance(row.get("evidence"),dict):
        raise ContractError("revision_conflict","Typed revision evidence required")
    payload=row["evidence"]
    if (row.get("revision")!=1
            or row.get("revision_kind") not in _KINDS
            or row.get("project_id")!=project_id
            or str(row.get("run_id"))!=run_id
            or row.get("video_id")!=video_id
            or payload.get("schema_version")!=RECOMPUTE_VERSION
            or payload.get("project_id")!=project_id
            or payload.get("run_id")!=run_id
            or payload.get("video_id")!=video_id
            or payload.get("revision_kind")!=row["revision_kind"]
            or payload.get("previous_calculated_metric_id")!=row.get("previous_calculated_metric_id")
            or _revision_digest(payload)!=row.get("evidence_sha256")):
        raise ContractError("revision_conflict","Original revision identity or evidence changed")
    return payload


class PostgreSQLIncrementalRecompute:
    """Synchronous event intake and atomic result-ready outbox insertion."""

    def plan(
        self, conn: Any, *, project_id: str, run_id: str,
        previous_calculated_metric_id: int, revision_kind: str,
        review_request_id: str, ports: GroupCPorts | None = None,
    ) -> RevisionPlan:
        """Adopt an already accepted NEW QDP run; NEVER fake an event/run/job.

        Exactly one revision per new QDP run; repeated identical planning is
        idempotent. Review/rights are current and must be independently checked
        again by GC20 at actual score commit.
        """
        _tx(conn)
        _uuid(run_id,"run_id")
        _uuid(review_request_id,"review_request_id")
        _int(previous_calculated_metric_id,"previous_calculated_metric_id")
        if type(project_id) is not str or len(project_id)!=32 or any(
            ch not in "0123456789abcdef" for ch in project_id
        ):
            raise ContractError("invalid_project_id","Canonical project required")
        if type(revision_kind) is not str or revision_kind not in _KINDS:
            raise ContractError("invalid_revision_kind","Only four score recompute causes supported")
        adapters=_approved_adapter(ports)
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            run=_record(conn.execute(
                """SELECT id,project_id,video_id,inbox_event_id,
                          qdp_input_sha256,source_id,source_policy_version_id,
                          snapshot_id,previous_snapshot_id,published_metadata_id,
                          calculation_config_id,state
                     FROM group_c_runs
                    WHERE project_id=%s AND id=%s::uuid FOR UPDATE""",
                (project_id,run_id),
            ))
            if run is None:
                raise ContractError("revision_run_missing","A new existing QDP run is required")
            prior=_certified_previous(
                conn,project_id,run["video_id"],previous_calculated_metric_id,
            )
            if (prior["completed_run_id"]==run_id
                    or prior["source_id"]!=run["source_id"]):
                raise ContractError("revision_identity_mismatch",
                                    "Revision cannot reuse original run or change source")
            job=_record(conn.execute(
                """SELECT id,state,attempts,job_kind
                     FROM group_c_jobs WHERE project_id=%s AND run_id=%s::uuid
                       AND video_id=%s AND job_kind='score'
                     FOR UPDATE""",
                (project_id,run_id,run["video_id"]),
            ))
            if job is None:
                raise ContractError("revision_job_missing",
                                    "An already durably QDP-enqueued score job is mandatory")
            existing=_read_revision(conn,project_id,run_id)
            if existing is not None:
                payload=validate_revision_record(
                    existing,project_id=project_id,run_id=run_id,
                    video_id=run["video_id"],
                )
                if (existing["revision_kind"]!=revision_kind
                        or existing["previous_calculated_metric_id"]!=previous_calculated_metric_id
                        or payload.get("review_request_id")!=review_request_id):
                    raise ContractError("revision_conflict",
                                        "Same QDP run cannot represent a different revision")
                return RevisionPlan(
                    revision_id=str(existing["id"]),project_id=project_id,
                    run_id=run_id,video_id=run["video_id"],
                    previous_calculated_metric_id=previous_calculated_metric_id,
                    revision_kind=revision_kind,
                    evidence_sha256=existing["evidence_sha256"],
                    score_job_id=job["id"],replayed=True,
                )
            if run["state"]!="queued" or job["state"]!="queued" or job["attempts"]!=0:
                raise ContractError("revision_run_not_queued",
                                    "Revision must be planned before worker claim")
            inbox=_record(conn.execute(
                """SELECT state,video_id,input_sha256 FROM group_c_inbox
                    WHERE project_id=%s AND event_id=%s::uuid""",
                (project_id,str(run["inbox_event_id"])),
            ))
            if (inbox is None or inbox["state"]!="accepted"
                    or inbox["video_id"]!=run["video_id"]
                    or inbox["input_sha256"]!=run["qdp_input_sha256"]):
                raise ContractError("revision_qdp_not_accepted",
                                    "Only accepted original QDP C handoffs are eligible")
            if any(run[key] is None for key in (
                    "snapshot_id","previous_snapshot_id",
                    "published_metadata_id","calculation_config_id",
                    "source_policy_version_id",
            )):
                raise ContractError("revision_run_incomplete",
                                    "QDP must pin full new source/version lineage")
            bundle=read_scoped_d1_bundle(
                conn,project_id,run["video_id"],
                (run["previous_snapshot_id"],run["snapshot_id"]),
                run["published_metadata_id"],ports=adapters,
            )
            if (bundle["source_id"]!=run["source_id"] or
                    bundle["source_policy_version_id"]!=run["source_policy_version_id"]):
                raise ContractError("revision_identity_mismatch","Current D1 source/policy differs")
            before,current=bundle["snapshots"]
            before=validate_c0_snapshot(
                before,video_id=run["video_id"],source_id=run["source_id"],
                source_policy_version_id=run["source_policy_version_id"],
                metric_definition="platform_post_views",
                metric_version=current["source_metric_version"],
                require_score_fields=False,
            )
            now=validate_c0_snapshot(
                current,video_id=run["video_id"],source_id=run["source_id"],
                source_policy_version_id=run["source_policy_version_id"],
                metric_definition="platform_post_views",
                metric_version=current["source_metric_version"],
                require_score_fields=True,
            )
            validate_c0_pair(before,now,require_score_fields=True)
            latest=adapters.require("d1",conn).read_recent_raw_observations(
                conn,run["video_id"],run["source_id"],2,
            )
            if [r.get("id") for r in latest]!=[
                    run["snapshot_id"],run["previous_snapshot_id"]]:
                raise ContractError("metric_history_changed","Only latest two unfiltered raw snapshots")
            proof=validate_publication_time(
                bundle["metadata"],video_id=run["video_id"],
                metadata_id=run["published_metadata_id"],
                source_id=run["source_id"],
                source_policy_version_id=run["source_policy_version_id"],
                previous_snapshot_id=run["previous_snapshot_id"],
                snapshot_id=run["snapshot_id"],
                previous_observed_at=before.observed_at,observed_at=now.observed_at,
            )
            review,config,subject,activation,creator,priors,niche=_reviewed(
                conn,project_id=project_id,video_id=run["video_id"],
                source_id=run["source_id"],policy_id=run["source_policy_version_id"],
                metric_version=current["source_metric_version"],
                config_id=run["calculation_config_id"],
                review_request_id=review_request_id,adapters=adapters,
            )
            # New frozen cohorts are already explicitly reviewed, but the
            # next GC20 worker must still revalidate all original members.
            candidate=dict(
                project_id=project_id,video_id=run["video_id"],
                source_id=run["source_id"],
                snapshot_id=run["snapshot_id"],
                previous_snapshot_id=run["previous_snapshot_id"],
                metadata_id=run["published_metadata_id"],
                config_id=run["calculation_config_id"],
                creator_cohort_id=creator["legacy_cohort_id"],
                prior_cohort_id=priors["legacy_cohort_id"],
                niche_cohort_id=niche["legacy_cohort_id"] if niche else None,
            )
            expected=determine_revision_kind(prior,candidate)
            if expected!=revision_kind:
                raise ContractError("revision_kind_mismatch",
                                    "Revision cause disagrees with exact source/config/cohort change")
            if _version_key(prior)==_version_key(candidate):
                # In-place publication correction alone cannot make a NEW
                # UNIQUE NULLS NOT DISTINCT calculated_metrics row. Explicit
                # new approved config/cohort or raw version is required.
                raise ContractError("revision_version_required",
                                    "Publication correction needs distinct immutable score lineage")
            payload=dict(
                schema_version=RECOMPUTE_VERSION,
                project_id=project_id,run_id=run_id,
                video_id=run["video_id"],qdp_event_id=str(run["inbox_event_id"]),
                qdp_input_sha256=run["qdp_input_sha256"],
                revision_kind=revision_kind,
                previous_calculated_metric_id=previous_calculated_metric_id,
                previous_calculation_evidence_sha256=prior["evidence_sha256"],
                previous_run_id=prior["completed_run_id"],
                previous_snapshot_id=run["previous_snapshot_id"],
                snapshot_id=run["snapshot_id"],
                published_metadata_id=run["published_metadata_id"],
                source_id=run["source_id"],
                source_policy_version_id=run["source_policy_version_id"],
                metric_definition="platform_post_views",
                metric_version=current["source_metric_version"],
                config_id=run["calculation_config_id"],
                review_request_id=review_request_id,
                review_subject_sha256=subject.subject_sha256,
                review_decision_id=str(review["decision_id"]),
                activation_event_id=str(activation["id"]),
                creator_freeze_id=creator["id"],
                prior_freeze_id=priors["id"],
                niche_freeze_id=niche["id"] if niche else None,
                new_publication_evidence_sha256=proof.evidence_sha256,
            )
            digest=_revision_digest(payload)
            revision_id=str(uuid4())
            conn.execute(
                """INSERT INTO group_c_revisions (
                     id,project_id,run_id,video_id,revision,revision_kind,
                     evidence_sha256,evidence,previous_calculated_metric_id)
                   VALUES (%s::uuid,%s,%s::uuid,%s,1,%s,%s,%s::jsonb,%s)""",
                (revision_id,project_id,run_id,run["video_id"],
                 revision_kind,digest,
                 json.dumps(payload,sort_keys=True,separators=(",",":")),
                 previous_calculated_metric_id),
            )
            return RevisionPlan(
                revision_id=revision_id,project_id=project_id,
                run_id=run_id,video_id=run["video_id"],
                previous_calculated_metric_id=previous_calculated_metric_id,
                revision_kind=revision_kind,evidence_sha256=digest,
                score_job_id=job["id"],
            )

    def enqueue_ready(
        self, conn: Any, *, project_id: str, run_id: str,
        ports: GroupCPorts | None = None,
        transaction_managed: bool = False,
    ) -> RevisionOutbox | None:
        """After complete C7: append exact revision outbox in same transaction.

        When called by GC21 within its locked SERIALIZABLE transaction, no
        separate commit is allowed. The trigger requires already completed
        Group C run with the matching complete calculated_metrics ID.
        """
        _tx(conn)
        _uuid(run_id,"run_id")
        if type(project_id) is not str or len(project_id)!=32 or any(
            ch not in "0123456789abcdef" for ch in project_id):
            raise ContractError("invalid_project_id","Canonical project ID required")
        if type(transaction_managed) is not bool:
            raise ContractError("transaction_contract_unavailable","Boolean transaction mode required")
        adapters=_approved_adapter(ports)
        from contextlib import nullcontext
        if transaction_managed:
            from psycopg.pq import TransactionStatus
            if conn.info.transaction_status!=TransactionStatus.INTRANS:
                raise ContractError("transaction_contract_unavailable",
                                    "Caller must own a transaction")
            if conn.execute("SHOW transaction_isolation").fetchone()[0]!="serializable":
                raise ContractError("transaction_contract_unavailable",
                                    "Outbox must use same SERIALIZABLE score transaction")
        with (nullcontext() if transaction_managed else conn.transaction()):
            if not transaction_managed:
                conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            run=_record(conn.execute(
                """SELECT id,project_id,video_id,source_id,
                          snapshot_id,previous_snapshot_id,published_metadata_id,
                          calculation_config_id,source_policy_version_id,
                          calculated_metric_id,state
                   FROM group_c_runs WHERE project_id=%s AND id=%s::uuid
                   FOR UPDATE""",
                (project_id,run_id),
            ))
            if run is None:
                raise ContractError("revision_run_missing","Exact QDP run required")
            revision=_read_revision(conn,project_id,run_id)
            if revision is None:
                return None
            evidence=validate_revision_record(
                revision,project_id=project_id,run_id=run_id,
                video_id=run["video_id"],
            )
            if run["state"]!="completed" or run["calculated_metric_id"] is None:
                raise ContractError("revision_not_complete",
                                    "Cannot publish non-complete C7 run")
            new_id=run["calculated_metric_id"]
            if new_id==revision["previous_calculated_metric_id"]:
                raise ContractError("revision_not_new","Cannot claim unchanged old score as revision")
            current=_record(conn.execute(
                """SELECT c.id,c.post_id AS video_id,c.snapshot_id,
                          c.previous_snapshot_id,c.published_metadata_id,
                          c.calculation_config_id,c.creator_cohort_id,
                          c.niche_cohort_id,c.prior_cohort_id,
                          c.score_status,c.calculation_status,c.breakout_score,
                          cr.evidence_sha256,cr.review_request_id,
                          cr.review_decision_id,cr.activation_event_id,
                          cr.review_subject_sha256,cr.calculation_version,
                          cr.creator_freeze_id,cr.prior_freeze_id,cr.niche_freeze_id,
                          s.source_id,s.source_metric_version AS metric_version,
                          s.source_policy_version_id
                     FROM calculated_metrics c
                     JOIN group_c_calculation_receipts cr
                       ON cr.calculated_metric_id=c.id
                     JOIN metric_snapshots s ON s.id=c.snapshot_id
                    WHERE c.id=%s AND cr.project_id=%s
                          AND cr.video_id=%s""",
                (new_id,project_id,run["video_id"]),
            ))
            if current is None or current["score_status"]!="complete" or current["calculation_status"]!="complete":
                raise ContractError("revision_not_complete","Only certified complete C7 can emit outbox")
            if (str(current["review_request_id"])!=evidence["review_request_id"]
                    or current["review_subject_sha256"]!=evidence["review_subject_sha256"]
                    or str(current["review_decision_id"])!=evidence["review_decision_id"]
                    or str(current["activation_event_id"])!=evidence["activation_event_id"]
                    or current["creator_freeze_id"]!=evidence["creator_freeze_id"]
                    or current["prior_freeze_id"]!=evidence["prior_freeze_id"]
                    or current["niche_freeze_id"]!=evidence["niche_freeze_id"]
                    or current["snapshot_id"]!=evidence["snapshot_id"]
                    or current["previous_snapshot_id"]!=evidence["previous_snapshot_id"]
                    or current["published_metadata_id"]!=evidence["published_metadata_id"]
                    or current["calculation_config_id"]!=evidence["config_id"]
                    or current["source_id"]!=run["source_id"]
                    or current["source_policy_version_id"]!=run["source_policy_version_id"]
                    or current["metric_version"]!=evidence["metric_version"]):
                raise ContractError("revision_conflict","Completed score no longer matches pinned revision")
            before=_certified_previous(
                conn,project_id,run["video_id"],revision["previous_calculated_metric_id"],
            )
            if before["evidence_sha256"]!=evidence["previous_calculation_evidence_sha256"]:
                raise ContractError("revision_conflict","Previous score evidence changed")
            bundle=read_scoped_d1_bundle(
                conn,project_id,run["video_id"],
                (run["previous_snapshot_id"],run["snapshot_id"]),
                run["published_metadata_id"],ports=adapters,
            )
            if bundle["source_id"]!=run["source_id"]:
                raise ContractError("revision_identity_mismatch","Current source rights changed")
            latest=adapters.require("d1",conn).read_recent_raw_observations(
                conn,run["video_id"],run["source_id"],2,
            )
            if [r.get("id") for r in latest]!=[run["snapshot_id"],run["previous_snapshot_id"]]:
                raise ContractError("metric_history_changed","Do not publish obsolete source revision")
            payload=dict(
                schema_version=OUTBOX_VERSION,
                project_id=project_id,run_id=run_id,
                video_id=run["video_id"],
                revision_id=str(revision["id"]),
                revision_kind=revision["revision_kind"],
                revision_sha256=revision["evidence_sha256"],
                previous_calculated_metric_id=revision["previous_calculated_metric_id"],
                calculated_metric_id=new_id,
                source_id=run["source_id"],
                source_policy_version_id=run["source_policy_version_id"],
                metric_definition="platform_post_views",
                metric_version=current["metric_version"],
                snapshot_id=current["snapshot_id"],
                previous_snapshot_id=current["previous_snapshot_id"],
                published_metadata_id=current["published_metadata_id"],
                calculation_config_id=current["calculation_config_id"],
                creator_freeze_id=current["creator_freeze_id"],
                prior_freeze_id=current["prior_freeze_id"],
                niche_freeze_id=current["niche_freeze_id"],
                review_request_id=str(current["review_request_id"]),
                review_decision_id=str(current["review_decision_id"]),
                activation_event_id=str(current["activation_event_id"]),
                review_subject_sha256=current["review_subject_sha256"],
                calculation_version=current["calculation_version"],
                evidence_sha256=current["evidence_sha256"],
                breakout_score=str(current["breakout_score"]),
            )
            digest=canonical_hash(payload)
            event_key="c7-revision:"+str(revision["id"])
            generated_id=str(uuid4())
            inserted=_record(conn.execute(
                """INSERT INTO group_c_outbox(
                     id,project_id,run_id,video_id,calculated_metric_id,
                     event_type,event_key,payload_sha256,payload)
                   VALUES (%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s,%s::jsonb)
                   ON CONFLICT DO NOTHING RETURNING id""",
                (generated_id,project_id,run_id,run["video_id"],new_id,
                 OUTBOX_VERSION,event_key,digest,
                 json.dumps(payload,sort_keys=True,separators=(",",":")),
            )))
            if inserted is None:
                previous_event=_record(conn.execute(
                    """SELECT id,calculated_metric_id,event_key,payload_sha256,payload
                         FROM group_c_outbox
                        WHERE project_id=%s AND run_id=%s::uuid
                          AND event_type=%s""",
                    (project_id,run_id,OUTBOX_VERSION),
                ))
                if (previous_event is None
                        or previous_event["event_key"]!=event_key
                        or previous_event["calculated_metric_id"]!=new_id
                        or previous_event["payload_sha256"]!=digest
                        or previous_event["payload"]!=payload):
                    raise ContractError("revision_outbox_conflict",
                                        "An existing result_ready event has different lineage")
            return RevisionOutbox(
                revision_id=str(revision["id"]),run_id=run_id,
                calculated_metric_id=new_id,
                previous_calculated_metric_id=revision["previous_calculated_metric_id"],
                outbox_id=str(inserted["id"] if inserted else previous_event["id"]),
                event_key=event_key,payload_sha256=digest,inserted=inserted is not None,
            )
