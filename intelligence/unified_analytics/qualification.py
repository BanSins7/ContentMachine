"""UA-MOD-03: offline, side-effect-free qualification over adapter-checked facts.

IMPORTANT: checked=True is an *adapter assertion* which a Python caller can
construct. This pure module does not authenticate it or read a database. Later
UA-MOD-05..13 integrations MUST establish checked/current from authoritative
records within a source-rights-aware transaction; never expose this function as
a production endpoint taking arbitrary user-supplied observations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping
from uuid import UUID

from .contracts import AnalyticsBoundaryError, canonical_hash, content_type_for_kind
from .dto import EvidenceRef, InputScope

GATE_SCHEMA = "uae-qualification/v1"
REQUIRED_PORTS = ("source_policy", "d1", "qdp", "sre", "group_a", "group_b", "group_c")
RIGHTS = ("can_store_metadata", "can_analyze_media", "can_store_metrics", "can_derive_scores")
REQUEST_KEY = re.compile(r"[A-Za-z0-9_.:-]{8,180}\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{1,79}\Z")
_VIDEO = frozenset(("video", "slideshow_video"))
_CAROUSEL = frozenset(("photo_carousel", "mixed_carousel"))


def validate_request(project_id: str, video_id: int, request_key: str) -> None:
    """Port the legacy immutable request-key syntax; prohibit bool/injected IDs."""
    if type(project_id) is not str or re.fullmatch(r"[0-9a-f]{32}\Z", project_id) is None:
        raise AnalyticsBoundaryError("invalid_project", "Expected canonical 32-lowerhex project")
    if type(video_id) is not int or video_id <= 0:
        raise AnalyticsBoundaryError("invalid_video", "Expected positive D1 video ID")
    if type(request_key) is not str or REQUEST_KEY.fullmatch(request_key) is None:
        raise AnalyticsBoundaryError("invalid_request_key", "Expected bounded immutable request key")


def _json_value(value: Any) -> Any:
    """Legacy-compatible Decimal/date/UUID JSON only, no arbitrary string casts."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise AnalyticsBoundaryError("invalid_payload", "Non-finite decimal not allowed")
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise AnalyticsBoundaryError("invalid_payload", "Naive datetime not allowed")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if type(value) is dict:
        if any(type(k) is not str for k in value):
            raise AnalyticsBoundaryError("invalid_payload", "JSON keys must be strings")
        return {k: _json_value(v) for k, v in value.items()}
    if type(value) in (tuple, list):
        return [_json_value(item) for item in value]
    if value is None or type(value) in (str, bool, int, float):
        return value
    raise AnalyticsBoundaryError("invalid_payload", "Non-JSON evidence value")


