"""Trusted RTB Deep V2 -> Search Planner context adapter.

Only the RTB publication repository can provide the source DTO. A published
export is verified before any projection. No 14->11 downgrade, fake B9 facts,
automatic payment, web requests, or implicit owner approval.
"""
from __future__ import annotations

from copy import deepcopy

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.taxonomy_builder.pipeline_repository import _pid, _connect
from intelligence.taxonomy_builder.publication_export_v2 import (
    SCHEMA_V2, RTBDeepV2PublicationExportRepository, validate_publication_export,
)
from intelligence.taxonomy_builder.research_contracts_v2 import project_v2_tree_to_v1_core
from intelligence.taxonomy_builder.deep_v2_http import _actor
from intelligence.search_planner.context_builder import build_hypothesis_contexts
from intelligence.search_planner.contracts import (
    publication_uuid, validate_published_snapshot, validate_search_policy,
)


SOURCE_V1 = "deep_c9_v1"
SOURCE_V2 = "rtb_deep_v2"


def trusted_v2_export(project_id, publication_id, *, accepted_versions):
    """RTB-owned re-play of SQL approvals and original B9/A provenance.

    Identity is pinned to CM_RTB_REVIEWER_ID on the server, never from HTTP.
    The RTB repository enforces independent project/reviewer authorization.
    """
    if accepted_versions != [SCHEMA_V2]:
        raise PipelineError("RTB V2 requires exact export version", 422)
    return RTBDeepV2PublicationExportRepository().export(
        _pid(project_id), publication_uuid(publication_id), _actor(),
        accepted_versions=[SCHEMA_V2],
    )


def verified_v2_export(project_id, publication_id, *, supplier=None):
    pid, pub = _pid(project_id), publication_uuid(publication_id)
    reader = trusted_v2_export if supplier is None else supplier
    if not callable(reader):
        raise PipelineError("RTB verified publication reader unavailable", 503)
    # The exporter checks the authoritative DB and server reviewer. Never
    # accept an HTTP-supplied object or browser-selected reviewer identity.
    result = validate_publication_export(
        reader(pid, pub, accepted_versions=[SCHEMA_V2]),
        expected_version=SCHEMA_V2,
    )
    if result["project_id"] != pid or result["publication_id"] != pub:
        raise PipelineError("Foreign RTB Deep V2 export", 409)
    return result


def build_v2_contexts(export, search_policy):
    """Full immutable V2 semantics, original A observations and B9 fact lineage.

    A V1-shaped *temporary* view is used solely for deterministic ancestry
    calculations; all persisted prompt contexts carry full V2 nodes/fields,
    signed export hash and source lineage. Never write a fake V1 publication.
    """
    data = validate_publication_export(export, expected_version=SCHEMA_V2)
    policy = validate_search_policy(search_policy)
    nodes = {node["id"]: node for node in data["nodes"]}
    core = project_v2_tree_to_v1_core(data["nodes"])
    # Internal deterministic ancestor helper. Its approval attributes come
    # only from the authenticated and validated V2 RTB export above.
    snapshot = validate_published_snapshot({
        "project_id": data["project_id"],
        "rtb_run_id": data["run_id"],
        "taxonomy_publication_id": data["publication_id"],
        "taxonomy_version": data["sre_taxonomy_version_id"],
        "tree": core,
        "approved_facts": [{
            "id": f["id"], "kind": f["kind"], "text": f["text"],
            "source_quote": f["source_quote"], "origin": f["origin"],
        } for f in data["approved_facts"]],
        "profile_snapshot": deepcopy(data["profile_snapshot"]),
        "run_status": "approved", "deep_status": "approved",
        "human_reviews": [1, 2],
    })
    original = {o["observation_id"]: o for o in data["original_a_observations"]}
    facts = {f["id"]: f for f in data["approved_facts"]}
    contexts = build_hypothesis_contexts(snapshot, policy)
    for context in contexts:
        path = [nodes[nid] for nid in context["ancestry_path"]]
        ids = sorted(set(oid for node in path for oid in node["source_observation_ids"]))
        if not set(ids).issubset(original):
            raise PipelineError("RTB original A evidence missing", 409)
        context["context_schema_version"] = "hypothesis-context/v2"
        context["taxonomy_sha256"] = data["tree_sha256"]
        for key in ("direction", "branch", "hypothesis"):
            if context[key] is not None:
                context[key] = deepcopy(nodes[context[key]["id"]])
        for neighbor in context["neighbor_hypotheses"]:
            peer = nodes[neighbor["id"]]
            for field in ("source_observation_ids", "search_signals", "validation_focus"):
                neighbor[field] = deepcopy(peer[field])
        context["rtb_deep_v2_provenance"] = {
            "publication_id": data["publication_id"],
            "run_id": data["run_id"],
            "export_sha256": data["export_sha256"],
            "v1_core_sha256": data["v1_core_sha256"],
            "source_lineage": deepcopy(data["source_lineage"]),
            "sre_taxonomy_version_id": data["sre_taxonomy_version_id"],
            "source_fact_lineage": [
                deepcopy(facts[fid]) for fid in sorted(
                    set(fid for node in path for fid in node["source_fact_ids"])
                )
            ],
            "original_a_observations": [deepcopy(original[oid]) for oid in ids],
            "original_a_observations_are_not_approved_product_facts": True,
        }
    if not contexts:
        raise PipelineError("RTB Deep V2 has no search hypotheses", 409)
    source = {
        "project_id": data["project_id"],
        "taxonomy_publication_id": data["publication_id"],
        "taxonomy_version": data["sre_taxonomy_version_id"],
        "taxonomy_sha256": data["tree_sha256"],
        "rtb_export_sha256": data["export_sha256"],
        "rtb_run_id": data["run_id"],
        "publication_source": SOURCE_V2,
    }
    return source, contexts


def list_v2_publications(project_id, *, limit=30, connect=None, supplier=None):
    """Discover published V2, independently verify every returned export."""
    pid = _pid(project_id)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise PipelineError("Invalid RTB Deep V2 list size", 422)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            rows = conn.execute(
                """SELECT p.id, p.published_at
                   FROM tb_deep_v2_publications p
                   JOIN tb_deep_v2_runs r ON r.id=p.run_id AND r.project_id=p.project_id
                  WHERE p.project_id=%s AND r.status='published'
                  ORDER BY p.published_at DESC, p.id LIMIT %s""",
                (pid, limit),
            ).fetchall()
    entries = []
    for pub, published_at in rows:
        verified = verified_v2_export(pid, str(pub), supplier=supplier)
        entries.append({
            "taxonomy_publication_id": verified["publication_id"],
            "taxonomy_version": verified["sre_taxonomy_version_id"],
            "taxonomy_sha256": verified["tree_sha256"],
            "rtb_export_sha256": verified["export_sha256"],
            "approved_hypothesis_count": sum(
                x["kind"] == "hypothesis" for x in verified["nodes"]
            ),
            "approved_at": published_at.isoformat() if published_at is not None else None,
            "source_status": "published_reviewed_rtb_deep_v2",
            "publication_source": SOURCE_V2,
            "rtb_export_schema_version": SCHEMA_V2,
        })
    return {
        "schema_version": "search-planner-approved-publications-v2/v1",
        "project_id": pid, "publications": entries, "count": len(entries),
        "provider_requests_in_this_read": 0,
    }
