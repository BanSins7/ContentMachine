"""Exact Bright Data TikTok carousel post metadata.

Keep captions, hashtags, sound details and original-sound metadata in their
provider-returned JSON representation. This does not infer song names or
modify author text, and does not make any network/LLM calls.

Field-level fallback uses a profile snapshot only when a later post-detail
snapshot omitted or could not represent that specific field.
"""
from __future__ import annotations

import json

FIELDS = ("description", "hashtags", "music", "original_sound")
MAX_FIELD_BYTES = 200_000
_MISSING = object()


def _valid_json_value(name: str, value: object) -> object:
    if name == "description":
        allowed = type(value) is str
    elif name in ("hashtags", "music"):
        allowed = type(value) in (str, list, dict)
    elif name == "original_sound":
        allowed = type(value) in (str, bool, list, dict)
    else:
        allowed = False
    if not allowed:
        return _MISSING
    try:
        wire = json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":"))
        if len(wire.encode("utf-8")) > MAX_FIELD_BYTES:
            return _MISSING
        # Copy nested values: later provider mutation cannot change the
        # saved snapshot, and a JSON round-trip verifies serializability.
        return json.loads(wire)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return _MISSING


def extract_post_metadata(primary: object, fallback: object = None) -> dict:
    """Return stable keys with exact source values (or null if unavailable).

    The post-detail result is authoritative for each field it supplies.
    A profile result supplements only fields missing from post-detail.
    A blank description or empty hashtags list is a valid observed value.
    """
    result: dict[str, object] = {}
    for field in FIELDS:
        result[field] = None
        for source in (primary, fallback):
            if type(source) is not dict or field not in source:
                continue
            candidate = _valid_json_value(field, source[field])
            if candidate is _MISSING:
                continue
            result[field] = candidate
            break
    return result
