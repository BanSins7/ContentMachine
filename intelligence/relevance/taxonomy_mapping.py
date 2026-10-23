"""S2: exact deterministic verification of approved RTB / SRE mirror.

This module is read-only and never creates or repairs any missing projection.
"""
from __future__ import annotations

from copy import deepcopy
from uuid import UUID, uuid5

from intelligence.taxonomy_builder.pipeline_contracts import sha256
from .v1_contracts import CONTRACT_VERSIONS, fingerprint, nonempty, reject, positive, project_id
from .v1_taxonomy import validate_taxonomy_snapshot


def expected_flat_projection(nodes, rtb_run_id):
    """Replicate deep_repository.final_review canonical flat projection."""
    return [{
        "id": str(uuid5(UUID(rtb_run_id), n["id"])),
        "name": n["name"], "relation": n["relation"],
        "definition": n["definition"], "include": n["include"],
        "exclude": n["exclude"], "fact_ids": list(n["source_fact_ids"]),
        "parent_id": n["parent_id"], "source_node_id": n["id"],
        "kind": n["kind"], "rationale": n["rationale"],
        "positive_examples": list(n["examples"]) or [n["name"]],
        "negative_examples": [n["exclude"]], "origin": "inferred",
    } for n in nodes]


def _expected_category(flat):
    return {
        "category_key": flat["id"],
        "name": flat["name"], "relation_type": flat["relation"],
        "definition": flat["definition"],
        "rules": {
            "include": [flat["include"]], "exclude": [flat["exclude"]],
            "positive_examples": flat["positive_examples"],
            "negative_examples": flat["negative_examples"],
            "hierarchy": {
                "source_node_id": flat["source_node_id"],
                "parent_id": flat["parent_id"], "kind": flat["kind"],
                "rationale": flat["rationale"],
            },
        },
        "related_fact_ids": list(flat["fact_ids"]),
        "review_status": "approved", "origin": "inferred",
        "independent": not bool(flat["fact_ids"]),
    }


