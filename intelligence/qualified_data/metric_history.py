"""FIX-08: bounded, verified D1 metric history for Group C handoff.

This is a PURE selector plus a read-only DB adapter. No score, rate,
synthetic zero, assumed timestamp or new observation is generated. Group C
receives two observed, same-video/source/policy/definition/version snapshots
with ascending time and nondecreasing exact view counters.

A later retraction/correction must not be silently skipped in favor of an
older apparently compatible history. All positive readiness is ephemeral:
consumer MUST check current raw facts before using these pointers.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from .contracts import sha256

MAX_OBSERVATIONS = 64
FIELDS = ("id", "observed_at", "views", "likes", "comments", "shares",
          "saves", "followers", "precision", "source_metric_definition",
          "source_metric_version", "data_quality_status")


def _timestamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or (
            value.utcoffset() is None):
        return False
    return True


def _key(row):
    return (row["observed_at"], row["id"])


def _quality(row, metadata, now) -> bool:
    if (row.get("video_id") != metadata["video_id"] or
            row.get("source_id") != metadata["source_id"] or
            row.get("source_policy_version_id") != metadata["source_policy_version_id"]):
        return False
    if row.get("data_quality_status") != "ok" or (
            row.get("observation_time_assumed") is not False):
        return False
    observed = row.get("observed_at")
    if not _timestamp(observed) or observed > now:
        return False
    expiry = row.get("expires_at")
    if expiry is not None and (not _timestamp(expiry) or expiry <= now):
        return False
    return True


def resolve_metric_history(rows: Sequence[Mapping], metadata: Mapping, *,
                           now: datetime | None = None) -> dict:
    """Choose the LATEST 2 compatible exact-view points; never infer growth.

    Rows should be PostgreSQL snapshots already scoped to a particular video
    and source/policy; the selector *rechecks* that scope. All D1 truth is
    retained in selected/history fields, no calculation or source fallback.
    """
    now = now or datetime.now(timezone.utc)
    if not _timestamp(now):
        raise ValueError("Metric evaluation requires timezone-aware now")
    if not isinstance(rows, (list, tuple)) or len(rows) > MAX_OBSERVATIONS:
        raise ValueError("Metric history must be a bounded snapshot list")
    for row in rows:
        if not isinstance(row, Mapping) or type(row.get("id")) is not int or row["id"] <= 0:
            raise ValueError("Metric history contains an invalid D1 snapshot")
    sorted_rows = sorted(rows, key=lambda r: (
        r.get("observed_at") if _timestamp(r.get("observed_at")) else
        datetime.min.replace(tzinfo=timezone.utc), r["id"]), reverse=True)
    valid = [r for r in sorted_rows if _quality(r, metadata, now)]
    # A later retraction or corrected counter invalidates a previously valid
    # last observation. Do not quietly use historical data as if current.
    newest_valid = valid[0]["observed_at"] if valid else None
    later_invalid = [
        r for r in sorted_rows if r.get("data_quality_status") in
        ("counter_correction", "retracted") and
        r.get("video_id") == metadata["video_id"] and
        r.get("source_id") == metadata["source_id"] and
        r.get("source_policy_version_id") == metadata["source_policy_version_id"] and
        _timestamp(r.get("observed_at")) and
        r["observed_at"] <= now and
        (newest_valid is None or r["observed_at"] >= newest_valid)
    ]
    # Legitimate history remains bounded and ordered oldest-first for
    # consuming services. Null counter means UNKNOWN, never zero.
    history = sorted(valid, key=_key)
    serializable = [
        {key: row.get(key) for key in FIELDS} for row in history
    ]
    counts = {
        "read_count": len(rows),
        "valid_count": len(history),
        "exact_view_count": sum(
            1 for r in history
            if r.get("precision") == "exact" and
            type(r.get("views")) is int and r["views"] >= 0),
    }
    eligible = [r for r in reversed(history)
                if r.get("precision") == "exact" and
                type(r.get("views")) is int and r["views"] >= 0]
    reason = None
    chosen = []
    definition = None
    if later_invalid:
        reason = "metric_observation_revoked_or_corrected"
    elif len(eligible) < 2:
        reason = "metric_history_insufficient"
    else:
        recent, previous = eligible[0], eligible[1]
        ra = (recent.get("source_metric_definition"),
              recent.get("source_metric_version"))
        rb = (previous.get("source_metric_definition"),
              previous.get("source_metric_version"))
        if any(type(s) is not str or not s.strip() for s in ra + rb) or ra != rb:
            reason = "metric_definition_incompatible"
        elif recent["observed_at"] <= previous["observed_at"]:
            reason = "metric_timestamp_incompatible"
        elif recent["views"] < previous["views"]:
            reason = "metric_counter_correction"
        elif metadata.get("published_at") is None:
            reason = "published_at_unknown"
        elif not _timestamp(metadata["published_at"]) or (
                previous["observed_at"] < metadata["published_at"]):
            reason = "metric_publication_time_invalid"
        else:
            chosen = [previous["id"], recent["id"]]
            definition = {"name": ra[0], "version": ra[1]}
    return {
        "schema_version": "qdp-metric-history/v1",
        "available_count": len(history),
        "observations": serializable,
        "metric_snapshot_ids": [r["id"] for r in history],
        "growth_pair_ids": chosen,
        "growth_definition": definition,
        "growth_reason_code": reason,
        "counts": counts,
        "history_sha256": sha256(serializable),
    }


def read_metric_history(conn, metadata):
    """One bounded same-source/policy D1 SELECT, never a remote scrape."""
    if not (metadata["can_store_metrics"] and
            metadata["permissions"].get("can_store_metrics") is True):
        return resolve_metric_history([], metadata)
    rows = conn.execute(
        """SELECT id,video_id,source_id,source_policy_version_id,
                  observed_at,views,likes,comments,shares,saves,followers,
                  precision,source_metric_definition,source_metric_version,
                  observation_time_assumed,data_quality_status,expires_at
           FROM metric_snapshots
           WHERE video_id=%s AND source_id=%s AND source_policy_version_id=%s
             AND (expires_at IS NULL OR expires_at>now())
           ORDER BY observed_at DESC,id DESC LIMIT 64""",
        (metadata["video_id"],metadata["source_id"],
         metadata["source_policy_version_id"])).fetchall()
    return resolve_metric_history(rows, metadata)
