"""VC-MOD-05: deterministic technical planning for ALL trusted QV shards.

This is deliberately an OFFLINE preview composition, not an execution grant.
Canonical Search Planner generates queries; VC chooses provider operations and
technical task grouping only. No HTTP, DB mutation, DTK POST or implicit caps.
"""
from __future__ import annotations

from .contracts import CollectorError, CollectorLimits, hash_object, require_int
from .planner import build_preview, validate_limits
from .query_publisher import (
    DEFAULT_SHARD_SIZE, MAX_SHARD_SIZE, prepare_qv_publication,
)
from .qv_reader import TrustedQVSource
from .registry import REGISTRY_VERSION, match_source

MANIFEST_PLAN_SCHEMA = "collector.manifest_technical_plan/v1"
MANIFEST_PLAN_VERSION = "collector.manifest_technical_planner/v1"


def build_manifest_technical_preview(
    source: TrustedQVSource, *,
    source_id: str = "dtk_tiktok",
    shard_size: int = DEFAULT_SHARD_SIZE,
    limits: CollectorLimits | dict | None = None,
    total_request_cap: int = 40,
) -> dict:
    """Explain task coverage, unsupported modes, and global quota before commit.

    Only a backend-trusted QV source can be transformed into a multi-shard
    manifest. This API never attests source policy readiness or dispatch rights.
    """
    require_int(shard_size, "shard_size", 1, MAX_SHARD_SIZE)
    require_int(total_request_cap, "total_request_cap", 1, 250)
    policy = validate_limits(limits)
    provider, keyword_op = match_source(source_id, "keyword")
    publication = prepare_qv_publication(source, shard_size=shard_size)
    snapshots = []
    all_task_ids = set()
    all_query_ids = set()
    rejected_ids = set()
    covered_hypotheses = set()
    aggregated_requests = 0
    aggregated_upper = 0
    block_reasons = set()
    for shard in publication.shards:
        data = build_preview(
            shard.query_set, source_id=source_id, limits=policy,
            expected_project_id=publication.project_id,
            expected_publication_id=source.publication_id,
        )
        task_ids = [t["task_fingerprint"] for t in data["tasks"]]
        selected_ids = set(shard.candidate_ids)
        accepted = set(qid for task in data["tasks"] for qid in task["query_ids"])
        rejected = set(item["query_id"] for item in data["rejected_queries"])
        if (accepted | rejected != selected_ids or accepted & rejected
            or set(task_ids) & all_task_ids):
            raise CollectorError("planner_integrity", "Task coverage/identity mismatch", 409)
        all_task_ids.update(task_ids)
        all_query_ids.update(accepted)
        rejected_ids.update(rejected)
        covered_hypotheses.update(data["summary"]["covered_hypothesis_ids"])
        aggregated_requests += data["summary"]["max_external_requests_upper_bound"]
        aggregated_upper += data["summary"]["requested_result_upper_bound"]
        block_reasons.update(data["blocks"])
        snapshots.append({
            "shard_ordinal": shard.ordinal,
            "shard_sha256": shard.shard_sha256,
            "query_set_sha256": shard.query_set_sha256,
            "selected_count": len(shard.candidate_ids),
            "task_count": len(data["tasks"]),
            "rejected_count": len(data["rejected_queries"]),
            "plan_hash": data["plan_hash"],
            "tasks": data["tasks"],
            "rejected": data["rejected_queries"],
            "blocks": data["blocks"],
        })
    if len(all_query_ids | rejected_ids) != publication.selected_count:
        raise CollectorError("planner_integrity", "Missing approved QV UUIDs", 409)
    if aggregated_requests > total_request_cap:
        block_reasons.add("manifest_request_cap_exceeded")
    if not publication.selected_count:
        block_reasons.add("no_queries")
    if not provider.operationally_verified:
        block_reasons.add("provider_connection_not_verified")
    if not provider.source_policy_verified:
        block_reasons.add("source_policy_not_verified")
    result = {
        "schema_version": MANIFEST_PLAN_SCHEMA,
        "planner_version": MANIFEST_PLAN_VERSION,
        "registry_version": REGISTRY_VERSION,
        "source_id": provider.source_id,
        "platform": provider.platform,
        "declared_keyword_operation": keyword_op,
        "project_id": publication.project_id,
        "validation_run_id": publication.validation_run_id,
        "manifest_sha256": publication.manifest_sha256,
        "status": publication.status,
        "selected_count": publication.selected_count,
        "source_candidate_count": publication.source_candidate_count,
        "coverage_count": publication.coverage_count,
        "shard_count": publication.shard_count,
        "query_covered_count": len(all_query_ids),
        "query_rejected_count": len(rejected_ids),
        "task_count": len(all_task_ids),
        "max_external_requests_upper_bound": aggregated_requests,
        "requested_result_upper_bound": aggregated_upper,
        "manifest_request_cap": total_request_cap,
        "covered_hypothesis_ids": sorted(covered_hypotheses),
        "uncovered_hypothesis_ids": sorted({
            q["hypothesis_id"] for q in source.selected
        } - covered_hypotheses),
        "blocks": sorted(block_reasons),
        "shards": snapshots,
        "execution_authorized": False,
        "external_calls": 0,
    }
    result["plan_hash"] = hash_object(result)
    return result
