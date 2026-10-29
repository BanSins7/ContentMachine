"""GC-MOD-18 — точная свежесть публикации C6.

Historical freshness belongs to source_video_metadata.published_at at the
actual D1 snapshot observed_at, NEVER to the time of a worker invocation.
No source provider calls, approval, C7 score, SQL writes or Analytics output.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, DecimalException, localcontext
from typing import Any

from .c0 import validate_c0_pair, validate_c0_snapshot
from .contracts import ContractError, FRESHNESS_HALF_LIFE_H, canonical_hash
from .d1 import local_d1_ports, read_scoped_d1_bundle
from .ports import GroupCPorts
from .publication_time import (
    PUBLICATION_EVIDENCE_VERSION, PublicationTimeEvidence,
    validate_publication_time,
)
from .pure_math import q

FRESHNESS_VERSION = "group-c-freshness/v1"
_METRIC = "platform_post_views"
_TIME_SOURCE = "source_video_metadata.published_at"
_MICROS_PER_HOUR = Decimal("3600000000")
_ONE_HUNDRED = Decimal("100")
_TWO = Decimal("2")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _instant(value: Any, label: str) -> datetime:
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() is None):
        raise ContractError(
            "invalid_publication_timestamp",
            label + " must be an explicit timezone-aware datetime",
        )
    try:
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ContractError("invalid_publication_timestamp", label) from exc


def _age_microseconds(published_at: datetime, observed_at: datetime) -> int:
    born = _instant(published_at, "published_at")
    seen = _instant(observed_at, "observed_at")
    elapsed = seen - born
    micros = ((elapsed.days * 86400 + elapsed.seconds) * 1000000
              + elapsed.microseconds)
    if micros < 0:
        raise ContractError(
            "invalid_publication_timestamp",
            "Future publication relative to observed_at is not a valid C6 fact",
        )
    return micros


def compute_freshness_score(
    published_at: datetime, observed_at: datetime,
) -> Decimal:
    """C6 = 100 * 2 ** (-(observed_at - published_at) / 48 hours).

    Never use datetime.now, elapsed.total_seconds() float, collected_at,
    fetched_at, discovery time, guessed publication time or local timezone.
    Use unrounded microsecond age and round only the output to scale 12.
    """
    micros = _age_microseconds(published_at, observed_at)
    with localcontext() as ctx:
        ctx.prec = 80
        # Large historical ages are valid; avoid artificial default Decimal
        # exponent-range failures. Quantization alone may yield exact 0E-12.
        ctx.Emax = 999999999
        ctx.Emin = -999999999
        try:
            hours = Decimal(micros) / _MICROS_PER_HOUR
            score = _ONE_HUNDRED * (_TWO ** (-hours / FRESHNESS_HALF_LIFE_H))
            return q(score)
        except DecimalException as exc:
            raise ContractError("freshness_numeric_failure",
                                "Freshness Decimal exponent could not be evaluated") from exc


def compute_verified_freshness(
    evidence: PublicationTimeEvidence,
) -> Decimal:
    """Re-check integrity of typed GC08 evidence. This is NOT IAM/DB trust.

    For real source-currentness use PostgreSQLFreshness.derive; the pure
    dataclass and its hash are not a cryptographic proof of source authority.
    """
    if not isinstance(evidence, PublicationTimeEvidence):
        raise ContractError("missing_publication_metadata", "Verified GC08 evidence object required")
    if (evidence.schema_version != PUBLICATION_EVIDENCE_VERSION
            or evidence.time_source != _TIME_SOURCE
            or type(evidence.evidence_sha256) is not str
            or not _SHA.fullmatch(evidence.evidence_sha256)):
        raise ContractError("publication_lineage_mismatch",
                            "Unknown publication evidence schema/time source/hash")
    checked = validate_publication_time(
        {
            "id": evidence.metadata_id,
            "video_id": evidence.video_id,
            "source_id": evidence.source_id,
            "source_policy_version_id": evidence.source_policy_version_id,
            "published_at": evidence.published_at,
            "fetched_at": evidence.fetched_at,
            "expires_at": evidence.expires_at,
            "is_current": True,
            "deleted_at": None,
        },
        video_id=evidence.video_id,
        metadata_id=evidence.metadata_id,
        source_id=evidence.source_id,
        source_policy_version_id=evidence.source_policy_version_id,
        previous_snapshot_id=evidence.previous_snapshot_id,
        snapshot_id=evidence.snapshot_id,
        previous_observed_at=evidence.previous_observed_at,
        observed_at=evidence.observed_at,
        as_of=evidence.checked_at,
    )
    if checked.evidence_sha256 != evidence.evidence_sha256:
        raise ContractError("publication_lineage_mismatch",
                            "Publication evidence was altered after validation")
    return compute_freshness_score(checked.published_at, checked.observed_at)


@dataclass(frozen=True)
class FreshnessResult:
    project_id: str
    video_id: int
    source_id: str
    source_policy_version_id: int
    metric_definition: str
    metric_version: str
    previous_snapshot_id: int
    snapshot_id: int
    metadata_id: int
    published_at: datetime
    observed_at: datetime
    age_microseconds: int
    age_hours: Decimal
    freshness_score: Decimal
    publication_evidence_sha256: str
    evidence_sha256: str
    half_life_hours: Decimal = FRESHNESS_HALF_LIFE_H
    schema_version: str = FRESHNESS_VERSION
    status: str = "freshness_derived_unapproved"

    @property
    def ready_for_breakout(self) -> bool:
        return False


class PostgreSQLFreshness:
    """Derive C6 from one exact, current, read-only D1/C0/GC08 snapshot."""

    def derive(
        self, conn: Any, *, project_id: str, video_id: int,
        metadata_id: int, source_id: str, source_policy_version_id: int,
        previous_snapshot_id: int, snapshot_id: int,
        metric_version: str,
        metric_definition: str = _METRIC,
        ports: GroupCPorts | None = None,
    ) -> FreshnessResult:
        if getattr(conn, "autocommit", None) is not True:
            raise ContractError("transaction_contract_unavailable",
                                "Independent autocommit connection is mandatory")
        if ports is not None and not isinstance(ports, GroupCPorts):
            raise ContractError("invalid_ports", "Explicit compatible Group C ports required")
        # A single non-overridable present-time check prevents callers
        # from injecting historical 'now' to bypass policy/retention.
        checked_at = datetime.now(timezone.utc)
        dependencies = ports if ports is not None else local_d1_ports()
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            bundle = read_scoped_d1_bundle(
                conn, project_id, video_id,
                (previous_snapshot_id, snapshot_id), metadata_id,
                ports=dependencies,
            )
            if (bundle["source_id"] != source_id
                    or bundle["source_policy_version_id"] != source_policy_version_id):
                raise ContractError("different_video_or_source",
                                    "Exact source/policy does not match D1")
            before, latest = bundle["snapshots"]
            previous = validate_c0_snapshot(
                before, video_id=video_id, source_id=source_id,
                source_policy_version_id=source_policy_version_id,
                metric_definition=metric_definition, metric_version=metric_version,
                now=checked_at, require_score_fields=False,
            )
            current = validate_c0_snapshot(
                latest, video_id=video_id, source_id=source_id,
                source_policy_version_id=source_policy_version_id,
                metric_definition=metric_definition, metric_version=metric_version,
                now=checked_at, require_score_fields=False,
            )
            validate_c0_pair(previous, current, require_score_fields=False)
            # Check UNFILTERED raw source history in the SAME transaction;
            # never select an older pair after a newer correction/retraction.
            raw = dependencies.require("d1", conn).read_recent_raw_observations(
                conn, video_id, source_id, 3,
            )
            if [row.get("id") for row in raw[:2]] != [
                    snapshot_id, previous_snapshot_id]:
                raise ContractError("metric_history_changed",
                                    "Selected source snapshots no longer latest two raw records")
            proof = validate_publication_time(
                bundle["metadata"], video_id=video_id,
                metadata_id=metadata_id, source_id=source_id,
                source_policy_version_id=source_policy_version_id,
                previous_snapshot_id=previous_snapshot_id,
                snapshot_id=snapshot_id,
                previous_observed_at=previous.observed_at,
                observed_at=current.observed_at,
                as_of=checked_at,
            )
            score = compute_verified_freshness(proof)
            micros = proof.age_microseconds
            with localcontext() as ctx:
                ctx.prec = 80
                age_hours = Decimal(micros) / _MICROS_PER_HOUR
            digest = canonical_hash({
                "version": FRESHNESS_VERSION,
                "project_id": project_id, "video_id": video_id,
                "metadata_id": metadata_id, "source_id": source_id,
                "source_policy_version_id": source_policy_version_id,
                "previous_snapshot_id": previous_snapshot_id,
                "snapshot_id": snapshot_id,
                "metric_definition": current.metric_definition,
                "metric_version": current.metric_version,
                "published_at": proof.published_at.isoformat(),
                "observed_at": current.observed_at.isoformat(),
                "age_microseconds": micros,
                "freshness_score": str(score),
                "half_life_hours": str(FRESHNESS_HALF_LIFE_H),
                "publication_evidence_sha256": proof.evidence_sha256,
            })
            return FreshnessResult(
                project_id=project_id, video_id=video_id,
                source_id=source_id,
                source_policy_version_id=source_policy_version_id,
                metric_definition=current.metric_definition,
                metric_version=current.metric_version,
                previous_snapshot_id=previous_snapshot_id,
                snapshot_id=snapshot_id, metadata_id=metadata_id,
                published_at=proof.published_at, observed_at=current.observed_at,
                age_microseconds=micros, age_hours=age_hours,
                freshness_score=score,
                publication_evidence_sha256=proof.evidence_sha256,
                evidence_sha256=digest,
            )
