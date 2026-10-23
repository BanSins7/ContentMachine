"""Deterministic, offline-only Video Collector task planning and previews.

This module does not perform network calls, check credentials, touch the
database, generate LLM queries or authorize any search. It accepts structurally
valid fixtures until a *server-verified* SQG Query Validation handoff exists.
"""
from __future__ import annotations

from collections import defaultdict
import unicodedata

from .contracts import (
    CollectorError, CollectorLimits, CollectorPlan, SourceTask,
    ValidatedQuerySet, hash_object, require_dict, require_int,
)
from .query_intake import parse_validated_query_set
from .registry import REGISTRY_VERSION, match_source

LIMIT_FIELDS = {
    "max_tasks", "max_pages_per_task", "max_results_per_page",
    "max_total_requests", "max_total_candidates",
}
PLANNER_VERSION = "collector-offline-planner/v1"


def validate_limits(raw: dict | CollectorLimits | None = None) -> CollectorLimits:
    if raw is None:
        return CollectorLimits()
    if type(raw) is CollectorLimits:
        return validate_limits(raw.to_dict())
    require_dict(raw, LIMIT_FIELDS, "limits")
    return CollectorLimits(
        max_tasks=require_int(raw["max_tasks"], "max_tasks", 1, 50),
        max_pages_per_task=require_int(raw["max_pages_per_task"], "max_pages_per_task", 1, 5),
        max_results_per_page=require_int(raw["max_results_per_page"], "max_results_per_page", 1, 50),
        max_total_requests=require_int(raw["max_total_requests"], "max_total_requests", 1, 250),
        max_total_candidates=require_int(raw["max_total_candidates"], "max_total_candidates", 1, 2500),
    )


def _equivalence_key(text: str) -> str:
    """Only dedupe keys are NFKC/casefolded. Preserve a canonical raw search."""
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


def build_preview(
    query_set: ValidatedQuerySet | dict, *,
    source_id: str = "dtk_tiktok",
    limits: CollectorLimits | dict | None = None,
    expected_project_id: str | None = None,
    expected_publication_id: str | None = None,
) -> dict:
    """Return a bounded, reproducible *non-executable* plan.

    The `validation_state=accepted` claim is accepted only as input data.
    It is NOT verified against the database in this phase. This method
    NEVER provides financial consent or authorization to the source.
    """
    if type(query_set) is dict:
        parsed = parse_validated_query_set(
            query_set,
            expected_project_id=expected_project_id,
            expected_publication_id=expected_publication_id,
        )
    elif type(query_set) is ValidatedQuerySet:
        # Reparse even the dataclass: direct constructors may be used elsewhere.
        parsed = parse_validated_query_set(
            query_set.to_dict(),
            expected_project_id=expected_project_id,
            expected_publication_id=expected_publication_id,
        )
    else:
        raise CollectorError("invalid_query_set", "Unsupported query set object")
    policy = validate_limits(limits)
    source, _ = match_source(source_id, "keyword")
    grouped = defaultdict(list)
    rejected = []

    for query in parsed.queries:
        operation = source.operation_for(query.mode)
        if operation == "search_posts" and len(query.text) > 120:
            rejected.append({
                "query_id": query.query_id,
                "mode": query.mode,
                "reason": "provider_query_limit_exceeded",
                "source_id": source_id,
            })
            continue
        if operation is None:
            rejected.append({
                "query_id": query.query_id,
                "mode": query.mode,
                "reason": "unsupported_capability",
                "source_id": source_id,
            })
            continue
        key = (query.mode, _equivalence_key(query.text), query.language, operation)
        grouped[key].append(query)

    tasks = []
    for (mode, normalized_text, language, operation), members in sorted(grouped.items()):
        # Choose the raw spelling independently of user-supplied array order.
        representative = min(
            members, key=lambda q: (q.text.casefold(), q.text, q.query_id)
        )
        query_ids = tuple(sorted(q.query_id for q in members))
        hypothesis_ids = tuple(sorted({q.hypothesis_id for q in members}))
        direction_ids = tuple(sorted({q.direction_id for q in members}))
        fingerprint = hash_object({
            "version": PLANNER_VERSION,
            "query_set_sha256": parsed.fingerprint,
            "source_id": source_id,
            "mode": mode,
            "normalized_text": normalized_text,
            "language": language,
            "operation": operation,
            "query_ids": query_ids,
            "limits": policy.to_dict(),
        })
        tasks.append(SourceTask(
            task_fingerprint=fingerprint,
            project_id=parsed.project_id,
            taxonomy_publication_id=parsed.taxonomy_publication_id,
            taxonomy_version=parsed.taxonomy_version,
            generation_id=parsed.generation_id,
            source_id=source_id,
            platform=source.platform,
            operation=operation,
            mode=mode,
            query_text=representative.text,
            language=language,
            query_ids=query_ids,
            hypothesis_ids=hypothesis_ids,
            direction_ids=direction_ids,
            max_pages=policy.max_pages_per_task,
            max_results_per_page=policy.max_results_per_page,
        ))

    blocks = ["query_set_not_server_verified", "source_policy_not_verified"]
    if not source.operationally_verified:
        blocks.append("provider_connection_not_verified")
    if not tasks:
        blocks.append("no_supported_tasks")
    if len(tasks) > policy.max_tasks:
        blocks.append("max_tasks_exceeded")
    external_requests = len(tasks) * policy.max_pages_per_task
    if external_requests > policy.max_total_requests:
        blocks.append("max_total_requests_exceeded")
    if rejected:
        blocks.append("partial_query_coverage")
    if policy.max_pages_per_task > 1:
        blocks.append("pagination_capability_not_verified")

    all_hypotheses = {q.hypothesis_id for q in parsed.queries}
    covered = set().union(*(set(t.hypothesis_ids) for t in tasks)) if tasks else set()
    footprint = {
        "version": PLANNER_VERSION,
        "registry_version": REGISTRY_VERSION,
        "query_set_sha256": parsed.fingerprint,
        "source_id": source_id,
        "source_integration_state": source.integration_state,
        "limits": policy.to_dict(),
        "tasks": [task.to_dict() for task in tasks],
        "rejected": rejected,
        "blocks": blocks,
    }
    digest = hash_object(footprint)
    plan = CollectorPlan(
        plan_hash=digest,
        source_id=source_id,
        query_set=parsed,
        limits=policy,
        source=source,
        tasks=tuple(tasks),
        rejected=tuple(rejected),
        block_reasons=tuple(blocks),
        requested_result_upper_bound=min(
            policy.max_total_candidates,
            external_requests * policy.max_results_per_page,
        ),
        max_external_requests_upper_bound=external_requests,
        covered_hypothesis_ids=tuple(sorted(covered)),
        uncovered_hypothesis_ids=tuple(sorted(all_hypotheses - covered)),
    )
    return plan.to_dict()
