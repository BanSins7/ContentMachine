"""GC-MOD-24: fail-closed retention/rights read, audit invalidation, erasure request.

Complete in calculated_metrics means calculation *once* succeeded, not that
historical raw/peer source rights are still valid. Expose no breakout_score
unless every current D1 target and frozen cohort member remains eligible.
The safe reader is necessary but NOT an SQL GRANT/RLS replacement; production
consumers must not query the raw calculated_metrics table directly.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from .contracts import ContractError, canonical_hash
from .creator_ratio import _read_frozen_cohort
from .d1 import _record, local_d1_ports, read_scoped_d1_bundle
from .human_review import _require_db_rights
from .ports import GroupCPorts

RETENTION_VERSION = "group-c-retention/v1"
ERASURE_VERSION = "group-c-erasure-request/v1"
_PROJECT = re.compile(r"[0-9a-f]{32}\Z")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
# Only actual source/retention/peer failures can irreversibly invalidate
# current canonical C7. Supersession is not revocation of historic evidence.
REVOCABLE_CAUSES = frozenset({
    "source_policy_unavailable","source_policy_expired",
    "source_expired_or_retracted","metric_snapshot_expired",
    "metadata_expired","metadata_unavailable","publication_expired",
    "publication_not_current","metric_snapshot_ineligible",
    "counter_decreased","metric_snapshot_unavailable",
    "review_cohort_incompatible","creator_cohort_not_approved",
    "retention_peer_ineligible","d1_lineage_mismatch",
})


def _positive(value: Any, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ContractError("invalid_identifier",name+" must be an exact positive ID")
    return value


def _project(value: str) -> str:
    if type(value) is not str or _PROJECT.fullmatch(value) is None:
        raise ContractError("invalid_project_id","Canonical project ID required")
    return value


def _uuid(value: str) -> str:
    if type(value) is not str:
        raise ContractError("invalid_authorization","Canonical UUID required")
    try:
        val = UUID(value)
    except (TypeError,AttributeError,ValueError) as exc:
        raise ContractError("invalid_authorization","Invalid authorization UUID") from exc
    if str(val)!=value:
        raise ContractError("invalid_authorization","Canonical lowercase UUID required")
    return value


def _transaction_contract(conn: Any) -> None:
    if getattr(conn,"autocommit",None) is not True:
        raise ContractError("transaction_contract_unavailable",
                            "Dedicated autocommit PostgreSQL connection required")


def _ports(ports: GroupCPorts | None) -> GroupCPorts:
    if ports is None:
        return local_d1_ports()
    if not isinstance(ports,GroupCPorts):
        raise ContractError("invalid_ports","Explicit GroupC source-capable ports required")
    return ports


@dataclass(frozen=True)
class RetentionRead:
    project_id: str
    calculated_metric_id: int
    state: str
    reason_code: str | None
    breakout_score: Decimal | None
    evidence_sha256: str | None
    version: str = RETENTION_VERSION

    @property
    def valid(self) -> bool:
        return self.state=="valid_current"


@dataclass(frozen=True)
class ScoreInvalidation:
    id: str
    project_id: str
    calculated_metric_id: int
    reason_code: str
    evidence_sha256: str
    replayed: bool
    state: str = "invalidated"
    version: str = RETENTION_VERSION


@dataclass(frozen=True)
class ErasureRequest:
    id: str
    project_id: str
    calculated_metric_id: int
    authorization_event_id: str
    reason_code: str
    replayed: bool
    state: str = "external_erasure_execution_required"
    version: str = ERASURE_VERSION


class ErasureAuthorityVerifier(Protocol):
    def verify_erasure_request(
        self, conn: Any, *, project_id: str, calculated_metric_id: int,
        authorization_event_id: str, reason_code: str,
        signed_assertion: str,
    ) -> bool: ...


def _certified(conn: Any, *, project_id: str, calculated_metric_id: int) -> dict:
    row=_record(conn.execute(
        """SELECT c.id,c.post_id AS video_id,c.score_status,
                  c.calculation_status,c.breakout_score,
                  c.snapshot_id,c.previous_snapshot_id,
                  c.published_metadata_id,c.calculation_config_id,
                  c.creator_cohort_id,c.niche_cohort_id,c.prior_cohort_id,
                  r.project_id,r.evidence_sha256,r.review_request_id,
                  r.creator_freeze_id,r.niche_freeze_id,r.prior_freeze_id,
                  s.source_id,s.source_policy_version_id,
                  s.source_metric_definition,s.source_metric_version,
                  gr.id AS completed_run_id
           FROM calculated_metrics c
           JOIN group_c_calculation_receipts r
             ON r.calculated_metric_id=c.id
           JOIN metric_snapshots s ON s.id=c.snapshot_id
           JOIN group_c_runs gr
             ON gr.project_id=r.project_id
               AND gr.video_id=r.video_id AND gr.calculated_metric_id=c.id
               AND gr.state='completed'
           WHERE c.id=%s AND r.project_id=%s AND r.video_id=c.post_id
             AND c.score_status='complete'
             AND c.calculation_status='complete'
           ORDER BY gr.created_at DESC,gr.id DESC LIMIT 1""",
        (calculated_metric_id,project_id),
    ))
    if row is None:
        # Missing and cross-project must be indistinguishable to callers.
        raise ContractError("score_not_found","No certified completed Group C score in project")
    return row


def _invalidation(conn: Any, project_id: str, calculated_metric_id: int) -> dict | None:
    return _record(conn.execute(
        """SELECT id,reason_code,evidence_sha256
           FROM group_c_score_invalidations
           WHERE project_id=%s AND calculated_metric_id=%s""",
        (project_id,calculated_metric_id),
    ))


def _validate_current(conn: Any, row: dict, adapters: GroupCPorts) -> None:
    project_id=row["project_id"]
    if _invalidation(conn,project_id,row["id"]) is not None:
        raise ContractError("score_explicitly_invalidated","Earlier permanent invalidation")
    if conn.execute(
        "SELECT EXISTS(SELECT 1 FROM group_c_erasure_requests "
        "WHERE project_id=%s AND calculated_metric_id=%s)",
        (project_id,row["id"]),
    ).fetchone()[0]:
        raise ContractError("score_erasure_requested",
                            "Legally erasure-bound C7 must not be exposed")
    if row["source_metric_definition"]!="platform_post_views":
        raise ContractError("incompatible_metric_definition","Unknown view definition")
    bundle=read_scoped_d1_bundle(
        conn,project_id,row["video_id"],
        (row["previous_snapshot_id"],row["snapshot_id"]),
        row["published_metadata_id"],ports=adapters,
    )
    if (bundle["source_id"]!=row["source_id"]
            or bundle["source_policy_version_id"]!=row["source_policy_version_id"]):
        raise ContractError("source_policy_unavailable","Score source/policy version no longer current")
    raw=adapters.require("d1",conn).read_recent_raw_observations(
        conn,row["video_id"],row["source_id"],2,
    )
    if [r.get("id") for r in raw]!=[
            row["snapshot_id"],row["previous_snapshot_id"]]:
        raise ContractError("retention_version_superseded",
                            "Score is historical, not the currently latest raw pair")
    # Signed freeze IDs and the 003 legacy cohort IDs must still agree.
    freezes=[]
    for field,expected,purpose in (
        ("creator_freeze_id",row["creator_cohort_id"],"creator"),
        ("prior_freeze_id",row["prior_cohort_id"],"engagement_prior"),
        ("niche_freeze_id",row["niche_cohort_id"],"niche"),
    ):
        fid=row[field]
        if fid is None:
            if expected is not None or purpose!="niche":
                raise ContractError("retention_peer_ineligible","Required frozen cohort missing")
            continue
        freeze=_read_frozen_cohort(conn,fid)
        if (freeze["project_id"]!=project_id
                or freeze["target_video_id"]!=row["video_id"]
                or freeze["purpose"]!=purpose
                or freeze["legacy_cohort_id"]!=expected
                or freeze["source_id"]!=row["source_id"]
                or freeze["source_policy_version_id"]!=row["source_policy_version_id"]
                or freeze["source_metric_version"]!=row["source_metric_version"]
                or freeze["state"]!="frozen_unreviewed"):
            raise ContractError("retention_peer_ineligible","Pinned freeze lineage was invalidated")
        freezes.append(freeze)
    # GC11 traverses original peer snapshot+metadata and target provenance
    # checking the current rights/TTL of *every* cohort member.
    _require_db_rights(conn,freezes,adapters)


class PostgreSQLScoreRetention:
    """Synchronous private project-scoped C7 read; does not publish Analytics."""

    def read_current(
        self, conn: Any, *, project_id: str, calculated_metric_id: int,
        ports: GroupCPorts | None = None,
    ) -> RetentionRead:
        _transaction_contract(conn)
        project_id=_project(project_id)
        _positive(calculated_metric_id,"calculated_metric_id")
        adapters=_ports(ports)
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            row=_certified(conn,project_id=project_id,calculated_metric_id=calculated_metric_id)
            try:
                _validate_current(conn,row,adapters)
            except ContractError as exc:
                return RetentionRead(
                    project_id=project_id,calculated_metric_id=calculated_metric_id,
                    state="unavailable",reason_code=exc.code,
                    breakout_score=None,evidence_sha256=None,
                )
            return RetentionRead(
                project_id=project_id,calculated_metric_id=calculated_metric_id,
                state="valid_current",reason_code=None,
                breakout_score=row["breakout_score"],
                evidence_sha256=row["evidence_sha256"],
            )

    def invalidate(
        self, conn: Any, *, project_id: str, calculated_metric_id: int,
        ports: GroupCPorts | None = None,
    ) -> ScoreInvalidation:
        """Persist only current demonstrable loss of source or peer rights."""
        _transaction_contract(conn)
        project_id=_project(project_id)
        _positive(calculated_metric_id,"calculated_metric_id")
        adapters=_ports(ports)
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            row=_certified(conn,project_id=project_id,calculated_metric_id=calculated_metric_id)
            # Serialize invalidation attempts for exact C7 identity.
            conn.execute("SELECT id FROM calculated_metrics WHERE id=%s FOR UPDATE",
                         (calculated_metric_id,))
            existing=_invalidation(conn,project_id,calculated_metric_id)
            if existing is not None:
                return ScoreInvalidation(
                    id=str(existing["id"]),project_id=project_id,
                    calculated_metric_id=calculated_metric_id,
                    reason_code=existing["reason_code"],
                    evidence_sha256=existing["evidence_sha256"],replayed=True,
                )
            try:
                _validate_current(conn,row,adapters)
            except ContractError as exc:
                code=exc.code
            else:
                raise ContractError("score_still_valid","Current C7 cannot be invalidated")
            if code not in REVOCABLE_CAUSES:
                raise ContractError("invalidation_not_proven",
                                    "A temporary or superseded version is not a permanent revoke")
            payload={
                "version":RETENTION_VERSION,
                "project_id":project_id,
                "calculated_metric_id":calculated_metric_id,
                "completed_run_id":str(row["completed_run_id"]),
                "source_id":row["source_id"],
                "source_policy_version_id":row["source_policy_version_id"],
                "snapshot_id":row["snapshot_id"],
                "previous_snapshot_id":row["previous_snapshot_id"],
                "published_metadata_id":row["published_metadata_id"],
                "creator_freeze_id":row["creator_freeze_id"],
                "prior_freeze_id":row["prior_freeze_id"],
                "niche_freeze_id":row["niche_freeze_id"],
                "evidence_sha256":row["evidence_sha256"],
                "reason_code":code,
            }
            digest=canonical_hash(payload)
            added=_record(conn.execute(
                """INSERT INTO group_c_score_invalidations(
                     project_id,video_id,calculated_metric_id,completed_run_id,
                     reason_code,evidence_sha256)
                   VALUES(%s,%s,%s,%s::uuid,%s,%s) RETURNING id""",
                (project_id,row["video_id"],calculated_metric_id,
                 str(row["completed_run_id"]),code,digest),
            ))
            if added is None:
                raise ContractError("invalidation_write_failed","No persistent invalidation")
            # Retain immutable outbox payload, but mark unsent pending/failed
            # items failed so a future adapter cannot claim they are fresh.
            conn.execute(
                """UPDATE group_c_outbox SET state='failed'
                   WHERE project_id=%s AND calculated_metric_id=%s
                     AND state IN ('pending','failed')""",
                (project_id,calculated_metric_id),
            )
            return ScoreInvalidation(
                id=str(added["id"]),project_id=project_id,
                calculated_metric_id=calculated_metric_id,
                reason_code=code,evidence_sha256=digest,replayed=False,
            )

    def request_erasure(
        self, conn: Any, *, project_id: str, calculated_metric_id: int,
        authorization_event_id: str, reason_code: str,
        signed_assertion: str,
        verifier: ErasureAuthorityVerifier | None,
    ) -> ErasureRequest:
        """Records approved NEED for legal erasure, never asserts bytes erased.

        Immutable GC20/GC11/GC12 FKs and triggers make generic physical
        deletion unsafe. External legal erasure executor must determine and
        execute dependency-aware cleanup with dedicated reviewed privileges.
        """
        _transaction_contract(conn)
        project_id=_project(project_id)
        _positive(calculated_metric_id,"calculated_metric_id")
        authorization_event_id=_uuid(authorization_event_id)
        if reason_code not in ("legal_erasure_required","retention_erase_required"):
            raise ContractError("invalid_erasure_reason","Explicit legal/retention basis required")
        if type(signed_assertion) is not str or len(signed_assertion)<32:
            raise ContractError("human_auth_unavailable","Signed erasure action required")
        if verifier is None or not callable(getattr(verifier,"verify_erasure_request",None)):
            raise ContractError("human_auth_unavailable","Trusted external erasure verifier required")
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            row=_certified(conn,project_id=project_id,calculated_metric_id=calculated_metric_id)
            authenticated=verifier.verify_erasure_request(
                conn,project_id=project_id,calculated_metric_id=calculated_metric_id,
                authorization_event_id=authorization_event_id,
                reason_code=reason_code,signed_assertion=signed_assertion,
            )
            if authenticated is not True:
                raise ContractError("human_auth_unavailable","Erasure action not authenticated")
            previous=_record(conn.execute(
                """SELECT id,authorization_event_id,reason_code,authority_assertion_sha256
                   FROM group_c_erasure_requests
                   WHERE project_id=%s AND calculated_metric_id=%s""",
                (project_id,calculated_metric_id),
            ))
            digest=hashlib.sha256(signed_assertion.encode("utf-8")).hexdigest()
            if previous is not None:
                if (str(previous["authorization_event_id"])!=authorization_event_id
                        or previous["reason_code"]!=reason_code
                        or previous["authority_assertion_sha256"]!=digest):
                    raise ContractError("erasure_request_conflict","Exact erasure authorization mismatch")
                return ErasureRequest(
                    id=str(previous["id"]),project_id=project_id,
                    calculated_metric_id=calculated_metric_id,
                    authorization_event_id=authorization_event_id,
                    reason_code=reason_code,replayed=True,
                )
            record=_record(conn.execute(
                """INSERT INTO group_c_erasure_requests(
                    project_id,video_id,calculated_metric_id,
                    authorization_event_id,authority_assertion_sha256,reason_code)
                   VALUES(%s,%s,%s,%s::uuid,%s,%s) RETURNING id""",
                (project_id,row["video_id"],calculated_metric_id,
                 authorization_event_id,digest,reason_code),
            ))
            if record is None:
                raise ContractError("erasure_request_failed","No durable erasure request")
            # Block undelivered notification without changing the original
            # immutable payload, and make all scoped reads deny this C7.
            conn.execute(
                """UPDATE group_c_outbox SET state='failed'
                     WHERE project_id=%s AND calculated_metric_id=%s
                       AND state IN ('pending','failed')""",
                (project_id,calculated_metric_id),
            )
            return ErasureRequest(
                id=str(record["id"]),project_id=project_id,
                calculated_metric_id=calculated_metric_id,
                authorization_event_id=authorization_event_id,
                reason_code=reason_code,replayed=False,
            )
