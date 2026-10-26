"""C9 RTB -> SRE contract: only approved leaf UUID5 links.

Pure reference validation is NOT C9 human review authorization. The verified
reader below goes through S2's actual PostgreSQL-authorized deep publication.
"""
from __future__ import annotations

from uuid import UUID, uuid5

from ..v1_contracts import (
    canonical_uuid, nonempty, positive, project_id, record, reject,
    sha256_digest,
)
from ..v1_taxonomy import validate_taxonomy_snapshot

TAXONOMY_REF_VERSION = "sre-taxonomy-ref/v1"


def validate_taxonomy_reference(payload):
    record(payload, "taxonomy_ref", required=(
        "schema_version", "project_id", "publication_id", "rtb_run_id",
        "sre_taxonomy_version_id", "taxonomy_sha256", "hypothesis_links"))
    if payload["schema_version"] != TAXONOMY_REF_VERSION:
        reject("taxonomy_ref.schema_version", "expected "+TAXONOMY_REF_VERSION,
               "unsupported_schema_version")
    project_id(payload["project_id"])
    canonical_uuid(payload["publication_id"], "taxonomy_ref.publication_id")
    run = canonical_uuid(payload["rtb_run_id"], "taxonomy_ref.rtb_run_id")
    positive(payload["sre_taxonomy_version_id"], "taxonomy_ref.sre_taxonomy_version_id")
    sha256_digest(payload["taxonomy_sha256"], "taxonomy_ref.taxonomy_sha256")
    raw = payload["hypothesis_links"]
    if type(raw) is not list or not 1 <= len(raw) <= 10000:
        reject("taxonomy_ref.hypothesis_links", "expected 1..10000 approved hypothesis links")
    links = []
    seen_sources, seen_categories = set(), set()
    for i, item in enumerate(raw):
        path = f"taxonomy_ref.hypothesis_links[{i}]"
        record(item, path, required=("source_node_id", "category_key"))
        node = nonempty(item["source_node_id"], path+".source_node_id", limit=256)
        category = canonical_uuid(item["category_key"], path+".category_key")
        # C9 deep_repository.final_review maps ALL RTB nodes with this formula.
        if category != str(uuid5(UUID(run), node)):
            reject(path+".category_key", "category must equal RTB UUID5 projection",
                   "snapshot_mismatch")
        if node in seen_sources or category in seen_categories:
            reject(path, "duplicate approved leaf")
        seen_sources.add(node)
        seen_categories.add(category)
        links.append({"source_node_id":node,"category_key":category})
    if links != sorted(links, key=lambda v:v["source_node_id"]):
        reject("taxonomy_ref.hypothesis_links", "leaf mapping order must be canonical")
    return {**payload, "hypothesis_links": links}


def reference_from_approved_snapshot(snapshot):
    """Pure shape transform, NOT the authority to approve arbitrary payloads."""
    source = validate_taxonomy_snapshot(snapshot)
    ref = {
        "schema_version": TAXONOMY_REF_VERSION,
        "project_id": source["project_id"],
        "publication_id": source["publication_id"],
        "rtb_run_id": source["rtb_run_id"],
        "sre_taxonomy_version_id": source["sre_taxonomy_version_id"],
        "taxonomy_sha256": source["tree_sha256"],
        "hypothesis_links": sorted(source["hypothesis_links"],
                                   key=lambda v:v["source_node_id"]),
    }
    return validate_taxonomy_reference(ref)


def load_verified_taxonomy_reference(project, publication, *, connect=None):
    """DB-authoritative entry: fail-closed C9 H1/H2/approval/mirror validation."""
    from ..taxonomy_adapter import load_approved_taxonomy
    return reference_from_approved_snapshot(
        load_approved_taxonomy(project, publication, connect=connect)
    )


def load_verified_c9_publication(project, publication, *, connect=None):
    """M06 full C9 v1 port; returns an authorized, all-leaf immutable bundle.

    Delegates to the separate M06 module to keep the M02 reference contract
    unchanged. The only authority is the real read-only PostgreSQL S2 loader.
    """
    from .approved_c9 import load_approved_c9
    return load_approved_c9(project, publication, connect=connect)
