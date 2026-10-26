"""SP-11: complete and reversible QV -> Collector V1 projection, no execution.

Only the server's immutable production QV/C9 reader can authorize projection
input. These envelopes are held evidence, not Collector bookings, receipts,
provider tasks or source-processing approval. Persistence/atomic SP inbox is
owned exclusively by SP-12; do not ACK qv_outbox here.
"""
from __future__ import annotations

from copy import deepcopy
from re import compile as re_compile
from typing import Callable

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.taxonomy_builder.pipeline_repository import _pid
from intelligence.search_planner.contracts import publication_uuid
from intelligence.search_planner.task_planner.repository import (
    load_verified_planning_input, validate_planning_input,
)

HANDOFF_SCHEMA = "search-planner-handoff-projection/v1"
COLLECTOR_QUERY_SET_SCHEMA = "validated_query_set/v1"
CHUNK_SCHEMA = "search-planner-handoff-chunk/v1"
MAX_CHUNK_SIZE = 50
MAX_QUERIES = 480
VALIDATION_VERSION = "sp-qv-verified/v1"
NODE_RE = re_compile(r"[A-Za-z][A-Za-z0-9_.:-]{1,127}\Z")
LANGUAGE_RE = re_compile(r"[a-z]{2,3}(?:-[A-Z]{2})?\Z")
QUERY_FIELDS = frozenset({
    "query_id", "hypothesis_id", "direction_id", "mode",
    "text", "language", "validation_state",
})
SET_FIELDS = frozenset({
    "schema_version", "project_id", "taxonomy_publication_id",
    "taxonomy_version", "taxonomy_sha256", "generation_id",
    "validation_version", "queries",
})


def _only_keys(value, fields, label):
    if type(value) is not dict or set(value) != fields:
        raise PipelineError("Unexpected " + label + " fields", 409)
    return value


def _collector_query(selected, decisions):
    cid = publication_uuid(selected.get("candidate_id"))
    d = decisions.get(cid)
    if d is None or d.get("decision") != "selected" or d.get("reason_code") != "selected":
        raise PipelineError("QV selected candidate lacks authoritative selected decision", 409)
    hypothesis = selected.get("hypothesis_id")
    direction = selected.get("direction_id")
    mode = selected.get("query_mode")
    language = selected.get("language")
    text = selected.get("text")
    if (type(hypothesis) is not str or NODE_RE.fullmatch(hypothesis) is None or
            type(direction) is not str or NODE_RE.fullmatch(direction) is None or
            hypothesis == direction or type(mode) is not str or
            mode not in ("keyword", "hashtag") or type(language) is not str or
            LANGUAGE_RE.fullmatch(language) is None or type(text) is not str or
            not 1 <= len(text) <= 120 or
            text != " ".join(text.split()) or
            any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in text)):
        raise PipelineError("QV query cannot be projected losslessly to Collector V1", 409)
    return {
        "query_id": cid,
        "hypothesis_id": hypothesis,
        "direction_id": direction,
        "mode": mode,
        "text": text,
        "language": language,
        # This flag is derived ONLY from stored qv_decisions, never user JSON.
        "validation_state": "accepted",
    }


def _validate_ancestry(source):
    """Match every linked alias to an actual QV decision, not merely a UUID."""
    bundle = source["validated_bundle"]
    links = source["shared_links"]["links"]
    decisions = {}
    for item in bundle["decisions"]:
        cid = publication_uuid(item.get("candidate_id"))
        if cid in decisions:
            raise PipelineError("Duplicate QV decision ID", 409)
        decisions[cid] = item
    if len(decisions) != len(bundle["decisions"]):
        raise PipelineError("Missing QV decision evidence", 409)
    selected = {q["candidate_id"]: q for q in bundle["queries"]}
    if len(selected) != len(bundle["queries"]):
        raise PipelineError("Duplicate QV selected candidate", 409)
    provenance = {}
    seen_ancestry = set()
    for link in links:
        primary = publication_uuid(link["selected_candidate_id"])
        winner = selected[primary]
        refs = deepcopy(link["linked_sources"])
        local_seen = set()
        decision_links = []
        for ref in refs:
            rid = publication_uuid(ref.get("candidate_id"))
            if rid in seen_ancestry or rid in local_seen or rid not in decisions:
                raise PipelineError("Unverifiable or duplicate QV candidate lineage", 409)
            local_seen.add(rid)
            seen_ancestry.add(rid)
            dec = decisions[rid]
            if rid == primary:
                if (dec["decision"] != "selected" or
                        ref["hypothesis_id"] != winner["hypothesis_id"] or
                        ref["source_fact_ids"] != winner["source_fact_ids"]):
                    raise PipelineError("QV primary query/hypothesis/fact mismatch", 409)
            elif (dec["decision"] != "rejected" or
                  dec["reason_code"] not in ("exact_duplicate", "semantic_duplicate") or
                  dec["duplicate_of"] != primary):
                raise PipelineError("Forged cross-hypothesis alias to selected query", 409)
            decision_links.append(deepcopy(dec))
        provenance[primary] = {
            "selected_candidate": deepcopy(winner),
            "selected_decision": deepcopy(decisions[primary]),
            "linked_sources": sorted(refs, key=lambda r: r["candidate_id"]),
            "linked_qv_decisions": sorted(
                decision_links, key=lambda d: d["candidate_id"],
            ),
        }
    # Each selected candidate must have exactly one primary provenance group.
    if set(provenance) != set(selected):
        raise PipelineError("Missing selected QV ancestry in handoff", 409)
    return decisions, provenance


