"""GC-MOD-17: approved Bayesian engagement (C5), isolated from C7 writes.

This read-only adapter needs current D1 raw facts, immutable reviewed peer
priors, the globally activated config AND a trusted external IAM decision.
The pure kernel is deterministic, but does not grant authorization.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any, Protocol

from .c0 import validate_c0_snapshot
from .contracts import ContractError, canonical_hash
from .creator_ratio import _read_frozen_cohort
from .d1 import _record, _records, local_d1_ports, read_scoped_d1_bundle
from .human_review import _require_db_rights, build_human_review_subject
from .ports import GroupCPorts
from .pure_math import d, q
from .raw_engagement import _count

BAYESIAN_ENGAGEMENT_VERSION = "group-c-bayesian-engagement/v1"
_METRIC = "platform_post_views"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_COMMENT_WEIGHT = Decimal("1.5")
_SHARE_WEIGHT = Decimal("3")


class BayesianEngagementApprovalVerifier(Protocol):
    """Production verifier must attest live human AND global IAM grants.

    No permissive default provider is installed. A literal True is accepted
    only from an explicitly injected, externally trusted implementation.
    """
    def verify_c5_use(
        self, conn: Any, *, project_id: str, video_id: int,
        snapshot_id: int, config_id: int, prior_freeze_id: int,
        review_subject_sha256: str, decision_id: str,
        activation_event_id: str, reviewer_id: str,
        identity_provider: str, authentication_event_id: str,
        attestation_sha256: str, activation_assertion_sha256: str,
    ) -> bool: ...


def _precise(value: Any, name: str, *, positive: bool = False,
             prior_rate: bool = False) -> Decimal:
    """Reviewed NUMERIC(30,12) value; never silently round an input."""
    number = d(value, name, positive=positive)
    fixed = q(number)
    if fixed != number:
        raise ContractError("invalid_c5_precision", name + " exceeds 12-decimal approved precision")
    if prior_rate and fixed > 1:
        raise ContractError("invalid_prior_rate", name + " must lie in [0,1]")
    return fixed


@dataclass(frozen=True)
class BayesianEngagementRates:
    """C5 *fractions*; weighted metric uses unrounded component fractions."""
    views: int
    likes: int
    comments: int
    shares: int
    k_used: Decimal
    p_l_used: Decimal
    p_c_used: Decimal
    p_s_used: Decimal
    like_rate_smoothed: Decimal
    comment_rate_smoothed: Decimal
    share_rate_smoothed: Decimal
    weighted_engagement_smoothed: Decimal
    unit: str = "fraction"


def compute_bayesian_engagement(
    views: int, likes: int, comments: int, shares: int,
    bayesian_k: Decimal | int | str,
    prior_like_rate: Decimal | int | str,
    prior_comment_rate: Decimal | int | str,
    prior_share_rate: Decimal | int | str,
) -> BayesianEngagementRates:
    """Exact Ls=(L+k*pL)/(V+k), Cs, Ss; weighted=Ls+1.5Cs+3Ss.

    k and three prior fractions MUST be supplied; never default them.
    In the current C4/C7 contract views > 0, even though mathematical
    smoothing could admit a zero-view post. Interactions are exact BIGINT.
    """
    v = _count(views, "views", positive=True)
    l = _count(likes, "likes")
    c = _count(comments, "comments")
    s = _count(shares, "shares")
    k = _precise(bayesian_k, "bayesian_k", positive=True)
    pl = _precise(prior_like_rate, "prior_like_rate", prior_rate=True)
    pc = _precise(prior_comment_rate, "prior_comment_rate", prior_rate=True)
    ps = _precise(prior_share_rate, "prior_share_rate", prior_rate=True)
    with localcontext() as ctx:
        ctx.prec = 80
        denominator = Decimal(v) + k
        ls = (Decimal(l) + k * pl) / denominator
        cs = (Decimal(c) + k * pc) / denominator
        ss = (Decimal(s) + k * ps) / denominator
        return BayesianEngagementRates(
            views=v, likes=l, comments=c, shares=s,
            k_used=k, p_l_used=pl, p_c_used=pc, p_s_used=ps,
            like_rate_smoothed=q(ls),
            comment_rate_smoothed=q(cs),
            share_rate_smoothed=q(ss),
            weighted_engagement_smoothed=q(
                ls + _COMMENT_WEIGHT * cs + _SHARE_WEIGHT * ss),
        )


def _approved_prior_context(
    *, project_id: str, video_id: int, snapshot_id: int,
    metadata_id: int, source_id: str, source_policy_version_id: int,
    metric_version: str, platform: str, content_type: str,
    prior_freeze: Mapping[str, Any], legacy_prior: Mapping[str, Any],
    members: list[Mapping[str, Any]],
) -> tuple[Decimal, Decimal, Decimal]:
    """Validate original frozen prior stats against exact raw member snapshots.

    Pure, not a replacement for source-policy rights/IAM checks. Requires
    original legacy membership, project, source, metric version, kind, actual
    raw snapshot counters and approved pooled rates to agree precisely.
    """
    freeze = prior_freeze
    if not isinstance(freeze, Mapping) or not isinstance(legacy_prior, Mapping):
        raise ContractError("prior_cohort_not_approved", "Frozen prior and parent are required")
    if (freeze.get("project_id") != project_id
            or freeze.get("target_video_id") != video_id
            or freeze.get("target_snapshot_id") != snapshot_id
            or freeze.get("target_metadata_id") != metadata_id
            or freeze.get("source_id") != source_id
            or freeze.get("source_policy_version_id") != source_policy_version_id
            or freeze.get("source_metric_definition") != _METRIC
            or freeze.get("source_metric_version") != metric_version
            or freeze.get("purpose") != "engagement_prior"
            or freeze.get("state") != "frozen_unreviewed"
            or freeze.get("median_views") is not None):
        raise ContractError("prior_cohort_not_approved", "Wrong prior freeze lineage")
    count = freeze.get("member_count")
    if type(count) is not int or count < 1 or not isinstance(members, list) or len(members) != count:
        raise ContractError("prior_cohort_not_approved", "Peer count does not match frozen prior")
    for label in ("rules_sha256", "selection_sha256", "evidence_sha256"):
        if type(freeze.get(label)) is not str or not _SHA.fullmatch(freeze[label]):
            raise ContractError("prior_cohort_not_approved", "Invalid frozen " + label)
    if (legacy_prior.get("id") != freeze.get("legacy_cohort_id")
            or legacy_prior.get("purpose") != "engagement_prior"
            or legacy_prior.get("is_frozen") is not True
            or legacy_prior.get("post_count") != count
            or legacy_prior.get("source_id") != source_id
            or legacy_prior.get("source_metric_definition") != _METRIC
            or legacy_prior.get("platform") != platform
            or legacy_prior.get("content_type") != content_type):
        raise ContractError("incompatible_prior_cohort", "Canonical parent changed")
    sums = {"views": 0, "likes": 0, "comments": 0, "shares": 0}
    seen = set()
    for row in members:
        if not isinstance(row, Mapping):
            raise ContractError("incompatible_prior_peer", "Malformed source peer")
        vid = row.get("video_id")
        if type(vid) is not int or vid < 1 or vid in seen or vid == video_id:
            raise ContractError("self_inclusion", "Target/duplicate peer is forbidden")
        seen.add(vid)
        if (row.get("project_id") != project_id
                or row.get("platform") != platform
                or row.get("c_content_type") != content_type
                or row.get("source_id") != source_id
                or row.get("source_policy_version_id") != source_policy_version_id
                or row.get("metric_definition") != _METRIC
                or row.get("metric_version") != metric_version
                or row.get("raw_snapshot_id") != row.get("snapshot_id")
                or row.get("raw_video_id") != vid
                or row.get("raw_source_id") != source_id
                or row.get("raw_policy_id") != source_policy_version_id
                or row.get("raw_definition") != _METRIC
                or row.get("raw_version") != metric_version
                or row.get("legacy_video_id") != vid
                or row.get("legacy_snapshot_id") != row.get("snapshot_id")
                or row.get("raw_observed_at") != row.get("observed_at")
                or row.get("raw_quality") != "ok"
                or row.get("raw_precision") != "exact"
                or row.get("raw_assumed") is not False):
            raise ContractError("incompatible_prior_peer", "Peer raw version, rights or identity drift")
        for name in sums:
            value = _count(row.get(name), "peer_" + name, positive=(name == "views"))
            if value != row.get("raw_" + name):
                raise ContractError("incompatible_prior_peer", "Member raw counter altered")
            sums[name] += value
    if sums["views"] <= 0:
        raise ContractError("invalid_prior_rate", "Pooled peer denominator is zero")
    with localcontext() as ctx:
        ctx.prec = 80
        expected = tuple(q(Decimal(sums[field]) / Decimal(sums["views"]))
                         for field in ("likes", "comments", "shares"))
    got = tuple(_precise(freeze.get(label), label, prior_rate=True) for label in
                ("prior_like_rate", "prior_comment_rate", "prior_share_rate"))
    if got != expected:
        raise ContractError("prior_evidence_changed", "Pooled frozen rates do not match original counters")
    legacy = tuple(_precise(legacy_prior.get(label), label, prior_rate=True) for label in
                   ("prior_like_rate", "prior_comment_rate", "prior_share_rate"))
    if got != legacy:
        raise ContractError("prior_evidence_changed", "Legacy and new frozen rates differ")
    return got


@dataclass(frozen=True)
class BayesianEngagementResult:
    project_id: str
    video_id: int
    snapshot_id: int
    metadata_id: int
    source_id: str
    source_policy_version_id: int
    metric_version: str
    config_id: int
    calculation_version: str
    prior_freeze_id: int
    legacy_prior_cohort_id: int
    review_request_id: str
    review_decision_id: str
    activation_event_id: str
    review_subject_sha256: str
    prior_evidence_sha256: str
    rates: BayesianEngagementRates
    evidence_sha256: str
    schema_version: str = BAYESIAN_ENGAGEMENT_VERSION
    status: str = "c5_derived_unpersisted"

    @property
    def ready_for_breakout(self) -> bool:
        return False


class PostgreSQLBayesianEngagement:
    """C5 diagnostic; one consistent source/review/activation/prior transaction."""

    def derive(
        self, conn: Any, *, project_id: str, video_id: int,
        snapshot_id: int, metadata_id: int, source_id: str,
        source_policy_version_id: int, metric_version: str,
        config_id: int, prior_freeze_id: int, review_request_id: str,
        approval_verifier: BayesianEngagementApprovalVerifier | None,
        ports: GroupCPorts | None = None,
    ) -> BayesianEngagementResult:
        if getattr(conn, "autocommit", None) is not True:
            raise ContractError("transaction_contract_unavailable", "Independent autocommit connection required")
        for label, value in (
            ("video_id", video_id), ("snapshot_id", snapshot_id),
            ("metadata_id", metadata_id), ("config_id", config_id),
            ("prior_freeze_id", prior_freeze_id),
            ("source_policy_version_id", source_policy_version_id),
        ):
            if type(value) is not int or value <= 0:
                raise ContractError("invalid_identifier", label + " must be positive int")
        if approval_verifier is None or not callable(getattr(approval_verifier, "verify_c5_use", None)):
            raise ContractError("human_auth_unavailable", "Current external C5 IAM verifier required")
        checked_ports = ports if ports is not None else local_d1_ports()
        if not isinstance(checked_ports, GroupCPorts):
            raise ContractError("invalid_ports", "Compatible Group C ports required")
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            bundle = read_scoped_d1_bundle(
                conn, project_id, video_id, (snapshot_id,), metadata_id,
                ports=checked_ports,
            )
            if (bundle["source_id"] != source_id
                    or bundle["source_policy_version_id"] != source_policy_version_id):
                raise ContractError("different_video_or_source", "D1 source/policy mismatch")
            recent = checked_ports.require("d1", conn).read_recent_raw_observations(
                conn, video_id, source_id, 2,
            )
            if not recent or recent[0].get("id") != snapshot_id:
                raise ContractError("metric_history_changed", "Cannot use a stale raw D1 observation")
            current = validate_c0_snapshot(
                bundle["snapshots"][0], video_id=video_id, source_id=source_id,
                source_policy_version_id=source_policy_version_id,
                metric_definition=_METRIC, metric_version=metric_version,
                require_score_fields=True,
            )
            if current.views == 0:
                raise ContractError("zero_views_rate_undefined", "C5 requires valid C4 denominator")
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
                    WHERE r.id=%s AND r.project_id=%s""",
                (review_request_id, project_id),
            ))
            if review is None or review.get("decision") != "approved" or review.get("decision_id") is None:
                raise ContractError("review_not_approved", "Explicit signed human approval required")
            if (review["target_video_id"] != video_id
                    or review["prior_freeze_id"] != prior_freeze_id
                    or review["calculation_config_id"] != config_id):
                raise ContractError("review_subject_changed", "C5 input not present in signed review")
            config = _record(conn.execute(
                """SELECT id,calculation_version,alpha,bayesian_k,is_active,
                          normalization_enabled,freshness_half_life_h,weights_json,
                          prior_source_policy,cohort_selection_rules,growth_policy
                     FROM metric_calculation_configs WHERE id=%s""",
                (config_id,),
            ))
            if config is None or config.get("is_active") is not True:
                raise ContractError("config_not_activated", "Reviewed config must be globally active")
            creator = _read_frozen_cohort(conn, review["creator_freeze_id"])
            prior = _read_frozen_cohort(conn, prior_freeze_id)
            niche = (_read_frozen_cohort(conn, review["niche_freeze_id"])
                     if review["niche_freeze_id"] is not None else None)
            subject = build_human_review_subject(
                {**config, "is_active": False}, creator, prior, niche,
                project_id=project_id,
                minimum_creator_posts=review["minimum_creator_posts"],
                minimum_prior_posts=review["minimum_prior_posts"],
                minimum_niche_posts=review["minimum_niche_posts"],
            )
            if (subject.subject_sha256 != review["subject_sha256"]
                    or subject.payload != review["review_payload"]
                    or subject.target_video_id != video_id
                    or subject.source_id != source_id
                    or subject.source_policy_version_id != source_policy_version_id
                    or subject.metric_version != metric_version):
                raise ContractError("review_subject_changed", "Review SHA, payload or pinned metric changed")
            activation = _record(conn.execute(
                """SELECT id,activation_assertion_sha256
                     FROM group_c_config_activation_events
                    WHERE kind='activated' AND project_id=%s AND config_id=%s
                      AND review_request_id=%s AND review_decision_id=%s
                      AND review_subject_sha256=%s
                    ORDER BY created_at DESC,id DESC LIMIT 1""",
                (project_id, config_id, review_request_id, review["decision_id"],
                 subject.subject_sha256),
            ))
            if activation is None or type(activation.get("activation_assertion_sha256")) is not str or not _SHA.fullmatch(activation["activation_assertion_sha256"]):
                raise ContractError("config_not_activated", "No exact signed activation event")
            _require_db_rights(conn, [creator, prior] + ([niche] if niche else []), checked_ports)
            legacy_prior = _record(conn.execute(
                """SELECT id,purpose,platform,content_type,source_id,
                          source_metric_definition,is_frozen,post_count,
                          prior_like_rate,prior_comment_rate,prior_share_rate
                     FROM comparison_cohorts WHERE id=%s""",
                (prior["legacy_cohort_id"],),
            ))
            if legacy_prior is None:
                raise ContractError("prior_cohort_not_approved", "Legacy prior cohort missing")
            members = _records(conn.execute(
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
                (project_id, prior["legacy_cohort_id"], prior_freeze_id),
            ))
            pl, pc, ps = _approved_prior_context(
                project_id=project_id, video_id=video_id,
                snapshot_id=snapshot_id, metadata_id=metadata_id,
                source_id=source_id, source_policy_version_id=source_policy_version_id,
                metric_version=metric_version, platform=bundle["platform"],
                content_type=bundle["c_content_type"],
                prior_freeze=prior, legacy_prior=legacy_prior, members=members,
            )
            if approval_verifier.verify_c5_use(
                conn, project_id=project_id, video_id=video_id,
                snapshot_id=snapshot_id, config_id=config_id,
                prior_freeze_id=prior_freeze_id,
                review_subject_sha256=subject.subject_sha256,
                decision_id=str(review["decision_id"]),
                activation_event_id=str(activation["id"]),
                reviewer_id=review["reviewer_id"],
                identity_provider=review["identity_provider"],
                authentication_event_id=review["authentication_event_id"],
                attestation_sha256=review["attestation_sha256"],
                activation_assertion_sha256=activation["activation_assertion_sha256"],
            ) is not True:
                raise ContractError("human_auth_unavailable", "Current signed IAM C5 approval denied")
            rates = compute_bayesian_engagement(
                current.views, current.likes, current.comments,
                current.shares, subject.bayesian_k, pl, pc, ps,
            )
            evidence = canonical_hash({
                "version": BAYESIAN_ENGAGEMENT_VERSION,
                "project_id": project_id, "video_id": video_id,
                "snapshot_id": snapshot_id, "metadata_id": metadata_id,
                "source_id": source_id,
                "source_policy_version_id": source_policy_version_id,
                "metric_version": metric_version, "config_id": config_id,
                "calculation_version": subject.calculation_version,
                "prior_freeze_id": prior_freeze_id,
                "prior_evidence_sha256": prior["evidence_sha256"],
                "review_subject_sha256": subject.subject_sha256,
                "decision_id": str(review["decision_id"]),
                "activation_event_id": str(activation["id"]),
                "views": current.views, "likes": current.likes,
                "comments": current.comments, "shares": current.shares,
                "k": str(rates.k_used), "p_l": str(pl), "p_c": str(pc),
                "p_s": str(ps),
                "like_rate_smoothed": str(rates.like_rate_smoothed),
                "comment_rate_smoothed": str(rates.comment_rate_smoothed),
                "share_rate_smoothed": str(rates.share_rate_smoothed),
                "weighted_engagement_smoothed": str(rates.weighted_engagement_smoothed),
            })
            return BayesianEngagementResult(
                project_id=project_id, video_id=video_id, snapshot_id=snapshot_id,
                metadata_id=metadata_id, source_id=source_id,
                source_policy_version_id=source_policy_version_id,
                metric_version=metric_version, config_id=config_id,
                calculation_version=subject.calculation_version,
                prior_freeze_id=prior_freeze_id,
                legacy_prior_cohort_id=prior["legacy_cohort_id"],
                review_request_id=str(review["id"]),
                review_decision_id=str(review["decision_id"]),
                activation_event_id=str(activation["id"]),
                review_subject_sha256=subject.subject_sha256,
                prior_evidence_sha256=prior["evidence_sha256"],
                rates=rates, evidence_sha256=evidence,
            )
