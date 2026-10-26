"""SP-10 server-only source of truth for held task previews.

QV decisions, C9 publications and source generations have immutable DB
semantics. Reads the complete verified QV bundle and shared links, then
rechecks source lineage and published C9 inside a scoped REPEATABLE READ
snapshot. This cannot be assembled from user-supplied accepted JSON.
"""
from __future__ import annotations

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid
from intelligence.search_planner.contracts import publication_uuid
from intelligence.search_planner.input_loader import read_published_snapshot
from intelligence.search_planner.query_validation.service import get_validated_bundle
from intelligence.search_planner.query_validation.linkage import get_selected_query_links
from intelligence.search_planner.query_validation.integrity import (
    verify_validated_bundle, verify_outbox_event,
)

INPUT_SCHEMA = "search-planner-verified-planning-input/v1"


def validate_planning_input(source, *, project_id, validation_run_id):
    pid, vid = _pid(project_id), publication_uuid(validation_run_id)
    if type(source) is not dict or set(source) != {
        "schema_version", "project_id", "validation_run_id",
        "generation_id", "taxonomy_publication_id", "taxonomy_version",
        "taxonomy_sha256", "generation_input_sha256", "outbox_id",
        "validated_bundle", "shared_links",
    }:
        raise PipelineError("Trusted planning input schema mismatch", 409)
    if (source["schema_version"] != INPUT_SCHEMA or
            source["project_id"] != pid or source["validation_run_id"] != vid):
        raise PipelineError("Foreign trusted planning input", 409)
    for k in ("generation_id", "taxonomy_publication_id", "outbox_id"):
        publication_uuid(source[k])
    if type(source["taxonomy_version"]) is not int or source["taxonomy_version"] <= 0:
        raise PipelineError("Invalid pinned C9 publication version", 409)
    for key in ("taxonomy_sha256", "generation_input_sha256"):
        v = source[key]
        if type(v) is not str or len(v) != 64 or any(ch not in "0123456789abcdef" for ch in v):
            raise PipelineError("Unpinned C9/generation SHA", 409)

    bundle, links = source["validated_bundle"], source["shared_links"]
    verify_validated_bundle(bundle)
    if (bundle["project_id"] != pid or bundle["validation_run_id"] != vid or
            bundle["generation_id"] != source["generation_id"] or
            bundle["embedding_mode"] != "semantic_local"):
        raise PipelineError("Planning requires project-scoped production QV", 409)
    if (type(links) is not dict or links.get("schema_version") !=
            "search-planner-query-hypothesis-links/v1" or
            links.get("project_id") != pid or
            links.get("validation_run_id") != vid or
            links.get("provider_tasks_dispatched") is not False or
            type(links.get("selected_count")) is not int or
            type(links.get("links")) is not list or
            type(links.get("links_sha256")) is not str or
            links["links_sha256"] != sha256(links["links"])):
        raise PipelineError("Untrusted QV shared link projection", 409)
    selected = {x["candidate_id"]: x for x in bundle["queries"]}
    if len(selected) != len(bundle["queries"]) or links["selected_count"] != len(selected):
        raise PipelineError("QV selected IDs/counts not matched", 409)
    linked = {}
    for group in links["links"]:
        if type(group) is not dict or set(group) != {
            "selected_candidate_id", "query_mode", "language",
            "text", "linked_sources",
        }:
            raise PipelineError("Malformed trusted shared query group", 409)
        cid = publication_uuid(group["selected_candidate_id"])
        if cid in linked or cid not in selected:
            raise PipelineError("Duplicated or foreign shared query ID", 409)
        q = selected[cid]
        if any(group[field] != q[origin] for field,origin in (
                ("query_mode","query_mode"),("language","language"),
                ("text","text"))):
            raise PipelineError("Shared query content differs from selected QV", 409)
        sources = group["linked_sources"]
        if type(sources) is not list or not sources:
            raise PipelineError("Missing QV hypothesis/fact source links", 409)
        seen = set()
        primary = False
        for ref in sources:
            if type(ref) is not dict or set(ref) != {
                "candidate_id","hypothesis_id","source_fact_ids"
            }:
                raise PipelineError("Malformed QV provenance source", 409)
            rid = publication_uuid(ref["candidate_id"])
            if rid in seen or type(ref["hypothesis_id"]) is not str or not ref["hypothesis_id"]:
                raise PipelineError("Duplicate QV provenance source", 409)
            facts = ref["source_fact_ids"]
            if type(facts) is not list or len(set(facts)) != len(facts) or any(
                    type(f) is not str or not f for f in facts):
                raise PipelineError("Invalid source facts", 409)
            seen.add(rid)
            if rid == cid:
                primary = (
                    ref["hypothesis_id"] == q["hypothesis_id"] and
                    facts == q["source_fact_ids"]
                )
        if not primary:
            raise PipelineError("Missing primary selected QV fact/hypothesis lineage", 409)
        linked[cid] = group
    if set(linked) != set(selected):
        raise PipelineError("Lost selected query provenance", 409)
    return source


