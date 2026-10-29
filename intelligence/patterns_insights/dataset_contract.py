"""PIE-MOD-03: strict, deterministic *untrusted* UAE research manifest v1.

Checks structural and intra-manifest integrity only. SHA-256 is NOT an
upstream signature, live qualification proof, or current source authorization.
A future adapter must authenticate producer and recheck provenance/rights.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any
from uuid import UUID

from .contracts import PIEBoundaryError, PIEDatasetPointer, canonical_hash

MANIFEST_SCHEMA = "uae-to-pie/v1"  # proposed wire, not currently exposed by UAE
DATASET_SCHEMA = "pie-row-dataset/v1"
MAX_OBSERVATIONS = 50_000  # parser safety cap, not a statistical min-N
MAX_EXCLUSIONS = 50_000
MAX_JSON_BYTES = 64 * 1024 * 1024
_MAX_PK = 2**63 - 1
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9._:-]{1,160}\Z")
_VERSION = re.compile(r"[A-Za-z0-9._:/-]{1,160}\Z")
_FEATURE_NAME = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{1,79}\Z")
_SCORE = re.compile(r"-?(?:0|[1-9][0-9]{0,20})(?:\.[0-9]{1,18})?\Z")
_KINDS = frozenset(("video", "slideshow_video", "photo_carousel", "mixed_carousel"))


def _fail(code: str, detail: str) -> None:
    raise PIEBoundaryError(code, detail)


def _object(value: Any, keys: tuple[str, ...], label: str) -> dict:
    if type(value) is not dict or set(value) != set(keys) or any(type(k) is not str for k in value):
        _fail("invalid_manifest_shape", f"{label}: missing/unknown keys or non-object")
    return value


def _text(value: Any, label: str, *, max_chars: int = 160) -> str:
    if type(value) is not str or not value.strip() or len(value) > max_chars or any(ord(c) < 32 or ord(c) == 127 for c in value):
        _fail("invalid_text", f"{label}: invalid bounded text")
    return value


def _token(value: Any, label: str) -> str:
    if type(value) is not str or not _TOKEN.fullmatch(value):
        _fail("invalid_token", f"{label}: invalid token")
    return value


def _version(value: Any, label: str) -> str:
    if type(value) is not str or not _VERSION.fullmatch(value):
        _fail("invalid_version", f"{label}: expected bounded explicit version")
    return value


def _sha(value: Any, label: str) -> str:
    if type(value) is not str or not _SHA.fullmatch(value):
        _fail("invalid_sha256", f"{label}: expected canonical SHA-256")
    return value


def _pk(value: Any, label: str) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_PK:
        _fail("invalid_identifier", f"{label}: expected positive BIGINT")
    return value


def _count(value: Any, label: str, limit: int) -> int:
    if type(value) is not int or not 0 <= value <= limit:
        _fail("invalid_count", f"{label}: invalid nonnegative count")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if type(value) is not str or len(value) > 40 or "T" not in value:
        _fail("invalid_timestamp", f"{label}: required RFC3339-like timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("invalid_timestamp", f"{label}: malformed timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail("invalid_timestamp", f"{label}: timezone required")
    return parsed


def _uuid(value: Any, label: str) -> str:
    if type(value) is not str:
        _fail("invalid_uuid", f"{label}: required UUID")
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError):
        _fail("invalid_uuid", f"{label}: malformed UUID")
    if str(parsed) != value:
        _fail("invalid_uuid", f"{label}: required canonical UUID")
    return value


def _features(value: Any, label: str) -> None:
    """Bounded, flat, unknown-preserving claims; semantics belong to PIE-MOD-11."""
    if type(value) is not dict or not 1 <= len(value) <= 100:
        _fail("invalid_features", f"{label}: must contain feature claims")
    for name, item in value.items():
        if type(name) is not str or not _FEATURE_NAME.fullmatch(name):
            _fail("invalid_features", f"{label}: invalid feature name")
        if item is None or type(item) is bool:
            continue
        if type(item) is int and -(2**63) <= item <= _MAX_PK:
            continue
        if type(item) is str and len(item) <= 250 and not any(ord(c) < 32 or ord(c) == 127 for c in item):
            continue
        # Ordered B3 sequences may repeat labels. Not a multinomial sample.
        if type(item) is list and len(item) <= 64 and all(
            type(x) is str and 0 < len(x) <= 120 and not any(ord(c) < 32 for c in x)
            for x in item
        ):
            continue
        _fail("invalid_features", f"{label}: unsupported feature value")


def _decimal(value: Any) -> Decimal:
    if type(value) is not str or not _SCORE.fullmatch(value):
        _fail("invalid_score", "C7 must be an explicit finite decimal string")
    try:
        d = Decimal(value)
    except InvalidOperation:
        _fail("invalid_score", "C7 is not decimal")
    if not d.is_finite():
        _fail("invalid_score", "Nonfinite score")
    # No normalization/clamp: raw C7 is *not* restricted to [0, 100].
    return d


@dataclass(frozen=True, slots=True)
class PIEObservation:
    """Frozen, replayable row snapshot. Access returns a fresh detached JSON dict."""
    video_id: int
    uae_snapshot_id: int
    creator_key: str
    score: Decimal
    row_sha256: str
    canonical_json: str

    def as_dict(self) -> dict:
        return json.loads(self.canonical_json)


@dataclass(frozen=True, slots=True)
class PIEValidatedDataset:
    """Structurally consistent, **not** authorized / authenticated research data."""
    pointer: PIEDatasetPointer
    manifest_sha256: str
    dataset_sha256: str
    manifest_json: str
    observations: tuple[PIEObservation, ...]
    excluded_video_ids: tuple[int, ...]
    scanned_count: int

    @property
    def verified_live(self) -> bool:
        return False

    @property
    def sample_size(self) -> int:
        return len(self.observations)

    def as_dict(self) -> dict:
        return json.loads(self.manifest_json)


def _row(row: Any, scope: dict, versions: dict, as_of: datetime) -> PIEObservation:
    keys = (
        "video_id", "uae_snapshot_id", "uae_snapshot_sha256", "project_id", "source_id",
        "source_policy_version_id", "creator_key", "platform", "content_kind",
        "observed_at", "published_at", "qdp_run_id", "qdp_preparation_sha256",
        "group_a_analysis_id", "group_b_analysis_id", "metric_snapshot_id",
        "calculated_metric_id", "sre_decision_sha256", "group_a_sha256",
        "group_b_sha256", "metric_snapshot_sha256", "calculated_metric_sha256",
        "features_a", "features_b", "features_sha256", "status", "score_status",
        "breakout_score", "group_a_version", "group_b_version", "group_b_taxonomy_version",
        "sre_taxonomy_version", "creator_cohort_version", "prior_cohort_version",
        "niche_cohort_version",
    )
    data = _object(row, keys, "observation")
    for name in ("video_id", "uae_snapshot_id", "source_policy_version_id",
                 "group_a_analysis_id", "group_b_analysis_id", "metric_snapshot_id", "calculated_metric_id"):
        _pk(data[name], name)
    for name in ("project_id", "source_id", "source_policy_version_id"):
        if data[name] != scope[name] or type(data[name]) is not type(scope[name]):
            _fail("scope_mismatch", f"observation.{name} mismatches manifest scope")
    _token(data["creator_key"], "creator_key")
    _token(data["platform"], "platform")
    if type(data["content_kind"]) is not str or data["content_kind"] not in _KINDS:
        _fail("unsupported_kind", "Only B-supported video/carousel kinds")
    observed = _timestamp(data["observed_at"], "observed_at")
    published = _timestamp(data["published_at"], "published_at")
    if published > observed or observed > as_of:
        _fail("temporal_mismatch", "published_at <= observed_at <= as_of required")
    _uuid(data["qdp_run_id"], "qdp_run_id")
    for name in ("uae_snapshot_sha256", "qdp_preparation_sha256", "sre_decision_sha256",
                 "group_a_sha256", "group_b_sha256", "metric_snapshot_sha256", "calculated_metric_sha256",
                 "features_sha256"):
        _sha(data[name], name)
    if data["status"] != "qualified" or data["score_status"] != "complete":
        _fail("unqualified_input", "Only claimed qualified / complete rows accepted")
    _features(data["features_a"], "features_a")
    _features(data["features_b"], "features_b")
    if data["features_sha256"] != canonical_hash({"a": data["features_a"], "b": data["features_b"]}):
        _fail("feature_hash_mismatch", "Reported feature payload checksum mismatch")
    for name in versions:
        if data[name] != versions[name] or type(data[name]) is not type(versions[name]):
            _fail("version_mismatch", f"observation.{name} mismatches frozen versions")
    score = _decimal(data["breakout_score"])
    canonical_json = json.dumps(data, sort_keys=True, ensure_ascii=False,
                                separators=(",", ":"), allow_nan=False)
    return PIEObservation(data["video_id"], data["uae_snapshot_id"], data["creator_key"],
                          score, canonical_hash(data), canonical_json)


def validate_dataset_manifest(pointer: PIEDatasetPointer, manifest: dict) -> PIEValidatedDataset:
    """Validate a proposed \'uae-to-pie/v1\' untrusted snapshot against its pointer.

    Checks schema, exact refs, frozen selection, row identity, row and feature
    hashes, timestamp consistency, nonfinite data, and **two** envelope hashes.
    Cannot prove UAE produced this data; never publish inferential conclusions
    without the later authenticated, source-rights-gated integration reader.
    """
    if not isinstance(pointer, PIEDatasetPointer):
        _fail("invalid_pointer", "A validated PIE pointer is required")
    m = _object(manifest, ("schema_version", "producer", "scope", "versions", "selection", "observations"), "manifest")
    if m["schema_version"] != MANIFEST_SCHEMA:
        _fail("unsupported_schema", "Manifest is not uae-to-pie/v1")
    producer = _object(m["producer"], ("engine_version", "run_id", "dataset_build_version"), "producer")
    _uuid(producer["run_id"], "run_id")
    _version(producer["engine_version"], "engine_version")
    _version(producer["dataset_build_version"], "dataset_build_version")
    if producer["engine_version"] != pointer.uae_engine_version:
        _fail("version_mismatch", "UAE engine does not match the pointer")
    scope_keys = ("project_id", "source_id", "source_policy_version_id", "calculation_config_id",
                  "source_metric_definition", "source_metric_version", "as_of")
    scope = _object(m["scope"], scope_keys, "scope")
    for name in scope_keys:
        if scope[name] != getattr(pointer, name) or type(scope[name]) is not type(getattr(pointer, name)):
            _fail("scope_mismatch", f"scope.{name} mismatches pinned pointer")
    _timestamp(scope["as_of"], "as_of")
    if type(scope["calculation_config_id"]) is not int:
        _fail("invalid_identifier", "C config ID must be integer")
    version_keys = ("group_a_version", "group_b_version", "group_b_taxonomy_version",
                    "sre_taxonomy_version", "creator_cohort_version", "prior_cohort_version",
                    "niche_cohort_version")
    versions = _object(m["versions"], version_keys, "versions")
    for name in version_keys:
        if name == "niche_cohort_version" and versions[name] is None:
            continue
        _version(versions[name], name)
    sel = _object(m["selection"], ("rule_version", "population_sha256", "scanned_count",
                                    "included_count", "excluded_count", "exclusions"), "selection")
    _version(sel["rule_version"], "rule_version")
    _sha(sel["population_sha256"], "population_sha256")
    scanned = _count(sel["scanned_count"], "scanned_count", MAX_OBSERVATIONS + MAX_EXCLUSIONS)
    included = _count(sel["included_count"], "included_count", MAX_OBSERVATIONS)
    excluded = _count(sel["excluded_count"], "excluded_count", MAX_EXCLUSIONS)
    if scanned != included + excluded:
        _fail("sample_count_mismatch", "Selection counts do not add up")
    exclusions = sel["exclusions"]
    if type(exclusions) is not list or len(exclusions) != excluded:
        _fail("sample_count_mismatch", "Exclusion count differs from excluded entries")
    excluded_ids = []
    for x in exclusions:
        x = _object(x, ("video_id", "reason_code"), "exclusion")
        excluded_ids.append(_pk(x["video_id"], "excluded_video_id"))
        if type(x["reason_code"]) is not str or not _REASON.fullmatch(x["reason_code"]):
            _fail("invalid_exclusion", "Exclusion must have safe typed reason")
    if excluded_ids != sorted(set(excluded_ids)):
        _fail("duplicate_video", "Excluded videos must have unique ascending IDs")
    rows = m["observations"]
    if type(rows) is not list or not 1 <= len(rows) <= MAX_OBSERVATIONS:
        _fail("invalid_sample", "Expected bounded nonempty row-level sample")
    if included != len(rows):
        _fail("sample_count_mismatch", "Included count differs from real row count")
    as_of = _timestamp(scope["as_of"], "as_of")
    typed_rows = tuple(_row(row, scope, versions, as_of) for row in rows)
    ids = [row.video_id for row in typed_rows]
    if ids != sorted(set(ids)) or set(ids).intersection(excluded_ids):
        _fail("duplicate_video", "Sample rows must have unique ascending canonical video IDs disjoint from exclusions")
    snapshot_ids = [row.uae_snapshot_id for row in typed_rows]
    if len(snapshot_ids) != len(set(snapshot_ids)):
        _fail("duplicate_snapshot", "One UAE snapshot ID cannot appear in multiple row identities")
    dataset_hash = canonical_hash({"schema_version": DATASET_SCHEMA, "observations": rows})
    manifest_hash = canonical_hash(m)
    if pointer.uae_dataset_sha256 != dataset_hash:
        _fail("dataset_hash_mismatch", "Dataset hash does not match frozen UAE pointer")
    if pointer.uae_manifest_sha256 != manifest_hash:
        _fail("manifest_hash_mismatch", "Manifest hash does not match frozen UAE pointer")
    canon = json.dumps(m, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False)
    return PIEValidatedDataset(pointer, manifest_hash, dataset_hash, canon,
                               typed_rows, tuple(excluded_ids), scanned)


def load_dataset_json(pointer: PIEDatasetPointer, raw: str | bytes) -> PIEValidatedDataset:
    """Parse canonical JSON without ignoring duplicate keys, NaN or Infinity."""
    if type(raw) not in (str, bytes):
        _fail("invalid_json", "Manifest must be UTF-8 JSON")
    if type(raw) is bytes:
        if len(raw) > MAX_JSON_BYTES:
            _fail("payload_too_large", "Manifest exceeds maximum transport size")
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            _fail("invalid_json", "Manifest must use UTF-8")
    elif len(raw.encode("utf-8")) > MAX_JSON_BYTES:
        _fail("payload_too_large", "Manifest exceeds maximum transport size")

    def pairs_no_duplicates(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                _fail("duplicate_json_key", "Duplicate JSON object key")
            obj[k] = v
        return obj

    def reject_constant(value):
        _fail("invalid_json", "Nonfinite JSON number is forbidden")

    try:
        payload = json.loads(raw, object_pairs_hook=pairs_no_duplicates,
                             parse_constant=reject_constant)
    except PIEBoundaryError:
        raise
    except (ValueError, TypeError, RecursionError):
        _fail("invalid_json", "Malformed JSON payload")
    return validate_dataset_manifest(pointer, payload)
