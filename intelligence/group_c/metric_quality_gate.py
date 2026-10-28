"""GC-MOD-23: explicit source/C0/cohort/data-quality decisions, no fake C7.

A failed quality gate is NOT a calculated_metrics row. Store only an immutable
project-scoped diagnostic bound to the original failed GC21 job/run. Preserve
original raw observations and historically complete certified C7 unchanged.
The growth anomaly threshold is NEVER guessed: it must be present in a
human-reviewed config.growth_policy as an exact positive NUMERIC(30,12) STRING.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .contracts import ContractError, canonical_hash
from .d1 import _record
from .pure_math import q
from .views_growth import compute_views_growth_per_hour

QUALITY_VERSION = "group-c-quality-gate/v1"
_DECIMAL_LITERAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,12})?\Z")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")

# Existing authoritative 003 permitted calculation_status/score_status values.
# The more precise reason_code is separately preserved, NOT collapsed to
# fabricated status or a NULL/zero "complete" breakout score.
_INCOMPATIBLE = frozenset({
    "different_video_or_source", "incompatible_metric_definition",
    "incompatible_metric_version", "metric_history_changed",
    "source_policy_unavailable", "source_policy_expired",
    "source_expired_or_retracted", "publication_expired",
    "publication_not_current", "publication_lineage_mismatch",
    "metric_snapshot_expired", "metric_snapshot_ineligible",
    "metadata_expired", "metadata_unavailable", "d1_lineage_mismatch",
    "incompatible_peer_kind", "content_type_unclassified",
    "content_kind_incompatible", "project_video_unavailable",
    "source_metric_definition_incompatible",
})
_COUNTER = frozenset({
    "counter_decreased", "counter_correction", "counter_corrected",
})
_INSUFFICIENT = frozenset({
    "missing_raw_counter", "zero_views_rate_undefined",
    "missing_snapshot", "missing_prior_snapshot",
    "missing_publication_metadata", "missing_review_evidence",
    "creator_cohort_not_approved", "prior_cohort_not_approved",
    "niche_cohort_incompatible", "incompatible_peer_creator",
    "incompatible_peer_topic", "incompatible_prior_cohort",
    "incompatible_prior_peer", "prior_evidence_changed",
    "cohort_selection_conflict", "insufficient_peer_count",
    "unverified_or_assumed_observation",
})
_TIMESTAMPS = frozenset({
    "invalid_observation_interval", "invalid_publication_timestamp",
    "invalid_timestamp", "freshness_numeric_failure",
})
_CONFIGURATION = frozenset({
    "config_not_activated", "review_not_approved",
    "review_subject_changed", "review_niche_mismatch",
    "invalid_growth_policy", "pending_configuration",
})
_ARITHMETIC = frozenset({
    "growth_anomaly", "cohort_statistic_overflow",
    "invalid_c1_precision", "invalid_c5_precision",
    "niche_statistic_overflow", "score_overflow",
    "calculation_error",
})


@dataclass(frozen=True)
class QualityDecision:
    """One terminal blocked classification, never a numeric score."""
    reason_code: str
    calculation_status: str
    score_status: str
    severity: str = "blocked"
    schema_version: str = QUALITY_VERSION

    def __post_init__(self):
        if type(self.reason_code) is not str or not _CODE.fullmatch(self.reason_code):
            raise ContractError("invalid_quality_code","Canonical reason code required")
        if (self.calculation_status not in (
            "insufficient_data", "pending_configuration", "invalid_timestamp",
            "incompatible_source", "counter_correction", "calculation_error",
        ) or self.score_status != self.calculation_status
            or self.severity not in ("blocked","anomaly")
            or (self.severity=="anomaly")!=(self.reason_code=="growth_anomaly")):
            raise ContractError("invalid_quality_status",
                                "Only valid non-complete metric states and anomaly severity")


def classify_quality_failure(exc: Exception) -> QualityDecision | None:
    """Only recognized metric evidence failures are quality decisions.

    Security/lease/IAM/SQL failures are NOT converted into quality statuses:
    those remain ordinary failed/retryable jobs. No permissive fallthrough.
    """
    if not isinstance(exc,ContractError):
        return None
    code=exc.code
    if type(code) is not str or _CODE.fullmatch(code) is None:
        return None
    if code in _COUNTER:
        status="counter_correction"
    elif code in _TIMESTAMPS:
        status="invalid_timestamp"
    elif code in _INCOMPATIBLE:
        status="incompatible_source"
    elif code in _INSUFFICIENT:
        status="insufficient_data"
    elif code in _CONFIGURATION:
        status="pending_configuration"
    elif code in _ARITHMETIC:
        status="calculation_error"
    else:
        return None
    return QualityDecision(
        reason_code=code,calculation_status=status,score_status=status,
        severity="anomaly" if code=="growth_anomaly" else "blocked",
    )


def approved_growth_limit(growth_policy: Mapping[str,Any]) -> Decimal | None:
    """Reviewed growth_policy.max_views_growth_per_hour is optional.

    Exactly a base-ten STRING prevents silent float rounding in JSON. Missing
    threshold means NO threshold is approved and NO anomaly decision is made.
    It does NOT imply that anomalous data were checked and accepted.
    """
    if not isinstance(growth_policy,Mapping):
        raise ContractError("invalid_growth_policy","Signed config growth_policy must be an object")
    if "max_views_growth_per_hour" not in growth_policy:
        return None
    value=growth_policy["max_views_growth_per_hour"]
    if type(value) is not str or _DECIMAL_LITERAL.fullmatch(value) is None:
        raise ContractError("invalid_growth_policy",
                            "Threshold must be canonical positive decimal STRING, <=12 decimals")
    try:
        parsed=q(Decimal(value))
    except (InvalidOperation, ValueError, ArithmeticError) as exc:
        raise ContractError("invalid_growth_policy","Threshold overflow/invalid precision") from exc
    if parsed <= 0:
        raise ContractError("invalid_growth_policy","Growth limit must be strictly positive")
    return parsed


def reject_anomalous_growth(
    *, growth_policy: Mapping[str,Any],previous_views: int,
    current_views: int, previous_observed_at: Any,current_observed_at: Any,
) -> Decimal:
    """Preserves the ORIGINAL exact growth; never caps/clamps/discards facts.

    A pure, independently approved positive max threshold blocks an
    excessive growth rate rather than replacing the raw rate with max/zero.
    """
    limit=approved_growth_limit(growth_policy)
    original=compute_views_growth_per_hour(
        previous_views,current_views,previous_observed_at,current_observed_at,
    )
    if limit is not None and original>limit:
        raise ContractError(
            "growth_anomaly",
            "Original views-growth rate exceeds independently reviewed "
            "growth_policy.max_views_growth_per_hour; no scoring allowed",
        )
    return original


@dataclass(frozen=True)
class PersistedQualityReceipt:
    report_id: str
    project_id: str
    run_id: str
    video_id: int
    job_id: int
    reason_code: str
    calculation_status: str
    score_status: str
    fingerprint_sha256: str
    schema_version: str = QUALITY_VERSION
    state: str = "blocked_unscored"


def append_quality_report(conn: Any, *, project_id: str,run_id: str,
                          video_id: int,job_id: int,
                          decision: QualityDecision) -> PersistedQualityReceipt:
    """Called ONLY inside GC21's failing job+run transaction.

    The database trigger requires a terminal FAILED exact score job,
    a BLOCKED exact Group C run, matching original source/IDs and no
    persisted C7 id. No externally supplied counter/score payload.
    """
    if not isinstance(decision,QualityDecision):
        raise ContractError("invalid_quality_status","Typed QualityDecision required")
    run=_record(conn.execute(
        """SELECT project_id,id,video_id,source_id,source_policy_version_id,
                  snapshot_id,previous_snapshot_id,published_metadata_id,
                  calculation_config_id,state,calculated_metric_id
             FROM group_c_runs
            WHERE project_id=%s AND id=%s::uuid AND video_id=%s""",
        (project_id,run_id,video_id),
    ))
    if (run is None or run["state"]!="blocked"
            or run["calculated_metric_id"] is not None):
        raise ContractError("quality_run_invalid","Only blocked unscored original run")
    evidence={
        "version":QUALITY_VERSION,"project_id":project_id,
        "run_id":run_id,"video_id":video_id,"job_id":job_id,
        "source_id":run["source_id"],
        "source_policy_version_id":run["source_policy_version_id"],
        "snapshot_id":run["snapshot_id"],
        "previous_snapshot_id":run["previous_snapshot_id"],
        "published_metadata_id":run["published_metadata_id"],
        "calculation_config_id":run["calculation_config_id"],
        "reason_code":decision.reason_code,
        "calculation_status":decision.calculation_status,
        "score_status":decision.score_status,
        "severity":decision.severity,
    }
    fingerprint=canonical_hash(evidence)
    row=_record(conn.execute(
        """INSERT INTO group_c_quality_reports (
             project_id,run_id,video_id,job_id,source_id,
             source_policy_version_id,snapshot_id,previous_snapshot_id,
             published_metadata_id,calculation_config_id,reason_code,
             calculation_status,score_status,severity,fingerprint_sha256)
           VALUES(%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           RETURNING id""",
        (project_id,run_id,video_id,job_id,run["source_id"],
         run["source_policy_version_id"],run["snapshot_id"],
         run["previous_snapshot_id"],run["published_metadata_id"],
         run["calculation_config_id"],decision.reason_code,
         decision.calculation_status,decision.score_status,
         decision.severity,fingerprint),
    ))
    if row is None:
        raise ContractError("quality_report_write_failed","No terminal report ID")
    return PersistedQualityReceipt(
        report_id=str(row["id"]),project_id=project_id,run_id=run_id,
        video_id=video_id,job_id=job_id,reason_code=decision.reason_code,
        calculation_status=decision.calculation_status,
        score_status=decision.score_status,fingerprint_sha256=fingerprint,
    )
