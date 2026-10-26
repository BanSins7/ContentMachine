"""M02 LIVE-only SRE -> QDP event transport shape, not a publisher.

No message is emitted or authenticated by this validator. M36 must read real
DB classification decisions, human review and source rights before enqueue,
and recheck rights before each delivery. This rejects S4 offline_fixture
results even if that run was marked completed.
"""
from __future__ import annotations

from ..v1_contracts import (
    canonical_uuid, fingerprint, positive, project_id, record, reject,
    sha256_digest, utc_time,
)

LIVE_RESULT_VERSION = "sre.classifications_ready/v1"
_DECISIONS = ("match", "no_match", "uncertain")


def validate_live_result_event(payload):
    record(payload, "result_event", required=(
        "schema_version", "event_id", "project_id", "run_id",
        "publication_id", "sre_taxonomy_version_id", "taxonomy_sha256",
        "video_id", "content_version_id", "content_sha256",
        "source_policy_version_id", "execution_mode", "run_status",
        "expected_hypothesis_keys", "decisions", "results_sha256",
        "rights_verified_at", "occurred_at", "event_sha256"))
    if payload["schema_version"] != LIVE_RESULT_VERSION:
        reject("result_event.schema_version", "unsupported result schema",
               "unsupported_schema_version")
    for key in ("event_id", "run_id", "publication_id"):
        canonical_uuid(payload[key], "result_event."+key)
    project_id(payload["project_id"])
    for key in ("sre_taxonomy_version_id","video_id","content_version_id",
                "source_policy_version_id"):
        positive(payload[key], "result_event."+key)
    for key in ("taxonomy_sha256","content_sha256","results_sha256",
                "event_sha256"):
        sha256_digest(payload[key], "result_event."+key)
    if payload["execution_mode"] != "live":
        reject("result_event.execution_mode", "synthetic/legacy results cannot be exported",
               "synthetic_result_denied")
    if payload["run_status"] != "completed":
        reject("result_event.run_status", "only complete LIVE results are qualified",
               "incomplete_run")
    expected = payload["expected_hypothesis_keys"]
    if type(expected) is not list or not 1 <= len(expected) <= 10000:
        reject("result_event.expected_hypothesis_keys", "expected 1..10000 leaf UUIDs")
    for i, key in enumerate(expected):
        canonical_uuid(key, f"result_event.expected_hypothesis_keys[{i}]")
    if expected != sorted(set(expected)):
        reject("result_event.expected_hypothesis_keys", "leaves must be unique/sorted")
    decisions = payload["decisions"]
    record(decisions, "result_event.decisions", required=_DECISIONS)
    all_results = []
    for label in _DECISIONS:
        keys = decisions[label]
        if type(keys) is not list or keys != sorted(set(keys)):
            reject("result_event.decisions."+label, "must be a unique sorted UUID array")
        for i, key in enumerate(keys):
            canonical_uuid(key, f"result_event.decisions.{label}[{i}]")
        all_results.extend(keys)
    if len(all_results) != len(set(all_results)) or set(all_results) != set(expected):
        reject("result_event.decisions", "exact complete leaf coverage required",
               "incomplete_coverage")
    # A static hash prevents payload tampering, not a claim of DB correctness.
    if fingerprint(decisions) != payload["results_sha256"]:
        reject("result_event.results_sha256", "decisions digest mismatch",
               "snapshot_mismatch")
    checked = utc_time(payload["rights_verified_at"],
                       "result_event.rights_verified_at")
    occurred = utc_time(payload["occurred_at"], "result_event.occurred_at")
    if checked > occurred:
        reject("result_event.rights_verified_at", "rights check cannot be after event")
    canonical = dict(payload)
    digest = canonical.pop("event_sha256")
    if fingerprint(canonical) != digest:
        reject("result_event.event_sha256", "event digest mismatch", "snapshot_mismatch")
    return dict(payload)


__all__ = ["LIVE_RESULT_VERSION", "validate_live_result_event"]
