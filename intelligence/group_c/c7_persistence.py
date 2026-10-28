"""GC-MOD-20: atomic, independently reviewed C7 persistence.

Only this writer can turn C7 arithmetic into a complete calculated_metrics row.
It does NOT trust a GC19 preview, skip signed IAM verification, use an
unreviewed config, or obtain evidence through separate read transactions.
No QDP ACK/worker/Analytics publish happens here.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from .bayesian_engagement import _approved_prior_context, compute_bayesian_engagement
from .breakout_score import compute_breakout_components
from .c0 import validate_c0_pair, validate_c0_snapshot
from .contracts import ContractError, canonical_hash
from .creator_ratio import (
    _approved_creator_context, _read_frozen_cohort, compute_creator_ratio,
)
from .d1 import _record, _records, local_d1_ports, read_scoped_d1_bundle
from .freshness import compute_freshness_score
from .human_review import _require_db_rights, build_human_review_subject
from .niche_comparison import _approved_niche_context, compute_niche_comparison
from .ports import GroupCPorts
from .publication_time import validate_publication_time
from .pure_math import q
from .raw_engagement import compute_raw_engagement
from .views_growth import compute_views_growth_per_hour

PERSISTED_C7_VERSION = "group-c-persisted-c7/v1"
METRIC = "platform_post_views"
_SQL_FIELDS = (
    "post_id", "snapshot_id", "calculation_config_id",
    "previous_snapshot_id", "published_metadata_id",
    "creator_cohort_id", "niche_cohort_id", "prior_cohort_id",
    "creator_median_views", "niche_median_views",
    "alpha_used", "k_used", "p_l_used", "p_c_used", "p_s_used",
    "like_rate_pct", "comment_rate_pct", "share_rate_pct",
    "engagement_rate_pct", "weighted_engagement_raw",
    "like_rate_smoothed", "comment_rate_smoothed", "share_rate_smoothed",
    "weighted_engagement_smoothed", "creator_ratio", "niche_outlier",
    "views_growth_per_hour", "freshness_score",
    "score_creator_component", "score_growth_component",
    "score_engagement_component", "score_freshness_component",
    "breakout_score", "calculation_status", "score_status",
)
# Explicit whitelist prevents arbitrary SQL column injection.
_INSERT = (
    "INSERT INTO calculated_metrics (" + ",".join(_SQL_FIELDS) + ") VALUES ("
    + ",".join("%s" for _ in _SQL_FIELDS)
    + ") ON CONFLICT ON CONSTRAINT uq_c_calculation_lineage DO NOTHING RETURNING id"
)
_RESULT_QUERY = (
    "SELECT id," + ",".join(_SQL_FIELDS)
    + " FROM calculated_metrics WHERE snapshot_id=%s "
    + "AND calculation_config_id=%s AND previous_snapshot_id=%s "
    + "AND creator_cohort_id=%s AND niche_cohort_id IS NOT DISTINCT FROM %s "
    + "AND prior_cohort_id=%s"
)


class C7PersistenceApprovalVerifier(Protocol):
    """Trusted IAM grant, absent by default.

    Implementation must verify currently authorized human signature,
    signed review subject, GLOBAL config-activation authority, non-revocation
    and allowed project score-write capability. Test doubles are NOT IAM.
    """
    def verify_c7_persistence(
        self, conn: Any, *, project_id: str, video_id: int,
        snapshot_id: int, config_id: int,
        creator_freeze_id: int, prior_freeze_id: int,
        niche_freeze_id: int | None, review_subject_sha256: str,
        decision_id: str, activation_event_id: str,
        reviewer_id: str, identity_provider: str,
        authentication_event_id: str, attestation_sha256: str,
        activation_assertion_sha256: str,
    ) -> bool: ...


@dataclass(frozen=True)
class PersistedC7Receipt:
    calculated_metric_id: int
    project_id: str
    video_id: int
    snapshot_id: int
    previous_snapshot_id: int
    published_metadata_id: int
    config_id: int
    creator_freeze_id: int
    prior_freeze_id: int
    niche_freeze_id: int | None
    review_request_id: str
    review_decision_id: str
    activation_event_id: str
    source_policy_version_id: int
    metric_version: str
    breakout_score: Decimal
    evidence_sha256: str
    inserted: bool
    schema_version: str = PERSISTED_C7_VERSION
    score_status: str = "complete"
    status: str = "persisted_complete_not_published"


def _id(v: Any, name: str) -> int:
    if type(v) is not int or v < 1:
        raise ContractError("invalid_identifier", name + " must be positive int")
    return v


def _reviewed(conn: Any, *, project_id: str, video_id: int, source_id: str,
              policy_id: int, metric_version: str, config_id: int,
              review_request_id: str, adapters: GroupCPorts) -> tuple:
    review = _record(conn.execute(
        """SELECT r.id,r.project_id,r.target_video_id,r.subject_sha256,
                  r.review_payload,r.creator_freeze_id,r.prior_freeze_id,
                  r.niche_freeze_id,r.minimum_creator_posts,
                  r.minimum_prior_posts,r.minimum_niche_posts,
                  r.calculation_config_id,d.id AS decision_id,
                  d.decision,d.reviewer_id,d.identity_provider,
                  d.authentication_event_id,d.attestation_sha256
             FROM group_c_review_requests r
             LEFT JOIN group_c_review_decisions d ON d.request_id=r.id
            WHERE r.id=%s AND r.project_id=%s FOR SHARE OF r""",
        (review_request_id,project_id),
    ))
    if review is None or review.get("decision")!="approved" or review["decision_id"] is None:
        raise ContractError("review_not_approved", "Exact signed human decision required")
    if review["target_video_id"]!=video_id or review["calculation_config_id"]!=config_id:
        raise ContractError("review_subject_changed", "Review target/config mismatch")
    config = _record(conn.execute(
        """SELECT id,calculation_version,alpha,bayesian_k,is_active,
                  normalization_enabled,freshness_half_life_h,weights_json,
                  prior_source_policy,cohort_selection_rules,growth_policy
             FROM metric_calculation_configs WHERE id=%s FOR SHARE""",(config_id,),
    ))
    if config is None or config["is_active"] is not True:
        raise ContractError("config_not_activated", "Current global active config required")
    creator = _read_frozen_cohort(conn,review["creator_freeze_id"])
    prior = _read_frozen_cohort(conn,review["prior_freeze_id"])
    niche = (_read_frozen_cohort(conn,review["niche_freeze_id"])
             if review["niche_freeze_id"] is not None else None)
    subject = build_human_review_subject(
        {**config,"is_active":False},creator,prior,niche,
        project_id=project_id,
        minimum_creator_posts=review["minimum_creator_posts"],
        minimum_prior_posts=review["minimum_prior_posts"],
        minimum_niche_posts=review["minimum_niche_posts"],
    )
    if (subject.subject_sha256!=review["subject_sha256"]
            or subject.payload!=review["review_payload"]
            or subject.source_id!=source_id
            or subject.source_policy_version_id!=policy_id
            or subject.metric_version!=metric_version
            or subject.target_video_id!=video_id):
        raise ContractError("review_subject_changed", "Approved subject/current D1 lineage changed")
    activation = _record(conn.execute(
        """SELECT id,activation_assertion_sha256
             FROM group_c_config_activation_events
            WHERE kind='activated' AND project_id=%s AND config_id=%s
              AND review_request_id=%s AND review_decision_id=%s
              AND review_subject_sha256=%s
            ORDER BY created_at DESC,id DESC LIMIT 1""",
        (project_id,config_id,review_request_id,review["decision_id"],
         subject.subject_sha256),
    ))
    if activation is None:
        raise ContractError("config_not_activated","No signed exact activation event")
    _require_db_rights(conn,[creator,prior]+([niche] if niche else []),adapters)
    return review,config,subject,activation,creator,prior,niche


def _creator_peers(conn: Any, *, project_id: str, freeze: dict) -> tuple[dict,list[dict]]:
    parent = _record(conn.execute(
        """SELECT id,purpose,creator_id,source_id,
                  source_metric_definition,platform,content_type,
                  is_frozen,post_count,median_views
             FROM comparison_cohorts WHERE id=%s""",
        (freeze["legacy_cohort_id"],),
    ))
    rows = _records(conn.execute(
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
        (project_id,freeze["legacy_cohort_id"],freeze["id"]),
    ))
    return parent,rows


def _prior_peers(conn: Any, *, project_id: str, freeze: dict) -> tuple[dict,list[dict]]:
    parent = _record(conn.execute(
        """SELECT id,purpose,platform,content_type,source_id,
                  source_metric_definition,is_frozen,post_count,
                  prior_like_rate,prior_comment_rate,prior_share_rate
             FROM comparison_cohorts WHERE id=%s""",
        (freeze["legacy_cohort_id"],),
    ))
    rows = _records(conn.execute(
        """SELECT m.video_id,m.snapshot_id,m.metadata_id,
                  m.source_id,m.source_policy_version_id,
                  m.metric_definition,m.metric_version,m.observed_at,
                  m.views,m.likes,m.comments,m.shares,
                  pv.project_id,v.platform,v.c_content_type,
                  s.id AS raw_snapshot_id,s.video_id AS raw_video_id,
                  s.source_id AS raw_source_id,
                  s.source_policy_version_id AS raw_policy_id,
                  s.source_metric_definition AS raw_definition,
                  s.source_metric_version AS raw_version,
                  s.observed_at AS raw_observed_at,
                  s.views AS raw_views,s.likes AS raw_likes,
                  s.comments AS raw_comments,s.shares AS raw_shares,
                  s.data_quality_status AS raw_quality,
                  s.precision AS raw_precision,
                  s.observation_time_assumed AS raw_assumed,
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
        (project_id,freeze["legacy_cohort_id"],freeze["id"]),
    ))
    return parent,rows


