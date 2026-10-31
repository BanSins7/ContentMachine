"""Collect *all returned numeric post fields* without guessing provider schemas.

Do not collect comment bodies, captions, usernames, IDs, URLs, or API secrets.
These are source-observed fields, NOT inferred retention or engagement metrics.
Canonical view/like/share/save/comment counters stay in D1.metric_snapshots.
"""
from __future__ import annotations
import math
import re

MAX_FIELDS = 2048
MAX_NESTING = 4
NAME = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{0,63}\Z")
# Numeric identifiers and dates are NOT performance signals.
NOT_METRICS = frozenset({
    "id", "post_id", "video_id", "user_id", "account_id", "author_id",
    "music_id", "sound_id", "hashtag_id", "effect_id", "sticker_id",
    "item_id", "profile_id", "channel_id", "media_id", "owner_id",
    "create_time", "created_at", "published_at", "date_posted",
    "post_date_created", "timestamp", "db_source", "fetched_at",
    "width", "height", "resolution", "fps", "frame_rate",
})
REJECT_PART = re.compile(r"(^|_)(id|ids|timestamp|date|time_created|epoch|unix|index|hash|token|phone|email|zip|postal|lat|long|longitude|latitude)(_|$)")
# Allow measurement units/times; do not confuse watch_time with a timestamp.


def numeric_fields(record: dict) -> dict[str, int | float | list[int | float]]:
    """Fail closed on unbounded nesting and reject all nonnumeric values."""
    if type(record) is not dict:
        return {}
    result = {}

    def visit(value, prefix, depth):
        if depth > MAX_NESTING or type(value) is not dict:
            return
        for key, entry in value.items():
            if len(result) >= MAX_FIELDS:
                raise ValueError("brightdata_metric_field_limit")
            if type(key) is not str or not NAME.fullmatch(key):
                continue
            lowered = key.lower()
            if lowered in NOT_METRICS or lowered.endswith("_id") or REJECT_PART.search(lowered):
                continue
            path = prefix + key
            if len(path) > 200:
                continue
            if type(entry) is dict:
                visit(entry,path+".",depth+1)
            elif type(entry) is int and -(2**63) < entry < 2**63:
                result[path] = entry
            elif type(entry) is float and math.isfinite(entry) and abs(entry) <= 1e15:
                result[path] = entry
            elif (type(entry) is list and 0 < len(entry) <= 256
                  and all((type(x) is int and -(2**63) < x < 2**63)
                          or (type(x) is float and math.isfinite(x) and abs(x) <= 1e15)
                          for x in entry)):
                # Numerical measurement series only; never lists of comments.
                result[path] = list(entry)

    visit(record,"",0)
    return result


def metric_coverage(fields: dict) -> dict:
    """Descriptive availability only: never mark an absent field as 0."""
    tokens = " ".join(fields)
    return {
        "field_count": len(fields),
        "has_watch_time_field": any(
            s in tokens for s in ("watch_time","watchtime","average_view_duration","avg_watch","view_duration")
        ),
        "has_retention_field": any(
            s in tokens for s in ("retention","completion_rate","average_view_percentage","avg_view_percentage")
        ),
        "note": "Provider field presence, not a verified owner analytics measurement",
    }
