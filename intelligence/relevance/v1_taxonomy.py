"""V1 shape contract for the immutable RTB -> SRE classifier handoff.

NOT an authorization loader. S2 must obtain all facts, reviews, links and
publication statuses from PostgreSQL and verify them before calling this code.
"""
from __future__ import annotations
from uuid import UUID, uuid5

from intelligence.taxonomy_builder.deep_contracts import validate_tree
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

from .v1_contracts import (
    CONTRACT_VERSIONS, SREContractError, _bounded_list, _version, canonical_uuid,
    fingerprint, nonempty, positive, project_id, record, reject, sha256_digest,
)

NODE_FIELDS = frozenset(("id", "parent_id", "kind", "relation",
    "source_fact_ids", "name", "rationale", "definition", "include",
    "exclude", "examples"))
FACT_KINDS = frozenset(("function", "problem", "audience", "product_type"))


def validate_taxonomy_snapshot(payload):
    """Ensure complete 11-field RTB nodes + exactly mapped hypothesis leaves."""
    record(payload, "taxonomy", required=(
        "schema_version", "project_id", "publication_id", "rtb_run_id",
        "taxonomy_version", "sre_taxonomy_version_id", "tree_sha256",
        "approved_facts", "human_reviews", "nodes", "hypothesis_links"))
    _version(payload, "taxonomy")
    pid = project_id(payload["project_id"])
    pub_id = canonical_uuid(payload["publication_id"], "publication_id")
    run_id = canonical_uuid(payload["rtb_run_id"], "rtb_run_id")
    version = positive(payload["taxonomy_version"], "taxonomy_version")
    sre_version = positive(payload["sre_taxonomy_version_id"], "sre_taxonomy_version_id")
    expected_digest = sha256_digest(payload["tree_sha256"], "tree_sha256")
    reviews = payload["human_reviews"]
    if type(reviews) is not list or reviews != [1, 2] or any(type(x) is not int for x in reviews):
        reject("human_reviews", "both ordered RTB human review gates [1,2] required")
    facts_raw = _bounded_list(payload["approved_facts"], "approved_facts", minimum=1, maximum=100)
    facts = []
    fact_ids = set()
    for i, raw in enumerate(facts_raw):
        path = f"approved_facts[{i}]"
        record(raw, path, required=("id", "kind", "text"),
               optional=("origin", "source_quote", "source_field", "review_status"))
        ident = canonical_uuid(raw["id"], f"{path}.id")
        if ident in fact_ids:
            reject(path, "duplicate approved fact ID")
        fact_ids.add(ident)
        if raw["kind"] not in FACT_KINDS:
            reject(f"{path}.kind", "invalid fact kind")
        fact = {"id": ident, "kind": raw["kind"],
                "text": nonempty(raw["text"], f"{path}.text", limit=5000)}
        for k in ("origin", "source_quote", "source_field"):
            if k in raw:
                if raw[k] is None and k in ("source_quote", "source_field"):
                    fact[k] = None  # Human-added facts have no original questionnaire quote.
                else:
                    fact[k] = nonempty(raw[k], f"{path}.{k}", limit=5000)
        if "review_status" in raw:
            if raw["review_status"] != "approved":
                reject(f"{path}.review_status", "fact must be human-approved")
            fact["review_status"] = "approved"
        facts.append(fact)
    source_nodes = _bounded_list(payload["nodes"], "nodes", minimum=2, maximum=48)
    for i, node in enumerate(source_nodes):
        record(node, f"nodes[{i}]", required=NODE_FIELDS)
    try:
        nodes = validate_tree(source_nodes, facts)
    except (PipelineError, ValueError, TypeError, KeyError) as exc:
        raise SREContractError("invalid_contract", "nodes", str(exc)) from exc
    if fingerprint(nodes) != expected_digest:
        reject("tree_sha256", "RTB node content does not match pinned sha256",
               "snapshot_mismatch")
    by_id = {node["id"]: node for node in nodes}
    for node in nodes:
        if node["kind"] == "direction":
            continue
        parent = by_id.get(node["parent_id"])
        if parent is None:
            reject(f"nodes.{node['id']}", "missing parent")
        # Explicitly prohibit a hierarchy with extra layers not supported in V1.
        if node["kind"] == "branch" and parent["kind"] != "direction":
            reject(f"nodes.{node['id']}", "branch must belong to direction")
        if node["kind"] == "hypothesis" and parent["kind"] not in ("branch", "direction"):
            reject(f"nodes.{node['id']}", "hypothesis must belong to branch or direction")
    # Every direction must contain at least one concrete hypothesis.
    covered_roots = set()
    for node in nodes:
        if node["kind"] != "hypothesis":
            continue
        ancestor = node
        while ancestor["parent_id"] is not None:
            ancestor = by_id[ancestor["parent_id"]]
        covered_roots.add(ancestor["id"])
    if any(n["kind"] == "direction" and n["id"] not in covered_roots for n in nodes):
        reject("nodes", "every direction needs at least one hypothesis")
    links_raw = _bounded_list(payload["hypothesis_links"],
                              "hypothesis_links", minimum=1, maximum=48)
    links = []
    seen_ids, seen_keys = set(), set()
    for i, raw in enumerate(links_raw):
        path = f"hypothesis_links[{i}]"
        record(raw, path, required=("source_node_id", "category_key"))
        node_id = nonempty(raw["source_node_id"], f"{path}.source_node_id", limit=40)
        category = canonical_uuid(raw["category_key"], f"{path}.category_key")
        if node_id not in by_id or by_id[node_id]["kind"] != "hypothesis":
            reject(path, "only actual RTB hypothesis nodes can be classified")
        if node_id in seen_ids or category in seen_keys:
            reject(path, "duplicate hypothesis link")
        # The upstream deep_repository derives category UUID from the RTB run UUID.
        if str(uuid5(UUID(run_id), node_id)) != category:
            reject(path, "category_key differs from published RTB stable mapping",
                   "snapshot_mismatch")
        seen_ids.add(node_id)
        seen_keys.add(category)
        links.append({"source_node_id": node_id, "category_key": category})
    if seen_ids != {n["id"] for n in nodes if n["kind"] == "hypothesis"}:
        reject("hypothesis_links", "every hypothesis must have exactly one link")
    return {"schema_version": CONTRACT_VERSIONS["taxonomy"],
            "project_id": pid, "publication_id": pub_id, "rtb_run_id": run_id,
            "taxonomy_version": version, "sre_taxonomy_version_id": sre_version,
            "tree_sha256": expected_digest, "approved_facts": facts,
            "human_reviews": [1, 2], "nodes": nodes, "hypothesis_links": links}