def _niche_peers(conn: Any, *, project_id: str, freeze: dict) -> tuple[dict,list[dict]]:
    parent = _record(conn.execute(
        """SELECT id,purpose,topic_id,platform,content_type,source_id,
                  source_metric_definition,is_frozen,post_count,median_views
             FROM comparison_cohorts WHERE id=%s""",(freeze["legacy_cohort_id"],),
    ))
    rows = _records(conn.execute(
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
        (project_id,freeze["legacy_cohort_id"],freeze["id"]),
    ))
    return parent,rows


def _calculate_rows(
    *, video_id: int, snapshot_id: int, metadata_id: int, previous_snapshot_id: int,
    config_id: int, previous: Any, current: Any,
    proof: Any, subject: Any, creator: dict, prior: dict,
    niche: dict | None, p_l: Decimal, p_c: Decimal, p_s: Decimal,
) -> dict:
    c1 = compute_creator_ratio(current.views,creator["median_views"],subject.alpha)
    c3 = compute_views_growth_per_hour(
        previous.views,current.views,previous.observed_at,current.observed_at,
    )
    c4 = compute_raw_engagement(current.views,current.likes,current.comments,current.shares)
    c5 = compute_bayesian_engagement(
        current.views,current.likes,current.comments,current.shares,
        subject.bayesian_k,p_l,p_c,p_s,
    )
    c6 = compute_freshness_score(proof.published_at, current.observed_at)
    c7 = compute_breakout_components(
        c1,c3,c5.weighted_engagement_smoothed,c6,
    )
    pct = c4.as_percentages()
    niche_median = q(niche["median_views"]) if niche is not None else None
    niche_outlier = (compute_niche_comparison(
        current.views,niche_median,subject.alpha,
    ) if niche is not None else None)
    return dict(
        post_id=video_id,snapshot_id=snapshot_id,
        calculation_config_id=config_id,
        previous_snapshot_id=previous_snapshot_id,
        published_metadata_id=metadata_id,
        creator_cohort_id=creator["legacy_cohort_id"],
        niche_cohort_id=niche["legacy_cohort_id"] if niche else None,
        prior_cohort_id=prior["legacy_cohort_id"],
        creator_median_views=q(creator["median_views"]),
        niche_median_views=niche_median,
        alpha_used=q(subject.alpha),k_used=q(subject.bayesian_k),
        p_l_used=p_l,p_c_used=p_c,p_s_used=p_s,
        like_rate_pct=pct.like_rate_percent,
        comment_rate_pct=pct.comment_rate_percent,
        share_rate_pct=pct.share_rate_percent,
        engagement_rate_pct=pct.engagement_rate_percent,
        weighted_engagement_raw=c4.weighted_engagement_raw_fraction,
        like_rate_smoothed=c5.like_rate_smoothed,
        comment_rate_smoothed=c5.comment_rate_smoothed,
        share_rate_smoothed=c5.share_rate_smoothed,
        weighted_engagement_smoothed=c5.weighted_engagement_smoothed,
        creator_ratio=c1,niche_outlier=niche_outlier,
        views_growth_per_hour=c3,freshness_score=c6,
        score_creator_component=c7.score_creator_component,
        score_growth_component=c7.score_growth_component,
        score_engagement_component=c7.score_engagement_component,
        score_freshness_component=c7.score_freshness_component,
        breakout_score=c7.breakout_score,
        calculation_status="complete",score_status="complete",
    )


