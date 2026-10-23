"""Build complete, deterministic HypothesisContext v1 from approved RTB.

Pure Python: the caller must supply the snapshot returned by input_loader.
No context compression, inferred audience, inferred market or paid requests.
"""
from __future__ import annotations

from copy import deepcopy

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from .contracts import NODE_FIELDS, validate_published_snapshot, validate_search_policy


def _node_copy(node):
    """Preserve every authoritative node field, including all examples."""
    if set(node) != set(NODE_FIELDS):
        raise PipelineError("RTB node field mismatch", 409)
    return {key: deepcopy(node[key]) for key in NODE_FIELDS}


def _text_from_profile(profile, primary, secondary=None, max_length=2500):
    value = profile.get(primary)
    if value is None and secondary:
        value = profile.get(secondary)
    if value is None:
        return None
    if isinstance(value, str):
        if len(value) > max_length:
            raise PipelineError("Project profile field exceeds HypothesisContext limit", 422)
        return value
    raise PipelineError("Unexpected project profile field type: " + primary, 422)


def _list_or_text(profile, key):
    value = profile.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        if len(value) > 2500:
            raise PipelineError("Project profile field exceeds HypothesisContext limit", 422)
        return value
    if isinstance(value, list) and len(value) <= 25 and all(
        isinstance(item, str) and len(item) <= 300 for item in value
    ):
        return deepcopy(value)
    raise PipelineError("Unexpected project profile field: " + key, 422)


def _project_context(profile):
    """Explicit field allowlist. Missing values remain null, never invented."""
    return {
        "product_type": _text_from_profile(profile, "product_type", max_length=1500),
        "product_functions": _list_or_text(profile, "product_functions"),
        "audience_problems": _list_or_text(profile, "audience_problems"),
        "target_audience": _text_from_profile(profile, "target_audience", "audience"),
        "research_goal": _text_from_profile(profile, "research_goal", max_length=1500),
    }


def _ordered_path(hypothesis, by_id):
    path = []
    seen = set()
    current = hypothesis
    while current is not None:
        ident = current["id"]
        if ident in seen:
            raise PipelineError("RTB ancestry cycle", 409)
        seen.add(ident)
        path.append(current)
        parent_id = current["parent_id"]
        if parent_id is None:
            break
        if parent_id not in by_id:
            raise PipelineError("RTB ancestor missing", 409)
        current = by_id[parent_id]
    path.reverse()
    kinds = [node["kind"] for node in path]
    if kinds not in (["direction", "hypothesis"],
                     ["direction", "branch", "hypothesis"]):
        raise PipelineError("Invalid RTB hypothesis ancestry", 409)
    return path


def _approved_fact_context(path, facts):
    linked_ids = set()
    for node in path:
        linked_ids.update(node["source_fact_ids"])
    lookup = {fact["id"]: fact for fact in facts}
    if not linked_ids.issubset(lookup):
        raise PipelineError("RTB references a non-approved fact", 409)
    result = []
    for ident in sorted(linked_ids):
        fact = lookup[ident]
        quote = fact.get("source_quote")
        origin = fact.get("origin")
        if any(value is not None and not isinstance(value, str) for value in (quote, origin)):
            raise PipelineError("Invalid approved fact provenance", 409)
        if len(fact["text"]) > 2500 or (quote is not None and len(quote) > 2500) or (
            origin is not None and len(origin) > 64
        ) or len(ident) > 128:
            raise PipelineError("Approved fact exceeds HypothesisContext limit", 422)
        result.append({
            "id": ident,
            "kind": fact["kind"],
            "text": fact["text"],
            "source_quote": quote,
            "origin": origin,
        })
    return result


def build_hypothesis_contexts(published_snapshot, search_policy):
    """Return one full HypothesisContext/v1 per hypothesis in stable path order.

    Revalidates snapshot so standalone calls can't quietly accept an
    unapproved/draft input. Does not mutate either of its inputs.
    """
    source = validate_published_snapshot(published_snapshot)
    policy = validate_search_policy(search_policy)
    tree = source["tree"]
    by_id = {n["id"]: n for n in tree}
    facts = source["approved_facts"]
    profile = _project_context(source["profile_snapshot"])
    hypotheses = [n for n in tree if n["kind"] == "hypothesis"]
    siblings = {}
    for hyp in hypotheses:
        siblings.setdefault(hyp["parent_id"], []).append(hyp)
    for nodes in siblings.values():
        nodes.sort(key=lambda n: n["id"])

    def sorting_path(n):
        return tuple(x["id"] for x in _ordered_path(n, by_id))

    contexts = []
    for hypothesis in sorted(hypotheses, key=sorting_path):
        path = _ordered_path(hypothesis, by_id)
        direction, branch = path[0], path[1] if len(path) == 3 else None
        neighbors = [
            {key: deepcopy(other[key]) for key in
             ("id", "name", "definition", "include", "exclude")}
            for other in siblings[hypothesis["parent_id"]]
            if other["id"] != hypothesis["id"]
        ][:5]
        contexts.append({
            "context_schema_version": "hypothesis-context/v1",
            "project_id": source["project_id"],
            "rtb_run_id": source["rtb_run_id"],
            "taxonomy_publication_id": source["taxonomy_publication_id"],
            "taxonomy_version": source["taxonomy_version"],
            "taxonomy_sha256": source["taxonomy_sha256"],
            "ancestry_path": [n["id"] for n in path],
            "direction": _node_copy(direction),
            "branch": _node_copy(branch) if branch is not None else None,
            "hypothesis": _node_copy(hypothesis),
            "approved_facts": _approved_fact_context(path, facts),
            "project_context": deepcopy(profile),
            "neighbor_hypotheses": neighbors,
            "search_policy": deepcopy(policy),
            "derived_search_facets": [],
        })
    return contexts
