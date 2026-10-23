"""Deterministic input contracts for the Search Planner RTB foundation.

The published RTB node's eleven fields are authoritative: do not summarize,
truncate or ask an LLM to reconstruct any of them in this layer.
"""
from __future__ import annotations

import re
from copy import deepcopy
from uuid import UUID

from intelligence.taxonomy_builder.deep_contracts import validate_tree
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.taxonomy_builder.pipeline_repository import _pid

NODE_FIELDS = (
    "id", "parent_id", "kind", "relation", "source_fact_ids",
    "name", "rationale", "definition", "include", "exclude", "examples",
)
LANGUAGE_RE = re.compile(r"[a-z]{2,3}(?:-[A-Z]{2})?\Z")
MODES = frozenset(("keyword", "hashtag"))
FACT_KINDS = frozenset(("function", "problem", "audience", "product_type"))


def publication_uuid(value):
    """Reject non-canonical input rather than accepting ambiguous UUID spellings."""
    if not isinstance(value, str):
        raise PipelineError("Invalid taxonomy publication ID", 422)
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise PipelineError("Invalid taxonomy publication ID", 422) from exc
    if str(parsed) != value:
        raise PipelineError("Invalid taxonomy publication ID", 422)
    return value


def validate_search_policy(policy):
    """No language or geography is inferred from machine/user location."""
    if not isinstance(policy, dict):
        raise PipelineError("Explicit search policy required", 422)
    fields = {
        "languages", "markets", "allowed_modes",
        "target_candidates_per_hypothesis",
        "max_candidates_per_hypothesis", "max_total_candidates",
    }
    if set(policy) != fields:
        raise PipelineError("Incomplete or unexpected search policy", 422)
    languages = policy["languages"]
    markets = policy["markets"]
    modes = policy["allowed_modes"]
    if not isinstance(languages, list) or not 1 <= len(languages) <= 10 or any(
        not isinstance(x, str) or not LANGUAGE_RE.fullmatch(x) for x in languages
    ) or len(set(languages)) != len(languages):
        raise PipelineError("Invalid search languages", 422)
    if not isinstance(markets, list) or len(markets) > 10 or any(
        not isinstance(x, str) or not 2 <= len(x) <= 50 for x in markets
    ) or len(set(markets)) != len(markets):
        raise PipelineError("Invalid search markets", 422)
    if not isinstance(modes, list) or not 1 <= len(modes) <= 2 or any(
        not isinstance(x, str) or x not in MODES for x in modes
    ) or len(set(modes)) != len(modes):
        raise PipelineError("Invalid search modes", 422)
    for key, maximum in (
        ("target_candidates_per_hypothesis", 16),
        ("max_candidates_per_hypothesis", 16),
        ("max_total_candidates", 480),
    ):
        value = policy[key]
        if type(value) is not int or not 1 <= value <= maximum:
            raise PipelineError("Invalid search policy limit: " + key, 422)
    if policy["target_candidates_per_hypothesis"] > policy["max_candidates_per_hypothesis"]:
        raise PipelineError("Search target exceeds per-hypothesis maximum", 422)
    return deepcopy(policy)


def validate_published_snapshot(snapshot):
    """Validate an already DB-authorized publication, including RTB invariants."""
    if not isinstance(snapshot, dict):
        raise PipelineError("Published RTB snapshot is missing", 409)
    pid = _pid(snapshot.get("project_id"))
    pub_id = publication_uuid(snapshot.get("taxonomy_publication_id"))
    run_id = publication_uuid(snapshot.get("rtb_run_id"))
    version = snapshot.get("taxonomy_version")
    if type(version) is not int or version < 1:
        raise PipelineError("Invalid taxonomy version", 409)
    if snapshot.get("run_status") != "approved" or snapshot.get("deep_status") != "approved":
        raise PipelineError("Taxonomy is not approved", 409)
    reviews = snapshot.get("human_reviews")
    if not isinstance(reviews, (list, tuple)) or set(reviews) != {1, 2} or len(reviews) != 2:
        raise PipelineError("Both RTB Human Reviews required", 409)
    profile = snapshot.get("profile_snapshot")
    if not isinstance(profile, dict):
        raise PipelineError("Missing approved project profile", 409)
    facts = snapshot.get("approved_facts")
    if not isinstance(facts, list) or not facts:
        raise PipelineError("Human-approved facts required", 409)
    known = set()
    for fact in facts:
        if not isinstance(fact, dict) or not isinstance(fact.get("id"), str):
            raise PipelineError("Invalid approved fact", 409)
        if fact["id"] in known or fact.get("kind") not in FACT_KINDS or not isinstance(fact.get("text"), str) or not fact["text"].strip():
            raise PipelineError("Invalid or duplicate approved fact", 409)
        known.add(fact["id"])
        for optional in ("source_quote", "origin"):
            val = fact.get(optional)
            if val is not None and not isinstance(val, str):
                raise PipelineError("Invalid approved fact provenance", 409)
    nodes = validate_tree(snapshot.get("tree"), facts)
    if any(set(n) != set(NODE_FIELDS) for n in nodes):
        raise PipelineError("Incomplete RTB node", 409)
    # RTB quality gate guarantees each direction is researchable at publication.
    by_id = {n["id"]: n for n in nodes}
    covered_roots = set()
    for n in nodes:
        if n["kind"] != "hypothesis":
            continue
        current = n
        while current["parent_id"] is not None:
            current = by_id[current["parent_id"]]
        covered_roots.add(current["id"])
    if any(n["kind"] == "direction" and n["id"] not in covered_roots for n in nodes):
        raise PipelineError("Published RTB direction has no hypothesis", 409)
    return {
        "project_id": pid,
        "taxonomy_publication_id": pub_id,
        "rtb_run_id": run_id,
        "taxonomy_version": version,
        "tree": nodes,
        "taxonomy_sha256": sha256(nodes),
        "run_status": "approved",
        "deep_status": "approved",
        "human_reviews": [1, 2],
        "profile_snapshot": deepcopy(profile),
        "approved_facts": deepcopy(facts),
    }