def _evidence_fingerprint(*, project_id: str, source_id: str,
                          policy_id: int, metric_version: str,
                          previous: Any,current: Any,proof: Any,
                          subject: Any,review: dict,activation: dict,
                          creator: dict,prior: dict,niche: dict | None,
                          row: dict) -> str:
    return canonical_hash({
        "version":PERSISTED_C7_VERSION,
        "project_id":project_id,"source_id":source_id,
        "source_policy_version_id":policy_id,
        "metric_definition":METRIC,"metric_version":metric_version,
        "previous_snapshot_id":previous.snapshot_id,
        "snapshot_id":current.snapshot_id,
        "previous_observed_at":previous.observed_at.isoformat(),
        "observed_at":current.observed_at.isoformat(),
        "previous_views":previous.views,
        "views":current.views,"likes":current.likes,
        "comments":current.comments,"shares":current.shares,
        "publication_sha256":proof.evidence_sha256,
        "subject_sha256":subject.subject_sha256,
        "creator_evidence_sha256":creator["evidence_sha256"],
        "prior_evidence_sha256":prior["evidence_sha256"],
        "niche_evidence_sha256":niche["evidence_sha256"] if niche else None,
        "review_request_id":str(review["id"]),
        "review_decision_id":str(review["decision_id"]),
        "activation_event_id":str(activation["id"]),
        "calculated_fields":{
            k:str(row[k]) if type(row[k]) is Decimal else row[k]
            for k in _SQL_FIELDS
        },
    })


