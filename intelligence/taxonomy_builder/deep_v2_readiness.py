"""RTB-FIX-06-A: deterministic Search Readiness Gate for Deep V2.

Pure, fail-closed and source-bound. An A8 original video observation is a
retrieval hint, NEVER an own-product fact. Search signal specificity is only
a structural/actionability check, not a promise of virality or performance.
This gate must be rerun AFTER every Human Review2 edit.
"""
from __future__ import annotations

import re
from copy import deepcopy

from intelligence.project_sources.context_builder import sha as canonical_sha
from .deep_contracts import quality_gate
from .deep_contracts_v2 import validate_deep_v2_tree
from .pipeline_contracts import PipelineError
from .research_contracts_v2 import project_v2_tree_to_v1_core

GATE_VERSION = "rtb-search-readiness/v1"
STOP_SIGNALS = {
    "video", "videos", "content", "create", "viral", "trend", "trending",
    "stuff", "things", "anything", "something", "everything",
    "видео", "контент", "тренд", "тренды", "вирусный", "разное",
}


def _tokens(value):
    return [
        t for t in re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE)
        if len(t) >= 3 and t not in STOP_SIGNALS
    ]


def search_readiness_gate(nodes, research_input, *, h1_nodes, first_nodes,
                          candidate_stage, model_duplicates=None):
    """Return a signed deterministic report; no mutation/DB/provider.

    Structural integrity and H1 preservation are fail-closed exceptions.
    Insufficient search specificity/coverage returns a blocking report.
    """
    if candidate_stage not in ("deep_second", "deep_expand_1", "deep_expand_2", "human_review2"):
        raise PipelineError("rtb_v2_readiness_candidate_stage_invalid", 422)
    tree = validate_deep_v2_tree(nodes, research_input)
    h1 = validate_deep_v2_tree(h1_nodes, research_input)
    original = validate_deep_v2_tree(first_nodes, research_input)
    ids = {n["id"]: n for n in tree}
    locked = {n["id"]: n for n in h1}
    rejected = {n["id"] for n in original} - set(locked)
    for ident, expected in locked.items():
        if ids.get(ident) != expected:
            raise PipelineError("rtb_v2_readiness_h1_mutated", 422)
    if rejected.intersection(ids):
        raise PipelineError("rtb_v2_readiness_rejected_id_restored", 422)

    facts = [
        {"id": f["rtb_fact_id"], "kind": f["kind"],
         "text": f["reviewed_text"]}
        for f in research_input["c1_approved_facts"]
    ]
    core = project_v2_tree_to_v1_core(tree)
    baseline = quality_gate(
        core, facts, previous=project_v2_tree_to_v1_core(h1),
        model_duplicates=model_duplicates or [],
    )
    blocking = []
    if baseline["severe_gap_count"]:
        blocking.append({
            "code": "rtb_v2_readiness_evidence_or_direction_gap",
            "node_ids": [g["node_id"] for g in baseline["branch_gaps"]],
            "fact_ids": [
                g["fact_id"] for g in baseline["gaps"]
                if g["gap_severity"] == "severe"
            ],
        })
    if baseline["duplicate_count"]:
        blocking.append({
            "code": "rtb_v2_readiness_duplicate_hypotheses",
            "pairs": deepcopy(baseline["duplicates"]),
        })
    if baseline["new_direction_count"]:
        blocking.append({
            "code": "rtb_v2_readiness_saturation_requires_review",
            "node_ids": list(baseline["new_direction_ids"]),
        })

    used_signals = {}
    used_focus = {}
    hypothesis_count = 0
    for node in tree:
        if node["kind"] != "hypothesis":
            continue
        hypothesis_count += 1
        for field, seen in (("search_signals", used_signals),
                            ("validation_focus", used_focus)):
            for value in node[field]:
                if len(set(_tokens(value))) < 2:
                    blocking.append({
                        "code": "rtb_v2_readiness_search_term_too_generic",
                        "node_id": node["id"], "field": field,
                        "value": value,
                    })
                key = " ".join(_tokens(value))
                if key in seen and seen[key] != node["id"]:
                    blocking.append({
                        "code": "rtb_v2_readiness_repeated_search_term",
                        "node_id": node["id"], "other_node_id": seen[key],
                        "field": field, "value": value,
                    })
                seen[key] = node["id"]

    if hypothesis_count < 2:
        blocking.append({
            "code": "rtb_v2_readiness_insufficient_hypothesis_count",
            "count": hypothesis_count,
        })

    report = {
        "schema_version": GATE_VERSION,
        "research_input_sha256": research_input["research_input_sha256"],
        "tree_sha256": canonical_sha(tree),
        "h1_sha256": canonical_sha(h1),
        "first_pass_sha256": canonical_sha(original),
        "candidate_stage": candidate_stage,
        "hypothesis_count": hypothesis_count,
        "covered_facts": baseline["covered_facts"],
        "total_facts": baseline["total_facts"],
        "legacy_quality": baseline,
        "blocking_errors": blocking,
        "passed": not blocking,
        "model_calls": 0,
        "scope_note": (
            "Search-readiness checks evidence/provenance, nonduplicate "
            "searchable hypotheses and structural coverage; not proof "
            "of real-world performance, search availability or virality."
        ),
    }
    return {**report, "report_sha256": canonical_sha(report)}
