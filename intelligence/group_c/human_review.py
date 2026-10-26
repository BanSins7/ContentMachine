"""GC-MOD-11: explicitly authenticated human review of exact frozen C evidence.

Request creation NEVER grants approval. Decision insertion has a MANDATORY,
externally-trusted human identity/authorization verifier (no default stub).
Approvals pin config alpha/k, source view definition/version, each frozen
member evidence hash and explicit minimum peer counts. GC-MOD-12 must
revalidate current source rights and activate configs separately.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from .contracts import (
    BREAKOUT_WEIGHTS, FRESHNESS_HALF_LIFE_H,
    ContractError, canonical_hash,
)
from .d1 import local_d1_ports, _record, _records
from .ports import GroupCPorts

REVIEW_SCHEMA_VERSION = "group-c-human-review/v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_MAX_FREEZE_MEMBERS = 500
_EXPECTED_WEIGHTS = dict(zip(
    ("creator_ratio","views_growth_per_hour",
     "weighted_engagement_smoothed","freshness_score"),
    BREAKOUT_WEIGHTS,
))


@dataclass(frozen=True)
class HumanReviewSubject:
    """Immutable review payload: the decision covers these EXACT values."""

    project_id: str
    target_video_id: int
    source_id: str
    source_policy_version_id: int
    metric_version: str
    calculation_config_id: int
    calculation_version: str
    alpha: Decimal
    bayesian_k: Decimal
    creator_freeze_id: int
    niche_freeze_id: int | None
    prior_freeze_id: int
    minimum_creator_posts: int
    minimum_niche_posts: int | None
    minimum_prior_posts: int
    payload: dict
    subject_sha256: str
    schema_version: str = REVIEW_SCHEMA_VERSION


@dataclass(frozen=True)
class VerifiedHumanReviewer:
    """Only a trusted reviewer-verifier may return this verified identity.

    This class alone proves NOTHING if constructed by untrusted application
    code. The verifier must authenticate a signed human action, project
    authorization, binding to the exact review subject + decision and replay
    nonce, and return this value only on success.
    """

    reviewer_id: str
    identity_provider: str
    authentication_event_id: str
    attestation_sha256: str


class HumanReviewerVerifier(Protocol):
    def verify(self, conn: Any, *, signed_assertion: str,
               project_id: str, subject_sha256: str,
               decision: str) -> VerifiedHumanReviewer: ...


@dataclass(frozen=True)
class ReviewReceipt:
    request_id: str
    subject_sha256: str
    status: str
    decision_id: str | None = None
    approved: bool = False


def _id(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ContractError("invalid_review_identifier", field + " must be positive integer")
    return value


def _min(value: Any, field: str) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_FREEZE_MEMBERS:
        raise ContractError("invalid_review_minimum", field + " must be explicit 1..500")
    return value


def _sha(value: Any, field: str) -> str:
    if type(value) is not str or not _SHA.fullmatch(value):
        raise ContractError("invalid_review_evidence", "Invalid SHA256 " + field)
    return value


def _decimal(value: Any, field: str) -> Decimal:
    if type(value) not in (str, int, Decimal):
        raise ContractError("invalid_review_config", field + " must be exact positive numeric")
    try:
        v = Decimal(str(value))
    except Exception as exc:
        raise ContractError("invalid_review_config", "Invalid " + field) from exc
    if not v.is_finite() or v <= 0 or v >= Decimal("1e18"):
        raise ContractError("invalid_review_config", "Invalid or zero " + field)
    if v.as_tuple().exponent < -12:
        raise ContractError("invalid_review_config", "Numeric scale >12 " + field)
    return v


def _weights(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != set(_EXPECTED_WEIGHTS):
        return False
    try:
        return all(
            type(value[key]) not in (bool, type(None)) and
            Decimal(str(value[key])) == expected
            for key, expected in _EXPECTED_WEIGHTS.items()
        )
    except Exception:
        return False


def _freeze_payload(row: Mapping[str, Any], expected_purpose: str,
                    project_id: str, target_id: int,
                    source_id: str, policy_id: int,
                    metric_version: str, minimum: int) -> dict:
    if row is None or row.get("purpose") != expected_purpose \
            or row.get("project_id") != project_id \
            or row.get("target_video_id") != target_id \
            or row.get("source_id") != source_id \
            or row.get("source_policy_version_id") != policy_id \
            or row.get("source_metric_definition") != "platform_post_views" \
            or row.get("source_metric_version") != metric_version \
            or row.get("state") != "frozen_unreviewed":
        raise ContractError("review_cohort_incompatible",
                            "Requested freeze is not exact verified Group C evidence")
    count = row.get("member_count")
    if type(count) is not int or count < minimum:
        raise ContractError("review_cohort_too_small",
                            "Explicit reviewer minimum not satisfied")
    for field in ("rules_sha256", "selection_sha256", "evidence_sha256"):
        _sha(row.get(field), field)
    return {
        "freeze_id": row["id"],
        "legacy_cohort_id": row["legacy_cohort_id"],
        "purpose": expected_purpose,
        "member_count": count,
        "rules_sha256": row["rules_sha256"],
        "selection_sha256": row["selection_sha256"],
        "evidence_sha256": row["evidence_sha256"],
        "metric_version": row["source_metric_version"],
        "median_views": str(row["median_views"]) if row["median_views"] is not None else None,
        "prior_like_rate": str(row["prior_like_rate"]) if row["prior_like_rate"] is not None else None,
        "prior_comment_rate": str(row["prior_comment_rate"]) if row["prior_comment_rate"] is not None else None,
        "prior_share_rate": str(row["prior_share_rate"]) if row["prior_share_rate"] is not None else None,
    }


def build_human_review_subject(
    config: Mapping[str, Any],
    creator: Mapping[str, Any],
    prior: Mapping[str, Any],
    niche: Mapping[str, Any] | None, *,
    project_id: str,
    minimum_creator_posts: int,
    minimum_prior_posts: int,
    minimum_niche_posts: int | None,
) -> HumanReviewSubject:
    """Pure deterministic review fingerprint; NOT reviewer authentication."""
    if type(project_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", project_id):
        raise ContractError("invalid_project_id", "Canonical project ID required")
    if not all(isinstance(r, Mapping) for r in (config,creator,prior)):
        raise ContractError("missing_review_evidence", "Exact frozen cohorts/config required")
    if niche is not None and not isinstance(niche, Mapping):
        raise ContractError("missing_review_evidence", "Exact niche evidence required")
    mc = _min(minimum_creator_posts, "minimum_creator_posts")
    mp = _min(minimum_prior_posts, "minimum_prior_posts")
    if (niche is None) != (minimum_niche_posts is None):
        raise ContractError("invalid_review_minimum", "Niche requires explicit reviewed minimum")
    mn = _min(minimum_niche_posts, "minimum_niche_posts") if niche is not None else None
    config_id = _id(config.get("id"), "calculation_config_id")
    calc_version = config.get("calculation_version")
    if type(calc_version) is not str or not 1 <= len(calc_version) <= 120:
        raise ContractError("invalid_review_config", "Exact config version required")
    alpha = _decimal(config.get("alpha"), "alpha")
    k = _decimal(config.get("bayesian_k"), "bayesian_k")
    if (config.get("is_active") is not False
            or config.get("normalization_enabled") is not False
            or config.get("freshness_half_life_h") != FRESHNESS_HALF_LIFE_H
            or not _weights(config.get("weights_json"))):
        raise ContractError("invalid_review_config", "Config must be inactive and have fixed C weights")
    target = _id(creator.get("target_video_id"), "target_video_id")
    policy = _id(creator.get("source_policy_version_id"), "source_policy_version_id")
    source = creator.get("source_id")
    version = creator.get("source_metric_version")
    if type(source) is not str or not source.strip() or len(source) > 250:
        raise ContractError("invalid_review_source", "Explicit pinned D1 source required")
    if type(version) is not str or not version.strip() or len(version) > 120:
        raise ContractError("invalid_review_version", "Pinned exact view metric version required")
    peers = [
        _freeze_payload(creator,"creator",project_id,target,source,policy,version,mc),
        _freeze_payload(prior,"engagement_prior",project_id,target,source,policy,version,mp),
    ]
    if niche is not None:
        peers.append(_freeze_payload(niche,"niche",project_id,target,source,policy,version,mn))
    ids = [p["freeze_id"] for p in peers]
    if len(ids) != len(set(ids)):
        raise ContractError("review_cohort_incompatible", "Same freeze cannot fill different purposes")
    data = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "project_id": project_id,
        "target_video_id": target,
        "source_id": source,
        "source_policy_version_id": policy,
        "metric_definition": "platform_post_views",
        "metric_version": version,
        "calculation_config_id": config_id,
        "calculation_version": calc_version,
        "alpha": str(alpha),
        "bayesian_k": str(k),
        "freshness_half_life_h": str(FRESHNESS_HALF_LIFE_H),
        "normalization_enabled": False,
        "weights": {k: str(v) for k,v in _EXPECTED_WEIGHTS.items()},
        "minimum_creator_posts": mc,
        "minimum_prior_posts": mp,
        "minimum_niche_posts": mn,
        "frozen_cohorts": peers,
    }
    return HumanReviewSubject(
        project_id=project_id, target_video_id=target, source_id=source,
        source_policy_version_id=policy, metric_version=version,
        calculation_config_id=config_id, calculation_version=calc_version,
        alpha=alpha, bayesian_k=k,
        creator_freeze_id=peers[0]["freeze_id"],
        prior_freeze_id=peers[1]["freeze_id"],
        niche_freeze_id=peers[2]["freeze_id"] if len(peers) > 2 else None,
        minimum_creator_posts=mc, minimum_prior_posts=mp,
        minimum_niche_posts=mn, payload=data,
        subject_sha256=canonical_hash(data),
    )


def _fetch_freeze(conn: Any, ident: int) -> dict:
    row = _record(conn.execute(
        """SELECT id,project_id,target_video_id,source_id,
                  source_policy_version_id,source_metric_definition,
                  source_metric_version,purpose,state,member_count,
                  legacy_cohort_id,rules_sha256,selection_sha256,
                  evidence_sha256,median_views,prior_like_rate,
                  prior_comment_rate,prior_share_rate,target_snapshot_id,
                  target_metadata_id
             FROM group_c_cohort_freezes WHERE id=%s FOR SHARE""",
        (_id(ident,"freeze_id"),),
    ))
    if row is None:
        raise ContractError("missing_review_evidence", "Exact freeze not found")
    return row


def _require_db_rights(conn: Any, rows: list[dict], ports: GroupCPorts) -> None:
    adapter = ports.require("source_policy",conn)
    for row in rows:
        adapter.assert_eligible(
            conn,row["source_id"],row["target_video_id"],
            [row["target_snapshot_id"]],row["target_metadata_id"],[],
        )
        members = _records(conn.execute(
            """SELECT video_id,snapshot_id,metadata_id
                 FROM group_c_cohort_freeze_members WHERE freeze_id=%s
                 ORDER BY video_id""",(row["id"],),
        ))
        if len(members)!=row["member_count"]:
            raise ContractError("review_cohort_incompatible","Frozen member count altered")
        for member in members:
            adapter.assert_eligible(
                conn,row["source_id"],member["video_id"],
                [member["snapshot_id"]],member["metadata_id"],[],
            )


def _receipt(row: Mapping[str, Any], status: str, *,
             decision_id: str | None = None, approved: bool = False) -> ReviewReceipt:
    return ReviewReceipt(request_id=str(row["id"]),
                         subject_sha256=row["subject_sha256"],
                         status=status,decision_id=decision_id,
                         approved=approved)


class PostgreSQLHumanReview:
    """Append-only request + human decision; no implicit approval path."""

    def create_request(
        self, conn: Any, *, project_id: str, config_id: int,
        creator_freeze_id: int, prior_freeze_id: int,
        niche_freeze_id: int | None = None,
        minimum_creator_posts: int, minimum_prior_posts: int,
        minimum_niche_posts: int | None = None,
        ports: GroupCPorts | None = None,
    ) -> ReviewReceipt:
        if getattr(conn,"autocommit",None) is not True:
            raise ContractError("transaction_contract_unavailable","Dedicated autocommit required")
        adapters = ports if ports is not None else local_d1_ports()
        if not isinstance(adapters,GroupCPorts):
            raise ContractError("invalid_ports","Expected Group C ports")
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            config = _record(conn.execute(
                """SELECT id,calculation_version,alpha,bayesian_k,is_active,
                          normalization_enabled,freshness_half_life_h,weights_json
                     FROM metric_calculation_configs WHERE id=%s FOR SHARE""",
                (_id(config_id,"config_id"),),
            ))
            if config is None:
                raise ContractError("missing_review_evidence","Exact C config not found")
            creator = _fetch_freeze(conn,creator_freeze_id)
            prior = _fetch_freeze(conn,prior_freeze_id)
            niche = _fetch_freeze(conn,niche_freeze_id) if niche_freeze_id is not None else None
            subject = build_human_review_subject(
                config,creator,prior,niche,project_id=project_id,
                minimum_creator_posts=minimum_creator_posts,
                minimum_prior_posts=minimum_prior_posts,
                minimum_niche_posts=minimum_niche_posts,
            )
            _require_db_rights(conn,[creator,prior]+([niche] if niche else []),adapters)
            existing = _record(conn.execute(
                """SELECT id,subject_sha256
                     FROM group_c_review_requests
                    WHERE project_id=%s AND subject_sha256=%s""",
                (project_id,subject.subject_sha256),
            ))
            if existing is not None:
                return _receipt(existing,"pending_human_review")
            inserted = _record(conn.execute(
                """INSERT INTO group_c_review_requests (
                     project_id,target_video_id,source_id,source_policy_version_id,
                     metric_definition,metric_version,calculation_config_id,
                     calculation_version,alpha,bayesian_k,
                     creator_freeze_id,niche_freeze_id,prior_freeze_id,
                     minimum_creator_posts,minimum_niche_posts,minimum_prior_posts,
                     subject_sha256,review_payload)
                   VALUES(%s,%s,%s,%s,'platform_post_views',%s,%s,%s,%s,%s,
                          %s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                   RETURNING id,subject_sha256""",
                (subject.project_id,subject.target_video_id,subject.source_id,
                 subject.source_policy_version_id,subject.metric_version,
                 subject.calculation_config_id,subject.calculation_version,
                 subject.alpha,subject.bayesian_k,subject.creator_freeze_id,
                 subject.niche_freeze_id,subject.prior_freeze_id,
                 subject.minimum_creator_posts,subject.minimum_niche_posts,
                 subject.minimum_prior_posts,subject.subject_sha256,
                 json.dumps(subject.payload,ensure_ascii=False,sort_keys=True)),
            ))
            if inserted is None:
                raise ContractError("review_write_failed","Review request INSERT returned nothing")
            return _receipt(inserted,"pending_human_review")

    def record_decision(
        self, conn: Any, *, request_id: str, decision: str,
        signed_assertion: str, note: str,
        reviewer_verifier: HumanReviewerVerifier | None,
    ) -> ReviewReceipt:
        if getattr(conn,"autocommit",None) is not True:
            raise ContractError("transaction_contract_unavailable","Dedicated autocommit required")
        if decision not in ("approved","rejected"):
            raise ContractError("invalid_review_decision","Explicit approve or reject required")
        if reviewer_verifier is None or not callable(getattr(reviewer_verifier,"verify",None)):
            raise ContractError("human_auth_unavailable","Trusted human IAM verifier is required")
        if type(signed_assertion) is not str or len(signed_assertion)<24:
            raise ContractError("human_auth_unavailable","Signed assertion required")
        if type(note) is not str or not 12<=len(note.strip())<=4000:
            raise ContractError("invalid_review_note","Meaningful explicit reviewer note required")
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            row = _record(conn.execute(
                """SELECT id,project_id,subject_sha256
                     FROM group_c_review_requests WHERE id=%s FOR SHARE""",
                (request_id,),
            ))
            if row is None:
                raise ContractError("missing_review_subject","Unknown immutable subject")
            verified = reviewer_verifier.verify(
                conn,signed_assertion=signed_assertion,project_id=row["project_id"],
                subject_sha256=row["subject_sha256"],decision=decision,
            )
            if not isinstance(verified,VerifiedHumanReviewer):
                raise ContractError("human_auth_unavailable","Reviewer signature/authorization not verified")
            if (type(verified.reviewer_id) is not str or
                not 3<=len(verified.reviewer_id.strip())<=200 or
                verified.reviewer_id.lower() in ("system","bot","automation","anonymous")
                or type(verified.identity_provider) is not str or
                not 3<=len(verified.identity_provider.strip())<=200
                or verified.identity_provider.lower() in ("unverified","self-reported")
                or type(verified.authentication_event_id) is not str or
                not 8<=len(verified.authentication_event_id.strip())<=256):
                raise ContractError("human_auth_unavailable","Invalid reviewed identity provenance")
            _sha(verified.attestation_sha256,"reviewer attestation")
            old = _record(conn.execute(
                """SELECT id,decision,reviewer_id,identity_provider,
                          authentication_event_id,attestation_sha256,reviewer_note
                     FROM group_c_review_decisions WHERE request_id=%s""",
                (request_id,),
            ))
            if old is not None:
                if any((
                    old["decision"] != decision,
                    old["reviewer_id"] != verified.reviewer_id,
                    old["identity_provider"] != verified.identity_provider,
                    old["authentication_event_id"] != verified.authentication_event_id,
                    old["attestation_sha256"] != verified.attestation_sha256,
                    old["reviewer_note"] != note,
                )):
                    raise ContractError("review_decision_conflict",
                                        "Immutable human decision cannot be changed")
                return _receipt(row,decision,decision_id=str(old["id"]),
                                approved=decision=="approved")
            inserted = _record(conn.execute(
                """INSERT INTO group_c_review_decisions
                     (request_id,project_id,subject_sha256,decision,
                      reviewer_id,identity_provider,authentication_event_id,
                      attestation_sha256,reviewer_note)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (request_id,row["project_id"],row["subject_sha256"],decision,
                 verified.reviewer_id,verified.identity_provider,
                 verified.authentication_event_id,verified.attestation_sha256,note),
            ))
            if inserted is None:
                raise ContractError("review_write_failed","Review decision INSERT returned nothing")
            return _receipt(row,decision,decision_id=str(inserted["id"]),
                            approved=decision=="approved")

    def read_status(self, conn: Any, *, request_id: str) -> ReviewReceipt:
        row = _record(conn.execute(
            """SELECT r.id,r.subject_sha256,d.id AS decision_id,d.decision
                 FROM group_c_review_requests r
                 LEFT JOIN group_c_review_decisions d ON d.request_id=r.id
                WHERE r.id=%s""",(request_id,),
        ))
        if row is None:
            raise ContractError("missing_review_subject","No exact request")
        if row["decision"] is None:
            return _receipt(row,"pending_human_review")
        return _receipt(row,row["decision"],decision_id=str(row["decision_id"]),
                        approved=row["decision"]=="approved")
