"""GC-MOD-10: serializable, evidence-bound immutable Group C cohort freezes.

Separate from GC09 candidate eligibility and GC11 human approval.
NO auto-approval, no config or score computation, no provider calls.
D1 candidate rights, timestamp, freshness, metric versions and membership
are reselected inside the SAME SERIALIZABLE transaction as all DB writes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Any

from .cohort_rules import (
    COHORT_RULES_VERSION, CohortRules, CohortSelection, PostgreSQLCohortCandidates,
)
from .contracts import ContractError, canonical_hash
from .d1 import local_d1_ports
from .ports import GroupCPorts

ESTIMATOR_VERSION = "group-c-cohort-estimator/v1"
_Q = Decimal("0.000000000001")
_LIMIT = Decimal("1000000000000000000")


@dataclass(frozen=True)
class FrozenCohortEvidence:
    """Pure draft statistics + original member snapshots, *not* DB approval."""
    selection: CohortSelection
    median_views: Decimal | None
    prior_like_rate: Decimal | None
    prior_comment_rate: Decimal | None
    prior_share_rate: Decimal | None
    evidence_sha256: str
    estimator_version: str = ESTIMATOR_VERSION


@dataclass(frozen=True)
class CohortFreezeReceipt:
    id: int
    legacy_cohort_id: int
    project_id: str
    purpose: str
    member_count: int
    rules_sha256: str
    selection_sha256: str
    evidence_sha256: str
    status: str = "frozen_unreviewed"
    approved: bool = False


def _q(value: Decimal) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ContractError("invalid_cohort_statistic", "Expected finite exact Decimal")
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.rounding = ROUND_HALF_UP
        try:
            result = value.quantize(_Q, rounding=ROUND_HALF_UP)
        except Exception as exc:
            raise ContractError("cohort_statistic_overflow", "Cannot quantize cohort statistic") from exc
    if result.copy_abs() >= _LIMIT:
        raise ContractError("cohort_statistic_overflow", "NUMERIC(30,12) range exceeded")
    return result


def build_frozen_evidence(selection: CohortSelection) -> FrozenCohortEvidence:
    """Compute reproducible median or pooled empirical rates with NO priors.

    This only computes observed cohort statistics for future reviewer approval.
    It deliberately NEVER invents alpha, k, minimum sample count or priors.
    """
    if not isinstance(selection, CohortSelection) or selection.approved is not False \
            or selection.status != "candidates_only":
        raise ContractError("invalid_cohort_selection", "Exact unapproved GC09 candidates required")
    records = selection.admitted
    if not records:
        raise ContractError("empty_cohort", "Empty peer cohort cannot be frozen")
    if len({x.video_id for x in records}) != len(records):
        raise ContractError("duplicate_peer_video", "Cannot freeze duplicate peers")
    if any(type(r.views) is not int or r.views < 0 for r in records):
        raise ContractError("invalid_raw_counter", "Invalid authoritative peer views")
    rules = selection.rules
    if any(r.video_id == rules.target_video_id for r in records):
        raise ContractError("self_inclusion", "Target is a peer")
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.rounding = ROUND_HALF_UP
        median = pl = pc = ps = None
        if rules.purpose in ("creator", "niche"):
            ordered = sorted(Decimal(r.views) for r in records)
            half = len(ordered) // 2
            median = _q(ordered[half] if len(ordered) % 2 else
                        (ordered[half - 1] + ordered[half]) / Decimal(2))
        elif rules.purpose == "engagement_prior":
            total_views = sum((Decimal(r.views) for r in records), Decimal(0))
            if total_views <= 0 or any(
                    r.views <= 0 or r.likes is None or r.comments is None or r.shares is None
                    for r in records):
                raise ContractError("missing_raw_counter", "Pooled rates require exact V>0/L/C/S")
            def pooled(key: str) -> Decimal:
                numerator = sum((Decimal(getattr(r, key)) for r in records), Decimal(0))
                rate = numerator / total_views
                if rate > 1:
                    raise ContractError("invalid_prior_rate", "Peer ratio >1; manual review required")
                return _q(rate)
            pl, pc, ps = pooled("likes"), pooled("comments"), pooled("shares")
        else:
            raise ContractError("invalid_cohort_purpose", "Unsupported estimator purpose")

        member_payload = [
            {"video_id": r.video_id, "snapshot_id": r.snapshot_id,
             "metadata_id": r.metadata_id,
             "source_policy_version_id": r.source_policy_version_id,
             "published_at": r.published_at.isoformat(),
             "observed_at": r.observed_at.isoformat(),
             "views": r.views, "likes": r.likes,
             "comments": r.comments, "shares": r.shares}
            for r in sorted(records, key=lambda m: (m.video_id, m.snapshot_id))
        ]
        digest = canonical_hash({
            "version": ESTIMATOR_VERSION,
            "rules_sha256": rules.sha256,
            "selection_sha256": selection.selection_sha256,
            "member_payload": member_payload,
            "median_views": str(median) if median is not None else None,
            "prior_like_rate": str(pl) if pl is not None else None,
            "prior_comment_rate": str(pc) if pc is not None else None,
            "prior_share_rate": str(ps) if ps is not None else None,
        })
        return FrozenCohortEvidence(
            selection=selection, median_views=median,
            prior_like_rate=pl, prior_comment_rate=pc,
            prior_share_rate=ps, evidence_sha256=digest,
        )


def _one(cursor: Any) -> dict | None:
    from .d1 import _record
    return _record(cursor)


def _rules_json(r: CohortRules) -> str:
    # Jsonb values are strings for timestamps and exact Decimal boundaries.
    return json.dumps({
        "schema_version": r.schema_version, "project_id": r.project_id,
        "target_video_id": r.target_video_id, "purpose": r.purpose,
        "platform": r.platform, "content_type": r.content_type,
        "source_id": r.source_id,
        "source_policy_version_id": r.source_policy_version_id,
        "metric_definition": r.metric_definition,
        "metric_version": r.metric_version,
        "evaluated_as_of": r.evaluated_as_of.isoformat(),
        "published_from": r.published_from.isoformat(),
        "published_before": r.published_before.isoformat(),
        "min_age_hours": str(r.min_age_hours),
        "max_age_hours": str(r.max_age_hours),
        "creator_id": r.creator_id, "topic_id": r.topic_id,
        "max_candidates": r.max_candidates,
    }, sort_keys=True, separators=(",", ":"))


class PostgreSQLCohortFreezeWriter:
    """Freeze historical C comparison rows under one SERIALIZABLE transaction."""

    def freeze(self, conn: Any, *, rules: CohortRules,
               target_snapshot_id: int, target_metadata_id: int,
               ports: GroupCPorts | None = None) -> CohortFreezeReceipt:
        if not isinstance(rules, CohortRules):
            raise ContractError("invalid_cohort_rule", "CohortRules required")
        if type(target_snapshot_id) is not int or target_snapshot_id <= 0 \
                or type(target_metadata_id) is not int or target_metadata_id <= 0:
            raise ContractError("invalid_identifier", "Exact target D1 snapshot/metadata IDs")
        if getattr(conn, "autocommit", None) is not True:
            raise ContractError("transaction_contract_unavailable",
                                "Freeze requires top-level autocommit connection")
        if ports is not None and not isinstance(ports, GroupCPorts):
            raise ContractError("invalid_ports", "Explicit GroupCPorts required")
        adapters = ports if ports is not None else local_d1_ports()

        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            selected = PostgreSQLCohortCandidates().select_locked(
                conn, rules=rules, target_snapshot_id=target_snapshot_id,
                target_metadata_id=target_metadata_id, ports=adapters,
            )
            evidence = build_frozen_evidence(selected)
            existing = _one(conn.execute(
                """SELECT id,legacy_cohort_id,project_id,purpose,member_count,
                          rules_sha256,selection_sha256,evidence_sha256
                     FROM group_c_cohort_freezes
                    WHERE project_id=%s AND purpose=%s AND rules_sha256=%s""",
                (rules.project_id, rules.purpose, rules.sha256),
            ))
            if existing:
                if existing["selection_sha256"] != selected.selection_sha256 \
                        or existing["evidence_sha256"] != evidence.evidence_sha256 \
                        or existing["member_count"] != len(selected.admitted):
                    raise ContractError("cohort_selection_conflict",
                                        "Changed raw evidence requires new explicit rules version")
                # Existing frozen membership remains in DB, it is not rebuilt.
                return CohortFreezeReceipt(
                    id=existing["id"], legacy_cohort_id=existing["legacy_cohort_id"],
                    project_id=existing["project_id"], purpose=existing["purpose"],
                    member_count=existing["member_count"],
                    rules_sha256=existing["rules_sha256"],
                    selection_sha256=existing["selection_sha256"],
                    evidence_sha256=existing["evidence_sha256"],
                )
            windows_age = json.dumps({
                "min_age_hours": str(rules.min_age_hours),
                "max_age_hours": str(rules.max_age_hours),
            }, sort_keys=True)
            windows_time = json.dumps({
                "published_from": rules.published_from.isoformat(),
                "published_before": rules.published_before.isoformat(),
            }, sort_keys=True)
            cohort = _one(conn.execute(
                """INSERT INTO comparison_cohorts
                     (purpose,definition_version,platform,creator_id,topic_id,
                      content_type,source_id,source_metric_definition,evaluated_as_of,
                      age_window,time_window,post_count,median_views,
                      prior_like_rate,prior_comment_rate,prior_share_rate,is_frozen)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,
                          %s,%s,%s,%s,%s,FALSE) RETURNING id""",
                (rules.purpose, COHORT_RULES_VERSION, rules.platform, rules.creator_id,
                 rules.topic_id, rules.content_type, rules.source_id,
                 rules.metric_definition, rules.evaluated_as_of,
                 windows_age, windows_time, len(selected.admitted),
                 evidence.median_views, evidence.prior_like_rate,
                 evidence.prior_comment_rate, evidence.prior_share_rate),
            ))
            if cohort is None:
                raise ContractError("cohort_write_failed", "Missing cohort insert result")
            cohort_id = cohort["id"]
            freeze = _one(conn.execute(
                """INSERT INTO group_c_cohort_freezes
                     (project_id,legacy_cohort_id,target_video_id,target_snapshot_id,
                      target_metadata_id,purpose,source_id,source_policy_version_id,
                      source_metric_definition,source_metric_version,rules_version,
                      rules_sha256,selection_sha256,evidence_sha256,rules_payload,
                      estimator_version,member_count,median_views,
                      prior_like_rate,prior_comment_rate,prior_share_rate)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                          %s::jsonb,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (rules.project_id, cohort_id, rules.target_video_id, target_snapshot_id,
                 target_metadata_id, rules.purpose, rules.source_id,
                 rules.source_policy_version_id, rules.metric_definition,
                 rules.metric_version, COHORT_RULES_VERSION, rules.sha256,
                 selected.selection_sha256, evidence.evidence_sha256,
                 _rules_json(rules), ESTIMATOR_VERSION, len(selected.admitted),
                 evidence.median_views, evidence.prior_like_rate,
                 evidence.prior_comment_rate, evidence.prior_share_rate),
            ))
            if freeze is None:
                raise ContractError("cohort_write_failed", "Missing provenance freeze row")
            freeze_id = freeze["id"]
            for member in selected.admitted:
                conn.execute(
                    """INSERT INTO comparison_cohort_members
                       (cohort_id,comparison_post_id,comparison_snapshot_id)
                       VALUES(%s,%s,%s)""",
                    (cohort_id, member.video_id, member.snapshot_id),
                )
                md = canonical_hash({
                    "freeze": evidence.evidence_sha256,
                    "video_id": member.video_id, "snapshot_id": member.snapshot_id,
                    "metadata_id": member.metadata_id,
                    "observed_at": member.observed_at.isoformat(),
                    "published_at": member.published_at.isoformat(),
                    "views": member.views, "likes": member.likes,
                    "comments": member.comments, "shares": member.shares,
                })
                conn.execute(
                    """INSERT INTO group_c_cohort_freeze_members
                       (freeze_id,video_id,snapshot_id,metadata_id,source_id,
                        source_policy_version_id,metric_definition,metric_version,
                        published_at,observed_at,views,likes,comments,shares,evidence_sha256)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (freeze_id,member.video_id,member.snapshot_id,member.metadata_id,
                     rules.source_id,member.source_policy_version_id,
                     rules.metric_definition,rules.metric_version,
                     member.published_at,member.observed_at,member.views,
                     member.likes,member.comments,member.shares,md),
                )
            conn.execute(
                "UPDATE comparison_cohorts SET is_frozen=TRUE WHERE id=%s",
                (cohort_id,),
            )
            # The DEFERRABLE commit-time constraint trigger validates exact
            # parent/member counts and frozen legacy state in the same tx.
            return CohortFreezeReceipt(
                id=freeze_id, legacy_cohort_id=cohort_id,
                project_id=rules.project_id, purpose=rules.purpose,
                member_count=len(selected.admitted),
                rules_sha256=rules.sha256,
                selection_sha256=selected.selection_sha256,
                evidence_sha256=evidence.evidence_sha256,
            )
