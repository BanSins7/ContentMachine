"""GC-MOD-09 — standalone, read-only, *unapproved* peer-cohort strata.

Two layers:
  - pure frozen and versioned rules + deterministic candidate admission
  - PostgreSQL snapshot-consistent, project-scoped raw candidate pool reader
No medians/priors, no invented defaults, no inserts/freeze/auto-approval.
Latest *raw* per video/source is picked BEFORE checking quality or version:
a newer retracted/corrected/reversioned observation cannot be hidden.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from .contracts import ContractError, canonical_hash
from .d1 import local_d1_ports, read_scoped_d1_bundle
from .ports import GroupCPorts

COHORT_RULES_VERSION = "group-c-cohort-rules/v1"
_ALLOWED_PURPOSES = frozenset(("creator", "niche", "engagement_prior"))
_ALLOWED_PLATFORMS = frozenset(("youtube", "tiktok", "instagram"))
_ALLOWED_KINDS = frozenset(("video", "carousel"))
_VIEW_UNIT = "platform_post_views"
_MAX_ROWS = 500


def _positive(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ContractError("invalid_cohort_rule", f"{name} must be a positive integer")
    return value


def _utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractError("invalid_cohort_window", f"{name} requires timezone-aware datetime")
    try:
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ContractError("invalid_cohort_window", f"{name} cannot be converted") from exc


def _age(value: object, name: str) -> Decimal:
    # No binary floats, booleans, implicit limits, or non-finite age.
    if type(value) not in (Decimal, int, str):
        raise ContractError("invalid_cohort_window", f"{name} must be exact Decimal")
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise ContractError("invalid_cohort_window", f"{name} invalid") from exc
    if not result.is_finite() or result < 0:
        raise ContractError("invalid_cohort_window", f"{name} must be finite >= 0")
    return result


def _canonical_age(value: Decimal) -> str:
    # Decimal.normalize() obeys global context precision; fingerprint must not.
    raw = format(value, "f")
    return (raw.rstrip("0").rstrip(".") if "." in raw else raw) or "0"


def _age_hours(start: datetime, end: datetime) -> Decimal:
    d = end - start
    micros = (d.days * 86400 + d.seconds) * 1000000 + d.microseconds
    with localcontext() as ctx:
        ctx.prec = 80
        return Decimal(micros) / Decimal("3600000000")


@dataclass(frozen=True)
class CohortRules:
    project_id: str
    target_video_id: int
    purpose: str
    platform: str
    content_type: str
    source_id: str
    source_policy_version_id: int
    metric_definition: str
    metric_version: str
    evaluated_as_of: datetime
    published_from: datetime
    published_before: datetime
    min_age_hours: Decimal
    max_age_hours: Decimal
    creator_id: int | None
    topic_id: int | None
    max_candidates: int
    schema_version: str = COHORT_RULES_VERSION

    def __post_init__(self):
        if self.schema_version != COHORT_RULES_VERSION:
            raise ContractError("unsupported_cohort_schema", "Unknown cohort rules version")
        if (type(self.project_id) is not str or len(self.project_id) != 32
                or any(c not in "0123456789abcdef" for c in self.project_id)):
            raise ContractError("invalid_project_id", "Canonical 32-hex project ID required")
        _positive(self.target_video_id, "target_video_id")
        _positive(self.source_policy_version_id, "source_policy_version_id")
        if type(self.purpose) is not str or self.purpose not in _ALLOWED_PURPOSES:
            raise ContractError("invalid_cohort_purpose", "Unrecognized cohort purpose")
        if (type(self.platform) is not str or self.platform not in _ALLOWED_PLATFORMS
                or type(self.content_type) is not str or self.content_type not in _ALLOWED_KINDS):
            raise ContractError("invalid_cohort_rule", "Explicit platform/content type required")
        if type(self.source_id) is not str or not self.source_id.strip() or len(self.source_id) > 250:
            raise ContractError("invalid_cohort_rule", "Exact D1 source ID required")
        if self.metric_definition != _VIEW_UNIT:
            raise ContractError("incompatible_metric_definition", "Only reviewed post-view units supported")
        if (type(self.metric_version) is not str or not self.metric_version.strip()
                or len(self.metric_version) > 120):
            raise ContractError("incompatible_metric_version", "Exact source metric version required")
        for key in ("creator_id", "topic_id"):
            value = getattr(self, key)
            if value is not None:
                _positive(value, key)
        if self.purpose == "creator" and self.creator_id is None:
            raise ContractError("invalid_cohort_rule", "Creator comparison needs explicit creator")
        if self.purpose == "niche" and self.topic_id is None:
            raise ContractError("invalid_cohort_rule", "Niche comparison needs explicit topic")
        if self.purpose == "engagement_prior" and (
                self.creator_id is None and self.topic_id is None):
            raise ContractError("invalid_cohort_rule", "Engagement prior needs reviewed strata")
        if type(self.max_candidates) is not int or not 1 <= self.max_candidates <= _MAX_ROWS:
            raise ContractError("invalid_cohort_rule", "Explicit candidate bound 1..500 required")
        cutoff = _utc(self.evaluated_as_of, "evaluated_as_of")
        first = _utc(self.published_from, "published_from")
        last = _utc(self.published_before, "published_before")
        if not first < last <= cutoff:
            raise ContractError("invalid_cohort_window", "Explicit [start,end) <= cutoff required")
        minimum = _age(self.min_age_hours, "min_age_hours")
        maximum = _age(self.max_age_hours, "max_age_hours")
        if minimum > maximum:
            raise ContractError("invalid_cohort_window", "Age interval reversed")
        # Frozen canonical UTC and exact ages ensure reproducible fingerprint.
        object.__setattr__(self, "evaluated_as_of", cutoff)
        object.__setattr__(self, "published_from", first)
        object.__setattr__(self, "published_before", last)
        object.__setattr__(self, "min_age_hours", minimum)
        object.__setattr__(self, "max_age_hours", maximum)

    @property
    def sha256(self) -> str:
        return canonical_hash({
            "schema_version": self.schema_version,
            "project_id": self.project_id, "target_video_id": self.target_video_id,
            "purpose": self.purpose, "platform": self.platform,
            "content_type": self.content_type, "source_id": self.source_id,
            "source_policy_version_id": self.source_policy_version_id,
            "metric_definition": self.metric_definition, "metric_version": self.metric_version,
            "evaluated_as_of": self.evaluated_as_of.isoformat(),
            "published_from": self.published_from.isoformat(),
            "published_before": self.published_before.isoformat(),
            "min_age_hours": _canonical_age(self.min_age_hours),
            "max_age_hours": _canonical_age(self.max_age_hours),
            "creator_id": self.creator_id, "topic_id": self.topic_id,
            "max_candidates": self.max_candidates,
        })


@dataclass(frozen=True)
class CohortCandidate:
    video_id: int
    snapshot_id: int
    metadata_id: int
    source_policy_version_id: int
    published_at: datetime
    observed_at: datetime
    age_hours: Decimal
    views: int
    likes: int | None
    comments: int | None
    shares: int | None


@dataclass(frozen=True)
class CohortRejection:
    video_id: int | None
    snapshot_id: int | None
    reason: str


@dataclass(frozen=True)
class CohortSelection:
    rules: CohortRules
    admitted: tuple[CohortCandidate, ...]
    rejected: tuple[CohortRejection, ...]
    selection_sha256: str
    status: str = "candidates_only"
    approved: bool = False

    @property
    def post_count(self) -> int:
        return len(self.admitted)


def _candidate(row: Mapping[str, Any], rules: CohortRules) -> CohortCandidate:
    video = row.get("video_id")
    snapshot = row.get("snapshot_id")
    if video == rules.target_video_id:
        raise ContractError("self_inclusion", "Target cannot appear in its own comparison group")
    _positive(video, "candidate_video_id")
    _positive(snapshot, "candidate_snapshot_id")
    metadata_id = _positive(row.get("metadata_id"), "candidate_metadata_id")
    if (row.get("platform") != rules.platform or
            row.get("c_content_type") != rules.content_type):
        raise ContractError("incompatible_peer_kind", "Platform or analytic type mismatch")
    if row.get("source_id") != rules.source_id:
        raise ContractError("different_video_or_source", "Peer source differs")
    if (row.get("source_policy_version_id") != rules.source_policy_version_id or
            row.get("metadata_policy_version_id") != rules.source_policy_version_id):
        raise ContractError("source_policy_unavailable", "Peer source-policy version mismatch")
    if row.get("source_metric_definition") != rules.metric_definition:
        raise ContractError("incompatible_metric_definition", "Peer view definition mismatch")
    if row.get("source_metric_version") != rules.metric_version:
        raise ContractError("incompatible_metric_version", "Peer view version mismatch")
    if rules.creator_id is not None and row.get("creator_id") != rules.creator_id:
        raise ContractError("incompatible_peer_creator", "Creator stratum mismatch")
    if rules.topic_id is not None and row.get("topic_id") != rules.topic_id:
        raise ContractError("incompatible_peer_topic", "Topic stratum mismatch")
    if (row.get("data_quality_status") != "ok" or row.get("precision") != "exact"
            or row.get("observation_time_assumed") is not False):
        raise ContractError("unverified_or_assumed_observation", "Peer raw metric invalid")
    if row.get("is_current") is not True or row.get("deleted_at") is not None:
        raise ContractError("peer_publication_unavailable", "Peer publication revoked")
    published = _utc(row.get("published_at"), "peer.published_at")
    observed = _utc(row.get("observed_at"), "peer.observed_at")
    fetched = _utc(row.get("fetched_at"), "peer.fetched_at")
    collected = _utc(row.get("collected_at"), "peer.collected_at")
    if (not published <= fetched <= rules.evaluated_as_of
            or observed > collected or collected > rules.evaluated_as_of):
        raise ContractError("invalid_peer_timestamps", "Peer publication/observation/collection chronology")
    kind = row.get("content_kind")
    kinds = {"video": "video", "slideshow_video": "video",
             "photo_carousel": "carousel", "mixed_carousel": "carousel"}
    if type(kind) is not str or kind not in kinds or kinds[kind] != rules.content_type:
        raise ContractError("incompatible_peer_kind", "Peer legacy kind disagrees with C type")
    if not rules.published_from <= published < rules.published_before:
        raise ContractError("outside_cohort_window", "Peer publication outside explicit window")
    if not published <= observed <= rules.evaluated_as_of:
        raise ContractError("invalid_cohort_window", "Observation before publication/after cutoff")
    age = _age_hours(published, observed)
    if not rules.min_age_hours <= age <= rules.max_age_hours:
        raise ContractError("outside_cohort_age", "Peer observation age outside stratum")
    for key in ("expires_at", "metadata_expires_at"):
        expired = row.get(key)
        if expired is not None and _utc(expired, key) <= rules.evaluated_as_of:
            raise ContractError("peer_expired", "Peer metric/metadata expired by cutoff")
    count = row.get("views")
    if type(count) is not int or not 0 <= count < 2**63:
        raise ContractError("missing_raw_counter", "Exact nonnegative peer views required")
    rates = {}
    for name in ("likes", "comments", "shares"):
        value = row.get(name)
        if value is not None and (type(value) is not int or not 0 <= value < 2**63):
            raise ContractError("invalid_raw_counter", "Invalid " + name)
        rates[name] = value
    if rules.purpose == "engagement_prior" and (
            count == 0 or any(value is None for value in rates.values())):
        raise ContractError("missing_raw_counter", "Engagement prior needs V>0 and L/C/S")
    return CohortCandidate(
        video_id=video, snapshot_id=snapshot, metadata_id=metadata_id,
        source_policy_version_id=rules.source_policy_version_id,
        published_at=published, observed_at=observed, age_hours=age,
        views=count, **rates,
    )


def select_compatible_peers(
    rules: CohortRules, rows: Sequence[Mapping[str, Any]],
) -> CohortSelection:
    """Pure deterministic stratum gate; explicit excluded rows are auditable."""
    if not isinstance(rules, CohortRules):
        raise ContractError("invalid_cohort_rule", "Validated frozen rules required")
    if type(rows) not in (tuple, list) or len(rows) > rules.max_candidates:
        raise ContractError("cohort_population_overflow",
                            "Cannot silently truncate or sample candidate population")
    admitted: list[CohortCandidate] = []
    rejected: list[CohortRejection] = []
    seen_video: set[int] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ContractError("invalid_peer_payload", "Peer must be a D1 row mapping")
        vid, sid = row.get("video_id"), row.get("snapshot_id")
        if type(vid) is not int or type(sid) is not int:
            raise ContractError("invalid_peer_payload", "Exact candidate IDs required")
        if vid in seen_video:
            raise ContractError("duplicate_peer_video", "One raw peer observation per video")
        seen_video.add(vid)
        try:
            admitted.append(_candidate(row, rules))
        except ContractError as exc:
            rejected.append(CohortRejection(vid, sid, exc.code))
    admitted.sort(key=lambda x: (x.video_id, x.snapshot_id))
    rejected.sort(key=lambda x: (x.video_id if x.video_id is not None else -1,
                                 x.snapshot_id if x.snapshot_id is not None else -1))
    selection_hash = canonical_hash({
        "schema_version": COHORT_RULES_VERSION,
        "rules_sha256": rules.sha256,
        "admitted": [(x.video_id, x.snapshot_id, x.metadata_id)
                     for x in admitted],
        "rejected": [(x.video_id, x.snapshot_id, x.reason)
                     for x in rejected],
    })
    return CohortSelection(rules=rules, admitted=tuple(admitted),
                           rejected=tuple(rejected),
                           selection_sha256=selection_hash)


class PostgreSQLCohortCandidates:
    """Read-only raw candidate pool; does NOT satisfy CohortApprovalReader port."""

    def select(self, conn: Any, *, rules: CohortRules, target_snapshot_id: int,
               target_metadata_id: int, ports: GroupCPorts | None = None) -> CohortSelection:
        if not isinstance(rules, CohortRules):
            raise ContractError("invalid_cohort_rule", "CohortRules required")
        _positive(target_snapshot_id, "target_snapshot_id")
        _positive(target_metadata_id, "target_metadata_id")
        if getattr(conn, "autocommit", None) is not True:
            raise ContractError("transaction_contract_unavailable",
                                "Independent autocommit connection required")
        if ports is not None and not isinstance(ports, GroupCPorts):
            raise ContractError("invalid_ports", "Explicit GroupCPorts required")
        adapters = ports if ports is not None else local_d1_ports()
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            bundle = read_scoped_d1_bundle(
                conn, rules.project_id, rules.target_video_id,
                (target_snapshot_id,), target_metadata_id, ports=adapters,
            )
            if (bundle["platform"] != rules.platform or
                    bundle["c_content_type"] != rules.content_type or
                    bundle["source_id"] != rules.source_id or
                    bundle["source_policy_version_id"] != rules.source_policy_version_id):
                raise ContractError("incompatible_target", "Target D1/stratum lineage mismatch")
            target = bundle["snapshots"][0]
            if (target["source_metric_definition"] != rules.metric_definition or
                    target["source_metric_version"] != rules.metric_version):
                raise ContractError("incompatible_target", "Target metric definition/version mismatch")
            if _utc(target["observed_at"], "target.observed_at") > rules.evaluated_as_of:
                raise ContractError("invalid_cohort_window", "Cutoff precedes target observation")
            # The stratum cannot be retrospectively forged using future cutoff.
            if rules.evaluated_as_of > datetime.now(timezone.utc):
                raise ContractError("invalid_cohort_window", "Evaluation cutoff is in the future")
            registry = adapters.require("registry", conn)
            link = registry.read_project_video(conn, rules.project_id, rules.target_video_id)
            if (rules.creator_id is not None and rules.creator_id != link.get("creator_id")) or (
                    rules.topic_id is not None and rules.topic_id != link.get("topic_id")):
                raise ContractError("incompatible_target", "Creator/topic stratum differs from target")
            # Latest raw observation is selected BEFORE any quality, policy
            # or metric-version predicates: bad newer snapshots are visible.
            cursor = conn.execute(
                """WITH latest AS (
                     SELECT DISTINCT ON (s.video_id)
                         s.id AS snapshot_id,s.video_id,s.source_id,
                         s.source_policy_version_id,
                         s.source_metric_definition,s.source_metric_version,
                         s.observed_at,s.collected_at,s.views,s.likes,s.comments,s.shares,
                         s.data_quality_status,s.precision,s.observation_time_assumed,
                         s.expires_at
                       FROM metric_snapshots s
                       JOIN sre_project_videos pv ON pv.video_id=s.video_id
                      WHERE pv.project_id=%s AND s.video_id<>%s
                        AND s.source_id=%s AND s.observed_at<=%s
                      ORDER BY s.video_id,s.observed_at DESC,s.id DESC
                   )
                   SELECT l.*,v.platform,v.creator_id,v.c_content_type,v.content_kind,
                          c.topic_id,
                          m.id AS metadata_id,m.source_policy_version_id AS metadata_policy_version_id,
                          m.published_at,m.fetched_at,m.is_current,m.deleted_at,
                          m.expires_at AS metadata_expires_at
                     FROM latest l
                     JOIN videos v ON v.id=l.video_id
                     LEFT JOIN creators c ON c.id=v.creator_id
                     LEFT JOIN LATERAL (
                        SELECT x.id,x.source_policy_version_id,x.published_at,
                               x.fetched_at,x.is_current,x.deleted_at,x.expires_at
                          FROM source_video_metadata x
                         WHERE x.video_id=l.video_id AND x.source_id=%s
                         ORDER BY x.fetched_at DESC,x.id DESC
                         LIMIT 1
                     ) m ON TRUE
                    ORDER BY l.video_id
                    LIMIT %s""",
                (rules.project_id, rules.target_video_id, rules.source_id,
                 rules.evaluated_as_of, rules.source_id, rules.max_candidates + 1),
            )
            from .d1 import _records
            rows = _records(cursor)
            if len(rows) > rules.max_candidates:
                raise ContractError("cohort_population_overflow",
                                    "Population exceeds explicit max_candidates")
            selected = select_compatible_peers(rules, rows)
            # Eligibility must be rechecked at read time for each admitted
            # peer; no right to retain/reference unlicensed snapshot.
            policy = adapters.require("source_policy", conn)
            for item in selected.admitted:
                policy.assert_eligible(
                    conn, rules.source_id, item.video_id,
                    [item.snapshot_id], item.metadata_id, [],
                )
            return selected
