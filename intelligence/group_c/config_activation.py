"""GC-MOD-12: explicit globally-single-active C config, no C7 scoring.

An externally-trusted IAM adapter MUST freshly attest the existing human
approval (signed actor/project/action) before any activation. An approved
Stage11 row alone is insufficient. Revalidate exact config/cohort SHA and
current D1 source/retention rights inside one SERIALIZABLE write transaction.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from .contracts import ContractError
from .d1 import _record, local_d1_ports
from .human_review import (
    _fetch_freeze, _require_db_rights, build_human_review_subject,
)
from .ports import GroupCPorts

ACTIVATION_VERSION = "group-c-config-activation/v1"


class HumanApprovalAttestationVerifier(Protocol):
    """Trusted IAM-backed service, NOT the verifier in synthetic tests.

    Must revalidate original signed human event, still-authorized project
    reviewer role, non-revocation, original exact approved decision/subject,
    and signature/event replay history, plus a separately signed present-time
    GLOBAL config activation action bound to exact old/new version IDs. Return literal True only if trusted.
    """

    def verify_approval(
        self, conn: Any, *, project_id: str, subject_sha256: str,
        decision_id: str, reviewer_id: str, identity_provider: str,
        authentication_event_id: str, attestation_sha256: str,
        signed_activation_assertion: str, capability: str,
        activating_config_id: int, replacing_config_id: int | None,
    ) -> bool: ...


@dataclass(frozen=True)
class ConfigActivationIntent:
    project_id: str
    review_request_id: str
    expected_config_id: int
    replace_active_config_id: int | None
    schema_version: str = ACTIVATION_VERSION


def validate_activation_intent(
    *, project_id: str, review_request_id: str, expected_config_id: int,
    replace_active_config_id: int | None,
) -> ConfigActivationIntent:
    if type(project_id) is not str or len(project_id)!=32 \
            or any(c not in "0123456789abcdef" for c in project_id):
        raise ContractError("invalid_project_id","Canonical project required")
    _id(expected_config_id,"expected_config_id")
    if replace_active_config_id is not None:
        _id(replace_active_config_id,"replace_active_config_id")
        if replace_active_config_id==expected_config_id:
            raise ContractError("invalid_activation_identifier",
                                "Replacement must be another config version")
    if type(review_request_id) is not str:
        raise ContractError("missing_review_subject","Canonical review UUID required")
    try:
        canonical=str(UUID(review_request_id))
    except (ValueError,TypeError,AttributeError) as exc:
        raise ContractError("missing_review_subject","Canonical review UUID required") from exc
    if canonical!=review_request_id:
        raise ContractError("missing_review_subject","Canonical review UUID required")
    return ConfigActivationIntent(project_id,review_request_id,expected_config_id,
                                  replace_active_config_id)


@dataclass(frozen=True)
class ConfigActivationReceipt:
    project_id: str
    config_id: int
    calculation_version: str
    review_request_id: str
    review_decision_id: str
    subject_sha256: str
    activation_event_id: str
    previous_config_id: int | None
    status: str = "active"
    approved_review: bool = True
    schema_version: str = ACTIVATION_VERSION


def _id(v: Any, field: str) -> int:
    if type(v) is not int or v <= 0:
        raise ContractError("invalid_activation_identifier",field+" must be positive integer")
    return v


def _receipt(row: Mapping[str, Any], active: Mapping[str, Any]) -> ConfigActivationReceipt:
    return ConfigActivationReceipt(
        project_id=row["project_id"], config_id=row["calculation_config_id"],
        calculation_version=row["calculation_version"],
        review_request_id=str(row["id"]),
        review_decision_id=str(row["decision_id"]),
        subject_sha256=row["subject_sha256"],
        activation_event_id=str(active["id"]),
        previous_config_id=active["previous_config_id"],
    )


class PostgreSQLConfigActivation:
    """One explicit global switch, one review, no auto-created defaults."""

    def activate(
        self, conn: Any, *, project_id: str, review_request_id: str,
        expected_config_id: int,
        replace_active_config_id: int | None,
        signed_activation_assertion: str,
        approval_verifier: HumanApprovalAttestationVerifier | None,
        ports: GroupCPorts | None = None,
    ) -> ConfigActivationReceipt:
        if getattr(conn,"autocommit",None) is not True:
            raise ContractError("transaction_contract_unavailable",
                                "Config activation needs top-level autocommit connection")
        validate_activation_intent(
            project_id=project_id, review_request_id=review_request_id,
            expected_config_id=expected_config_id,
            replace_active_config_id=replace_active_config_id,
        )
        if (type(signed_activation_assertion) is not str
                or len(signed_activation_assertion)<32):
            raise ContractError("human_auth_unavailable",
                                "Separately signed global config activation required")
        if approval_verifier is None or not callable(getattr(approval_verifier,"verify_approval",None)):
            raise ContractError("human_auth_unavailable",
                                "Live trusted external human attestation verifier required")
        adapters=ports if ports is not None else local_d1_ports()
        if not isinstance(adapters,GroupCPorts):
            raise ContractError("invalid_ports","Group C source-policy ports required")
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            row=_record(conn.execute(
                """SELECT r.id,r.project_id,r.subject_sha256,r.review_payload,
                          r.calculation_config_id,r.calculation_version,
                          r.creator_freeze_id,r.prior_freeze_id,r.niche_freeze_id,
                          r.minimum_creator_posts,r.minimum_prior_posts,
                          r.minimum_niche_posts,
                          d.id AS decision_id,d.decision,d.reviewer_id,
                          d.identity_provider,d.authentication_event_id,
                          d.attestation_sha256
                     FROM group_c_review_requests r
                     LEFT JOIN group_c_review_decisions d ON d.request_id=r.id
                    WHERE r.id=%s""",
                (review_request_id,),
            ))
            if row is None or row["project_id"]!=project_id:
                raise ContractError("missing_review_subject","Request not found for project")
            if row["decision"]!="approved" or row["decision_id"] is None:
                raise ContractError("review_not_approved","Explicit human approved decision required")
            if row["calculation_config_id"]!=expected_config_id:
                raise ContractError("review_config_mismatch","Requested config version not human-approved")
            config=_record(conn.execute(
                """SELECT id,calculation_version,alpha,bayesian_k,
                          prior_source_policy,cohort_selection_rules,growth_policy,
                          is_active,normalization_enabled,freshness_half_life_h,
                          weights_json
                     FROM metric_calculation_configs WHERE id=%s FOR SHARE""",
                (expected_config_id,),
            ))
            if config is None:
                raise ContractError("review_config_mismatch","Reviewed config no longer exists")
            # The pure review subject was made while config.is_active=False.
            # For idempotent replay, only that boolean may have changed to True.
            if config["is_active"] not in (False,True) or type(config["is_active"]) is not bool:
                raise ContractError("invalid_activation_config","Unknown config activation state")
            neutral_config={**config,"is_active":False}
            creator=_fetch_freeze(conn,row["creator_freeze_id"])
            prior=_fetch_freeze(conn,row["prior_freeze_id"])
            niche=(_fetch_freeze(conn,row["niche_freeze_id"])
                   if row["niche_freeze_id"] is not None else None)
            proof=build_human_review_subject(
                neutral_config,creator,prior,niche,project_id=project_id,
                minimum_creator_posts=row["minimum_creator_posts"],
                minimum_prior_posts=row["minimum_prior_posts"],
                minimum_niche_posts=row["minimum_niche_posts"],
            )
            if proof.subject_sha256!=row["subject_sha256"] or proof.payload!=row["review_payload"] \
                    or proof.calculation_config_id!=expected_config_id:
                raise ContractError("review_subject_changed",
                                    "Exact human-reviewed config/peer provenance changed")
            _require_db_rights(
                conn,[creator,prior]+([niche] if niche is not None else []),
                adapters,
            )
            # A database row is not an independently verified IAM signature.
            trust=approval_verifier.verify_approval(
                conn,project_id=project_id,subject_sha256=row["subject_sha256"],
                decision_id=str(row["decision_id"]),reviewer_id=row["reviewer_id"],
                identity_provider=row["identity_provider"],
                authentication_event_id=row["authentication_event_id"],
                attestation_sha256=row["attestation_sha256"],
                signed_activation_assertion=signed_activation_assertion,
                capability="group_c.activate_global_config",
                activating_config_id=expected_config_id,
                replacing_config_id=replace_active_config_id,
            )
            if trust is not True:
                raise ContractError("human_auth_unavailable",
                                    "Approval signature, role or grant not currently trusted")
            action_sha=hashlib.sha256(signed_activation_assertion.encode("utf-8")).hexdigest()
            previous=_record(conn.execute(
                """SELECT id FROM metric_calculation_configs
                    WHERE is_active=TRUE FOR UPDATE""",
            ))
            current_active_id=previous["id"] if previous is not None else None
            already=_record(conn.execute(
                """SELECT id,previous_config_id FROM group_c_config_activation_events
                    WHERE review_request_id=%s AND config_id=%s AND kind='activated'""",
                (review_request_id,expected_config_id),
            ))
            if already is not None:
                if current_active_id!=expected_config_id or config["is_active"] is not True:
                    raise ContractError("activation_superseded",
                                        "Historical approval cannot reactivate a retired config")
                # Same subject, same event, same current permissions/IAM: safe replay.
                return _receipt(row,already)
            if config["is_active"] is not False:
                raise ContractError("activation_conflict","Config active without this reviewed event")
            if current_active_id!=replace_active_config_id:
                raise ContractError("active_config_conflict",
                                    "Global active config must exactly match explicit replacement")
            if previous is not None:
                # Old config inputs remain immutable; ONLY is_active may change.
                conn.execute(
                    "UPDATE metric_calculation_configs SET is_active=FALSE WHERE id=%s",
                    (current_active_id,),
                )
                conn.execute(
                    """INSERT INTO group_c_config_activation_events
                       (project_id,config_id,review_request_id,review_decision_id,
                        review_subject_sha256,activation_assertion_sha256,
                        kind,replacement_config_id)
                       VALUES(%s,%s,%s,%s,%s,%s,'superseded',%s)""",
                    (project_id,current_active_id,review_request_id,
                     row["decision_id"],row["subject_sha256"],action_sha,expected_config_id),
                )
            conn.execute(
                "UPDATE metric_calculation_configs SET is_active=TRUE WHERE id=%s",
                (expected_config_id,),
            )
            activation=_record(conn.execute(
                """INSERT INTO group_c_config_activation_events
                   (project_id,config_id,review_request_id,review_decision_id,
                    review_subject_sha256,activation_assertion_sha256,
                    kind,previous_config_id)
                   VALUES(%s,%s,%s,%s,%s,%s,'activated',%s)
                   RETURNING id,previous_config_id""",
                (project_id,expected_config_id,review_request_id,
                 row["decision_id"],row["subject_sha256"],action_sha,current_active_id),
            ))
            if activation is None:
                raise ContractError("activation_write_failed","Missing activation event")
            # Deferred SQL triggers check both state transitions at COMMIT.
            return _receipt(row,activation)

    def read_approval(self, conn: Any, *, project_id: str,
                      config_id: int) -> dict:
        """Read global activation evidence; NOT score-ready approval proof.

        A worker must still recheck rights, IAM and frozen lineage.
        """
        result=_record(conn.execute(
            """SELECT e.id AS activation_event_id,e.config_id,e.project_id,
                      e.review_request_id,e.review_decision_id,
                      e.review_subject_sha256,e.previous_config_id,
                      c.calculation_version,c.is_active
                 FROM group_c_config_activation_events e
                 JOIN metric_calculation_configs c ON c.id=e.config_id
                WHERE e.project_id=%s AND e.config_id=%s AND e.kind='activated'
                ORDER BY e.created_at DESC,e.id DESC LIMIT 1""",
            (project_id,_id(config_id,"config_id")),
        ))
        if result is None:
            raise ContractError("config_not_activated","No signed historical activation")
        return dict(result,currently_active=(result["is_active"] is True))
