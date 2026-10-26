"""SP-07 durable reconstruction of cross-hypothesis query identity.

Reads existing *immutable* qv_decisions and sqg_candidates rows; it does not
write a new relation or redefine validated_queries/v1. The resulting
versioned projection is for the future SP10 planning port, NOT an executable
provider task or Collector receipt.
"""
from __future__ import annotations

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid
from intelligence.search_planner.contracts import publication_uuid
from .selection import normalized

LINK_SCHEMA = "search-planner-query-hypothesis-links/v1"


def build_selected_query_links(rows, *, project_id, validation_run_id,
                               selected_count):
    """Reconstruct linked candidate/hypothesis/fact records without data loss.

    Row: candidate_id, hypothesis_id, decision, reason_code, duplicate_of,
    source_fact_ids, query_mode, language, text.
    The selected winner MUST remain selected. An alias to a candidate later
    quota-rejected is ignored (it is not an executable selected task).
    """
    pid, vid = _pid(project_id), publication_uuid(validation_run_id)
    if type(selected_count) is not int or not 0 <= selected_count <= 480:
        raise PipelineError("Invalid validated selected query count", 409)
    if type(rows) not in (list, tuple) or len(rows) > 480:
        raise PipelineError("Invalid persisted query/decision source", 409)
    decisions = {}
    for item in rows:
        if type(item) not in (tuple, list) or len(item) != 9:
            raise PipelineError("Malformed persisted QV decision projection", 409)
        cid = publication_uuid(str(item[0]))
        hyp, decision, reason, duplicate = item[1:5]
        facts, mode, language, raw_text = item[5:]
        if cid in decisions or type(hyp) is not str or not hyp:
            raise PipelineError("Duplicate or unscoped QV candidate", 409)
        if (decision not in ("selected", "rejected") or
                type(reason) is not str or
                type(facts) is not list or
                any(type(f) is not str or not f for f in facts) or
                len(set(facts)) != len(facts) or
                mode not in ("keyword", "hashtag") or
                type(language) is not str or not language or
                type(raw_text) is not str or not raw_text.strip()):
            raise PipelineError("Untrusted QV candidate lineage or locale", 409)
        duplicate_id = publication_uuid(str(duplicate)) if duplicate is not None else None
        if (decision == "selected" and
                (duplicate_id is not None or reason != "selected")):
            raise PipelineError("Invalid selected query decision", 409)
        if duplicate_id is not None and reason not in ("exact_duplicate", "semantic_duplicate"):
            raise PipelineError("Unjustified QV duplicate lineage", 409)
        decisions[cid] = {
            "candidate_id": cid, "hypothesis_id": hyp,
            "decision": decision, "reason_code": reason,
            "duplicate_of": duplicate_id, "source_fact_ids": list(facts),
            "query_mode": mode, "language": language, "text": raw_text,
        }

    selected = {
        k: v for k, v in decisions.items() if v["decision"] == "selected"
    }
    if len(selected) != selected_count:
        raise PipelineError("Persisted selected count does not match QV run", 409)

    groups = {
        cid: [{
            "candidate_id": cid,
            "hypothesis_id": row["hypothesis_id"],
            "source_fact_ids": list(row["source_fact_ids"]),
        }] for cid, row in selected.items()
    }
    for cid, alias in sorted(decisions.items()):
        target = groups.get(alias["duplicate_of"])
        if target is None:
            continue
        winner = selected[alias["duplicate_of"]]
        if alias["query_mode"] != winner["query_mode"] or alias["language"] != winner["language"]:
            raise PipelineError("Cross-mode or cross-language query alias detected", 409)
        if alias["reason_code"] == "exact_duplicate" and normalized(alias["text"]) != normalized(winner["text"]):
            raise PipelineError("Stored exact duplicate is not text equivalent", 409)
        target.append({
            "candidate_id": cid,
            "hypothesis_id": alias["hypothesis_id"],
            "source_fact_ids": list(alias["source_fact_ids"]),
        })
    links = []
    for cid, winner in sorted(selected.items()):
        links.append({
            "selected_candidate_id": cid,
            "query_mode": winner["query_mode"],
            "language": winner["language"],
            "text": winner["text"],
            "linked_sources": sorted(groups[cid], key=lambda x: x["candidate_id"]),
        })
    return {
        "schema_version": LINK_SCHEMA,
        "project_id": pid,
        "validation_run_id": vid,
        "selected_count": len(links),
        "links_sha256": sha256(links),
        "links": links,
        "provider_tasks_dispatched": False,
    }


def get_selected_query_links(project_id, validation_run_id, *, connect=None):
    """Read-only PostgreSQL source truth, without changing QV v1 wire format."""
    pid, vid = _pid(project_id), publication_uuid(validation_run_id)
    factory = connect if connect is not None else _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            run = conn.execute(
                """SELECT selected_count,embedding_mode
                   FROM qv_runs
                   WHERE id=%s::uuid AND project_id=%s""",
                (vid, pid),
            ).fetchone()
            if run is None:
                raise PipelineError("Validated query run not found", 404)
            if run[1] != "semantic_local":
                raise PipelineError("Fixture QV runs cannot produce production query links", 409)
            rows = conn.execute(
                """SELECT d.candidate_id,d.hypothesis_id,d.decision,
                          d.reason_code,d.duplicate_of,c.source_fact_ids,
                          c.query_mode,c.language,c.text
                   FROM qv_decisions d
                   JOIN qv_runs r ON r.id=d.run_id
                   JOIN sqg_candidates c ON c.generation_id=d.generation_id
                                         AND c.id=d.candidate_id
                   WHERE d.run_id=%s::uuid AND r.project_id=%s
                   ORDER BY d.candidate_id""",
                (vid, pid),
            ).fetchall()
    return build_selected_query_links(
        rows, project_id=pid, validation_run_id=vid, selected_count=run[0],
    )