def verify_authorized_mirror(published, db):
    """Validate real, cross-referenced DB evidence and emit S1 taxonomy envelope."""
    pid = project_id(published["project_id"])
    publication_id = published["taxonomy_publication_id"]
    rid = published["rtb_run_id"]
    if (db.get("project_id") != pid or db.get("publication_id") != publication_id or
            db.get("rtb_run_id") != rid):
        reject("publication", "mismatched RTB and SRE project/run/publication IDs",
               "snapshot_mismatch")
    sre_version = positive(db.get("sre_taxonomy_version_id"), "sre_taxonomy_version_id")
    if db.get("sre_status") != "approved":
        reject("sre_status", "SRE taxonomy is not approved", "snapshot_mismatch")
    for field in ("publication_approved_by", "sre_approved_by"):
        nonempty(db.get(field), field, limit=150)
    reviews = db.get("reviews")
    if type(reviews) is not list or len(reviews) != 2 or [
        r[0] for r in reviews if type(r) in (tuple, list) and len(r) == 3
    ] != [1, 2]:
        reject("reviews", "two real, ordered, numbered human reviews required",
               "snapshot_mismatch")
    for review in reviews:
        if type(review[0]) is not int or type(review[1]) is not list:
            reject("reviews", "invalid human review record", "snapshot_mismatch")
        nonempty(review[2], "reviews.reviewed_by", limit=150)
    # H1 may change during pass 2; H2 MUST equal final published tree.
    if fingerprint(reviews[1][1]) != published["taxonomy_sha256"]:
        reject("reviews[1].approved_nodes", "final human review differs from publication",
               "snapshot_mismatch")
    quality = db.get("quality_report")
    if (type(quality) is not dict or type(quality.get("severe_gap_count")) is not int or
        type(quality.get("duplicate_count")) is not int or
        quality["severe_gap_count"] != 0 or quality["duplicate_count"] != 0):
        reject("quality_report", "published tree lacks final safety gates",
               "snapshot_mismatch")

    flat = expected_flat_projection(published["tree"], rid)
    flat_hash = sha256(flat)
    if (db.get("approved_topics") != flat or db.get("source_sha256") != flat_hash
            or db.get("sre_snapshot_checksum") != flat_hash):
        reject("projection", "published RTB projection and SRE checksums differ",
               "snapshot_mismatch")

    rows = db.get("categories")
    if type(rows) is not list or len(rows) != len(flat):
        reject("categories", "SRE mirror must contain every RTB tree node exactly once",
               "snapshot_mismatch")
    expected = {x["id"]: _expected_category(x) for x in flat}
    actual = {}
    for i, row in enumerate(rows):
        if type(row) not in (tuple, list) or len(row) != 9:
            reject(f"categories[{i}]", "invalid SRE category DB row")
        key, name, relation, definition, rules, fact_ids, review_status, origin, independent = row
        if type(key) is not str or key not in expected or key in actual:
            reject(f"categories[{i}]", "unexpected or repeated SRE category key",
                   "snapshot_mismatch")
        actual[key] = {
            "category_key": key, "name": name, "relation_type": relation,
            "definition": definition, "rules": rules,
            "related_fact_ids": fact_ids, "review_status": review_status,
            "origin": origin, "independent": independent,
        }
        if actual[key] != expected[key]:
            reject(f"categories[{i}]", "SRE mirrored category differs from approved RTB",
                   "snapshot_mismatch")

    source_facts = {fact["id"]: fact for fact in published["approved_facts"]}
    mirrored_facts = db.get("sre_facts")
    if type(mirrored_facts) is not list or len(mirrored_facts) != len(source_facts):
        reject("sre_facts", "SRE approved fact count mismatches RTB",
               "snapshot_mismatch")
    seen_facts = set()
    for i, row in enumerate(mirrored_facts):
        if type(row) not in (tuple, list) or len(row) != 6:
            reject(f"sre_facts[{i}]", "invalid mirrored fact record")
        ident, kind, text, quote, origin, review_status = row
        fact = source_facts.get(ident)
        if fact is None or ident in seen_facts:
            reject(f"sre_facts[{i}]", "unexpected/duplicate mirrored fact",
                   "snapshot_mismatch")
        seen_facts.add(ident)
        if (review_status != "approved" or kind != fact["kind"] or text != fact["text"]
            or quote != (fact.get("source_quote") or fact["text"])
            or origin != ("user_added" if fact.get("origin") in
                          ("human_edited", "human_added") else "explicit")):
            reject(f"sre_facts[{i}]", "SRE fact evidence differs from human-approved fact",
                   "snapshot_mismatch")

    expected_links = {(item["id"], fid) for item in flat for fid in item["fact_ids"]}
    links = db.get("fact_links")
    if (type(links) is not list or
        any(type(r) not in (tuple, list) or len(r) != 2 for r in links)):
        reject("fact_links", "invalid fact-link records")
    if len(links) != len(expected_links) or set(map(tuple, links)) != expected_links:
        reject("fact_links", "actual SRE fact edges differ from approved RTB",
               "snapshot_mismatch")

    return validate_taxonomy_snapshot({
        "schema_version": CONTRACT_VERSIONS["taxonomy"],
        "project_id": pid, "publication_id": publication_id,
        "rtb_run_id": rid, "taxonomy_version": published["taxonomy_version"],
        "sre_taxonomy_version_id": sre_version,
        "tree_sha256": published["taxonomy_sha256"],
        "approved_facts": deepcopy(published["approved_facts"]),
        "human_reviews": [1, 2], "nodes": deepcopy(published["tree"]),
        "hypothesis_links": [
            {"source_node_id": f["source_node_id"], "category_key": f["id"]}
            for f in flat if f["kind"] == "hypothesis"
        ],
    })
