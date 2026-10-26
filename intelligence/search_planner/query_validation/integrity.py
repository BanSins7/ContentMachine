"""SP-08 conservative immutable Query Validation bundle/outbox integrity checks.

Verifier deliberately does not interpret model relevance as ground truth.
It checks *stored accounting and ancestry*: every source candidate decided
once, all selected candidates match selected decisions and hypothesis counts,
duplicates refer to a known source candidate and never to a different mode
via a fabricated selected row. Does not mutate original SQL views or v1 DTO.
"""
from __future__ import annotations

from collections import Counter

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.search_planner.contracts import publication_uuid

COVERAGE_STATES = {"selected", "no_source_candidates", "all_rejected"}
REASON_STATES = {
    "selected", "explicit_exclusion", "low_relevance", "negative_scope",
    "exact_duplicate", "semantic_duplicate", "quota",
}


def verify_validated_bundle(bundle, *, expected_selected_count=None,
                            expected_source_count=None):
    """Check accounting of a completed project-scoped qv_runs source snapshot."""
    if type(bundle) is not dict or bundle.get("schema_version") != "validated_queries/v1":
        raise PipelineError("Invalid validated query schema", 409)
    if bundle.get("status") != "completed":
        raise PipelineError("Validated query set is incomplete", 409)
    for field in ("generation_id", "validation_run_id"):
        publication_uuid(bundle.get(field))
    queries, coverage, decisions = (bundle.get("queries"), bundle.get("coverage"),
                                    bundle.get("decisions"))
    if not all(type(v) is list for v in (queries, coverage, decisions)):
        raise PipelineError("Incomplete validation accounting", 409)
    if len(queries) > 480 or len(decisions) > 480 or not 1 <= len(coverage) <= 48:
        raise PipelineError("Unbounded QV accounting", 409)
    if type(expected_selected_count) is not int and expected_selected_count is not None:
        raise PipelineError("Invalid expected selected count", 409)
    if type(expected_source_count) is not int and expected_source_count is not None:
        raise PipelineError("Invalid expected source count", 409)
    if ((expected_selected_count is not None and len(queries) != expected_selected_count) or
            (expected_source_count is not None and len(decisions) != expected_source_count)):
        raise PipelineError("QV stored counts do not match authoritative run", 409)

    by_id = {}
    for d in decisions:
        if type(d) is not dict:
            raise PipelineError("Malformed QV decision", 409)
        cid = publication_uuid(d.get("candidate_id"))
        reason, choice = d.get("reason_code"), d.get("decision")
        if cid in by_id or choice not in ("selected", "rejected") or reason not in REASON_STATES:
            raise PipelineError("Duplicate or invalid QV decision", 409)
        dup = d.get("duplicate_of")
        if dup is not None:
            publication_uuid(dup)
            if reason not in ("exact_duplicate", "semantic_duplicate") or dup == cid:
                raise PipelineError("Invalid QV duplicate link", 409)
        elif reason in ("exact_duplicate", "semantic_duplicate"):
            raise PipelineError("Missing duplicate target", 409)
        if (choice == "selected") != (reason == "selected" and dup is None):
            raise PipelineError("Inconsistent selected QV decision", 409)
        by_id[cid] = d

    chosen = {}
    by_hyp = Counter()
    ranks = set()
    for q in queries:
        if type(q) is not dict:
            raise PipelineError("Malformed selected QV query", 409)
        cid = publication_uuid(q.get("candidate_id"))
        hid = q.get("hypothesis_id")
        rank = q.get("rank_in_hypothesis")
        if (cid in chosen or cid not in by_id or
                by_id[cid]["decision"] != "selected" or
                type(hid) is not str or not hid or type(rank) is not int or
                not 1 <= rank <= 16 or (hid, rank) in ranks):
            raise PipelineError("Selected query has missing decision/rank", 409)
        chosen[cid] = q
        ranks.add((hid, rank))
        by_hyp[hid] += 1
    if set(chosen) != {cid for cid, d in by_id.items() if d["decision"] == "selected"}:
        raise PipelineError("Not every selected QV decision has a source query", 409)

    covered = set()
    source_total = 0
    selected_total = 0
    for c in coverage:
        if type(c) is not dict:
            raise PipelineError("Malformed QV coverage", 409)
        hid = c.get("hypothesis_id")
        source, selected = c.get("source_count"), c.get("selected_count")
        outcome, reason = c.get("outcome"), c.get("reason")
        if (type(hid) is not str or not hid or hid in covered or
                type(source) is not int or not 0 <= source <= 16 or
                type(selected) is not int or not 0 <= selected <= source or
                outcome not in COVERAGE_STATES or selected != by_hyp[hid]):
            raise PipelineError("QV coverage contradicts saved selection", 409)
        if (outcome == "selected" and (selected == 0 or reason is not None)) or (
                outcome == "no_source_candidates" and (source != 0 or selected != 0 or
                type(reason) is not str or not reason.strip())) or (
                outcome == "all_rejected" and (source == 0 or selected != 0 or
                type(reason) is not str or not reason.strip())):
            raise PipelineError("Inconsistent QV coverage outcome", 409)
        covered.add(hid)
        source_total += source
        selected_total += selected
    if (set(by_hyp) - covered or source_total != len(decisions) or
            selected_total != len(queries)):
        raise PipelineError("QV candidate/coverage accounting mismatch", 409)
    return bundle


def verify_outbox_event(event, *, expected_run_id=None):
    """No arbitrary browser JSON can be treated as a trusted QV event."""
    if type(event) is not dict or event.get("event_type") != "validated_queries_ready/v1":
        raise PipelineError("Untrusted QV outbox event type", 409)
    publication_uuid(event.get("outbox_id"))
    run_id = publication_uuid(event.get("validation_run_id"))
    if expected_run_id is not None and run_id != publication_uuid(expected_run_id):
        raise PipelineError("Outbox QV run mismatch", 409)
    pointer = event.get("payload_pointer")
    if (type(pointer) is not dict or
            pointer.get("schema_version") != "validated_queries/v1" or
            pointer.get("validation_run_id") != run_id or
            type(pointer.get("selected_count")) is not int or
            not 0 <= pointer["selected_count"] <= 480 or
            pointer.get("embedding_mode") not in ("semantic_local", "test_fixture")):
        raise PipelineError("Invalid QV outbox identity/payload", 409)
    publication_uuid(pointer.get("generation_id"))
    return event