def load_verified_planning_input(project_id, validation_run_id, *, connect=None,
                                 validated_reader=None, links_reader=None):
    """Read immutable QV evidence then source-check its C9+outbox lineage.

    The SQL lookup is one repeatable-read snapshot, including C9 reviews.
    QV evidence is read with existing independent immutable/SQL-verified
    projections and cannot be changed after creation. No Collector imports.
    """
    pid, vid = _pid(project_id), publication_uuid(validation_run_id)
    qreader = validated_reader if validated_reader is not None else get_validated_bundle
    lreader = links_reader if links_reader is not None else get_selected_query_links
    if not callable(qreader) or not callable(lreader):
        raise PipelineError("Invalid internal QV evidence reader", 422)
    bundle = qreader(pid, vid, include_test=False)
    links = lreader(pid, vid)
    factory = connect if connect is not None else _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = conn.execute(
                """SELECT r.id,r.generation_id,r.source_bundle_sha256,
                          r.embedding_mode,r.status,r.selected_count,
                          g.publication_id,g.taxonomy_version,g.taxonomy_sha256,
                          g.input_sha256,g.status,
                          o.id,o.event_type,o.payload_pointer,
                          sqo.delivered_at
                     FROM qv_runs r
                     JOIN sqg_generations g
                       ON g.id=r.generation_id AND g.project_id=r.project_id
                     JOIN qv_outbox o ON o.run_id=r.id
                     JOIN sqg_outbox sqo ON sqo.id=r.source_outbox_id
                                          AND sqo.generation_id=g.id
                    WHERE r.id=%s::uuid AND r.project_id=%s""",
                (vid, pid),
            ).fetchone()
            if row is None:
                raise PipelineError("Trusted QV run/outbox unavailable", 404)
            (rid, gid, source_sha, embedding_mode, qv_status, selected_count,
             pub, taxonomy_version, tax_sha, input_sha, generation_status,
             outbox, event_type, pointer, sqg_ack) = row
            if (str(rid) != vid or str(gid) != bundle["generation_id"] or
                    source_sha != bundle["source_bundle_sha256"] or
                    embedding_mode != "semantic_local" or qv_status != "completed" or
                    selected_count != len(bundle["queries"]) or
                    generation_status != "completed" or sqg_ack is None):
                raise PipelineError("QV/SQG source identity or completion mismatch", 409)
            verify_outbox_event({
                "outbox_id": str(outbox),
                "validation_run_id": vid,
                "event_type": event_type,
                "payload_pointer": pointer,
            }, expected_run_id=vid)
            if (pointer["generation_id"] != str(gid) or
                    pointer["selected_count"] != selected_count or
                    pointer["embedding_mode"] != embedding_mode):
                raise PipelineError("QV event pointer and source run disagree", 409)
            approved = read_published_snapshot(conn, pid, str(pub))
            if (approved["taxonomy_version"] != taxonomy_version or
                    approved["taxonomy_sha256"] != tax_sha):
                raise PipelineError("Generation C9 revision/SHA differs from reviewed tree", 409)
            result = {
                "schema_version": INPUT_SCHEMA,
                "project_id": pid, "validation_run_id": vid,
                "generation_id": str(gid),
                "taxonomy_publication_id": str(pub),
                "taxonomy_version": taxonomy_version,
                "taxonomy_sha256": tax_sha,
                "generation_input_sha256": input_sha,
                "outbox_id": str(outbox),
                "validated_bundle": bundle,
                "shared_links": links,
            }
    return validate_planning_input(result, project_id=pid,
                                   validation_run_id=vid)
