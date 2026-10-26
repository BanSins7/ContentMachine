"""Collector → Evil0ctal DTK v5 capability bridge (Stage 7, no dispatch).

This module prepares a bounded *description* of a future DTK keyword task.
No method here submits searches or releases queued Collector jobs.
A manual, explicit diagnostics call may issue one local GET /auth/me.
"""
from __future__ import annotations

from intelligence.connectors import dtk
from intelligence.collector.contracts import (
    CollectorError, PROJECT_RE, HASH_RE, require_dict, require_id,
    require_int, require_uuid,
)

_EXPECTED_TASK_FIELDS = {
    "schema_version", "task_fingerprint", "project_id",
    "taxonomy_publication_id", "taxonomy_version", "generation_id",
    "source_id", "platform", "operation", "method", "params", "language",
    "query_ids", "hypothesis_ids", "direction_ids", "max_pages",
    "max_results_per_page",
}


def prepare_keyword_task(task: dict) -> dict:
    """Fail closed on unsupported/malformed tasks; structural check only.

    A prepared payload is NEVER an authorization. Future worker must verify
    immutable committed plan, source policy, task fingerprint, rights and user
    execution consent before invoking dtk.submit even once.
    """
    require_dict(task, _EXPECTED_TASK_FIELDS, "source_task")
    if (task["schema_version"] != "source_task/v1"
        or task["source_id"] != dtk.TOOL_ID or task["platform"] != "tiktok"
        or task["operation"] != "search_posts" or task["method"] != "keyword"):
        raise CollectorError("unsupported_operation",
                             "Stage 7 DTK adapter supports only TikTok keyword search")
    require_id(task["project_id"], "project_id", PROJECT_RE)
    require_uuid(task["taxonomy_publication_id"], "taxonomy_publication_id")
    require_int(task["taxonomy_version"], "taxonomy_version", 1, 2**31-1)
    require_uuid(task["generation_id"], "generation_id")
    require_id(task["task_fingerprint"], "task_fingerprint", HASH_RE)
    if type(task["language"]) is not str or not 2 <= len(task["language"]) <= 6:
        raise CollectorError("invalid_task", "Invalid language")
    for prop in ("query_ids", "hypothesis_ids", "direction_ids"):
        value = task[prop]
        if type(value) is not list or not value or len(value) > 50 or any(
            type(k) is not str or not 1 <= len(k) <= 128 for k in value
        ) or len(set(value)) != len(value):
            raise CollectorError("invalid_task", "Invalid task provenance " + prop)
    require_int(task["max_pages"], "max_pages", 1, 1)
    require_int(task["max_results_per_page"], "max_results_per_page", 1, 50)
    if type(task["params"]) is not dict or set(task["params"]) != {"keyword"}:
        raise CollectorError("invalid_task", "Only explicit keyword params supported")
    try:
        path, params = dtk.validate_action("search_posts", task["params"])
    except dtk.DTKError:
        raise CollectorError("invalid_task",
                             "Query cannot be sent to DTK; check 120-character limit") from None
    if path != "/api/v1/tiktok/search/posts":
        raise CollectorError("invalid_task", "Unexpected DTK operation")
    return {
        "adapter": "dtk_tiktok/v1",
        "operation": "search_posts",
        "params": params,
        "source_id": "dtk_tiktok",
        "task_fingerprint": task["task_fingerprint"],
        "query_ids": list(task["query_ids"]),
        "hypothesis_ids": list(task["hypothesis_ids"]),
        "taxonomy_publication_id": task["taxonomy_publication_id"],
        "generation_id": task["generation_id"],
        "external_dispatch_authorized": False,
        "external_calls": 0,
    }


def diagnose_local_connection() -> dict:
    """EXPLICIT diagnostics only. One auth/me GET, never TikTok search.

    Does not imply source license, DTK archive policy, pricing or consent.
    """
    state = dtk.status()
    ready = state.get("available") is True and state.get("read_scope") is True
    return {
        "source_id": "dtk_tiktok",
        "provider": "evil0ctal_dtk_v5",
        "connection_check_only": True,
        "configured": state.get("configured") is True,
        "available": state.get("available") is True,
        "read_scope": state.get("read_scope") is True,
        "keyword_search_transport_ready": ready,
        "declared_operation_count": len(dtk.ACTIONS),
        "declared_operations": list(dtk.ACTIONS),
        "source_policy_verified": False,
        "upstream_archive_verified": False,
        "actual_tiktok_search_verified": False,
        "automatic_collector_execution_enabled": False,
        "error_code": state.get("error_code"),
        "warning": state.get("warning"),
    }
