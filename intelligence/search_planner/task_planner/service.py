"""SP-10 bounded, deterministic, held-only task plan (NO external execution).

Separate server-trusted input assembly from a pure planner:
 - Never accept browser claims of selected QV candidate acceptance.
 - Catalog may be declared/available, yet not dispatch-authorized.
 - Cost estimates are conservative and only for known unit prices.
 - Every original hypothesis/candidate/fact alias survives dedup and quotas.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable
from unicodedata import normalize as u_normalize

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.taxonomy_builder.pipeline_repository import _pid
from intelligence.search_planner.contracts import publication_uuid
from intelligence.search_planner.capabilities import CapabilityMatcher
from intelligence.search_planner.capabilities.service import (
    match_capabilities, validate_capability_snapshot,
)
from .repository import load_verified_planning_input, validate_planning_input

PLAN_SCHEMA = "search-planner-held-task-plan/v1"
LIMIT_FIELDS = {
    "source_ids", "source_routing", "market", "max_tasks", "max_external_requests",
    "max_results_per_page", "max_pages_per_task", "max_total_candidates",
    "max_cost_usd",
}
USD_QUANT = Decimal("0.00000001")
DEFAULT_LIMITS = {
    "source_ids": [], "source_routing": "single_best",
    "market": None, "max_tasks": 20,
    "max_external_requests": 40, "max_results_per_page": 20,
    "max_pages_per_task": 1, "max_total_candidates": 480,
    "max_cost_usd": "0.00000000",
}


def _usd(value, *, field):
    if type(value) is not str or not 1 <= len(value) <= 24:
        raise PipelineError("Invalid " + field + " cap", 422)
    try:
        n = Decimal(value)
    except (ValueError, InvalidOperation) as exc:
        raise PipelineError("Invalid " + field + " cap", 422) from exc
    if not n.is_finite() or n < 0 or n.as_tuple().exponent < -8 or n > 10000:
        raise PipelineError("Unbounded " + field + " cap", 422)
    return n


def validate_plan_limits(raw=None):
    """Read-only planning policy; does NOT authorize spending of max_cost_usd."""
    obj = DEFAULT_LIMITS if raw is None else raw
    if type(obj) is not dict or set(obj) != LIMIT_FIELDS:
        raise PipelineError("Incomplete task planner limits", 422)
    verified = deepcopy(obj)
    sources = verified["source_ids"]
    if (type(sources) is not list or len(sources) > 64 or
            any(type(x) is not str or len(x) > 63 or not x for x in sources) or
            len(set(sources)) != len(sources)):
        raise PipelineError("Invalid preferred source IDs", 422)
    verified["source_ids"] = sorted(sources)
    if verified["source_routing"] not in ("single_best", "all_compatible") or type(verified["source_routing"]) is not str:
        raise PipelineError("Unsupported explicit source routing mode", 422)
    market = verified["market"]
    if (market is not None and
            (type(market) is not str or len(market) != 2 or not market.isupper()
             or not market.isalpha() or not market.isascii())):
        raise PipelineError("Invalid explicit planning market", 422)
    for key, upper in (
        ("max_tasks", 50), ("max_external_requests", 250),
        ("max_results_per_page", 50), ("max_pages_per_task", 5),
        ("max_total_candidates", 2500),
    ):
        v = verified[key]
        if type(v) is not int or not 1 <= v <= upper:
            raise PipelineError("Invalid task planner limit " + key, 422)
    if verified["max_external_requests"] < verified["max_pages_per_task"]:
        raise PipelineError("External request limit below one task", 422)
    verified["max_cost_usd"] = format(_usd(verified["max_cost_usd"],
                                         field="max_cost_usd").quantize(USD_QUANT), "f")
    return verified


def _normalized(text):
    return " ".join(u_normalize("NFKC", text).split()).casefold()


def _safe_failure(cid, reasons, scope=None):
    return {
        "candidate_id": cid,
        "block_reasons": sorted(set(reasons)),
        "scope": scope,
    }


def _planner_snapshot(source, *, project_id, validation_run_id):
    validated = validate_planning_input(source, project_id=project_id,
                                        validation_run_id=validation_run_id)
    # A deep copy is deliberate: no source data returned by an injected
    # dependency is mutated by cost/coverage/dedup bookkeeping.
    return deepcopy(validated)


def build_held_task_plan(source, capability_snapshot, *, limits=None,
                         now: datetime | None = None):
    """Pure plan from SERVER-verified evidence only; never returns an ACK.

    Internal build function is not an HTTP endpoint. SearchPlannerService
    invokes it only after loading production QV from PostgreSQL and SP-09
    capabilities from a trusted server-side port.
    """
    if type(source) is not dict:
        raise PipelineError("Server QV evidence is required", 409)
    pid = _pid(source.get("project_id"))
    vid = publication_uuid(source.get("validation_run_id"))
    trusted = _planner_snapshot(source, project_id=pid, validation_run_id=vid)
    policy = validate_plan_limits(limits)
    clock = now if now is not None else datetime.now(timezone.utc)
    catalog = validate_capability_snapshot(capability_snapshot, project_id=pid,
                                          now=clock)
    catalog_sha = sha256(catalog)

    selected = {c["candidate_id"]: c for c in trusted["validated_bundle"]["queries"]}
    # Deterministic per-hypothesis round-robin avoids exhausting all source
    # quota on one hypothesis before another gets a single fair opportunity.
    buckets = {}
    for group in trusted["shared_links"]["links"]:
        q = selected[group["selected_candidate_id"]]
        buckets.setdefault(q["hypothesis_id"], []).append(group)
    for hid in buckets:
        buckets[hid].sort(key=lambda g:(
            selected[g["selected_candidate_id"]]["rank_in_hypothesis"],
            g["selected_candidate_id"],
        ))
    physical = []
    depth = 0
    while any(depth < len(group) for group in buckets.values()):
        for hid in sorted(buckets):
            if depth < len(buckets[hid]):
                physical.append(buckets[hid][depth])
        depth += 1
    all_source_ids = {s["source_id"] for s in catalog["sources"]}
    preferred = (policy["source_ids"] if policy["source_ids"]
                 else sorted(all_source_ids))
    cap_by_source = {s["source_id"]:s for s in catalog["sources"]}
    missing_sources = sorted(set(preferred) - all_source_ids)
    accounted_quota = {sid: 0 for sid in all_source_ids}
    spent = Decimal("0")
    requests = 0
    tasks = []
    rejected = []
    partially_blocked = []
    grouped = {}
    planned_ids = []
    no_work = len(physical) == 0

    for link in physical:
        cid = link["selected_candidate_id"]
        q = selected[cid]
        probe = {
            "query_mode": q["query_mode"],
            "text": q["text"], "language": q["language"],
            "market": policy["market"],
        }
        comparison = match_capabilities(catalog, probe, project_id=pid,
                                        now=clock)
        matches = {m["source_id"]:m for m in comparison["matches"]}
        candidates = []
        blocks = set()
        if missing_sources:
            blocks.add("source_not_in_catalog")
        for sid in preferred:
            if sid not in matches:
                continue
            m = matches[sid]
            if not m["planning_compatible"]:
                blocks.update(m["block_reasons"])
                continue
            descriptor = cap_by_source[sid]
            capability = next(c for c in descriptor["capabilities"]
                              if c["mode"] == q["query_mode"])
            if policy["max_pages_per_task"] > capability["max_pages"]:
                blocks.add("pages_exceed_verified_capability")
                continue
            if (policy["max_pages_per_task"] > 1 and
                    not capability["pagination_verified"]):
                blocks.add("pagination_not_verified")
                continue
            candidates.append((sid, m["declared_operation"], descriptor))
        if not candidates:
            rejected.append(_safe_failure(cid, blocks or {"no_compatible_source"},
                                          "capability"))
            continue

        assigned = False
        for sid, operation, descriptor in candidates:
            key = (sid, operation, q["query_mode"], q["language"],
                   policy["market"], _normalized(q["text"]))
            task = grouped.get(key)
            if task is None:
                if len(tasks) >= policy["max_tasks"]:
                    blocks.add("task_limit_reached")
                    continue
                if requests + policy["max_pages_per_task"] > policy["max_external_requests"]:
                    blocks.add("request_limit_reached")
                    continue
                if ((requests + policy["max_pages_per_task"]) *
                        policy["max_results_per_page"] > policy["max_total_candidates"]):
                    blocks.add("candidate_result_cap_exceeded")
                    continue
                if (accounted_quota[sid] + policy["max_pages_per_task"] >
                        descriptor["quota_available_requests"]):
                    blocks.add("source_quota_exceeded")
                    continue
                upper = (Decimal(descriptor["cost_usd_per_request"]) *
                         policy["max_pages_per_task"])
                if spent + upper > Decimal(policy["max_cost_usd"]):
                    blocks.add("planning_cost_cap_exceeded")
                    continue
                task = {
                    "source_id": sid, "platform": descriptor["platform"],
                    "operation": operation, "mode": q["query_mode"],
                    "query_text": q["text"], "language": q["language"],
                    "market": policy["market"],
                    "selected_candidate_ids": [],
                    "linked_sources": [],
                    "hypothesis_ids": [], "source_fact_ids": [],
                    "max_pages": policy["max_pages_per_task"],
                    "max_results_per_page": policy["max_results_per_page"],
                    "estimated_upper_cost_usd": format(upper.quantize(USD_QUANT), "f"),
                    "external_dispatch_authorized": False,
                }
                grouped[key] = task
                tasks.append(task)
                requests += policy["max_pages_per_task"]
                accounted_quota[sid] += policy["max_pages_per_task"]
                spent += upper
            task["selected_candidate_ids"].append(cid)
            task["linked_sources"].extend(deepcopy(link["linked_sources"]))
            task["hypothesis_ids"] = sorted(set(task["hypothesis_ids"]) |
                                            {a["hypothesis_id"] for a in link["linked_sources"]})
            task["source_fact_ids"] = sorted(set(task["source_fact_ids"]) |
                                             {f for a in link["linked_sources"]
                                              for f in a["source_fact_ids"]})
            assigned = True
            if policy["source_routing"] == "single_best":
                break
        if assigned:
            planned_ids.append(cid)
            if blocks:
                partially_blocked.append(_safe_failure(cid, blocks, "partial_source_coverage"))
        else:
            rejected.append(_safe_failure(cid, blocks or {"resource_limits_exceeded"},
                                          "budget_or_quota"))
    # Full lineage and stable IDs are calculated AFTER physical dedup.
    for task in tasks:
        task["selected_candidate_ids"].sort()
        unique = {ref["candidate_id"]: ref for ref in task["linked_sources"]}
        if len(unique) != len(task["linked_sources"]):
            raise PipelineError("Duplicated provenance across physical source task", 409)
        task["linked_sources"] = [unique[k] for k in sorted(unique)]
        task["task_fingerprint"] = sha256({
            "version": PLAN_SCHEMA,
            "project_id": pid,
            "generation_id": trusted["generation_id"],
            "validation_run_id": vid,
            "taxonomy_sha256": trusted["taxonomy_sha256"],
            "catalog_sha256": catalog_sha,
            "limits": policy,
            "source": task,
        })
    tasks.sort(key=lambda x:(x["source_id"], x["operation"], x["mode"],
                             x["language"], _normalized(x["query_text"]),
                             x["task_fingerprint"]))
    planned = set(planned_ids)
    if planned != {c for task in tasks for c in task["selected_candidate_ids"]}:
        raise PipelineError("Selected QV candidate lost during task deduplication", 409)
    all_hypotheses = {c["hypothesis_id"] for c in trusted["validated_bundle"]["coverage"]}
    covered = {h for t in tasks for h in t["hypothesis_ids"]}
    summary = {
        "source_selected_count": len(physical),
        "planned_selected_count": len(planned),
        "blocked_selected_count": len(rejected),
        "partially_blocked_selected_count": len(partially_blocked),
        "task_count": len(tasks),
        "max_external_requests_upper_bound": requests,
        "requested_result_upper_bound": requests * policy["max_results_per_page"],
        "estimated_upper_cost_usd": format(spent.quantize(USD_QUANT), "f"),
        "estimated_cost_is_verified": bool(tasks) and all(
            cap_by_source[t["source_id"]]["pricing_status"] != "unknown" for t in tasks
        ),
        "source_hypothesis_count": len(all_hypotheses),
        "covered_hypothesis_ids": sorted(covered),
        "uncovered_hypothesis_ids": sorted(all_hypotheses - covered),
    }
    if len(physical) != len(planned) + len(rejected):
        raise PipelineError("Some QV selections have no planning accounting", 409)
    body = {
        "schema_version": PLAN_SCHEMA,
        "status": "held_preview_no_dispatch",
        "project_id": pid, "validation_run_id": vid,
        "generation_id": trusted["generation_id"],
        "taxonomy_publication_id": trusted["taxonomy_publication_id"],
        "taxonomy_version": trusted["taxonomy_version"],
        "taxonomy_sha256": trusted["taxonomy_sha256"],
        "generation_input_sha256": trusted["generation_input_sha256"],
        "qv_outbox_id": trusted["outbox_id"],
        "qv_source_bundle_sha256": trusted["validated_bundle"]["source_bundle_sha256"],
        "qv_config_sha256": trusted["validated_bundle"]["config_sha256"],
        "embedding_model_key": trusted["validated_bundle"]["embedding_model_key"],
        "links_sha256": trusted["shared_links"]["links_sha256"],
        "capability_registry_version": catalog["registry_version"],
        "capability_catalog_sha256": catalog_sha,
        "capability_expires_at": catalog["expires_at"],
        "limits": policy, "tasks": tasks, "blocked_queries": rejected,
        "partially_blocked_queries": partially_blocked,
        "summary": summary,
        "no_work": no_work,
        "execution_enabled": False,
        "external_dispatch_authorized": False,
        "provider_requests_in_this_plan": 0,
        "dollars_spent_in_this_plan": "0.00000000",
    }
    body["plan_hash"] = sha256(body)
    return body


class TaskPlanner:
    """Standalone preview-only orchestrator over trusted internal read ports."""

    def __init__(self, *, planning_reader: Callable = load_verified_planning_input,
                 capability_matcher: CapabilityMatcher | None = None,
                 clock: Callable[[], datetime] | None = None,
                 owner_action_guard=None,
                 commit_writer: Callable | None = None):
        if not callable(planning_reader):
            raise PipelineError("Invalid QV read-only planning port", 422)
        if capability_matcher is not None and not isinstance(capability_matcher, CapabilityMatcher):
            raise PipelineError("Invalid task planner source capability port", 422)
        if clock is not None and not callable(clock):
            raise PipelineError("Invalid task planner clock", 422)
        self._reader = planning_reader
        self._clock = clock if clock is not None else (lambda: datetime.now(timezone.utc))
        self._capabilities = (capability_matcher if capability_matcher is not None
                              else CapabilityMatcher(clock=self._clock))
        if owner_action_guard is not None and not callable(owner_action_guard):
            raise PipelineError("Invalid owner action guard", 422)
        if commit_writer is not None and not callable(commit_writer):
            raise PipelineError("Invalid trusted held-plan writer", 422)
        self._owner_guard = owner_action_guard
        self._commit_writer = commit_writer

    def preview(self, project_id: str, validation_run_id: str, *,
                limits=None) -> dict:
        """Only accepts project/QV IDs and bounded policy, never raw accepted JSON."""
        pid, vid = _pid(project_id), publication_uuid(validation_run_id)
        validated_limits = validate_plan_limits(limits)
        trusted = self._reader(pid, vid)
        clock = self._clock()
        source_catalog = self._capabilities.snapshot(pid)
        return build_held_task_plan(trusted, source_catalog,
                                    limits=validated_limits, now=clock)

    def commit_held(self, project_id: str, validation_run_id: str,
                    idempotency_key: str, *, limits=None) -> dict:
        """Owner-approved immutable held snapshot; NO Collector dispatch.

        Always regenerate the entire preview from trusted DB/catalog state.
        The owner predicate must be assembled from authenticated server
        session, NOT from request JSON. SP-13 wires the actual session ACL.
        """
        from .commit import persist_approved_held_plan, get_existing_held_approval
        pid, vid = _pid(project_id), publication_uuid(validation_run_id)
        validated_limits = validate_plan_limits(limits)
        # Only the real DB writer has a durable-key lookup. Injected test
        # writers are deliberately explicit, not trusted persistent storage.
        if self._commit_writer is None:
            previous = get_existing_held_approval(
                pid, vid, idempotency_key,
                limits=validated_limits,
                owner_action_guard=self._owner_guard,
            )
            if previous is not None:
                return previous
        plan = self.preview(pid, vid, limits=validated_limits)
        writer = self._commit_writer if self._commit_writer is not None else persist_approved_held_plan
        return writer(pid, vid, plan, idempotency_key,
                      owner_action_guard=self._owner_guard, now=self._clock())
