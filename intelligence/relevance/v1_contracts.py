"""SRE V1 transport contracts: pure, provider-agnostic, no I/O or paid calls.

These validators are a stable boundary for S2–S10. They do not authenticate a
project, grant source rights, verify RTB publication in PostgreSQL or reserve
money. Those operations MUST be performed by the owning services.
Existing relevance.contracts / pipeline public APIs remain unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID

from .contracts import InvalidModelOutput, validate_labels

VERSION = "v1"
CONTRACT_VERSIONS = {
    "taxonomy": "sre-taxonomy/v1",
    "content": "sre-content-input/v1",
    "labels": "sre-labels/v1",
    "pipeline": "sre-pipeline-snapshot/v1",
    "run": "sre-run-plan/v1",
    "quote": "sre-cost-quote/v1",
    "consent": "sre-spending-consent/v1",
    "event": "sre-analytics-event/v1",
}
DECISIONS = frozenset(("match", "no_match", "uncertain"))
SYSTEM_DECISIONS = DECISIONS | frozenset(("not_evaluated",))
RUN_STATES = frozenset(("awaiting_authorization", "queued", "running",
    "paused_budget", "paused_rights", "uncertain", "partial",
    "completed", "failed", "cancelled"))
ATTEMPT_STATES = frozenset(("reserved", "dispatch_intent", "completed",
    "failed_known", "uncertain", "abandoned_before_dispatch"))
EVENT_TYPES = frozenset(("sre.classification_item_completed",
    "sre.classification_run_completed", "sre.classification_invalidated",
    "sre.rights_revoked"))
CONTENT_COMPLETENESS = frozenset(("metadata_only", "partial", "full"))
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
PROJECT_RE = re.compile(r"[0-9a-f]{32}\Z")
CODE_RE = re.compile(r"[a-z][a-z0-9_.-]{0,79}\Z")

RUN_TRANSITIONS = {
    "awaiting_authorization": frozenset(("queued", "cancelled", "failed")),
    "queued": frozenset(("running", "paused_budget", "paused_rights", "cancelled", "failed")),
    "running": frozenset(("paused_budget", "paused_rights", "uncertain", "partial",
                          "completed", "failed", "cancelled")),
    "paused_budget": frozenset(("queued", "running", "cancelled", "failed")),
    "paused_rights": frozenset(("queued", "running", "cancelled", "failed")),
    "uncertain": frozenset(("running", "partial", "completed", "failed", "cancelled")),
    "partial": frozenset(),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}
ATTEMPT_TRANSITIONS = {
    "reserved": frozenset(("dispatch_intent", "abandoned_before_dispatch")),
    "dispatch_intent": frozenset(("completed", "failed_known", "uncertain")),
    "uncertain": frozenset(("completed", "failed_known")),
    "completed": frozenset(),
    "failed_known": frozenset(),
    "abandoned_before_dispatch": frozenset(),
}


class SREContractError(ValueError):
    """Invalid data at a trust boundary. code is stable for API callers."""

    def __init__(self, code: str, path: str, message: str):
        self.code, self.path = code, path
        super().__init__(f"{path}: {message}")


def reject(path: str, message: str, code: str = "invalid_contract"):
    raise SREContractError(code, path, message)


def record(value, path, *, required=(), optional=()):
    if type(value) is not dict:
        reject(path, "expected object")
    missing = set(required) - set(value)
    extra = set(value) - set(required) - set(optional)
    if missing or extra:
        reject(path, f"missing={sorted(missing)} unexpected={sorted(extra)}")
    return value


def nonempty(value, path, *, limit=2048):
    if type(value) is not str or not value.strip() or len(value) > limit:
        reject(path, f"expected nonempty text of at most {limit} characters")
    return value.strip()


def code(value, path):
    value = nonempty(value, path, limit=80)
    if not CODE_RE.fullmatch(value):
        reject(path, "invalid symbolic identifier")
    return value


def positive(value, path, *, zero=False, maximum=None):
    if type(value) is not int or value < (0 if zero else 1) or (maximum is not None and value > maximum):
        reject(path, "invalid integer")
    return value


def sha256_digest(value, path):
    if type(value) is not str or SHA_RE.fullmatch(value) is None:
        reject(path, "expected lower-case sha256")
    return value


def canonical_uuid(value, path):
    if type(value) is not str:
        reject(path, "expected canonical UUID")
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError):
        reject(path, "expected canonical UUID")
    if str(parsed) != value:
        reject(path, "expected canonical lower-case UUID")
    return value


def project_id(value, path="project_id"):
    if type(value) is not str or PROJECT_RE.fullmatch(value) is None:
        reject(path, "expected 32 lower-case hexadecimal characters")
    return value


def utc_time(value, path):
    value = nonempty(value, path, limit=40)
    if not (value.endswith("Z") or re.search(r"[+-]\d\d:\d\d\Z", value)):
        reject(path, "timezone offset required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        reject(path, "invalid timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        reject(path, "timezone offset required")
    return parsed.astimezone(timezone.utc)


def decimal_usd(value, path, *, strictly_positive=False):
    # Never accept binary floats, booleans, NaN, Inf, negatives or >8 dp.
    if type(value) is not str or not re.fullmatch(r"(?:0|[1-9]\d{0,11})(?:\.\d{1,8})?", value):
        reject(path, "USD must be a nonnegative decimal string with <=8 fractional digits")
    try:
        result = Decimal(value)
    except InvalidOperation:
        reject(path, "invalid decimal USD")
    if not result.is_finite() or (strictly_positive and result <= 0):
        reject(path, "USD amount must be finite and positive")
    return result


def canonical_json(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise SREContractError("invalid_contract", "$", "not canonicalizable JSON") from exc


def fingerprint(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _version(payload, kind):
    if payload["schema_version"] != CONTRACT_VERSIONS[kind]:
        reject("schema_version", f"expected {CONTRACT_VERSIONS[kind]}", "unsupported_schema_version")


def _bounded_list(value, path, *, minimum=0, maximum=1000):
    if type(value) is not list or not minimum <= len(value) <= maximum:
        reject(path, "invalid list length")
    return value


def _unique_strings(value, path, *, maximum=1000, entries="text"):
    values = _bounded_list(value, path, maximum=maximum)
    result = []
    for index, item in enumerate(values):
        p = f"{path}[{index}]"
        result.append(code(item, p) if entries == "code" else nonempty(item, p))
    if len(set(result)) != len(result):
        reject(path, "duplicate values")
    return result


def _source_segment(raw, path):
    record(raw, path, required=("text", "start_ms", "end_ms"))
    text = nonempty(raw["text"], f"{path}.text", limit=10000)
    start = positive(raw["start_ms"], f"{path}.start_ms", zero=True)
    end = positive(raw["end_ms"], f"{path}.end_ms")
    if end <= start:
        reject(path, "end_ms must exceed start_ms")
    return {"text": text, "start_ms": start, "end_ms": end}


def validate_content_input(payload):
    """Validate a server-assembled snapshot shape; does NOT authorize the source."""
    record(payload, "content", required=(
        "schema_version", "project_id", "video_id", "source_id",
        "source_policy_version_id", "content_version_id", "content_sha256",
        "completeness", "rights", "data", "provenance"),
        optional=("media_asset_id", "expires_at"))
    _version(payload, "content")
    pid = project_id(payload["project_id"])
    vid = positive(payload["video_id"], "video_id")
    source = code(payload["source_id"], "source_id")
    policy = positive(payload["source_policy_version_id"], "source_policy_version_id")
    revision = positive(payload["content_version_id"], "content_version_id")
    digest = sha256_digest(payload["content_sha256"], "content_sha256")
    completeness = payload["completeness"]
    if completeness not in CONTENT_COMPLETENESS:
        reject("completeness", "unsupported completeness")
    rights = record(payload["rights"], "rights", required=(
        "can_store_metadata", "can_semantic_classify", "can_external_llm", "can_analyze_media"))
    if any(type(v) is not bool for v in rights.values()):
        reject("rights", "all capabilities must be explicit booleans")
    prov = record(payload["provenance"], "provenance",
                  required=("discovery_id", "search_query_id", "source_request_id"))
    for key, value in prov.items():
        if value is not None:
            positive(value, f"provenance.{key}")
    media_id = payload.get("media_asset_id")
    if media_id is not None:
        positive(media_id, "media_asset_id")
    expiry = payload.get("expires_at")
    if expiry is not None:
        utc_time(expiry, "expires_at")
    data = record(payload["data"], "data", required=("title", "description", "hashtags"),
                  optional=("transcript", "scenes"))
    result_data = {}
    for name in ("title", "description"):
        value = data[name]
        result_data[name] = None if value is None else nonempty(value, f"data.{name}", limit=30000)
    tags = _bounded_list(data["hashtags"], "data.hashtags", maximum=50)
    if any(type(tag) is not str or not tag.strip() or len(tag) > 150 for tag in tags):
        reject("data.hashtags", "invalid tag")
    result_data["hashtags"] = list(tags)
    media_present = False
    for key in ("transcript", "scenes"):
        segments = data.get(key)
        if segments is not None:
            parsed = [_source_segment(raw, f"data.{key}[{i}]")
                      for i, raw in enumerate(_bounded_list(segments, f"data.{key}", maximum=1000))]
            for left, right in zip(parsed, parsed[1:]):
                if right["start_ms"] < left["start_ms"]:
                    reject(f"data.{key}", "segments must be sorted by start_ms")
            result_data[key] = parsed
            media_present = media_present or bool(parsed)
        else:
            result_data[key] = None
    if len(canonical_json(result_data).encode("utf-8")) > 1048576:
        reject("data", "content snapshot exceeds 1 MiB; split at verified segment boundaries")
    if completeness == "metadata_only" and media_present:
        reject("completeness", "metadata_only cannot include media-derived segments")
    if media_present and (not rights["can_analyze_media"] or media_id is None):
        reject("rights", "media-derived content requires media rights and media asset")
    return {"schema_version": CONTRACT_VERSIONS["content"], "project_id": pid,
            "video_id": vid, "source_id": source, "source_policy_version_id": policy,
            "content_version_id": revision, "content_sha256": digest,
            "completeness": completeness, "rights": dict(rights), "data": result_data,
            "provenance": dict(prov), "media_asset_id": media_id,
            "expires_at": expiry}


def authorize_content_usage(content, *, external_llm=False, at_time=None):
    """Secondary fail-closed local guard. S3 must additionally query LIVE D1 policy."""
    obj = validate_content_input(content)
    rights = obj["rights"]
    if not rights["can_store_metadata"] or not rights["can_semantic_classify"]:
        reject("rights", "semantic processing not explicitly allowed", "rights_denied")
    if external_llm and not rights["can_external_llm"]:
        reject("rights.can_external_llm", "external provider transmission denied", "rights_denied")
    if at_time is None:
        at_time = datetime.now(timezone.utc)
    if not isinstance(at_time, datetime) or at_time.tzinfo is None or at_time.utcoffset() is None:
        reject("at_time", "timezone-aware datetime required")
    if obj["expires_at"] is not None and utc_time(obj["expires_at"], "expires_at") <= at_time:
        reject("expires_at", "content expired", "rights_expired")
    return obj


def validate_model_output(payload, expected_category_keys, content):
    """Pure model-output validation; system verdict 'not_evaluated' is forbidden."""
    record(payload, "labels", required=("schema_version", "labels"))
    _version(payload, "labels")
    snapshot = validate_content_input(content)
    expected = _unique_strings(_bounded_list(expected_category_keys, "expected_category_keys",
                                            minimum=1, maximum=48),
                               "expected_category_keys", maximum=48, entries="text")
    expected = [canonical_uuid(k, "expected_category_keys") for k in expected]
    rows = _bounded_list(payload["labels"], "labels.labels", minimum=1, maximum=48)
    raw = {}
    for i, row in enumerate(rows):
        path = f"labels.labels[{i}]"
        record(row, path, required=("category_key", "decision", "reason", "evidence"))
        key = canonical_uuid(row["category_key"], f"{path}.category_key")
        if key in raw:
            reject(path, "duplicate category_key", "invalid_model_output")
        if row["decision"] not in DECISIONS:
            reject(f"{path}.decision", "unsupported semantic decision", "invalid_model_output")
        nonempty(row["reason"], f"{path}.reason", limit=4000)
        if type(row["evidence"]) is not list or len(row["evidence"]) > 20:
            reject(f"{path}.evidence", "invalid evidence size", "invalid_model_output")
        for j, evidence in enumerate(row["evidence"]):
            record(evidence, f"{path}.evidence[{j}]",
                   required=("source", "quote"),
                   optional=("index", "start_ms", "end_ms"))
            nonempty(evidence["quote"], f"{path}.evidence[{j}].quote", limit=2000)
        raw[key] = {k: row[k] for k in ("decision", "reason", "evidence")}
    if set(raw) != set(expected):
        reject("labels", "model must provide every requested category exactly once",
               "invalid_model_output")
    try:
        labels = validate_labels(raw, expected, snapshot["data"])
    except InvalidModelOutput as exc:
        raise SREContractError("invalid_model_output", "labels", str(exc)) from exc
    return {"schema_version": CONTRACT_VERSIONS["labels"],
            "labels": [{"category_key": key, **labels[key]} for key in expected]}


def validate_retrieval_scores(scores, expected_category_keys):
    """Embedding output must cover every candidate; rank ties by UUID."""
    expected = [canonical_uuid(x, "expected_category_keys")
                for x in _unique_strings(_bounded_list(expected_category_keys, "expected_category_keys",
                                                       minimum=1, maximum=48),
                                         "expected_category_keys", maximum=48)]
    if type(scores) is not dict or set(scores) != set(expected):
        reject("scores", "expected exact category keys", "invalid_model_output")
    for k, v in scores.items():
        if type(v) not in (int, float) or not math.isfinite(v) or not -1 <= v <= 1:
            reject(f"scores.{k}", "invalid cosine similarity", "invalid_model_output")
    return sorted(expected, key=lambda k: (-scores[k], k))


def validate_transition(kind, before, after):
    transitions = {"run": RUN_TRANSITIONS, "attempt": ATTEMPT_TRANSITIONS}.get(kind)
    if transitions is None:
        reject("kind", "unsupported state machine")
    if before not in transitions or after not in transitions[before]:
        reject("transition", f"illegal {kind} transition {before!r} -> {after!r}",
               "illegal_state_transition")
    return after
