"""Protect immutable Human Review1 during Opus Pass2/expansions.

Opus may ADD grounded nodes only. Every approved H1 node, its parent, all
14 fields and evidence references must remain identical. Previously rejected,
edited-away or merged Pass1 IDs may not silently reappear.
"""
from __future__ import annotations

from copy import deepcopy

from .deep_contracts_v2 import validate_deep_v2_response, validate_deep_v2_tree
from .pipeline_contracts import PipelineError


def validate_protected_followup(result: dict, research_input: dict,
                                h1_nodes: list, first_pass_nodes: list,
                                *, role: str = "deep_second") -> dict:
    if role not in ("deep_second", "deep_expand"):
        raise PipelineError("rtb_v2_followup_role_invalid", 422)
    h1 = validate_deep_v2_tree(h1_nodes, research_input)
    first = validate_deep_v2_tree(first_pass_nodes, research_input)
    protected = {n["id"]: n for n in h1}
    first_map = {n["id"]: n for n in first}
    output = validate_deep_v2_response(result, research_input, role)
    generated = {node["id"]: node for node in output["nodes"]}
    for ident, node in protected.items():
        if ident not in generated or generated[ident] != node:
            raise PipelineError("rtb_v2_h1_locked_node_modified_or_deleted", 422)
    rejected = set(first_map) - set(protected)
    if rejected & set(generated):
        raise PipelineError("rtb_v2_rejected_h1_id_reintroduced", 422)
    return deepcopy(output)
