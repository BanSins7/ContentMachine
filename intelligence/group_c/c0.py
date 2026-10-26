"""GC-MOD-06: C0 raw snapshot validation and exact current-pair selection.

Pure validators never fetch, mutate, normalize or score observations. The DB
reader rechecks a project-scoped Stage05 D1 bundle under a repeatable-read
transaction and compares the newest *raw* source observations, including
retracted/partial/provider-version changes. No "latest good" fallback.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from .contracts import ContractError
from .contract_audit import RAW_SNAPSHOT_FACTS
from .d1 import read_scoped_d1_bundle, local_d1_ports

C0_SCHEMA_VERSION = "group-c-c0/v1"
# The reviewed canonical *views* definition in the current QDP C contract.
# Provider reach/impressions must never be silently interpreted as views.
VIEW_DEFINITIONS = frozenset({"platform_post_views"})
MAX_COUNTER = (1 << 63) - 1
REQUIRED_SCORE_COUNTERS = ("views", "likes", "comments", "shares")
_BANNED = object()


@dataclass(frozen=True)
class C0Snapshot:
    """Verified, exact raw facts with nullable counters unchanged."""
    snapshot_id: int
    video_id: int
    source_id: str
    source_policy_version_id: int
    metric_definition: str
    metric_version: str
    observed_at: datetime
    collected_at: datetime
    expires_at: datetime | None
    views: int | None
    likes: int | None
    comments: int | None
    shares: int | None
    saves: int | None
    followers: int | None
    schema_version: str = C0_SCHEMA_VERSION

    @property
    def missing_score_fields(self) -> tuple[str, ...]:
        return tuple(name for name in REQUIRED_SCORE_COUNTERS
                     if getattr(self, name) is None)

    @property
    def ready_for_rate_math(self) -> bool:
        return not self.missing_score_fields and self.views > 0


@dataclass(frozen=True)
class C0Pair:
    previous: C0Snapshot
    current: C0Snapshot

    @property
    def elapsed(self) -> timedelta:
        return self.current.observed_at - self.previous.observed_at


def _positive(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ContractError("invalid_identifier", f"Expected positive integer {label}")
    return value


def _instant(value: Any, label: str) -> datetime:
    if (type(value) is not datetime or value.tzinfo is None
            or value.utcoffset() is None):
        raise ContractError("invalid_observation_interval",
                            f"{label} must be a timezone-aware timestamp")
    try:
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ContractError("invalid_observation_interval", label) from exc


def _counter(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= MAX_COUNTER:
        raise ContractError("invalid_raw_counter",
                            f"{label} must be a nullable PostgreSQL BIGINT >= 0")
    return value


def validate_c0_snapshot(
    row: Mapping[str, Any], *, video_id: int, source_id: str,
    source_policy_version_id: int, metric_definition: str,
    metric_version: str, now: datetime | None = None,
    require_score_fields: bool = False,
) -> C0Snapshot:
    """Only exact D1 raw observation qualifies; preserve SQL NULL as None.

    An individually valid zero-view or partially-populated raw observation
    remains observable, but never silently becomes a rate-ready C7 input.
    """
    if not isinstance(row, Mapping):
        raise ContractError("missing_snapshot", "D1 snapshot object missing")
    _positive(video_id, "video_id")
    _positive(source_policy_version_id, "source_policy_version_id")
    if type(source_id) is not str or not source_id.strip():
        raise ContractError("different_video_or_source", "Pinned D1 source required")
    if type(metric_definition) is not str or metric_definition not in VIEW_DEFINITIONS:
        raise ContractError("incompatible_metric_definition",
                            "Only explicitly reviewed post view units are supported")
    if type(metric_version) is not str or not metric_version.strip():
        raise ContractError("incompatible_metric_version", "Exact view version required")
    if type(require_score_fields) is not bool:
        raise ContractError("invalid_c0_option", "require_score_fields must be boolean")
    moment = _instant(now, "now") if now is not None else datetime.now(timezone.utc)
    sid = _positive(row.get("id"), "snapshot_id")
    if row.get("video_id") != video_id or row.get("source_id") != source_id:
        raise ContractError("different_video_or_source", "Cross-video or cross-source D1 snapshot")
    if row.get("source_policy_version_id") != source_policy_version_id:
        raise ContractError("source_policy_unavailable", "Snapshot policy version changed")
    if row.get("source_metric_definition") != metric_definition:
        raise ContractError("incompatible_metric_definition", "Source view definition changed")
    if row.get("source_metric_version") != metric_version:
        raise ContractError("incompatible_metric_version", "Provider metric version changed")
    quality = row.get("data_quality_status")
    if quality == "retracted":
        raise ContractError("source_expired_or_retracted", "D1 observation retracted")
    if quality == "counter_correction":
        raise ContractError("counter_decreased", "Source view counter corrected")
    if quality != "ok" or row.get("precision") != "exact":
        raise ContractError("unverified_or_assumed_observation",
                            "Partial/estimated/unverified observation cannot pass C0")
    if row.get("observation_time_assumed") is not False:
        raise ContractError("unverified_or_assumed_observation",
                            "Assumed observation timestamp cannot pass C0")
    observed = _instant(row.get("observed_at"), "observed_at")
    collected = _instant(row.get("collected_at"), "collected_at")
    if observed > collected or observed > moment or collected > moment:
        raise ContractError("invalid_observation_interval",
                            "Future or post-collection observation is not verifiable")
    expiry_value = row.get("expires_at")
    expiry = _instant(expiry_value, "expires_at") if expiry_value is not None else None
    if expiry is not None and expiry <= moment:
        raise ContractError("source_expired_or_retracted", "D1 observation has expired")
    raw = {key: _counter(row.get(key, _BANNED), key)
           for key in RAW_SNAPSHOT_FACTS}
    snapshot = C0Snapshot(
        snapshot_id=sid, video_id=video_id, source_id=source_id,
        source_policy_version_id=source_policy_version_id,
        metric_definition=metric_definition, metric_version=metric_version,
        observed_at=observed, collected_at=collected, expires_at=expiry,
        **raw,
    )
    if require_score_fields and snapshot.missing_score_fields:
        raise ContractError("missing_raw_counter",
                            "Missing mandatory C7 counters: " +
                            ",".join(snapshot.missing_score_fields))
    if require_score_fields and snapshot.views == 0:
        raise ContractError("zero_views_rate_undefined",
                            "Views=0 is valid raw fact but not a rate denominator")
    return snapshot


def validate_c0_pair(
    previous: C0Snapshot, current: C0Snapshot, *,
    require_score_fields: bool = True,
) -> C0Pair:
    """Two immutable D1 observations, same video/source/policy/view version."""
    if type(require_score_fields) is not bool:
        raise ContractError("invalid_c0_option", "require_score_fields must be boolean")
    if not isinstance(previous, C0Snapshot) or not isinstance(current, C0Snapshot):
        raise ContractError("missing_snapshot", "Both verified C0 observations required")
    for key in ("video_id", "source_id", "source_policy_version_id"):
        if getattr(previous, key) != getattr(current, key):
            raise ContractError("different_video_or_source", "C0 pair identity drift: " + key)
    if previous.metric_definition != current.metric_definition:
        raise ContractError("incompatible_metric_definition", "C0 pair view-unit drift")
    if previous.metric_version != current.metric_version:
        raise ContractError("incompatible_metric_version", "C0 pair provider-version drift")
    if previous.snapshot_id == current.snapshot_id:
        raise ContractError("missing_prior_snapshot", "Distinct D1 snapshot IDs required")
    if not previous.observed_at < current.observed_at:
        raise ContractError("invalid_observation_interval",
                            "C0 pair requires strictly increasing observed_at")
    if previous.views is None or current.views is None:
        raise ContractError("missing_raw_counter", "View counter required for growth")
    if current.views < previous.views:
        raise ContractError("counter_decreased", "Do not clamp corrected counters to zero")
    if require_score_fields:
        if current.missing_score_fields:
            raise ContractError("missing_raw_counter", "C4/C5 require current L/C/S/V")
        if current.views == 0:
            raise ContractError("zero_views_rate_undefined", "Undefined C4 denominator")
    return C0Pair(previous=previous, current=current)


def read_c0_pair(
    conn: Any, *, project_id: str, video_id: int, source_id: str,
    source_policy_version_id: int, previous_snapshot_id: int,
    snapshot_id: int, metadata_id: int, metric_definition: str,
    metric_version: str, ports: Any = None, now: datetime | None = None,
) -> C0Pair:
    """DB-backed C0 gate; no C1–C7 scoring and no QDP ACK.

    Must own a top-level consistent read transaction. An independent latest
    raw-source check catches newer retractions/provider version changes even
    when the pinned QDP pair is still historically readable.
    """
    if getattr(conn, "autocommit", None) is not True:
        raise ContractError("transaction_contract_unavailable",
                            "C0 reads require independent autocommit connection")
    if previous_snapshot_id == snapshot_id:
        raise ContractError("missing_prior_snapshot", "Same snapshot cannot form growth pair")
    _positive(previous_snapshot_id, "previous_snapshot_id")
    _positive(snapshot_id, "snapshot_id")
    checked = ports if ports is not None else local_d1_ports()
    instant = _instant(now, "now") if now is not None else datetime.now(timezone.utc)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        bundle = read_scoped_d1_bundle(
            conn, project_id, video_id,
            (previous_snapshot_id, snapshot_id), metadata_id, ports=checked,
        )
        if (bundle["source_id"] != source_id
                or bundle["source_policy_version_id"] != source_policy_version_id):
            raise ContractError("different_video_or_source", "QDP/D1 source or policy changed")
        before, current = bundle["snapshots"]
        previous_checked = validate_c0_snapshot(
            before, video_id=video_id, source_id=source_id,
            source_policy_version_id=source_policy_version_id,
            metric_definition=metric_definition, metric_version=metric_version,
            now=instant,
        )
        current_checked = validate_c0_snapshot(
            current, video_id=video_id, source_id=source_id,
            source_policy_version_id=source_policy_version_id,
            metric_definition=metric_definition, metric_version=metric_version,
            now=instant, require_score_fields=True,
        )
        result = validate_c0_pair(previous_checked, current_checked)
        # The latest two *raw* D1 records for this post/source must be exactly
        # the selected pair, regardless of version/quality/policy. Never
        # let a newer unverified/retracted/corrected record disappear.
        d1 = checked.require("d1", conn)
        recent: Sequence[Mapping[str, Any]] = d1.read_recent_raw_observations(
            conn, video_id, source_id, 3,
        )
        if ([r.get("id") for r in recent[:2]]
                != [snapshot_id, previous_snapshot_id]):
            raise ContractError("metric_history_changed",
                                "QDP pair no longer the latest two raw observations")
        return result
