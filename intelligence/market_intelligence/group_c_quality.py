"""Marketing-native C0 raw metric quality gate, versioned and reproducible.

Only canonical D1 raw snapshots. Explicitly refuse corrected, assumed,
rounded and definition-mismatched measurements. A source permission filter
must be applied by the SQL caller and is NEVER inferred from a hash.
"""
from __future__ import annotations
from .contracts import digest
from .performance import timestamp

VERSION="mi-c0-exact-metric-evidence/v1"
MAX_COUNT=2**63-1
VIEW_UNIT="platform_post_views"
COUNTERS=("views","likes","comments","shares")

def _uint(value):
    return type(value) is int and 0<=value<=MAX_COUNT

def validate_history(row,history,*,strict=True):
    """Return a canonical quality decision; never fall back to an older good row."""
    if not isinstance(row,dict) or type(history) is not list:
        raise ValueError("c0_input_invalid")
    result={"schema_version":VERSION,"video_id":row.get("video_id"),
            "state":"unavailable","reason":"snapshot_missing",
            "snapshot_id":None,"prior_snapshot_id":None,
            "source_id":None,"metric_definition":None,"metric_version":None,
            "snapshot_lineage_sha256":None}
    if not history:
        return result
    newest=history[-1]
    if type(newest) is not dict:
        return {**result,"reason":"snapshot_invalid"}
    fields={
        "snapshot_id":newest.get("id",row.get("metric_snapshot_id")),
        "source_id":newest.get("source_id",row.get("metric_source_id")),
        "metric_definition":newest.get("source_metric_definition",row.get("metric_definition")),
        "metric_version":newest.get("source_metric_version",row.get("metric_version"))
    }
    base={**result,**fields}
    failure=None
    if (fields["metric_definition"]!=VIEW_UNIT
        or not isinstance(fields["metric_version"],str)
        or not fields["metric_version"]):
        failure="incompatible_view_unit_or_version"
    elif row.get("metric_source_id") is not None and fields["source_id"]!=row.get("metric_source_id"):
        failure="source_id_mismatch"
    elif row.get("metric_snapshot_id") is not None and fields["snapshot_id"]!=row["metric_snapshot_id"]:
        failure="metric_snapshot_id_mismatch"
    elif strict and newest.get("data_quality_status") not in ("ok",):
        failure="quality_not_exact_or_retracted"
    elif strict and newest.get("precision")!="exact":
        failure="precision_not_exact"
    elif strict and newest.get("observation_time_assumed") is not False:
        failure="observation_time_assumed"
    elif not _uint(newest.get("views")):
        failure="views_missing_or_invalid"
    elif any(not _uint(newest.get(k)) for k in COUNTERS[1:]):
        failure="engagement_counters_missing"
    observed=timestamp(newest.get("observed_at"))
    if failure is None and observed is None:
        failure="observation_time_missing"
    collected=timestamp(newest.get("collected_at"))
    if failure is None and strict and collected is None:
        failure="collection_time_missing"
    if failure is None and collected and observed>collected:
        failure="observation_after_collection"
    # Temporal expiry is a current rights/data-quality boundary, not a
    # numeric convenience. A metric cannot be revived from its cached hash.
    from datetime import datetime,timezone
    expired=timestamp(newest.get("expires_at"))
    if failure is None and expired is not None and expired<=datetime.now(timezone.utc):
        failure="source_observation_expired"
    if failure is None and strict and (
        type(newest.get("source_policy_version_id")) is not int
        or newest["source_policy_version_id"]<=0):
        failure="source_policy_version_missing"
    if failure:
        return {**base,"reason":failure}
    earlier=history[-2] if len(history)>1 else None
    prior_reason=None
    prior_id=None
    if earlier is not None:
        prev=timestamp(earlier.get("observed_at"))
        if (earlier.get("source_id",fields["source_id"])!=fields["source_id"]
            or earlier.get("source_metric_definition",fields["metric_definition"])!=fields["metric_definition"]
            or earlier.get("source_metric_version",fields["metric_version"])!=fields["metric_version"]
            or (strict and earlier.get("data_quality_status")!="ok")
            or (strict and earlier.get("precision")!="exact")
            or (strict and earlier.get("observation_time_assumed") is not False)):
            prior_reason="prior_incompatible"
        elif prev is None or prev>=observed:
            prior_reason="prior_observation_clock_invalid"
        elif not _uint(earlier.get("views")) or earlier["views"]>newest["views"]:
            prior_reason="prior_views_decreased_or_invalid"
        else:
            prior_id=earlier.get("id")
    payload={**base,"state":"eligible_exact_observation","reason":None,
             "prior_snapshot_id":prior_id,
             "prior_reason":prior_reason,
             "observed_at":observed.isoformat(),
             "views":newest["views"],"likes":newest["likes"],
             "comments":newest["comments"],"shares":newest["shares"],
             "quality_source_scoped_by_reader":True}
    return {**payload,"snapshot_lineage_sha256":digest(payload)}
