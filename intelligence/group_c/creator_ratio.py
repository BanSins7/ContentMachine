"""GC-MOD-13 — C1 Creator Ratio with exact approved-source provenance.

Mathematical kernel is independent of all DB/approval objects.
DB adapter RE-CHECKS active config, original review subject and cohort freeze,
current D1 source rights, raw latest C0 observation, project/creator and each
immutable peer ID in ONE repeatable-read snapshot. A trusted external IAM
verifier is required to authorize read/use of signed approval; no default.
No worker, write, QDP ACK, complete score or Analytics publication occurs.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from typing import Any, Protocol

from .c0 import validate_c0_snapshot
from .contracts import ContractError, canonical_hash
from .d1 import _record, _records, local_d1_ports, read_scoped_d1_bundle
from .human_review import (
    _require_db_rights, build_human_review_subject,
)
from .ports import GroupCPorts
from .pure_math import d, q

C1_VERSION = "group-c-creator-ratio/v1"
_METRIC = "platform_post_views"


class CreatorRatioApprovalVerifier(Protocol):
    """External privileged IAM decision/activation validator, no local stub.

    Implementer must re-verify signed human review, current project review
    grant, global activation action/role and non-revocation. Returning True
    without cryptographic verification is unsafe in production.
    """
    def verify_c1_use(
        self, conn: Any, *, project_id: str, video_id: int,
        snapshot_id: int, config_id: int, creator_freeze_id: int,
        subject_sha256: str, decision_id: str, activation_event_id: str,
        reviewer_id: str, identity_provider: str,
        authentication_event_id: str, attestation_sha256: str,
        activation_assertion_sha256: str,
    ) -> bool: ...


@dataclass(frozen=True)
class CreatorRatioResult:
    project_id: str
    video_id: int
    snapshot_id: int
    metadata_id: int
    source_id: str
    source_policy_version_id: int
    metric_version: str
    creator_id: int
    config_id: int
    calculation_version: str
    creator_freeze_id: int
    legacy_creator_cohort_id: int
    review_request_id: str
    review_decision_id: str
    activation_event_id: str
    review_subject_sha256: str
    cohort_evidence_sha256: str
    views: int
    creator_median_views: Decimal
    alpha_used: Decimal
    creator_ratio: Decimal
    evidence_sha256: str
    schema_version: str = C1_VERSION
    status: str = "c1_derived_unpersisted"

    @property
    def ready_for_c7(self) -> bool:
        return False


def _positive(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ContractError("invalid_c1_identifier", "Positive integer "+label+" required")
    return value


def _sha(value: Any, label: str) -> str:
    if type(value) is not str or len(value) != 64 or any(
            c not in "0123456789abcdef" for c in value):
        raise ContractError("invalid_c1_evidence", "Canonical SHA256 required: "+label)
    return value


def compute_creator_ratio(views: int, creator_median_views: Any, alpha: Any) -> Decimal:
    """Pure C1 = (views+alpha)/(frozen_creator_median+alpha), NUMERIC(30,12).

    0 views is a genuine raw observation for C1. This kernel neither
    asserts evidence approvals nor calculates any other metric or score.
    """
    if type(views) is not int or not 0 <= views < 2**63:
        raise ContractError("invalid_raw_counter", "Exact nonnegative BIGINT views required")
    median = d(creator_median_views, "creator_median")
    smooth = d(alpha, "alpha", positive=True)
    # Approved SQL NUMERIC(30,12) inputs must be exact at that scale;
    # silently rounding unsanctioned alpha/median changes the review subject.
    rounded_median, rounded_alpha = q(median), q(smooth)
    if rounded_median != median or rounded_alpha != smooth or rounded_alpha <= 0:
        raise ContractError("invalid_c1_precision",
                            "C1 median and alpha must be exact NUMERIC(30,12)")
    median, smooth = rounded_median, rounded_alpha
    with localcontext() as context:
        context.prec = 80
        denominator = median + smooth
        if denominator <= 0:
            raise ContractError("invalid_c1_denominator", "C1 denominator must be positive")
        return q((Decimal(views) + smooth) / denominator)


def _approved_creator_context(
    *, project_id: str, video_id: int, snapshot_id: int,
    metadata_id: int, creator_id: int,
    source_id: str, source_policy_version_id: int,
    metric_version: str, freeze: Mapping[str, Any],
    cohort_parent: Mapping[str, Any],
    peer_rows: list[Mapping[str, Any]],
) -> None:
    """Pure exact lineage gate; does not itself authorize any DB row."""
    for key,value in (("video_id",video_id),("snapshot_id",snapshot_id),
                      ("metadata_id",metadata_id),("creator_id",creator_id),
                      ("source_policy_version_id",source_policy_version_id)):
        _positive(value,key)
    if type(project_id) is not str or len(project_id)!=32 or any(
            c not in "0123456789abcdef" for c in project_id):
        raise ContractError("invalid_project_id", "Canonical project ID required")
    if type(source_id) is not str or not source_id.strip():
        raise ContractError("invalid_source_id", "Canonical nonblank source required")
    if type(metric_version) is not str or not metric_version.strip():
        raise ContractError("incompatible_metric_version", "Pinned metric version required")
    if not isinstance(freeze, Mapping) or not isinstance(cohort_parent,Mapping):
        raise ContractError("creator_cohort_not_approved","Exact frozen creator evidence required")
    if (freeze.get("project_id")!=project_id or freeze.get("target_video_id")!=video_id
            or freeze.get("target_snapshot_id")!=snapshot_id
            or freeze.get("target_metadata_id")!=metadata_id
            or freeze.get("purpose")!="creator"
            or freeze.get("state")!="frozen_unreviewed"
            or freeze.get("source_id")!=source_id
            or freeze.get("source_policy_version_id")!=source_policy_version_id
            or freeze.get("source_metric_definition")!=_METRIC
            or freeze.get("source_metric_version")!=metric_version):
        raise ContractError("creator_cohort_not_approved",
                            "Creator freeze lineage differs from selected D1 snapshot")
    for name in ("rules_sha256","selection_sha256","evidence_sha256"):
        _sha(freeze.get(name),name)
    if (cohort_parent.get("id")!=freeze.get("legacy_cohort_id")
            or cohort_parent.get("purpose")!="creator"
            or cohort_parent.get("creator_id")!=creator_id
            or cohort_parent.get("source_id")!=source_id
            or cohort_parent.get("source_metric_definition")!=_METRIC
            or cohort_parent.get("is_frozen") is not True
            or cohort_parent.get("median_views")!=freeze.get("median_views")
            or cohort_parent.get("post_count")!=freeze.get("member_count")):
        raise ContractError("incompatible_peer_creator",
                            "Canonical frozen legacy creator stratum differs")
    platform,kind=cohort_parent.get("platform"),cohort_parent.get("content_type")
    if platform not in ("youtube","tiktok","instagram") or kind not in ("video","carousel"):
        raise ContractError("incompatible_peer_kind","Classified platform/kind required")
    if (type(freeze.get("member_count")) is not int or freeze["member_count"]<1
            or len(peer_rows)!=freeze["member_count"]):
        raise ContractError("creator_cohort_not_approved","Frozen membership count mismatch")
    seen=set()
    for row in peer_rows:
        if not isinstance(row,Mapping):
            raise ContractError("creator_cohort_not_approved","Malformed frozen peer")
        vid=_positive(row.get("video_id"),"peer_video_id")
        _positive(row.get("snapshot_id"),"peer_snapshot_id")
        _positive(row.get("metadata_id"),"peer_metadata_id")
        if vid in seen or vid==video_id:
            raise ContractError("self_inclusion","Target or duplicate in creator cohort")
        seen.add(vid)
        if (row.get("project_id")!=project_id or row.get("creator_id")!=creator_id
                or row.get("platform")!=platform or row.get("c_content_type")!=kind
                or row.get("source_id")!=source_id
                or row.get("source_policy_version_id")!=source_policy_version_id
                or row.get("metric_definition")!=_METRIC
                or row.get("metric_version")!=metric_version
                or row.get("source_metric_definition")!=_METRIC
                or row.get("source_metric_version")!=metric_version
                or row.get("raw_snapshot_id")!=row.get("snapshot_id")
                or row.get("raw_views")!=row.get("views")
                or row.get("legacy_video_id")!=vid
                or row.get("legacy_snapshot_id")!=row.get("snapshot_id")):
            raise ContractError("incompatible_peer_creator",
                                "Exact creator/project/view version or peer snapshot changed")


def _read_frozen_cohort(conn: Any, freeze_id: int) -> dict:
    """Read the SAME evidence fields as GC11, without FOR SHARE row locks.

    PostgreSQL forbids SELECT FOR SHARE inside READ ONLY transactions;
    REPEATABLE READ fixes a consistent snapshot without locking rows.
    """
    freeze=_record(conn.execute(
        """SELECT f.id,f.project_id,f.target_video_id,f.source_id,
                  f.source_policy_version_id,f.source_metric_definition,
                  f.source_metric_version,f.purpose,f.state,f.member_count,
                  f.legacy_cohort_id,f.rules_sha256,f.selection_sha256,
                  f.evidence_sha256,f.median_views,f.prior_like_rate,
                  f.prior_comment_rate,f.prior_share_rate,f.target_snapshot_id,
                  f.target_metadata_id,c.platform,c.content_type
             FROM group_c_cohort_freezes f
             JOIN comparison_cohorts c ON c.id=f.legacy_cohort_id
            WHERE f.id=%s""",(_positive(freeze_id,"freeze_id"),),
    ))
    if freeze is None:
        raise ContractError("creator_cohort_not_approved","Exact freeze not found")
    return freeze


class PostgreSQLCreatorRatio:
    """Read-only C1 gate. Never reports a complete production C7 score."""

    def derive(
        self, conn: Any, *, project_id: str, video_id: int,
        snapshot_id: int, metadata_id: int, source_id: str,
        source_policy_version_id: int, metric_version: str,
        config_id: int, creator_freeze_id: int, review_request_id: str,
        approval_verifier: CreatorRatioApprovalVerifier | None,
        ports: GroupCPorts | None = None,
    ) -> CreatorRatioResult:
        if getattr(conn,"autocommit",None) is not True:
            raise ContractError("transaction_contract_unavailable",
                                "Independent autocommit read connection required")
        for field,value in (("video_id",video_id),("snapshot_id",snapshot_id),
                            ("metadata_id",metadata_id),("config_id",config_id),
                            ("creator_freeze_id",creator_freeze_id),
                            ("source_policy_version_id",source_policy_version_id)):
            _positive(value,field)
        if approval_verifier is None or not callable(getattr(approval_verifier,"verify_c1_use",None)):
            raise ContractError("human_auth_unavailable","Current trusted IAM C1-use verifier required")
        adapters=ports if ports is not None else local_d1_ports()
        if not isinstance(adapters,GroupCPorts):
            raise ContractError("invalid_ports","Explicit compatible Group C ports required")
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            bundle=read_scoped_d1_bundle(
                conn,project_id,video_id,(snapshot_id,),metadata_id,ports=adapters,
            )
            if (bundle["source_id"]!=source_id
                    or bundle["source_policy_version_id"]!=source_policy_version_id):
                raise ContractError("different_video_or_source","D1 pinned source/policy changed")
            recent=adapters.require("d1",conn).read_recent_raw_observations(
                conn,video_id,source_id,1,
            )
            if len(recent)!=1 or recent[0].get("id")!=snapshot_id:
                raise ContractError("metric_history_changed",
                                    "Only latest raw D1 observation may be evaluated")
            checked=validate_c0_snapshot(
                bundle["snapshots"][0],video_id=video_id,
                source_id=source_id,source_policy_version_id=source_policy_version_id,
                metric_definition=_METRIC,metric_version=metric_version,
            )
            if checked.views is None:
                raise ContractError("missing_raw_counter","Current C1 views not observed")
            freeze=_read_frozen_cohort(conn,creator_freeze_id)
            row=_record(conn.execute(
                """SELECT r.id,r.project_id,r.target_video_id,r.subject_sha256,
                          r.review_payload,r.creator_freeze_id,r.prior_freeze_id,
                          r.niche_freeze_id,r.minimum_creator_posts,
                          r.minimum_prior_posts,r.minimum_niche_posts,
                          r.calculation_config_id,d.id AS decision_id,
                          d.decision,d.reviewer_id,d.identity_provider,
                          d.authentication_event_id,d.attestation_sha256
                     FROM group_c_review_requests r
                     LEFT JOIN group_c_review_decisions d ON d.request_id=r.id
                    WHERE r.id=%s AND r.project_id=%s""",
                (review_request_id,project_id),
            ))
            if row is None or row.get("decision")!="approved" or row["decision_id"] is None:
                raise ContractError("review_not_approved","Exact human approval required")
            if (row["creator_freeze_id"]!=creator_freeze_id
                    or row["target_video_id"]!=video_id
                    or row["calculation_config_id"]!=config_id):
                raise ContractError("review_subject_changed","Target/freeze/config not approved together")
            config=_record(conn.execute(
                """SELECT id,calculation_version,alpha,bayesian_k,is_active,
                          normalization_enabled,freshness_half_life_h,weights_json,
                          prior_source_policy,cohort_selection_rules,growth_policy
                     FROM metric_calculation_configs WHERE id=%s""",
                (config_id,),
            ))
            if config is None or config.get("is_active") is not True:
                raise ContractError("config_not_activated","Current global config must be active")
            prior=_read_frozen_cohort(conn,row["prior_freeze_id"])
            niche=(_read_frozen_cohort(conn,row["niche_freeze_id"])
                   if row["niche_freeze_id"] is not None else None)
            neutral={**config,"is_active":False}
            subject=build_human_review_subject(
                neutral,freeze,prior,niche,project_id=project_id,
                minimum_creator_posts=row["minimum_creator_posts"],
                minimum_prior_posts=row["minimum_prior_posts"],
                minimum_niche_posts=row["minimum_niche_posts"],
            )
            if subject.subject_sha256!=row["subject_sha256"] or subject.payload!=row["review_payload"]:
                raise ContractError("review_subject_changed","Approved input SHA/config changed")
            if (subject.source_id!=source_id
                    or subject.source_policy_version_id!=source_policy_version_id
                    or subject.metric_version!=metric_version
                    or subject.target_video_id!=video_id):
                raise ContractError("review_subject_changed","Approved D1 view lineage changed")
            activation=_record(conn.execute(
                """SELECT id,activation_assertion_sha256
                     FROM group_c_config_activation_events
                    WHERE kind='activated' AND project_id=%s AND config_id=%s
                      AND review_request_id=%s AND review_decision_id=%s
                      AND review_subject_sha256=%s
                    ORDER BY created_at DESC,id DESC LIMIT 1""",
                (project_id,config_id,review_request_id,row["decision_id"],
                 subject.subject_sha256),
            ))
            if activation is None:
                raise ContractError("config_not_activated",
                                    "Active config lacks matching exact approved activation")
            _sha(activation["activation_assertion_sha256"],"global_activation")
            # Re-check all frozen peers in the signed review, even though only
            # creator peers contribute to C1; evidence may be revoked.
            _require_db_rights(conn,[freeze,prior]+([niche] if niche else []),adapters)
            parent=_record(conn.execute(
                """SELECT id,purpose,creator_id,source_id,
                          source_metric_definition,platform,content_type,
                          is_frozen,post_count,median_views
                     FROM comparison_cohorts WHERE id=%s""",
                (freeze["legacy_cohort_id"],),
            ))
            if parent is None:
                raise ContractError("creator_cohort_not_approved","Frozen parent disappeared")
            peers=_records(conn.execute(
                """SELECT m.video_id,m.snapshot_id,m.metadata_id,
                          m.source_id,m.source_policy_version_id,
                          m.metric_definition,m.metric_version,m.views,
                          pv.project_id,v.creator_id,v.platform,v.c_content_type,
                          s.id AS raw_snapshot_id,s.views AS raw_views,
                          s.source_metric_definition,s.source_metric_version,
                          lm.comparison_post_id AS legacy_video_id,
                          lm.comparison_snapshot_id AS legacy_snapshot_id
                     FROM group_c_cohort_freeze_members m
                     JOIN videos v ON v.id=m.video_id
                     LEFT JOIN sre_project_videos pv ON pv.video_id=m.video_id
                       AND pv.project_id=%s
                     LEFT JOIN metric_snapshots s ON s.id=m.snapshot_id
                     LEFT JOIN comparison_cohort_members lm
                       ON lm.cohort_id=%s AND lm.comparison_post_id=m.video_id
                    WHERE m.freeze_id=%s ORDER BY m.video_id""",
                (project_id,freeze["legacy_cohort_id"],creator_freeze_id),
            ))
            _approved_creator_context(
                project_id=project_id,video_id=video_id,snapshot_id=snapshot_id,
                metadata_id=metadata_id,creator_id=bundle["creator_id"],
                source_id=source_id,source_policy_version_id=source_policy_version_id,
                metric_version=metric_version,freeze=freeze,cohort_parent=parent,
                peer_rows=peers,
            )
            if (bundle["platform"]!=parent["platform"] or
                    bundle["c_content_type"]!=parent["content_type"]):
                raise ContractError("incompatible_peer_kind","Target and creator cohort kind differ")
            authorized=approval_verifier.verify_c1_use(
                conn,project_id=project_id,video_id=video_id,
                snapshot_id=snapshot_id,config_id=config_id,
                creator_freeze_id=creator_freeze_id,
                subject_sha256=subject.subject_sha256,
                decision_id=str(row["decision_id"]),
                activation_event_id=str(activation["id"]),
                reviewer_id=row["reviewer_id"],
                identity_provider=row["identity_provider"],
                authentication_event_id=row["authentication_event_id"],
                attestation_sha256=row["attestation_sha256"],
                activation_assertion_sha256=activation["activation_assertion_sha256"],
            )
            if authorized is not True:
                raise ContractError("human_auth_unavailable","Current human/global approval not verified")
            value=compute_creator_ratio(checked.views,freeze["median_views"],subject.alpha)
            evidence=canonical_hash({
                "version":C1_VERSION,"project_id":project_id,
                "video_id":video_id,"snapshot_id":snapshot_id,
                "metadata_id":metadata_id,"source_id":source_id,
                "source_policy_version_id":source_policy_version_id,
                "metric_version":metric_version,"creator_id":bundle["creator_id"],
                "config_id":config_id,"calculation_version":subject.calculation_version,
                "creator_freeze_id":creator_freeze_id,
                "cohort_evidence_sha256":freeze["evidence_sha256"],
                "review_subject_sha256":subject.subject_sha256,
                "decision_id":str(row["decision_id"]),
                "activation_event_id":str(activation["id"]),
                "raw_views":checked.views,
                "creator_median_views":str(q(d(freeze["median_views"],"median"))),
                "alpha":str(q(subject.alpha)),"ratio":str(value),
            })
            return CreatorRatioResult(
                project_id=project_id,video_id=video_id,snapshot_id=snapshot_id,
                metadata_id=metadata_id,source_id=source_id,
                source_policy_version_id=source_policy_version_id,
                metric_version=metric_version,creator_id=bundle["creator_id"],
                config_id=config_id,calculation_version=subject.calculation_version,
                creator_freeze_id=creator_freeze_id,
                legacy_creator_cohort_id=freeze["legacy_cohort_id"],
                review_request_id=str(row["id"]),
                review_decision_id=str(row["decision_id"]),
                activation_event_id=str(activation["id"]),
                review_subject_sha256=subject.subject_sha256,
                cohort_evidence_sha256=freeze["evidence_sha256"],
                views=checked.views,creator_median_views=q(d(freeze["median_views"],"median")),
                alpha_used=q(subject.alpha),creator_ratio=value,
                evidence_sha256=evidence,
            )
