"""M06: authoritative C9 v1 approved publication port for standalone SRE.

The ONLY authorization entry is load_approved_c9(), which calls the S2 reader
against a specific C9 publication in one REPEATABLE READ READ ONLY transaction.
Pure build/validate helpers merely check transport shape and never convey DB
approval, ownership or authority; never trust a client-created bundle for work.
No optional Collector, QDP, provider, worker or external network imports.
"""
from __future__ import annotations

from uuid import UUID, uuid5

from ..hypothesis_context import build_classifier_contexts
from ..v1_contracts import (
    canonical_uuid, fingerprint, positive, project_id, record, reject,
    sha256_digest,
)
from ..v1_taxonomy import validate_taxonomy_snapshot
from .taxonomy import reference_from_approved_snapshot

APPROVED_C9_VERSION = "sre-approved-c9/v1"
_BUNDLE_FIELDS = (
    "schema_version", "project_id", "publication_id", "rtb_run_id",
    "taxonomy_version", "sre_taxonomy_version_id", "taxonomy_sha256",
    "approved_snapshot", "taxonomy_reference", "node_projections",
    "hypothesis_keys", "contexts", "bundle_sha256",
)


def _assemble(snapshot):
    """Pure deterministic projection from a shape-valid snapshot.

    Source shape validation does not authenticate a real C9 publication. Only
    the DB-backed load_approved_c9() is suitable for trusted SRE intake.
    """
    source = validate_taxonomy_snapshot(snapshot)
    reference = reference_from_approved_snapshot(source)
    contexts = build_classifier_contexts(source)
    tree = source["nodes"]
    run = UUID(source["rtb_run_id"])
    by_id = {item["id"]: item for item in tree}
    projections = []
    for node in sorted(tree, key=lambda item: item["id"]):
        parent_id = node["parent_id"]
        projections.append({
            "source_node_id": node["id"],
            "category_key": str(uuid5(run, node["id"])),
            "kind": node["kind"],
            "parent_source_node_id": parent_id,
            "parent_category_key": (
                str(uuid5(run, parent_id)) if parent_id is not None else None
            ),
        })
    leaves = {node["id"] for node in tree if node["kind"] == "hypothesis"}
    linked = {item["source_node_id"] for item in reference["hypothesis_links"]}
    context_ids = {item["source_node_id"] for item in contexts}
    if linked != leaves or context_ids != leaves or len(contexts) != len(leaves):
        reject("approved_c9.contexts", "every approved hypothesis must be present exactly once",
               "snapshot_mismatch")
    # Every context must resolve to exactly one true hierarchy path, never a
    # search-query candidate, implicit branch, or mutable active pointer.
    by_context = {item["source_node_id"]: item for item in contexts}
    for item in reference["hypothesis_links"]:
        ctx = by_context[item["source_node_id"]]
        if ctx["category_key"] != item["category_key"]:
            reject("approved_c9.contexts", "hypothesis UUID5 linkage mismatch",
                   "snapshot_mismatch")
        ancestors = ctx["ancestry_path"]
        if ancestors[-1] != item["source_node_id"] or not all(x in by_id for x in ancestors):
            reject("approved_c9.contexts", "non-canonical ancestry",
                   "snapshot_mismatch")
    result = {
        "schema_version": APPROVED_C9_VERSION,
        "project_id": source["project_id"],
        "publication_id": source["publication_id"],
        "rtb_run_id": source["rtb_run_id"],
        "taxonomy_version": source["taxonomy_version"],
        "sre_taxonomy_version_id": source["sre_taxonomy_version_id"],
        "taxonomy_sha256": source["tree_sha256"],
        "approved_snapshot": source,
        "taxonomy_reference": reference,
        "node_projections": projections,
        "hypothesis_keys": sorted(item["category_key"]
                                  for item in reference["hypothesis_links"]),
        "contexts": contexts,
    }
    result["bundle_sha256"] = fingerprint(result)
    return result


def build_c9_bundle(snapshot):
    """Build a pure transport bundle. NOT a publication authorization check."""
    return _assemble(snapshot)


def validate_c9_bundle(payload):
    """Fail closed on stale, partial, cross-project or poisoned transport data.

    Does NOT authorize database access: a matching self-signed hash is not
    evidence of human reviews in the owning PostgreSQL database.
    """
    record(payload, "approved_c9", required=_BUNDLE_FIELDS)
    if payload["schema_version"] != APPROVED_C9_VERSION:
        reject("approved_c9.schema_version", "unsupported C9 contract version",
               "unsupported_schema_version")
    pid = project_id(payload["project_id"], "approved_c9.project_id")
    pub = canonical_uuid(payload["publication_id"], "approved_c9.publication_id")
    run = canonical_uuid(payload["rtb_run_id"], "approved_c9.rtb_run_id")
    positive(payload["taxonomy_version"], "approved_c9.taxonomy_version")
    positive(payload["sre_taxonomy_version_id"], "approved_c9.sre_taxonomy_version_id")
    sha256_digest(payload["taxonomy_sha256"], "approved_c9.taxonomy_sha256")
    sha256_digest(payload["bundle_sha256"], "approved_c9.bundle_sha256")
    if not isinstance(payload["approved_snapshot"], dict):
        reject("approved_c9.approved_snapshot", "expected taxonomy snapshot")
    expected = _assemble(payload["approved_snapshot"])
    if (pid != expected["project_id"] or pub != expected["publication_id"] or
            run != expected["rtb_run_id"]):
        reject("approved_c9.identity", "project/run/publication changed",
               "snapshot_mismatch")
    if payload != expected:
        reject("approved_c9", "bundle differs from exact pinned C9 publication",
               "snapshot_mismatch")
    return expected


def load_approved_c9(project, publication, *, connect=None):
    """Authoritative C9 port. One S2 PostgreSQL snapshot, no provider calls.

    Caller supplies the explicitly approved publication UUID, NEVER a mutable
    'current taxonomy' workspace pointer. RTB H1/H2, deep publication status,
    human-reviewed fact lineage and all SRE mirrored categories are checked by
    the pre-existing S2 reader before returning anything.
    """
    from ..taxonomy_adapter import load_approved_taxonomy

    pid = project_id(project)
    pub = canonical_uuid(publication, "publication_id")
    authorized = load_approved_taxonomy(pid, pub, connect=connect)
    result = _assemble(authorized)
    return validate_c9_bundle(result)


__all__ = [
    "APPROVED_C9_VERSION", "build_c9_bundle", "validate_c9_bundle",
    "load_approved_c9",
]
