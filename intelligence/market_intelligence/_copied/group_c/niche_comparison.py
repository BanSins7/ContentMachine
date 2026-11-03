"""GC-MOD-14: optional niche comparison with immutable topic and review evidence.

Result is diagnostic only. It is NEVER part of the Breakout composite.
NULL is allowed only for a reviewed subject that truly omitted niche.
No D1 writes, inferred topics, sample replacement or IAM bypass.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from .c0 import validate_c0_snapshot
from .contracts import ContractError, canonical_hash
from .creator_ratio import _positive, _sha, _read_frozen_cohort, compute_creator_ratio
from .d1 import _record, _records, local_d1_ports, read_scoped_d1_bundle
from .human_review import _require_db_rights, build_human_review_subject
from .ports import GroupCPorts
from .pure_math import d, q

NICHE_COMPARISON_VERSION = "group-c-niche-outlier/v1"
METRIC = "platform_post_views"


class NicheComparisonApprovalVerifier(Protocol):
    """Externally validated signed human+global authorization, no default."""
    def verify_niche_use(
        self, conn: Any, *, project_id: str, video_id: int,
        snapshot_id: int, config_id: int, niche_freeze_id: int | None,
        subject_sha256: str, decision_id: str, activation_event_id: str,
        reviewer_id: str, identity_provider: str,
        authentication_event_id: str, attestation_sha256: str,
        activation_assertion_sha256: str,
    ) -> bool: ...


@dataclass(frozen=True)
class NicheComparisonResult:
    project_id: str
    video_id: int
    snapshot_id: int
    metadata_id: int
    source_id: str
    source_policy_version_id: int
    metric_version: str
    topic_id: int | None
    config_id: int
    calculation_version: str
    niche_freeze_id: int | None
    legacy_niche_cohort_id: int | None
    review_request_id: str
    review_decision_id: str
    activation_event_id: str
    review_subject_sha256: str
    niche_evidence_sha256: str | None
    views: int
    niche_median_views: Decimal | None
    alpha_used: Decimal
    niche_outlier: Decimal | None
    evidence_sha256: str
    schema_version: str = NICHE_COMPARISON_VERSION
    status: str = "niche_derived_unpersisted"

    @property
    def contributes_to_breakout(self) -> bool:
        return False

    @property
    def ready_for_breakout(self) -> bool:
        return False


def compute_niche_comparison(
    views: int, niche_median_views: Any | None, alpha: Any
) -> Decimal | None:
    """Return optional (views+alpha)/(niche_median+alpha) NUMERIC(30,12).

    Only exact explicit absence (None) maps to SQL NULL; invalid median
    always fails closed, not silently treated as missing. Alpha remains
    mandatory even if niche is absent. Do not compute a Breakout component.
    """
    # Validate the exact approved alpha via shared C1 Decimal kernel.
    # If niche is absent, validate views and alpha explicitly, without
    # inventing a median or dividing by a fabricated denominator.
    if niche_median_views is None:
        if type(views) is not int or not 0 <= views < 2**63:
            raise ContractError("invalid_raw_counter", "Exact nonnegative BIGINT required")
        smooth = d(alpha, "alpha", positive=True)
        if q(smooth) != smooth or q(smooth) <= 0:
            raise ContractError("invalid_niche_precision",
                                "Explicit exact NUMERIC(30,12) alpha required")
        return None
    return compute_creator_ratio(views, niche_median_views, alpha)


def _approved_niche_context(
    *, project_id: str, video_id: int, snapshot_id: int,
    metadata_id: int, topic_id: int, source_id: str,
    source_policy_version_id: int, metric_version: str,
    platform: str, content_type: str,
    niche_freeze: Mapping[str, Any],
    cohort_parent: Mapping[str, Any],
    peer_rows: list[Mapping[str, Any]],
) -> None:
    """Pure exact topic-based peer gate; never infers topic from titles."""
    for name,v in (("video_id",video_id),("snapshot_id",snapshot_id),
                   ("metadata_id",metadata_id),("topic_id",topic_id),
                   ("source_policy_version_id",source_policy_version_id)):
        _positive(v,name)
    if not isinstance(niche_freeze,Mapping) or not isinstance(cohort_parent,Mapping):
        raise ContractError("niche_cohort_incompatible","Exact approved frozen niche required")
    f=niche_freeze; p=cohort_parent
    if (f.get("project_id")!=project_id or f.get("target_video_id")!=video_id
            or f.get("target_snapshot_id")!=snapshot_id
            or f.get("target_metadata_id")!=metadata_id
            or f.get("purpose")!="niche" or f.get("state")!="frozen_unreviewed"
            or f.get("source_id")!=source_id
            or f.get("source_policy_version_id")!=source_policy_version_id
            or f.get("source_metric_definition")!=METRIC
            or f.get("source_metric_version")!=metric_version):
        raise ContractError("niche_cohort_incompatible",
                            "Frozen niche does not match exact target evidence")
    for field in ("rules_sha256","selection_sha256","evidence_sha256"):
        _sha(f.get(field),field)
    if (p.get("id")!=f.get("legacy_cohort_id") or p.get("purpose")!="niche"
            or p.get("topic_id")!=topic_id or p.get("platform")!=platform
            or p.get("content_type")!=content_type
            or p.get("source_id")!=source_id
            or p.get("source_metric_definition")!=METRIC
            or p.get("is_frozen") is not True
            or p.get("median_views")!=f.get("median_views")
            or p.get("post_count")!=f.get("member_count")):
        raise ContractError("incompatible_peer_topic",
                            "Legacy niche topic, platform or median differs")
    if (type(f.get("member_count")) is not int or f["member_count"]<1
            or len(peer_rows)!=f["member_count"]):
        raise ContractError("niche_cohort_incompatible","Exact peer count mismatch")
    seen=set()
    for member in peer_rows:
        if not isinstance(member,Mapping):
            raise ContractError("incompatible_peer_topic","Malformed frozen niche peer")
        vid=_positive(member.get("video_id"),"peer_video_id")
        _positive(member.get("snapshot_id"),"peer_snapshot_id")
        _positive(member.get("metadata_id"),"peer_metadata_id")
        if vid==video_id or vid in seen:
            raise ContractError("self_inclusion","Target/duplicate cannot be niche peer")
        seen.add(vid)
        if (member.get("project_id")!=project_id
                or member.get("topic_id")!=topic_id
                or member.get("platform")!=platform
                or member.get("c_content_type")!=content_type
                or member.get("source_id")!=source_id
                or member.get("source_policy_version_id")!=source_policy_version_id
                or member.get("metric_definition")!=METRIC
                or member.get("metric_version")!=metric_version
                or member.get("source_metric_definition")!=METRIC
                or member.get("source_metric_version")!=metric_version
                or member.get("raw_snapshot_id")!=member.get("snapshot_id")
                or member.get("raw_views")!=member.get("views")
                or member.get("legacy_video_id")!=vid
                or member.get("legacy_snapshot_id")!=member.get("snapshot_id")):
            raise ContractError("incompatible_peer_topic",
                                "Niche peer source/topic/project/raw snapshot changed")


class PostgreSQLNicheComparison:
    """One independent repeatable-read/READ ONLY transaction per derivation."""

    def derive(
        self, conn: Any, *, project_id: str, video_id: int,
        snapshot_id: int, metadata_id: int, source_id: str,
        source_policy_version_id: int, metric_version: str,
        config_id: int, review_request_id: str,
        niche_freeze_id: int | None,
        approval_verifier: NicheComparisonApprovalVerifier | None,
        ports: GroupCPorts | None = None,
    ) -> NicheComparisonResult:
        if getattr(conn,"autocommit",None) is not True:
            raise ContractError("transaction_contract_unavailable",
                                "Independent autocommit connection required")
        for label,value in (("video_id",video_id),("snapshot_id",snapshot_id),
                            ("metadata_id",metadata_id),("config_id",config_id),
                            ("source_policy_version_id",source_policy_version_id)):
            _positive(value,label)
        if niche_freeze_id is not None:
            _positive(niche_freeze_id,"niche_freeze_id")
        if approval_verifier is None or not callable(getattr(approval_verifier,"verify_niche_use",None)):
            raise ContractError("human_auth_unavailable",
                                "Real external niche-use IAM verifier required")
        adapters=ports if ports is not None else local_d1_ports()
        if not isinstance(adapters,GroupCPorts):
            raise ContractError("invalid_ports","Exact Group C ports required")
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            bundle=read_scoped_d1_bundle(
                conn,project_id,video_id,(snapshot_id,),metadata_id,ports=adapters,
            )
            if (bundle["source_id"]!=source_id
                    or bundle["source_policy_version_id"]!=source_policy_version_id):
                raise ContractError("different_video_or_source","Target source/policy changed")
            recent=adapters.require("d1",conn).read_recent_raw_observations(
                conn,video_id,source_id,2,
            )
            if not recent or recent[0].get("id")!=snapshot_id:
                raise ContractError("metric_history_changed",
                                    "Pinned target no longer newest unfiltered raw snapshot")
            current=validate_c0_snapshot(
                bundle["snapshots"][0],video_id=video_id,source_id=source_id,
                source_policy_version_id=source_policy_version_id,
                metric_definition=METRIC,metric_version=metric_version,
            )
            if current.views is None:
                raise ContractError("missing_raw_counter","Current views unavailable")
            # Source rights & target latest verified *before* any optional
            # result. No missing-topic shortcut around review/approval.
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
                raise ContractError("review_not_approved","Exact human-approved subject required")
            if row["target_video_id"]!=video_id or row["calculation_config_id"]!=config_id:
                raise ContractError("review_subject_changed","Different target/config in approval")
            if row["niche_freeze_id"]!=niche_freeze_id:
                raise ContractError("review_niche_mismatch",
                                    "Optional niche must match exact approved review")
            config=_record(conn.execute(
                """SELECT id,calculation_version,alpha,bayesian_k,is_active,
                          normalization_enabled,freshness_half_life_h,weights_json,
                          prior_source_policy,cohort_selection_rules,growth_policy
                     FROM metric_calculation_configs WHERE id=%s""",
                (config_id,),
            ))
            if config is None or config.get("is_active") is not True:
                raise ContractError("config_not_activated",
                                    "Exact reviewed global config must be currently active")
            creator=_read_frozen_cohort(conn,row["creator_freeze_id"])
            prior=_read_frozen_cohort(conn,row["prior_freeze_id"])
            niche=(_read_frozen_cohort(conn,niche_freeze_id)
                   if niche_freeze_id is not None else None)
            subject=build_human_review_subject(
                {**config,"is_active":False},creator,prior,niche,
                project_id=project_id,
                minimum_creator_posts=row["minimum_creator_posts"],
                minimum_prior_posts=row["minimum_prior_posts"],
                minimum_niche_posts=row["minimum_niche_posts"],
            )
            if subject.subject_sha256!=row["subject_sha256"] or subject.payload!=row["review_payload"]:
                raise ContractError("review_subject_changed","Frozen review/config SHA mismatch")
            if (subject.target_video_id!=video_id or subject.source_id!=source_id
                    or subject.source_policy_version_id!=source_policy_version_id
                    or subject.metric_version!=metric_version):
                raise ContractError("review_subject_changed","Reviewed source/metric version drift")
            activated=_record(conn.execute(
                """SELECT id,activation_assertion_sha256
                     FROM group_c_config_activation_events
                    WHERE kind='activated' AND project_id=%s AND config_id=%s
                      AND review_request_id=%s AND review_decision_id=%s
                      AND review_subject_sha256=%s
                    ORDER BY created_at DESC,id DESC LIMIT 1""",
                (project_id,config_id,review_request_id,row["decision_id"],
                 subject.subject_sha256),
            ))
            if activated is None:
                raise ContractError("config_not_activated",
                                    "Current globally active config has no matching approval event")
            _sha(activated["activation_assertion_sha256"],"activation_assertion_sha256")
            _require_db_rights(
                conn,[creator,prior]+([niche] if niche is not None else []),adapters,
            )
            topic_id=None
            legacy_id=None
            median=None
            ratio=None
            niche_digest=None
            if niche is not None:
                # Canonical topic registry, not inferred keywords or labels.
                target_topic=_record(conn.execute(
                    """SELECT c.topic_id,t.enabled FROM videos v
                       JOIN creators c ON c.id=v.creator_id
                       LEFT JOIN topics t ON t.id=c.topic_id
                      WHERE v.id=%s AND v.creator_id=%s""",
                    (video_id,bundle["creator_id"]),
                ))
                if target_topic is None or target_topic.get("enabled") is not True:
                    raise ContractError("niche_topic_unavailable",
                                        "Reviewed canonical topic is absent/disabled")
                topic_id=_positive(target_topic.get("topic_id"),"topic_id")
                parent=_record(conn.execute(
                    """SELECT id,purpose,topic_id,platform,content_type,source_id,
                              source_metric_definition,is_frozen,post_count,
                              median_views
                         FROM comparison_cohorts WHERE id=%s""",
                    (niche["legacy_cohort_id"],),
                ))
                if parent is None:
                    raise ContractError("niche_cohort_incompatible","Frozen niche legacy parent missing")
                members=_records(conn.execute(
                    """SELECT m.video_id,m.snapshot_id,m.metadata_id,m.source_id,
                              m.source_policy_version_id,m.metric_definition,
                              m.metric_version,m.views,
                              pv.project_id,v.platform,v.c_content_type,
                              c.topic_id,s.id AS raw_snapshot_id,
                              s.views AS raw_views,s.source_metric_definition,
                              s.source_metric_version,
                              lm.comparison_post_id AS legacy_video_id,
                              lm.comparison_snapshot_id AS legacy_snapshot_id
                         FROM group_c_cohort_freeze_members m
                         JOIN videos v ON v.id=m.video_id
                         LEFT JOIN creators c ON c.id=v.creator_id
                         LEFT JOIN sre_project_videos pv ON pv.video_id=m.video_id
                           AND pv.project_id=%s
                         LEFT JOIN metric_snapshots s ON s.id=m.snapshot_id
                         LEFT JOIN comparison_cohort_members lm
                           ON lm.cohort_id=%s AND lm.comparison_post_id=m.video_id
                        WHERE m.freeze_id=%s ORDER BY m.video_id""",
                    (project_id,niche["legacy_cohort_id"],niche_freeze_id),
                ))
                _approved_niche_context(
                    project_id=project_id,video_id=video_id,
                    snapshot_id=snapshot_id,metadata_id=metadata_id,
                    topic_id=topic_id,source_id=source_id,
                    source_policy_version_id=source_policy_version_id,
                    metric_version=metric_version,platform=bundle["platform"],
                    content_type=bundle["c_content_type"],
                    niche_freeze=niche,cohort_parent=parent,peer_rows=members,
                )
                median=q(d(niche["median_views"],"niche_median"))
                ratio=compute_niche_comparison(current.views,median,subject.alpha)
                legacy_id=niche["legacy_cohort_id"]
                niche_digest=niche["evidence_sha256"]
            else:
                # Even the explicit missing optional niche must not skip
                # alpha validation, permissions or signed review.
                ratio=compute_niche_comparison(current.views,None,subject.alpha)
            trusted=approval_verifier.verify_niche_use(
                conn,project_id=project_id,video_id=video_id,
                snapshot_id=snapshot_id,config_id=config_id,
                niche_freeze_id=niche_freeze_id,
                subject_sha256=subject.subject_sha256,
                decision_id=str(row["decision_id"]),
                activation_event_id=str(activated["id"]),
                reviewer_id=row["reviewer_id"],
                identity_provider=row["identity_provider"],
                authentication_event_id=row["authentication_event_id"],
                attestation_sha256=row["attestation_sha256"],
                activation_assertion_sha256=activated["activation_assertion_sha256"],
            )
            if trusted is not True:
                raise ContractError("human_auth_unavailable",
                                    "Current human/global niche-use permission not verified")
            digest=canonical_hash({
                "schema_version":NICHE_COMPARISON_VERSION,
                "project_id":project_id,"video_id":video_id,
                "snapshot_id":snapshot_id,"metadata_id":metadata_id,
                "source_id":source_id,"source_policy_version_id":source_policy_version_id,
                "metric_version":metric_version,"config_id":config_id,
                "calculation_version":subject.calculation_version,
                "review_subject_sha256":subject.subject_sha256,
                "review_decision_id":str(row["decision_id"]),
                "activation_event_id":str(activated["id"]),
                "topic_id":topic_id,"niche_freeze_id":niche_freeze_id,
                "niche_evidence_sha256":niche_digest,
                "raw_views":current.views,
                "niche_median_views":str(median) if median is not None else None,
                "alpha":str(q(subject.alpha)),
                "niche_outlier":str(ratio) if ratio is not None else None,
            })
            return NicheComparisonResult(
                project_id=project_id,video_id=video_id,snapshot_id=snapshot_id,
                metadata_id=metadata_id,source_id=source_id,
                source_policy_version_id=source_policy_version_id,
                metric_version=metric_version,topic_id=topic_id,
                config_id=config_id,calculation_version=subject.calculation_version,
                niche_freeze_id=niche_freeze_id,
                legacy_niche_cohort_id=legacy_id,
                review_request_id=str(row["id"]),
                review_decision_id=str(row["decision_id"]),
                activation_event_id=str(activated["id"]),
                review_subject_sha256=subject.subject_sha256,
                niche_evidence_sha256=niche_digest,views=current.views,
                niche_median_views=median,alpha_used=q(subject.alpha),
                niche_outlier=ratio,evidence_sha256=digest,
                status=("niche_derived_unpersisted" if niche is not None
                        else "niche_not_configured"),
            )
