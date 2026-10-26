"""Frozen v1 wire contracts for standalone Group A (GA-MOD-01).

Pure validators: no database, filesystem, QDP/PSI imports or provider calls.
Never infer measurements from statuses, IDs from URL strings, or legacy names.
The JSON Schema files mirror wire shape; cross-field facts are enforced here.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from uuid import UUID

PROJECT = re.compile(r"[0-9a-f]{32}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
SOURCE = re.compile(r"[A-Za-z0-9._:-]{1,160}\Z")
OPAQUE_REF = re.compile(r"[a-z][a-z0-9-]{0,31}:[A-Za-z0-9._:-]{1,200}\Z")
SECOND = re.compile(r"(?:0|[1-9][0-9]*)\.[0-9]{3}\Z")
REASON = re.compile(r"[a-z][a-z0-9_]{1,79}\Z")
KIND_ITEMS = {
    "video": "video", "slideshow_video": "video",
    "photo_carousel": "image", "mixed_carousel": "mixed",
    "image_post": "image",
}
REQUIRED_STREAMS = {
    "video": frozenset(("technical", "speech", "screen_text", "audio", "subtitles")),
    "slideshow_video": frozenset(("technical", "speech", "screen_text", "audio", "subtitles")),
    "photo_carousel": frozenset(("slides", "screen_text")),
    "image_post": frozenset(("slides", "screen_text")),
    "mixed_carousel": frozenset(("slides", "technical", "speech", "screen_text", "audio", "subtitles")),
}
STREAM_STATUSES = frozenset(("not_run", "complete", "partial", "unavailable"))


class ContractViolation(ValueError):
    def __init__(self, code: str, field: str):
        super().__init__(f"{code}: {field}")
        self.code, self.field = code, field


def _fail(code: str, field: str):
    raise ContractViolation(code, field)


def _object(raw, required, optional=(), *, name="payload"):
    if type(raw) is not dict:
        _fail("invalid_shape", name)
    missing = set(required) - raw.keys()
    extra = raw.keys() - set(required) - set(optional)
    if missing:
        _fail("missing_field", name + "." + sorted(missing)[0])
    if extra:
        _fail("unexpected_field", name + "." + sorted(extra)[0])
    return raw


def _token(value, regex, field):
    if type(value) is not str or regex.fullmatch(value) is None:
        _fail("invalid_field", field)
    return value


def _positive(value, field):
    if type(value) is not int or value < 1:
        _fail("invalid_id", field)
    return value


def _uuid(value, field):
    if type(value) is not str:
        _fail("invalid_id", field)
    try:
        if str(UUID(value)) != value:
            raise ValueError("noncanonical")
    except (ValueError, AttributeError):
        _fail("invalid_id", field)
    return value


def _seconds(value, field):
    _token(value, SECOND, field)
    try:
        result = Decimal(value)
    except InvalidOperation:
        _fail("invalid_timestamp", field)
    return result


def canonical_json_bytes(payload):
    """No default=str, NaN or implicit coercion of external objects."""
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        _fail("invalid_canonical_json", "payload")


def canonical_sha256(payload) -> str:
    return sha256(canonical_json_bytes(payload)).hexdigest()


def qdp_handoff_v1(raw):
    """Mirror the observed QDP-owned QDPHandoffPointer, not a guess at its DB."""
    a = _object(raw, ("schema_version", "event_id", "project_id",
                       "qdp_run_id", "video_id", "analysis_group", "input_sha256"),
                ("event_payload_sha256", "preferred_media_asset_id"), name="qdp")
    if a["schema_version"] != "qdp-handoff/v1":
        _fail("incompatible_schema", "qdp.schema_version")
    _uuid(a["event_id"], "qdp.event_id")
    _uuid(a["qdp_run_id"], "qdp.qdp_run_id")
    _token(a["project_id"], PROJECT, "qdp.project_id")
    _positive(a["video_id"], "qdp.video_id")
    if a["analysis_group"] != "A":
        _fail("invalid_group", "qdp.analysis_group")
    _token(a["input_sha256"], DIGEST, "qdp.input_sha256")
    if "event_payload_sha256" in a:
        _token(a["event_payload_sha256"], DIGEST, "qdp.event_payload_sha256")
    if "preferred_media_asset_id" in a and a["preferred_media_asset_id"] is not None:
        _positive(a["preferred_media_asset_id"], "qdp.preferred_media_asset_id")
    return dict(a)


def _validated_kind(raw):
    if type(raw) is not str or raw not in KIND_ITEMS:
        _fail("unsupported_media_kind", "content_kind")
    return raw


def _verified_at(value):
    if type(value) is not str or not value.endswith("Z"):
        _fail("invalid_timestamp", "manifest.verified_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("invalid_timestamp", "manifest.verified_at")
    if parsed.utcoffset().total_seconds() != 0:
        _fail("invalid_timestamp", "manifest.verified_at")


def media_manifest_v1(raw):
    fields = ("schema_version", "project_id", "qdp_run_id", "video_id",
              "qdp_input_sha256", "content_kind", "source_id",
              "source_policy_version_id", "verified_at", "items", "manifest_sha256")
    a = _object(raw, fields, name="manifest")
    if a["schema_version"] != "ga-media-manifest/v1":
        _fail("incompatible_schema", "manifest.schema_version")
    _token(a["project_id"], PROJECT, "manifest.project_id")
    _uuid(a["qdp_run_id"], "manifest.qdp_run_id")
    _positive(a["video_id"], "manifest.video_id")
    _token(a["qdp_input_sha256"], DIGEST, "manifest.qdp_input_sha256")
    kind = _validated_kind(a["content_kind"])
    _token(a["source_id"], SOURCE, "manifest.source_id")
    _positive(a["source_policy_version_id"], "manifest.source_policy_version_id")
    _verified_at(a["verified_at"])
    items = a["items"]
    if type(items) is not list or not 1 <= len(items) <= 100:
        _fail("invalid_items", "manifest.items")
    seen = set()
    count = {"image": 0, "video": 0}
    for i, entry in enumerate(items, 1):
        item = _object(entry, ("index", "kind", "media_asset_id", "sha256",
                               "byte_size", "verified_local_path_ref"),
                       ("duration_sec",), name=f"items[{i}]")
        if type(item["index"]) is not int or item["index"] != i:
            _fail("noncanonical_slide_order", f"items[{i}].index")
        if item["kind"] not in count or type(item["kind"]) is not str:
            _fail("invalid_media_kind", f"items[{i}].kind")
        count[item["kind"]] += 1
        asset = _positive(item["media_asset_id"], f"items[{i}].media_asset_id")
        if asset in seen:
            _fail("duplicate_media_asset", f"items[{i}].media_asset_id")
        seen.add(asset)
        _token(item["sha256"], DIGEST, f"items[{i}].sha256")
        if type(item["byte_size"]) is not int or not 1 <= item["byte_size"] <= 2*1024**3:
            _fail("invalid_media_bytes", f"items[{i}].byte_size")
        _token(item["verified_local_path_ref"], OPAQUE_REF, f"items[{i}].verified_local_path_ref")
        if item["kind"] == "image" and "duration_sec" in item:
            _fail("image_has_duration", f"items[{i}].duration_sec")
        if item["kind"] == "video" and "duration_sec" in item:
            if not (Decimal("0") < _seconds(item["duration_sec"], f"items[{i}].duration_sec")
                    <= Decimal("600")):
                _fail("invalid_duration", f"items[{i}].duration_sec")
    if kind in ("video", "slideshow_video") and (len(items) != 1 or count["video"] != 1):
        _fail("kind_item_mismatch", "manifest.items")
    if kind == "image_post" and (len(items) != 1 or count["image"] != 1):
        _fail("kind_item_mismatch", "manifest.items")
    if kind == "photo_carousel" and (len(items) < 2 or count["image"] != len(items)):
        _fail("kind_item_mismatch", "manifest.items")
    if kind == "mixed_carousel" and (len(items) < 2 or min(count.values()) < 1):
        _fail("kind_item_mismatch", "manifest.items")
    _token(a["manifest_sha256"], DIGEST, "manifest.manifest_sha256")
    expected = canonical_sha256({k: v for k, v in a.items() if k != "manifest_sha256"})
    if a["manifest_sha256"] != expected:
        _fail("manifest_digest_mismatch", "manifest.manifest_sha256")
    return dict(a)


def stream_truth_v1(raw, *, field="stream"):
    a = _object(raw, ("status", "evidence_count", "verified_absence", "reason_code"),
                name=field)
    status = a["status"]
    count = a["evidence_count"]
    absence = a["verified_absence"]
    reason = a["reason_code"]
    if type(status) is not str or status not in STREAM_STATUSES:
        _fail("invalid_status", field + ".status")
    if type(absence) is not bool:
        _fail("invalid_field", field + ".verified_absence")
    if count is not None and (type(count) is not int or count < 0):
        _fail("invalid_field", field + ".evidence_count")
    if status == "complete":
        if count is None or reason is not None or (count == 0 and not absence) or (count > 0 and absence):
            _fail("inconsistent_truth", field)
    else:
        _token(reason, REASON, field + ".reason_code")
        if absence or (status in ("not_run", "unavailable") and count is not None):
            _fail("inconsistent_truth", field)
    return dict(a)


def group_a_to_b_v1(raw):
    fields = ("schema_version", "project_id", "qdp_run_id", "video_id",
              "qdp_input_sha256", "manifest_sha256", "content_kind",
              "analysis_state", "video_a_analysis_id", "carousel_a_analysis_id",
              "method_version", "streams", "evidence")
    a = _object(raw, fields, name="result")
    if a["schema_version"] != "group-a-to-b/v1":
        _fail("incompatible_schema", "result.schema_version")
    _token(a["project_id"], PROJECT, "result.project_id")
    _uuid(a["qdp_run_id"], "result.qdp_run_id")
    _positive(a["video_id"], "result.video_id")
    _token(a["qdp_input_sha256"], DIGEST, "result.qdp_input_sha256")
    _token(a["manifest_sha256"], DIGEST, "result.manifest_sha256")
    kind = _validated_kind(a["content_kind"])
    a_state = a["analysis_state"]
    if type(a_state) is not str or a_state not in ("partial", "completed"):
        _fail("invalid_analysis_state", "result.analysis_state")
    for key in ("video_a_analysis_id", "carousel_a_analysis_id"):
        if a[key] is not None:
            _positive(a[key], "result." + key)
    vid_kind = kind in ("video", "slideshow_video")
    selected = a["video_a_analysis_id"] if vid_kind else a["carousel_a_analysis_id"]
    unrelated = a["carousel_a_analysis_id"] if vid_kind else a["video_a_analysis_id"]
    if selected is None or unrelated is not None:
        _fail("analysis_kind_mismatch", "result.analysis_id")
    _token(a["method_version"], SOURCE, "result.method_version")
    streams = a["streams"]
    if type(streams) is not dict or set(streams) != REQUIRED_STREAMS[kind]:
        _fail("incomplete_stream_matrix", "result.streams")
    for key, value in streams.items():
        stream_truth_v1(value, field="streams." + key)
    all_complete = all(s["status"] == "complete" for s in streams.values())
    if (a_state == "completed") != all_complete:
        _fail("inconsistent_analysis_state", "result.analysis_state")
    ev = a["evidence"]
    if type(ev) is not list or len(ev) > 2000:
        _fail("invalid_evidence", "result.evidence")
    for i, x in enumerate(ev):
        _object(x, ("kind", "ref_sha256", "asset_sha256"), name=f"evidence[{i}]")
        if x["kind"] not in ("scene", "transcript", "screen_text", "audio", "frame", "slide"):
            _fail("invalid_evidence_kind", f"evidence[{i}].kind")
        _token(x["ref_sha256"], DIGEST, f"evidence[{i}].ref_sha256")
        _token(x["asset_sha256"], DIGEST, f"evidence[{i}].asset_sha256")
    return dict(a)


def ensure_same_lineage(pointer, manifest, result=None):
    """Check exact project/run/video/preparation hash equality, never URL-based linking."""
    q = qdp_handoff_v1(pointer)
    m = media_manifest_v1(manifest)
    for left, right in (("project_id", "project_id"), ("qdp_run_id", "qdp_run_id"),
                        ("video_id", "video_id"), ("input_sha256", "qdp_input_sha256")):
        if q[left] != m[right]:
            _fail("lineage_mismatch", "qdp." + left)
    if "preferred_media_asset_id" in q and q["preferred_media_asset_id"] is not None:
        if q["preferred_media_asset_id"] not in {x["media_asset_id"] for x in m["items"]}:
            _fail("preferred_media_mismatch", "qdp.preferred_media_asset_id")
    if result is not None:
        a = group_a_to_b_v1(result)
        for field in ("project_id", "qdp_run_id", "video_id", "qdp_input_sha256",
                      "manifest_sha256", "content_kind"):
            if a[field] != m[field]:
                _fail("lineage_mismatch", "result." + field)
    return True


def peer_state(expected_version: str, observed_version: str | None) -> str:
    """Non-negotiated versions fail closed. No guessed compatibility."""
    if type(expected_version) is not str or not expected_version:
        _fail("invalid_field", "expected_version")
    if observed_version is None:
        return "unavailable"
    if type(observed_version) is not str:
        _fail("invalid_field", "observed_version")
    return "available" if observed_version == expected_version else "incompatible"