def _existing_result(conn: Any, *, row: dict, fingerprint: str) -> tuple[int,bool]:
    result = _record(conn.execute(
        _INSERT,tuple(row[c] for c in _SQL_FIELDS),
    ))
    if result is not None:
        return result["id"],True
    key = ("snapshot_id","calculation_config_id","previous_snapshot_id",
           "creator_cohort_id","niche_cohort_id","prior_cohort_id")
    stored = _record(conn.execute(_RESULT_QUERY,tuple(row[k] for k in key)))
    if stored is None:
        raise ContractError("c7_lineage_conflict","Conflicting C7 row not visible; retry transaction")
    for key in _SQL_FIELDS:
        if stored.get(key) != row[key]:
            raise ContractError("c7_lineage_conflict","Same version lineage has different C7 field: "+key)
    certified = _record(conn.execute(
        "SELECT evidence_sha256,review_subject_sha256,review_request_id "
        "FROM group_c_calculation_receipts WHERE calculated_metric_id=%s",
        (stored["id"],),
    ))
    if (certified is None or certified["evidence_sha256"]!=fingerprint):
        raise ContractError("c7_lineage_conflict","Uncertified/different complete score cannot be reused")
    return stored["id"],False


class PostgreSQLC7Persistence:
    """Atomic SERIALIZABLE writer. Requires a production external IAM verifier."""

    def persist(
        self, conn: Any, *, project_id: str,video_id: int,
        snapshot_id: int,previous_snapshot_id: int,
        metadata_id: int,source_id: str,source_policy_version_id: int,
        metric_version: str,config_id: int,review_request_id: str,
        approval_verifier: C7PersistenceApprovalVerifier | None,
        ports: GroupCPorts | None = None,
        transaction_managed: bool = False,
    ) -> PersistedC7Receipt:
        if getattr(conn,"autocommit",None) is not True:
            raise ContractError("transaction_contract_unavailable",
                                "C7 writer requires independent autocommit connection")
        for name,value in (
            ("video_id",video_id),("snapshot_id",snapshot_id),
            ("previous_snapshot_id",previous_snapshot_id),
            ("metadata_id",metadata_id),("source_policy_version_id",source_policy_version_id),
            ("config_id",config_id),
        ):
            _id(value,name)
        if snapshot_id==previous_snapshot_id:
            raise ContractError("invalid_observation_interval","Distinct snapshots required")
        if (type(metric_version) is not str or not metric_version.strip()
                or type(source_id) is not str or not source_id.strip()):
            raise ContractError("incompatible_metric_version","Pinned metric source/version required")
        if (approval_verifier is None
                or not callable(getattr(approval_verifier,"verify_c7_persistence",None))):
            raise ContractError("human_auth_unavailable","Trusted live human/global IAM verifier required")
        adapters = ports if ports is not None else local_d1_ports()
        if not isinstance(adapters,GroupCPorts):
            raise ContractError("invalid_ports","Explicit source-capable Group C ports required")
        if type(transaction_managed) is not bool:
            raise ContractError("transaction_contract_unavailable", "transaction_managed must be bool")
        if transaction_managed:
            # GC21 holds and fences the job in an outer SERIALIZABLE
            # transaction. Do not open an independent connection/savepoint.
            from psycopg.pq import TransactionStatus
            if (conn.info.transaction_status != TransactionStatus.INTRANS
                    or conn.execute("SHOW transaction_isolation").fetchone()[0] != "serializable"):
                raise ContractError("transaction_contract_unavailable",
                                    "Caller must own an active SERIALIZABLE transaction")
        with (nullcontext() if transaction_managed else conn.transaction()):
            if not transaction_managed:
                conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            # Currentness and *every* authorization proof belong to this
            # same transaction. An offline GC19 preview is never an input.
            bundle = read_scoped_d1_bundle(
                conn,project_id,video_id,
                (previous_snapshot_id,snapshot_id),metadata_id,ports=adapters,
            )
            if (bundle["source_id"]!=source_id
                    or bundle["source_policy_version_id"]!=source_policy_version_id):
                raise ContractError("different_video_or_source","Current source/policy mismatch")
            before_raw,current_raw=bundle["snapshots"]
            checked_at=datetime.now(timezone.utc)
            before=validate_c0_snapshot(
                before_raw,video_id=video_id,source_id=source_id,
                source_policy_version_id=source_policy_version_id,
                metric_definition=METRIC,metric_version=metric_version,now=checked_at,
                require_score_fields=False,
            )
            current=validate_c0_snapshot(
                current_raw,video_id=video_id,source_id=source_id,
                source_policy_version_id=source_policy_version_id,
                metric_definition=METRIC,metric_version=metric_version,now=checked_at,
                require_score_fields=True,
            )
            validate_c0_pair(before,current,require_score_fields=True)
            recent=adapters.require("d1",conn).read_recent_raw_observations(
                conn,video_id,source_id,2,
            )
            if [r.get("id") for r in recent]!=[snapshot_id,previous_snapshot_id]:
                raise ContractError("metric_history_changed",
                                    "Latest TWO unfiltered D1 raw observations required")
            proof=validate_publication_time(
                bundle["metadata"],video_id=video_id,metadata_id=metadata_id,
                source_id=source_id,source_policy_version_id=source_policy_version_id,
                previous_snapshot_id=previous_snapshot_id,snapshot_id=snapshot_id,
                previous_observed_at=before.observed_at,observed_at=current.observed_at,
                as_of=checked_at,
            )
            review,config,subject,activation,creator,prior,niche=_reviewed(
                conn,project_id=project_id,video_id=video_id,source_id=source_id,
                policy_id=source_policy_version_id,metric_version=metric_version,
                config_id=config_id,review_request_id=review_request_id,adapters=adapters,
            )
            creator_parent,creator_members=_creator_peers(
                conn,project_id=project_id,freeze=creator,
            )
            if creator_parent is None:
                raise ContractError("creator_cohort_not_approved","No canonical creator cohort")
            _approved_creator_context(
                project_id=project_id,video_id=video_id,snapshot_id=snapshot_id,
                metadata_id=metadata_id,creator_id=bundle["creator_id"],
                source_id=source_id,source_policy_version_id=source_policy_version_id,
                metric_version=metric_version,freeze=creator,
                cohort_parent=creator_parent,peer_rows=creator_members,
            )
            if (bundle["platform"]!=creator_parent["platform"]
                    or bundle["c_content_type"]!=creator_parent["content_type"]):
                raise ContractError("incompatible_peer_kind","Creator kind incompatible")
            prior_parent,prior_members=_prior_peers(
                conn,project_id=project_id,freeze=prior,
            )
            if prior_parent is None:
                raise ContractError("prior_cohort_not_approved","No canonical prior cohort")
            p_l,p_c,p_s=_approved_prior_context(
                project_id=project_id,video_id=video_id,snapshot_id=snapshot_id,
                metadata_id=metadata_id,source_id=source_id,
                source_policy_version_id=source_policy_version_id,
                metric_version=metric_version,platform=bundle["platform"],
                content_type=bundle["c_content_type"],
                prior_freeze=prior,legacy_prior=prior_parent,members=prior_members,
            )
            if niche is not None:
                niche_parent,niche_members=_niche_peers(
                    conn,project_id=project_id,freeze=niche,
                )
                creator_info=adapters.require("registry",conn).read_creator(
                    conn,bundle["creator_id"],
                )
                if niche_parent is None:
                    raise ContractError("niche_cohort_incompatible","No canonical niche cohort")
                _approved_niche_context(
                    project_id=project_id,video_id=video_id,
                    snapshot_id=snapshot_id,metadata_id=metadata_id,
                    topic_id=creator_info["topic_id"],source_id=source_id,
                    source_policy_version_id=source_policy_version_id,
                    metric_version=metric_version,platform=bundle["platform"],
                    content_type=bundle["c_content_type"],
                    niche_freeze=niche,cohort_parent=niche_parent,
                    peer_rows=niche_members,
                )
            granted=approval_verifier.verify_c7_persistence(
                conn,project_id=project_id,video_id=video_id,
                snapshot_id=snapshot_id,config_id=config_id,
                creator_freeze_id=creator["id"],prior_freeze_id=prior["id"],
                niche_freeze_id=niche["id"] if niche else None,
                review_subject_sha256=subject.subject_sha256,
                decision_id=str(review["decision_id"]),
                activation_event_id=str(activation["id"]),
                reviewer_id=review["reviewer_id"],
                identity_provider=review["identity_provider"],
                authentication_event_id=review["authentication_event_id"],
                attestation_sha256=review["attestation_sha256"],
                activation_assertion_sha256=activation["activation_assertion_sha256"],
            )
            if granted is not True:
                raise ContractError("human_auth_unavailable",
                                    "Current signed human and global score-write IAM not verified")
            row=_calculate_rows(
                video_id=video_id,snapshot_id=snapshot_id,
                metadata_id=metadata_id,previous_snapshot_id=previous_snapshot_id,
                config_id=config_id,previous=before,current=current,
                proof=proof,subject=subject,creator=creator,prior=prior,
                niche=niche,p_l=p_l,p_c=p_c,p_s=p_s,
            )
            fingerprint=_evidence_fingerprint(
                project_id=project_id,source_id=source_id,
                policy_id=source_policy_version_id,metric_version=metric_version,
                previous=before,current=current,proof=proof,
                subject=subject,review=review,activation=activation,
                creator=creator,prior=prior,niche=niche,row=row,
            )
            metric_id,inserted=_existing_result(
                conn,row=row,fingerprint=fingerprint,
            )
            if inserted:
                conn.execute(
                    """INSERT INTO group_c_calculation_receipts (
                         calculated_metric_id,project_id,video_id,
                         snapshot_id,previous_snapshot_id,published_metadata_id,
                         config_id,creator_freeze_id,niche_freeze_id,prior_freeze_id,
                         review_request_id,review_decision_id,activation_event_id,
                         review_subject_sha256,evidence_sha256,calculation_version)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (metric_id,project_id,video_id,snapshot_id,
                     previous_snapshot_id,metadata_id,config_id,
                     creator["id"],niche["id"] if niche else None,prior["id"],
                     str(review["id"]),str(review["decision_id"]),
                     str(activation["id"]),subject.subject_sha256,fingerprint,
                     subject.calculation_version),
                )
            return PersistedC7Receipt(
                calculated_metric_id=metric_id,project_id=project_id,
                video_id=video_id,snapshot_id=snapshot_id,
                previous_snapshot_id=previous_snapshot_id,
                published_metadata_id=metadata_id,config_id=config_id,
                creator_freeze_id=creator["id"],prior_freeze_id=prior["id"],
                niche_freeze_id=niche["id"] if niche else None,
                review_request_id=str(review["id"]),
                review_decision_id=str(review["decision_id"]),
                activation_event_id=str(activation["id"]),
                source_policy_version_id=source_policy_version_id,
                metric_version=metric_version,
                breakout_score=row["breakout_score"],
                evidence_sha256=fingerprint,inserted=inserted,
            )