def canonical_gate_hash(value: dict) -> str:
    """Deterministic canonical digest; never authenticates an upstream producer."""
    if type(value) is not dict:
        raise AnalyticsBoundaryError("invalid_payload", "Canonical object required")
    try:
        data = json.dumps(_json_value(value), sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise AnalyticsBoundaryError("invalid_payload", "Canonical JSON required") from exc
    return hashlib.sha256(data).hexdigest()


class ObservationState(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class QualificationObservation:
    """Immutable *claim* of a checked DB read; NOT a cryptographic attestation.

    A future adapter must construct these from authoritative rows, never from
    event payloads, user JSON, fabricated scores or bare EvidenceRead.located.
    """
    port: str
    state: ObservationState
    reference: EvidenceRef | None = None
    facts: Mapping[str, Any] | None = None
    checked: bool = False
    current: bool = False
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.port not in REQUIRED_PORTS or type(self.port) is not str:
            raise AnalyticsBoundaryError("invalid_port", "Unknown qualification port")
        if type(self.state) is not ObservationState:
            raise AnalyticsBoundaryError("invalid_state", "Expected explicit evidence state")
        if type(self.checked) is not bool or type(self.current) is not bool:
            raise AnalyticsBoundaryError("invalid_verification", "No truthy verification coercion")
        if self.reference is not None and (
            not isinstance(self.reference, EvidenceRef) or self.reference.port != self.port
        ):
            raise AnalyticsBoundaryError("lineage_mismatch", "Port/ref mismatch")
        if self.facts is not None and type(self.facts) is not dict:
            raise AnalyticsBoundaryError("invalid_payload", "Facts must be canonical JSON mapping")
        if self.state in (ObservationState.MISSING, ObservationState.UNAVAILABLE, ObservationState.REVOKED):
            if self.reference is not None or self.facts is not None or self.checked or self.current:
                raise AnalyticsBoundaryError("invalid_state", "Absent/revoked evidence cannot carry facts")
            if type(self.reason_code) is not str or _REASON.fullmatch(self.reason_code) is None:
                raise AnalyticsBoundaryError("invalid_reason", "Typed reason required for absent evidence")
        else:
            if self.reference is None or self.facts is None or self.reason_code is not None:
                raise AnalyticsBoundaryError("invalid_state", "Located partial/complete needs a reference and facts")
            if self.reference.payload_sha256 != canonical_gate_hash(dict(self.facts)):
                raise AnalyticsBoundaryError("payload_digest_mismatch", "Observed facts differ from reference hash")

    @property
    def usable(self) -> bool:
        return (
            self.state is ObservationState.COMPLETE
            and self.checked is True and self.current is True
        )


@dataclass(frozen=True, slots=True)
class QualificationInput:
    """As-of UTC and exact input scope must be selected explicitly by caller."""
    scope: InputScope
    as_of: datetime
    observations: Mapping[str, QualificationObservation]
    schema_version: str = GATE_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.scope, InputScope):
            raise AnalyticsBoundaryError("invalid_scope", "Expected pinned InputScope")
        if (type(self.as_of) is not datetime or self.as_of.tzinfo is None
                or self.as_of.utcoffset() is None or self.as_of.utcoffset().total_seconds() != 0):
            raise AnalyticsBoundaryError("invalid_time", "Explicit timezone-aware UTC as_of required")
        if type(self.observations) is not dict:
            raise AnalyticsBoundaryError("invalid_input", "Expected explicit port map")
        if set(self.observations) - set(REQUIRED_PORTS):
            raise AnalyticsBoundaryError("invalid_port", "Unexpected qualification port")
        for port, observation in self.observations.items():
            if type(port) is not str or not isinstance(observation, QualificationObservation):
                raise AnalyticsBoundaryError("invalid_input", "Typed observation required")
            if observation.port != port:
                raise AnalyticsBoundaryError("lineage_mismatch", "Observation map key mismatch")
            if observation.reference is not None and observation.reference.scope != self.scope:
                raise AnalyticsBoundaryError("lineage_mismatch", "Cross-publication observation")
        if self.schema_version != GATE_SCHEMA:
            raise AnalyticsBoundaryError("unsupported_schema_version", "Unknown gate version")


def _valid_positive(value: object) -> bool:
    return type(value) is int and value > 0


def _is_uuid(value: str) -> bool:
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def _valid_version(value: object) -> bool:
    return type(value) is str and bool(value.strip()) and len(value) <= 160


def _quality_score(value: object) -> bool:
    """Group C score is descriptive raw numeric, not a [0,100] normalized KPI."""
    if type(value) is bool or value is None:
        return False
    if type(value) not in (str, int, float, Decimal):
        return False
    try:
        return Decimal(str(value)).is_finite()
    except (InvalidOperation, ValueError):
        return False


def evaluate_qualification(value: QualificationInput) -> dict:
    """A pure legacy-informed, stricter three-state gate.

    Missing/revoked source or unauthorized QDP => blocked.
    A/B/C incomplete => partial (unless another hard blocker exists).
    Any missing/unchecked/current-unknown source assertion => blocked.
    'qualified' is only a conditional offline gate result; NEVER an assertion
    that peer adapters were actually run or reviewed in production.
    """
    if not isinstance(value, QualificationInput):
        raise AnalyticsBoundaryError("invalid_input", "Expected pinned QualificationInput")

    scope = value.scope
    obs = value.observations
    blockers: list[str] = []

    def block(code: str) -> None:
        if code not in blockers:
            blockers.append(code)

    readiness: dict[str, str] = {}
    for port in REQUIRED_PORTS:
        item = obs.get(port)
        if item is None:
            block(port + "_missing")
            readiness[port] = "missing"
        elif item.state is ObservationState.REVOKED:
            block(port + "_revoked")
            readiness[port] = "revoked"
        elif item.state is ObservationState.UNAVAILABLE:
            block(port + "_unavailable")
            readiness[port] = "unavailable"
        elif item.state is ObservationState.MISSING:
            block(port + "_missing")
            readiness[port] = "missing"
        elif not item.checked or not item.current:
            block(port + "_unchecked_or_stale")
            readiness[port] = "unverified"
        elif item.state is ObservationState.PARTIAL:
            block(port + "_incomplete")
            readiness[port] = "incomplete"
        else:
            readiness[port] = "ready"

    def facts(port: str) -> Mapping[str, Any]:
        item = obs.get(port)
        if item is None or readiness[port] != "ready":
            return {}
        return item.facts or {}

    # A COMPLETE status on an empty canonical payload is not positive evidence.
    # Without this guard seven empty 'checked' claims could become qualified.
    for port in REQUIRED_PORTS:
        if readiness[port] == "ready" and not facts(port):
            block(port + "_facts_missing")

    source = facts("source_policy")
    d1 = facts("d1")
    qdp = facts("qdp")
    sre = facts("sre")
    a = facts("group_a")
    b = facts("group_b")
    c = facts("group_c")

    # Current source rights always outrank cached positive analytics.
    # Not having authoritative current policy is a HARD blocker below.
    rights_source = source.get("source") if type(source.get("source")) is dict else {}
    policy = source.get("policy") if type(source.get("policy")) is dict else {}
    perms = policy.get("permissions") if type(policy.get("permissions")) is dict else {}
    if source:
        if (rights_source.get("id") != scope.source_id
                or rights_source.get("status") != "enabled"
                or policy.get("source_id") != scope.source_id
                or policy.get("version_id") != scope.source_policy_version_id
                or policy.get("is_active") is not True):
            block("source_policy_inactive")
        for right in RIGHTS:
            if rights_source.get(right) is not True or perms.get(right) is not True:
                block("rights_" + right)
        if policy.get("retention_valid") is not True or policy.get("not_revoked") is not True:
            block("source_retention_or_revocation")
        expiry = policy.get("expires_at")
        if expiry is not None:
            if type(expiry) is not str:
                block("source_expiry_invalid")
            else:
                try:
                    dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                    if dt.tzinfo is None or dt.utcoffset() is None or dt <= value.as_of:
                        block("source_expired")
                except (ValueError, OverflowError):
                    block("source_expiry_invalid")

    if d1:
        if (d1.get("project_id") != scope.project_id
                or d1.get("video_id") != scope.video_id
                or d1.get("source_id") != scope.source_id
                or d1.get("content_kind") != scope.content_kind
                or d1.get("project_link") is not True
                or type(d1.get("content_sha256")) is not str
                or re.fullmatch(r"[0-9a-f]{64}\Z", d1["content_sha256"]) is None):
            block("not_in_project")
        metric = d1.get("metric") if type(d1.get("metric")) is dict else {}
        if not metric or not _valid_positive(metric.get("id")):
            block("c_metric_missing")
        else:
            if metric.get("video_id") != scope.video_id or metric.get("source_id") != scope.source_id:
                block("metric_source_mismatch")
            if metric.get("data_quality_status") != "ok" or metric.get("valid_retention") is not True:
                block("metric_quality_or_retention")
            if not _valid_version(metric.get("metric_definition")) or not _valid_version(metric.get("metric_version")):
                block("metric_definition_missing")

    if qdp:
        if (qdp.get("project_id") != scope.project_id
                or qdp.get("video_id") != scope.video_id
                or qdp.get("qdp_run_id") != scope.qdp_run_id
                or qdp.get("source_id") != scope.source_id
                or qdp.get("source_policy_version_id") != scope.source_policy_version_id
                or qdp.get("input_sha256") != scope.qdp_input_sha256
                or qdp.get("qualified") is not True):
            block("qdp_membership_or_lineage_mismatch")

    if sre:
        if (sre.get("execution_mode") != "live"
                or sre.get("run_status") != "completed"
                or sre.get("decision") != "match"
                or sre.get("approved") is not True
                or sre.get("is_current") is not True
                or sre.get("superseded") is not False
                or not _valid_version(sre.get("taxonomy_version"))
                or not _valid_positive(sre.get("content_version_id"))
                or type(sre.get("run_id")) is not str
                or not _is_uuid(sre.get("run_id"))
                or type(sre.get("taxonomy_sha256")) is not str
                or re.fullmatch(r"[0-9a-f]{64}\Z", sre["taxonomy_sha256"]) is None
                or sre.get("source_policy_version_id") != scope.source_policy_version_id
                or sre.get("content_sha256") != d1.get("content_sha256")):
            block("no_approved_current_live_relevance")

    if a:
        if (a.get("state") != "completed" or not _valid_positive(a.get("analysis_id"))
                or a.get("video_id") != scope.video_id or a.get("source_id") != scope.source_id
                or a.get("content_kind") != scope.content_kind
                or a.get("qdp_input_sha256") != scope.qdp_input_sha256):
            block("a_missing_or_incomplete")
        if (a.get("verified_media") is not True
                or type(a.get("manifest_sha256")) is not str
                or re.fullmatch(r"[0-9a-f]{64}\Z", a["manifest_sha256"]) is None
                or type(a.get("snapshot_sha256")) is not str
                or re.fullmatch(r"[0-9a-f]{64}\Z", a["snapshot_sha256"]) is None):
            block("a_media_unverified")
        if scope.content_kind in _VIDEO:
            if (not _quality_score(a.get("duration_sec"))
                    or Decimal(str(a.get("duration_sec"))) <= 0
                    or not _valid_positive(a.get("scene_count"))
                    or any(a.get(k) != "complete" for k in ("scene_status","speech_status","screen_text_status"))
                    or a.get("has_subtitles") not in ("true","false")):
                block("a_incomplete_measurement_coverage")
        else:
            if (a.get("slide_status") != "complete" or a.get("text_status") != "complete"
                    or type(a.get("slide_count")) is not int or a["slide_count"] < 2):
                block("a_incomplete_slide_coverage")

    if b:
        expect_kind = content_type_for_kind(scope.content_kind)
        if (b.get("state") != "completed" or b.get("needs_review") is not False
                or not _valid_positive(b.get("analysis_id"))
                or b.get("video_id") != scope.video_id or b.get("source_id") != scope.source_id):
            block("b_missing_or_unreviewed")
        if b.get("content_type") != expect_kind:
            block("b_kind_mismatch")
        if a and (b.get("a_analysis_id") != a.get("analysis_id")
                  or b.get("group_a_snapshot_sha256") != a.get("snapshot_sha256")
                  or b.get("media_manifest_sha256") != a.get("manifest_sha256")):
            block("a_b_lineage_mismatch")
        if b.get("qdp_input_sha256") != scope.qdp_input_sha256:
            block("b_qdp_lineage_mismatch")
        version = b.get("taxonomy_version")
        features = b.get("features") if type(b.get("features")) is dict else {}
        hook = features.get("hook") if type(features.get("hook")) is dict else {}
        fmt = features.get("format") if type(features.get("format")) is dict else {}
        topic = features.get("topic") if type(features.get("topic")) is dict else {}
        if hook.get("classification_status") != "inferred":
            block("b1_hook_unresolved")
        if fmt.get("classification_status") != "inferred":
            block("b2_format_unresolved")
        story = features.get("story") if type(features.get("story")) is dict else {}
        if expect_kind == "video":
            frames, intervals = story.get("frames"), story.get("intervals")
            units = frames + intervals if type(frames) is list and type(intervals) is list else None
        else:
            units = story.get("slides")
        if (type(units) is not list or not units
                or any(type(x) is not dict or x.get("needs_review") is not False for x in units)):
            block("b3_story_unresolved")
        visual = features.get("visual")
        if type(visual) is not dict or type(visual.get("person_visible")) is not bool:
            block("b4_visual_not_measured")
        editing = features.get("editing")
        if expect_kind == "video" and (
            type(editing) is not dict or type(editing.get("cut_count")) is not int
            or editing["cut_count"] < 0
        ):
            block("b5_editing_not_measured")
        subtitles = features.get("subtitles")
        if expect_kind == "video" and (
            type(subtitles) is not dict
            or subtitles.get("subtitle_type") not in ("none", "standard", "dynamic")
        ):
            block("b6_subtitles_not_measured")
        cta = features.get("cta_summary") if type(features.get("cta_summary")) is dict else {}
        ctas = features.get("ctas")
        if (cta.get("analysis_status") != "complete"
                or type(cta.get("has_cta")) is not bool
                or type(ctas) is not list
                or cta.get("has_cta") is not (bool(ctas) if type(ctas) is list else None)):
            block("b7_cta_unresolved")
        if type(ctas) is not list or any(
            type(x) is not dict or x.get("extraction_status") != "detected" for x in ctas
        ):
            block("b7_cta_needs_review")
        if not _valid_positive(topic.get("topic_id")) or topic.get("needs_review") is not False:
            block("b8_topic_unresolved")
        if not _valid_version(version) or any(
            section.get("taxonomy_version") != version for section in (hook, fmt, topic)
        ):
            block("b_taxonomy_version_mismatch")

    if c:
        metric = d1.get("metric") if type(d1.get("metric")) is dict else {}
        if (not _valid_positive(c.get("calculated_metric_id"))
                or c.get("persisted") is not True
                or c.get("score_status") != "complete"
                or not _quality_score(c.get("breakout_score"))):
            block("c_score_not_complete")
        if (c.get("post_id") != scope.video_id
                or c.get("source_id") != scope.source_id
                or not metric or c.get("snapshot_id") != metric.get("id")
                or c.get("metric_definition") != metric.get("metric_definition")
                or c.get("metric_version") != metric.get("metric_version")):
            block("c_lineage_mismatch")
        if (c.get("approved_config") is not True
                or c.get("approved_cohorts") is not True
                or not _valid_positive(c.get("score_config_id"))
                or not _valid_version(c.get("score_version"))):
            block("c_configuration_unapproved")

    # Required unknown / not_current facts remain UNKNOWN and cannot default to True.
    # These hard blockers prevent cached, revoked or unqualified inputs leaking
    # through read-side aggregate/ranking logic in future stages.
    hard = any(
        readiness[x] != "ready" or x + "_facts_missing" in blockers
        for x in ("source_policy", "d1", "qdp")
    ) or any(code.startswith(("rights_", "source_", "not_in_project", "qdp_"))
             or code in ("metric_quality_or_retention", "metric_source_mismatch")
             for code in blockers)
    if sre and "no_approved_current_live_relevance" in blockers:
        hard = True
    status = "blocked" if hard else ("qualified" if not blockers else "partial")
    # Never turn an unknown/not-run/partial port into a boolean False readiness
    # or a numeric 0. The caller receives explicit tri/extended-state truth.
    # Ready means both structurally checked and semantically eligible. Keep
    # tri-state unknown/incomplete/revoked reason distinctions intact.
    affected = {
        "source_policy": lambda code: code.startswith(("rights_", "source_")),
        "d1": lambda code: code in ("not_in_project", "c_metric_missing",
                                  "metric_source_mismatch", "metric_quality_or_retention",
                                  "metric_definition_missing"),
        "qdp": lambda code: code.startswith("qdp_"),
        "sre": lambda code: code == "no_approved_current_live_relevance"
                            or code == "sre_facts_missing",
        "group_a": lambda code: code.startswith(("a_", "carousel_", "group_a_facts_")),
        "group_b": lambda code: code.startswith(("b_", "group_b_facts_"))
                              or code == "a_b_lineage_mismatch",
        "group_c": lambda code: code.startswith(("c_", "group_c_facts_")),
    }
    for port, predicate in affected.items():
        if readiness[port] == "ready" and any(predicate(code) for code in blockers):
            readiness[port] = "incomplete"

    # Pin exact input versions and observation digests. A different Group C raw
    # score or evidence hash must yield a different deterministic decision ID.
    input_fingerprint = {
        "as_of": value.as_of.isoformat(),
        "scope_sha256": scope.scope_sha256,
        "ports": {
            port: {
                "state": obs[port].state.value,
                "checked": obs[port].checked,
                "current": obs[port].current,
                "reason_code": obs[port].reason_code,
                "lineage_sha256": obs[port].reference.lineage_sha256
                                  if obs[port].reference else None,
            } if port in obs else None
            for port in REQUIRED_PORTS
        },
    }
    outcome = {
        "schema_version": GATE_SCHEMA,
        "status": status,
        "blockers": blockers,
        "readiness": readiness,
        "scope_sha256": scope.scope_sha256,
        "inputs_sha256": canonical_hash(input_fingerprint),
    }
    outcome["decision_sha256"] = canonical_hash(outcome)
    return outcome