def project_verified_query_handoff(source: dict, *, chunk_size: int = MAX_CHUNK_SIZE) -> dict:
    """Deterministic held-only projection from a server-verified QV source.

    The caller MUST use load_verified_planning_input (or trusted internal port)
    to obtain source; do not call with a browser-built validation state.
    """
    if type(chunk_size) is not int or not 1 <= chunk_size <= MAX_CHUNK_SIZE:
        raise PipelineError("Invalid versioned Collector handoff chunk size", 422)
    if type(source) is not dict:
        raise PipelineError("Missing DB-verified QV source", 409)
    pid, vid = _pid(source.get("project_id")), publication_uuid(
        source.get("validation_run_id")
    )
    trusted = validate_planning_input(
        source, project_id=pid, validation_run_id=vid,
    )
    decisions, provenance = _validate_ancestry(trusted)
    bundle = trusted["validated_bundle"]
    selected = sorted(
        bundle["queries"],
        key=lambda q: (
            q["hypothesis_id"], q["rank_in_hypothesis"], q["candidate_id"],
        ),
    )
    if len(selected) > MAX_QUERIES or len(selected) != len(provenance):
        raise PipelineError("Unbounded or incomplete QV selection", 409)
    accepted = [_collector_query(row, decisions) for row in selected]
    ordered_ids = [q["query_id"] for q in accepted]
    if len(ordered_ids) != len(set(ordered_ids)):
        raise PipelineError("Duplicated projected QV query ID", 409)
    source_sha = sha256(bundle)
    full_links_sha = trusted["shared_links"]["links_sha256"]
    total = len(accepted)
    count = (total + chunk_size - 1) // chunk_size
    chunks = []
    for ix in range(count):
        begin = ix * chunk_size
        end = min(total, begin + chunk_size)
        rows = accepted[begin:end]
        ids = ordered_ids[begin:end]
        # Collector's DTO is exact, with query_id == original candidate_id.
        collector_set = {
            "schema_version": COLLECTOR_QUERY_SET_SCHEMA,
            "project_id": pid,
            "taxonomy_publication_id": trusted["taxonomy_publication_id"],
            "taxonomy_version": trusted["taxonomy_version"],
            "taxonomy_sha256": trusted["taxonomy_sha256"],
            "generation_id": trusted["generation_id"],
            "validation_version": VALIDATION_VERSION,
            "queries": deepcopy(rows),
        }
        chunk = {
            "schema_version": CHUNK_SCHEMA,
            "chunk_index": ix,
            "chunk_count": count,
            "chunk_size": len(rows),
            "total_selected_count": total,
            "query_ids": list(ids),
            "collector_query_set": collector_set,
            "collector_query_set_sha256": sha256(collector_set),
            "source_provenance": [
                {
                    "query_id": cid,
                    **deepcopy(provenance[cid]),
                }
                for cid in ids
            ],
        }
        chunk["chunk_sha256"] = sha256(chunk)
        chunks.append(chunk)

    coverage = deepcopy(bundle["coverage"])
    result = {
        "schema_version": HANDOFF_SCHEMA,
        "status": "no_work" if not total else "awaiting_sp_inbox",
        "project_id": pid,
        "validation_run_id": vid,
        "qv_outbox_id": trusted["outbox_id"],
        "generation_id": trusted["generation_id"],
        "taxonomy_publication_id": trusted["taxonomy_publication_id"],
        "taxonomy_version": trusted["taxonomy_version"],
        "taxonomy_sha256": trusted["taxonomy_sha256"],
        "generation_input_sha256": trusted["generation_input_sha256"],
        "qv_config_sha256": bundle["config_sha256"],
        "qv_source_bundle_sha256": bundle["source_bundle_sha256"],
        "embedding_model_key": bundle["embedding_model_key"],
        "validated_bundle_sha256": source_sha,
        "linked_provenance_sha256": full_links_sha,
        "coverage": coverage,
        "coverage_sha256": sha256(coverage),
        "source_candidate_decision_count": len(bundle["decisions"]),
        "selected_query_count": total,
        "selected_query_ids": ordered_ids,
        "chunk_capacity": chunk_size,
        "chunk_count": count,
        "chunks": chunks,
        "no_work_reason": (
            "No selected production QV queries; coverage retained"
            if total == 0 else None
        ),
        "qv_outbox_acknowledged_by_this_projection": False,
        "collector_receipt": None,
        "external_dispatch_authorized": False,
        "provider_calls_in_this_projection": 0,
    }
    result["projection_sha256"] = sha256(result)
    return result


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


class VerifiedHandoffProjector:
    """Only project/run identifiers reach the server-trusted QV loader."""

    def __init__(self, *, planning_reader: Callable = load_verified_planning_input):
        if not callable(planning_reader):
            raise PipelineError("Invalid trusted SP11 QV planning reader", 422)
        self._reader = planning_reader

    def preview(self, project_id: str, validation_run_id: str, *,
                chunk_size: int = MAX_CHUNK_SIZE) -> dict:
        pid = _pid(project_id)
        vid = publication_uuid(validation_run_id)
        source = self._reader(pid, vid)
        envelope = project_verified_query_handoff(source, chunk_size=chunk_size)
        return verify_handoff_projection(envelope, trusted_source=source)
