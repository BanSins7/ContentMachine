"""RTB-FIX-03 initial validator: 14-key Deep V2 tree against exact B9/A8 input.

Pure deterministic validation; no HTTP, billing, SQL or modification to Deep V1.
Passing this validator is necessary but not sufficient for Human Review2 /
Search Readiness publication quality.
"""
from __future__ import annotations

from copy import deepcopy

from .deep_contracts import validate_tree
from .deep_v2_lifecycle import validate_frozen_input
from .deep_v2_transport import ALL, CORE, EXTENSIONS
from .pipeline_contracts import PipelineError

MAX_REFERENCES = 12
MAX_SIGNALS = 6
MAX_FOCUS = 6
MIN_SIGNALS = 1
MIN_FOCUS = 1


def _fail(reason: str):
    raise PipelineError(reason, 422)


def _checked_array(value, *, label: str, maximum: int, minimum: int = 0,
                   allowed: set[str] | None = None, text_limit=180) -> list[str]:
    if (not isinstance(value, list) or
            not minimum <= len(value) <= maximum):
        _fail("rtb_v2_" + label + "_count")
    seen = set()
    output = []
    for candidate in value:
        if (not isinstance(candidate, str)
                or candidate != candidate.strip() or not candidate
                or len(candidate) > text_limit
                or "\x00" in candidate):
            _fail("rtb_v2_" + label + "_invalid")
        if allowed is not None and candidate not in allowed:
            _fail("rtb_v2_" + label + "_unknown")
        folded = candidate.casefold()
        if folded in seen:
            _fail("rtb_v2_" + label + "_duplicate")
        seen.add(folded)
        output.append(candidate)
    return output


def validate_deep_v2_tree(nodes, research_input: dict):
    """Return validated 14-field nodes; never drops or modifies A/B original data."""
    source = validate_frozen_input(research_input)
    if not isinstance(nodes, list) or not nodes:
        _fail("rtb_v2_nodes_invalid")
    facts = []
    by_id = {}
    for fact in source["c1_approved_facts"]:
        if not isinstance(fact, dict):
            _fail("rtb_v2_source_fact_invalid")
        uid = fact.get("rtb_fact_id")
        if not isinstance(uid, str) or uid in by_id:
            _fail("rtb_v2_source_fact_identity_invalid")
        facts.append({"id": uid, "kind": fact.get("kind"), "text": fact.get("reviewed_text")})
        by_id[uid] = fact
    allowed_obs = {
        entry.get("observation_id")
        for entry in source["c2_original_video_observations"]
        if isinstance(entry, dict) and isinstance(entry.get("observation_id"), str)
    }
    if len(allowed_obs) != len(source["c2_original_video_observations"]):
        _fail("rtb_v2_source_observations_not_unique")
    projected = []
    extension_rows = []
    for n in nodes:
        if not isinstance(n, dict) or set(n) != set(ALL):
            _fail("rtb_v2_exactly_fourteen_node_fields_required")
        core = {k: deepcopy(n[k]) for k in CORE}
        projected.append(core)
        obs = _checked_array(
            n["source_observation_ids"], label="observations",
            maximum=MAX_REFERENCES, allowed=allowed_obs, text_limit=320)
        signals = _checked_array(
            n["search_signals"], label="search_signals",
            maximum=MAX_SIGNALS, minimum=MIN_SIGNALS if n["kind"] == "hypothesis" else 0)
        focus = _checked_array(
            n["validation_focus"], label="validation_focus",
            maximum=MAX_FOCUS, minimum=MIN_FOCUS if n["kind"] == "hypothesis" else 0)
        if n["kind"] in ("direction", "branch") and (obs or signals or focus):
            _fail("rtb_v2_nonhypothesis_extensions_not_empty")
        extension_rows.append({
            "source_observation_ids": obs,
            "search_signals": signals,
            "validation_focus": focus,
        })
    validated = validate_tree(projected, facts)
    for node in validated:
        if node["relation"] == "direct":
            if not any(
                by_id[fid].get("kind") in ("function", "product_type")
                and by_id[fid].get("source_role") == "own_product"
                and by_id[fid].get("subject_scope") == "own_product"
                and by_id[fid].get("may_support_own_product_claim") is True
                for fid in node["source_fact_ids"]
            ):
                _fail("rtb_v2_unapproved_direct_product_claim")
    return [
        {**node, **extension_rows[i]}
        for i, node in enumerate(validated)
    ]


def validate_deep_v2_response(output: dict, research_input: dict, role: str) -> dict:
    if role not in ("deep_first", "deep_second", "deep_expand"):
        _fail("rtb_v2_response_role_invalid")
    if not isinstance(output, dict):
        _fail("rtb_v2_response_not_an_object")
    expected = {"nodes"} if role == "deep_first" else {"nodes", "duplicates"}
    if set(output) != expected:
        _fail("rtb_v2_response_unexpected_properties")
    nodes = validate_deep_v2_tree(output["nodes"], research_input)
    result = {"nodes": nodes}
    if role != "deep_first":
        duplicates = output["duplicates"]
        if not isinstance(duplicates, list) or len(duplicates) > 80:
            _fail("rtb_v2_duplicates_invalid")
        known = {node["id"] for node in nodes}
        pairs = set()
        checked = []
        for d in duplicates:
            if (not isinstance(d, dict) or set(d) != {"a", "b", "reason"}
                    or not isinstance(d.get("a"), str)
                    or not isinstance(d.get("b"), str)
                    or d["a"] == d["b"] or d["a"] not in known or d["b"] not in known
                    or not isinstance(d.get("reason"), str)
                    or not d["reason"].strip() or len(d["reason"]) > 350):
                _fail("rtb_v2_duplicate_claim_invalid")
            pair = tuple(sorted((d["a"], d["b"])))
            if pair in pairs:
                _fail("rtb_v2_duplicate_claim_repeated")
            pairs.add(pair)
            checked.append(deepcopy(d))
        result["duplicates"] = checked
    return result
