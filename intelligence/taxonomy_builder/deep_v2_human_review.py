"""Deep V2 human decisions: pure complete-review reducer, no model/SQL.

Every Pass1 candidate receives one explicit action. Edits retain IDs; merges
resolve to approved targets and children are reparented only along that
declared merge. Rejection never orphans descendants silently.
"""
from __future__ import annotations

from copy import deepcopy

from .deep_contracts_v2 import validate_deep_v2_tree
from .pipeline_contracts import PipelineError

MAX_DECISIONS = 90
MAX_ADD = 20


def _reject(code):
    raise PipelineError(code, 422)


def apply_human_review1(proposed_nodes: list, decisions: list,
                        frozen_research_input: dict) -> list:
    validate_deep_v2_tree(proposed_nodes, frozen_research_input)
    if (not isinstance(decisions, list)
            or len(decisions) > MAX_DECISIONS):
        _reject("rtb_v2_review_decisions_invalid")
    source = {n["id"]: deepcopy(n) for n in proposed_nodes}
    processed = set()
    kept = {}
    added = []
    merges = {}
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("action") not in (
            "approve", "reject", "edit", "merge", "add"
        ):
            _reject("rtb_v2_review_action_invalid")
        action = decision["action"]
        if action == "add":
            if set(decision) != {"action", "node"}:
                _reject("rtb_v2_review_add_shape_invalid")
            node = decision.get("node")
            if not isinstance(node, dict) or node.get("id") in source:
                _reject("rtb_v2_review_added_node_id_invalid")
            added.append(deepcopy(node))
            if len(added) > MAX_ADD:
                _reject("rtb_v2_review_too_many_manual_additions")
            continue
        ident = decision.get("id")
        if not isinstance(ident, str) or ident not in source or ident in processed:
            _reject("rtb_v2_review_each_candidate_once")
        processed.add(ident)
        keys = {
            "approve": {"action", "id"},
            "reject": {"action", "id"},
            "edit": {"action", "id", "node"},
            "merge": {"action", "id", "target_id"},
        }[action]
        if set(decision) != keys:
            _reject("rtb_v2_review_decision_shape_invalid")
        if action == "approve":
            kept[ident] = deepcopy(source[ident])
        elif action == "edit":
            node = decision["node"]
            if not isinstance(node, dict) or node.get("id") != ident:
                _reject("rtb_v2_review_edit_stable_id_required")
            kept[ident] = deepcopy(node)
        elif action == "merge":
            target = decision["target_id"]
            if not isinstance(target, str) or target == ident or target not in source:
                _reject("rtb_v2_review_merge_target_invalid")
            merges[ident] = target

    if processed != set(source):
        _reject("rtb_v2_review_incomplete")
    def ultimate(ident):
        seen = set()
        while ident in merges:
            if ident in seen:
                _reject("rtb_v2_review_merge_cycle")
            seen.add(ident)
            ident = merges[ident]
        if ident not in kept:
            _reject("rtb_v2_review_merge_target_not_approved")
        return ident
    resolved = {ident:ultimate(ident) for ident in merges}
    nodes = list(kept.values()) + added
    seen_ids = set()
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            _reject("rtb_v2_review_added_node_invalid")
        if node["id"] in seen_ids:
            _reject("rtb_v2_review_duplicate_id")
        seen_ids.add(node["id"])
        if node.get("parent_id") in resolved:
            node["parent_id"] = resolved[node["parent_id"]]
        elif node.get("parent_id") in source and node["parent_id"] not in kept:
            _reject("rtb_v2_review_rejected_parent_requires_explicit_reparent")
    return validate_deep_v2_tree(nodes, frozen_research_input)
