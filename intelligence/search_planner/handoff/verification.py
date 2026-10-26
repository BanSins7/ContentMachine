"""SP-11 verifiable chunked QV handoff integrity and DB-source comparison.

Self-hashes are useful for corruption detection but never prove acceptance.
Production reprojects authoritative immutable QV data and requires exact
semantic equality of every chunk, alias and Collector-shaped query.
"""
from __future__ import annotations

from copy import deepcopy
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.search_planner.contracts import publication_uuid
from .contracts import (
    HANDOFF_SCHEMA, COLLECTOR_QUERY_SET_SCHEMA, CHUNK_SCHEMA,
    MAX_QUERIES, MAX_CHUNK_SIZE, VALIDATION_VERSION, QUERY_FIELDS, SET_FIELDS,
)
from .projection import _only_keys, project_verified_query_handoff


def verify_handoff_projection(envelope: dict, *, trusted_source: dict | None = None) -> dict:
    """Validate all cross-chunk counts, IDs, hashes, provenance and contract.

    A self-hashed object is not an authority. Passing trusted_source performs
    exact byte-semantic comparison with DB-verified inputs; only that stronger
    check is acceptable before SP12 inserts an immutable inbox snapshot.
    """
    if type(envelope) is not dict or envelope.get("schema_version") != HANDOFF_SCHEMA:
        raise PipelineError("Invalid SP11 projection schema", 409)
    digest = envelope.get("projection_sha256")
    if type(digest) is not str or sha256({
        k: v for k, v in envelope.items() if k != "projection_sha256"
    }) != digest:
        raise PipelineError("Handoff projection digest mismatch", 409)
    total = envelope.get("selected_query_count")
    cap = envelope.get("chunk_capacity")
    chunks = envelope.get("chunks")
    count = envelope.get("chunk_count")
    if (type(total) is not int or not 0 <= total <= MAX_QUERIES or
            type(cap) is not int or not 1 <= cap <= MAX_CHUNK_SIZE or
            type(count) is not int or count != (total + cap - 1) // cap or
            type(chunks) is not list or len(chunks) != count or
            type(envelope.get("selected_query_ids")) is not list or
            envelope.get("external_dispatch_authorized") is not False or
            envelope.get("qv_outbox_acknowledged_by_this_projection") is not False or
            envelope.get("collector_receipt") is not None or
            envelope.get("provider_calls_in_this_projection") != 0):
        raise PipelineError("Untrusted handoff count or execution state", 409)
    if envelope.get("status") != ("no_work" if total == 0 else "awaiting_sp_inbox"):
        raise PipelineError("Invalid QV projection status", 409)
    if (total == 0 and (type(envelope.get("no_work_reason")) is not str or
                        not envelope["no_work_reason"].strip())) or (
            total > 0 and envelope.get("no_work_reason") is not None):
        raise PipelineError("Invalid QV zero-work reason", 409)
    all_ids = []
    for i, chunk in enumerate(chunks):
        if type(chunk) is not dict:
            raise PipelineError("Malformed handoff chunk", 409)
        digest = chunk.get("chunk_sha256")
        if type(digest) is not str or sha256({
            k:v for k,v in chunk.items() if k != "chunk_sha256"
        }) != digest:
            raise PipelineError("Corrupted handoff chunk", 409)
        size = chunk.get("chunk_size")
        if (chunk.get("schema_version") != CHUNK_SCHEMA or
                chunk.get("chunk_index") != i or chunk.get("chunk_count") != count or
                chunk.get("total_selected_count") != total or
                type(size) is not int or
                size != min(cap, total - i * cap) or
                type(chunk.get("query_ids")) is not list or
                len(chunk["query_ids"]) != size or
                type(chunk.get("source_provenance")) is not list or
                len(chunk["source_provenance"]) != size):
            raise PipelineError("Invalid handoff chunk boundaries", 409)
        col = _only_keys(chunk.get("collector_query_set"), SET_FIELDS,
                         "Collector QuerySet")
        if (col["schema_version"] != COLLECTOR_QUERY_SET_SCHEMA or
                col["project_id"] != envelope.get("project_id") or
                col["taxonomy_publication_id"] != envelope.get("taxonomy_publication_id") or
                col["taxonomy_version"] != envelope.get("taxonomy_version") or
                col["taxonomy_sha256"] != envelope.get("taxonomy_sha256") or
                col["generation_id"] != envelope.get("generation_id") or
                col["validation_version"] != VALIDATION_VERSION or
                type(col["queries"]) is not list or len(col["queries"]) != size or
                chunk.get("collector_query_set_sha256") != sha256(col)):
            raise PipelineError("Invalid Collector V1 handoff query set", 409)
        ids = []
        for q in col["queries"]:
            _only_keys(q, QUERY_FIELDS, "Collector query")
            if q.get("validation_state") != "accepted":
                raise PipelineError("Unselected QV query in handoff", 409)
            ids.append(publication_uuid(q.get("query_id")))
        if ids != chunk["query_ids"] or ids != [
            item.get("query_id") for item in chunk["source_provenance"]
        ]:
            raise PipelineError("Handoff lost source query ID/order", 409)
        for cid, query, evidence in zip(
                ids, col["queries"], chunk["source_provenance"]):
            selected = evidence.get("selected_candidate")
            decision = evidence.get("selected_decision")
            refs = evidence.get("linked_sources")
            related = evidence.get("linked_qv_decisions")
            if (type(selected) is not dict or
                    selected.get("candidate_id") != cid or
                    selected.get("text") != query["text"] or
                    selected.get("query_mode") != query["mode"] or
                    selected.get("language") != query["language"] or
                    selected.get("hypothesis_id") != query["hypothesis_id"] or
                    selected.get("direction_id") != query["direction_id"] or
                    type(decision) is not dict or
                    decision.get("candidate_id") != cid or
                    decision.get("decision") != "selected" or
                    type(refs) is not list or not refs or
                    type(related) is not list or not related or
                    [r.get("candidate_id") for r in refs
                     if type(r) is dict] !=
                    [d.get("candidate_id") for d in related
                     if type(d) is dict] or
                    cid not in {r.get("candidate_id") for r in refs
                                if type(r) is dict}):
                raise PipelineError("Inconsistent QV origin or alias evidence", 409)
        all_ids.extend(ids)
    # Zero-work must be truthful: it has no Collector QuerySet, but keeps
    # the entire original QV hypothesis coverage and zero selected count.
    coverage = envelope.get("coverage")
    if type(coverage) is not list or not coverage:
        raise PipelineError("Missing original QV hypothesis coverage", 409)
    if total == 0 and any(
            type(row) is not dict or row.get("selected_count") != 0
            for row in coverage):
        raise PipelineError("False zero-work handoff with selected hypotheses", 409)
    if (type(envelope.get("source_candidate_decision_count")) is not int or
            not 0 <= envelope["source_candidate_decision_count"] <= MAX_QUERIES):
        raise PipelineError("Missing QV source decision accounting", 409)
    seen_hypotheses = set()
    source_count = 0
    selected_count = 0
    for item in coverage:
        if (type(item) is not dict or
                type(item.get("hypothesis_id")) is not str or
                not item["hypothesis_id"] or
                item["hypothesis_id"] in seen_hypotheses or
                type(item.get("source_count")) is not int or
                type(item.get("selected_count")) is not int or
                not 0 <= item["selected_count"] <= item["source_count"] <= 16):
            raise PipelineError("Corrupt handoff QV hypothesis coverage", 409)
        seen_hypotheses.add(item["hypothesis_id"])
        source_count += item["source_count"]
        selected_count += item["selected_count"]
    if (selected_count != total or
            source_count != envelope["source_candidate_decision_count"]):
        raise PipelineError("Handoff coverage source/selected totals diverge", 409)
    if (all_ids != envelope["selected_query_ids"] or
            len(set(all_ids)) != total or
            envelope.get("coverage_sha256") != sha256(envelope.get("coverage"))):
        raise PipelineError("Incomplete handoff selection or coverage", 409)
    if trusted_source is not None:
        expected = project_verified_query_handoff(
            trusted_source, chunk_size=cap,
        )
        if expected != envelope:
            raise PipelineError("Handoff content differs from immutable QV/C9", 409)
    return deepcopy(envelope)
