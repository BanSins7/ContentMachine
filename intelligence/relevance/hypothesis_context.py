"""S2 deterministic per-hypothesis classifier input from a checked RTB snapshot.

No summarization, fact invention, prompt construction, external calls or
classification here. Only exact, human-approved RTB strings and fact provenance.
"""
from __future__ import annotations

from copy import deepcopy

from .v1_contracts import fingerprint, reject
from .v1_taxonomy import validate_taxonomy_snapshot

CONTEXT_VERSION = "sre-hypothesis-context/v1"


def _path(node, by_id):
    """Return root -> leaf ancestry, protecting against broken trees."""
    result = []
    visited = set()
    current = node
    while current is not None:
        if current["id"] in visited:
            reject("ancestry", "cyclic RTB tree")
        visited.add(current["id"])
        result.append(current)
        parent = current["parent_id"]
        if parent is None:
            break
        current = by_id.get(parent)
        if current is None:
            reject("ancestry", "missing RTB parent")
    result.reverse()
    kinds = [n["kind"] for n in result]
    if kinds not in (["direction", "hypothesis"],
                     ["direction", "branch", "hypothesis"]):
        reject("ancestry", "invalid RTB classification hierarchy")
    return result


def build_classifier_contexts(taxonomy_snapshot):
    """One deterministic immutable context per hypothesis leaf.

    Accepts any shape-valid S1 envelope; callers needing authorized data MUST
    use taxonomy_adapter.load_classifier_contexts to check PostgreSQL lineage.
    """
    source = validate_taxonomy_snapshot(taxonomy_snapshot)
    by_id = {node["id"]: node for node in source["nodes"]}
    links = {entry["source_node_id"]: entry["category_key"]
             for entry in source["hypothesis_links"]}
    facts = {fact["id"]: fact for fact in source["approved_facts"]}
    nodes = [n for n in source["nodes"] if n["kind"] == "hypothesis"]
    nodes.sort(key=lambda n: tuple(x["id"] for x in _path(n, by_id)))
    grouped = {}
    for node in nodes:
        grouped.setdefault(node["parent_id"], []).append(node)
    for group in grouped.values():
        group.sort(key=lambda n: n["id"])
    contexts = []
    for node in nodes:
        path = _path(node, by_id)
        linked_facts = sorted(set(fid for ancestor in path
                                  for fid in ancestor["source_fact_ids"]))
        if not set(linked_facts).issubset(facts):
            reject("approved_facts", "RTB hypothesis ancestry references unknown fact")
        other = [item for item in grouped[node["parent_id"]]
                 if item["id"] != node["id"]]
        sibling_context = [
            {k: deepcopy(item[k])
             for k in ("id", "name", "definition", "include", "exclude")}
            for item in other
        ]
        result = {
            "schema_version": CONTEXT_VERSION,
            "project_id": source["project_id"],
            "rtb_run_id": source["rtb_run_id"],
            "publication_id": source["publication_id"],
            "taxonomy_version": source["taxonomy_version"],
            "sre_taxonomy_version_id": source["sre_taxonomy_version_id"],
            "taxonomy_sha256": source["tree_sha256"],
            "source_node_id": node["id"],
            "category_key": links[node["id"]],
            "ancestry_path": [entry["id"] for entry in path],
            "direction": deepcopy(path[0]),
            "branch": deepcopy(path[1]) if len(path) == 3 else None,
            "hypothesis": deepcopy(node),
            "approved_facts": [
                {"id": fid, "kind": facts[fid]["kind"],
                 "text": facts[fid]["text"],
                 "source_quote": facts[fid].get("source_quote"),
                 "origin": facts[fid].get("origin")}
                for fid in linked_facts
            ],
            "sibling_hypotheses": sibling_context,
        }
        result["context_sha256"] = fingerprint(result)
        contexts.append(result)
    return contexts


def summarize_by_direction(contexts):
    """Pure derived roll-up IDs for Analytics; NOT model classifications."""
    if type(contexts) is not list:
        reject("contexts", "expected list")
    grouped = {}
    for context in contexts:
        direction = context["direction"]["id"]
        grouped.setdefault(direction, []).append(context["category_key"])
    return {direction: sorted(keys) for direction, keys in sorted(grouped.items())}
