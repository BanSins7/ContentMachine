"""GC-MOD-08: verified, versioned D1 publication-time evidence for C6.

The trusted time comes ONLY from a pinned source_video_metadata row, never
videos.published_at, discovery time, fetched_at, or the wall clock. This module
does not calculate freshness scores or persist scores. The DB adapter enforces
current project/source policy and retention in one repeatable-read transaction.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from typing import Any

from .contracts import ContractError, canonical_hash
from .d1 import local_d1_ports, read_scoped_d1_bundle
from .ports import GroupCPorts

PUBLICATION_EVIDENCE_VERSION = "group-c-publication-evidence/v1"
_TIME_SOURCE = "source_video_metadata.published_at"


def _id(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ContractError("invalid_identifier", "Positive D1 " + label + " required")
    return value


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractError("invalid_publication_timestamp", "Timezone-aware " + label + " required")
    try:
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ContractError("invalid_publication_timestamp", "Unrepresentable " + label) from exc


@dataclass(frozen=True)
class PublicationTimeEvidence:
    """Pinned source time and snapshot cutoff; immutable, not an approval."""

    video_id: int
    metadata_id: int
    source_id: str
    source_policy_version_id: int
    previous_snapshot_id: int
    snapshot_id: int
    published_at: datetime
    fetched_at: datetime
    previous_observed_at: datetime
    observed_at: datetime
    expires_at: datetime | None
    checked_at: datetime
    evidence_sha256: str
    schema_version: str = PUBLICATION_EVIDENCE_VERSION
    time_source: str = _TIME_SOURCE

    @property
    def age(self) -> timedelta:
        """The historical age at the actual observation, not at read time."""
        return self.observed_at - self.published_at

    @property
    def age_microseconds(self) -> int:
        delta = self.age
        return ((delta.days * 86400 + delta.seconds) * 1000000
                + delta.microseconds)

    @property
    def age_hours(self) -> Decimal:
        with localcontext() as ctx:
            ctx.prec = 70
            return Decimal(self.age_microseconds) / Decimal("3600000000")

    def calculation_publication(self) -> dict:
        """Exact compatibility field for pure_math; no wall-clock inference."""
        return {"published_at": self.published_at}


def validate_publication_time(
    metadata: Mapping[str, Any], *,
    video_id: int, metadata_id: int, source_id: str,
    source_policy_version_id: int,
    previous_snapshot_id: int, snapshot_id: int,
    previous_observed_at: datetime, observed_at: datetime,
    as_of: datetime | None = None,
) -> PublicationTimeEvidence:
    """Pure provenance/time gate. DB caller separately proves active rights.

    as_of is an explicit clock *for pure tests*. Production DB reader never
    accepts caller-supplied historical as_of to bypass real current retention.
    """
    if not isinstance(metadata, Mapping):
        raise ContractError("missing_publication_metadata", "Exact D1 metadata required")
    for name, value in (
        ("video_id", video_id), ("metadata_id", metadata_id),
        ("source_policy_version_id", source_policy_version_id),
        ("previous_snapshot_id", previous_snapshot_id),
        ("snapshot_id", snapshot_id),
    ):
        _id(value, name)
    if snapshot_id == previous_snapshot_id:
        raise ContractError("invalid_observation_interval", "Two different snapshot IDs required")
    if type(source_id) is not str or not source_id.strip():
        raise ContractError("source_policy_unavailable", "Pinned source ID required")
    if any(type(metadata.get(key)) is not int for key in
           ("id", "video_id", "source_policy_version_id")):
        raise ContractError("publication_lineage_mismatch",
                            "Metadata must contain exact integer D1 identifiers")
    if (metadata.get("id") != metadata_id or metadata.get("video_id") != video_id
            or metadata.get("source_id") != source_id):
        raise ContractError("publication_lineage_mismatch", "D1 publication metadata identity changed")
    if metadata.get("source_policy_version_id") != source_policy_version_id:
        raise ContractError("source_policy_unavailable", "Publication source-policy version changed")
    if metadata.get("is_current") is not True or metadata.get("deleted_at") is not None:
        raise ContractError("publication_not_current", "Deleted or superseded D1 metadata")
    checked = _utc(as_of, "as_of") if as_of is not None else datetime.now(timezone.utc)
    published = _utc(metadata.get("published_at"), "source_video_metadata.published_at")
    fetched = _utc(metadata.get("fetched_at"), "source_video_metadata.fetched_at")
    prev = _utc(previous_observed_at, "previous.observed_at")
    current = _utc(observed_at, "current.observed_at")
    expiry = (_utc(metadata["expires_at"], "source_video_metadata.expires_at")
              if metadata.get("expires_at") is not None else None)
    if not (published <= fetched <= checked):
        raise ContractError("invalid_publication_timestamp", "Publication/fetch in the future or reversed")
    if not (published <= prev < current <= checked):
        raise ContractError("invalid_publication_timestamp", "Snapshot before publication or invalid C3 interval")
    if expiry is not None and expiry <= checked:
        raise ContractError("publication_expired", "Publication evidence no longer available")
    if expiry is not None and expiry < fetched:
        raise ContractError("invalid_publication_timestamp", "Metadata expires before fetched_at")
    # All recorded lineage fields are pinned and digestable; checked_at and
    # wall-clock age are intentionally excluded from the immutable fingerprint.
    digest = canonical_hash({
        "schema_version": PUBLICATION_EVIDENCE_VERSION,
        "video_id": video_id, "metadata_id": metadata_id,
        "source_id": source_id, "source_policy_version_id": source_policy_version_id,
        "previous_snapshot_id": previous_snapshot_id, "snapshot_id": snapshot_id,
        "published_at": published.isoformat(), "fetched_at": fetched.isoformat(),
        "previous_observed_at": prev.isoformat(), "observed_at": current.isoformat(),
        "expires_at": expiry.isoformat() if expiry is not None else None,
    })
    return PublicationTimeEvidence(
        video_id=video_id, metadata_id=metadata_id, source_id=source_id,
        source_policy_version_id=source_policy_version_id,
        previous_snapshot_id=previous_snapshot_id, snapshot_id=snapshot_id,
        published_at=published, fetched_at=fetched,
        previous_observed_at=prev, observed_at=current, expires_at=expiry,
        checked_at=checked, evidence_sha256=digest,
    )


def read_publication_time(
    conn: Any, *, project_id: str, video_id: int, metadata_id: int,
    source_id: str, source_policy_version_id: int,
    previous_snapshot_id: int, snapshot_id: int,
    ports: GroupCPorts | None = None,
) -> PublicationTimeEvidence:
    """Read current authoritative D1/QDP-compatible evidence in one snapshot.

    This is not C0 validation and does not certify score readiness. The future
    scoring worker must revalidate all evidence in its *own* write transaction.
    """
    if getattr(conn, "autocommit", None) is not True:
        raise ContractError("transaction_contract_unavailable",
                            "Require an independent autocommit connection")
    if ports is not None and not isinstance(ports, GroupCPorts):
        raise ContractError("invalid_ports", "Use explicit typed GroupCPorts")
    _id(video_id, "video_id")
    _id(metadata_id, "metadata_id")
    _id(previous_snapshot_id, "previous_snapshot_id")
    _id(snapshot_id, "snapshot_id")
    _id(source_policy_version_id, "source_policy_version_id")
    checked = datetime.now(timezone.utc)
    dependencies = ports if ports is not None else local_d1_ports()
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        bundle = read_scoped_d1_bundle(
            conn, project_id, video_id, (previous_snapshot_id, snapshot_id),
            metadata_id, ports=dependencies,
        )
        if (bundle["source_id"] != source_id
                or bundle["source_policy_version_id"] != source_policy_version_id):
            raise ContractError("publication_lineage_mismatch", "Pinned source/version differs from D1")
        old, new = bundle["snapshots"]
        # Every C6 age is anchored to observed_at, never fetched_at/read time.
        # Previous and latest snapshot data were verified as same source/policy
        # and current permissions by read_scoped_d1_bundle.
        return validate_publication_time(
            bundle["metadata"], video_id=video_id, metadata_id=metadata_id,
            source_id=source_id,
            source_policy_version_id=source_policy_version_id,
            previous_snapshot_id=previous_snapshot_id, snapshot_id=snapshot_id,
            previous_observed_at=old["observed_at"], observed_at=new["observed_at"],
            as_of=checked,
        )
