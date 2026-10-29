"""Non-persisting A/B experiment: identical immutable SP4 queries, Jina v3.

A: original text -> Jina retrieval.query
B: LLM additional_info -> original + additional_info -> SAME Jina retrieval.query
Both rank against unchanged RTB references embedded via retrieval.passage.

No local model, no fake production vectors, no source dispatch, no writes to
sqg/qv/Collector and no generation of new SP4 candidates.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid
from intelligence.search_planner.contracts import publication_uuid
from intelligence.search_planner.generation_engine.output_manager import get_candidates_bundle
from .contracts import validate_policy
from .embedding import encode_checked
from .selection import cosine, select_queries
from .jina_remote import JinaV3RemoteEmbeddings, JinaLLMQueryExpander

SCHEMA = "jina-query-expansion-ab/v1"


def _sample(bundle, contexts, limit):
    """Stable fair round robin so one hypothesis cannot use all 20 positions."""
    if type(limit) is not int or not 1 <= limit <= 20:
        raise PipelineError("Jina A/B candidates limit must be 1..20", 422)
    original = bundle["candidates"]
    by_hyp = defaultdict(list)
    for c in sorted(original, key=lambda item: item["candidate_id"]):
        by_hyp[c["hypothesis_id"]].append(c)
    sampled = []
    while len(sampled) < limit and any(by_hyp.values()):
        for hid in sorted(by_hyp):
            if by_hyp[hid] and len(sampled) < limit:
                sampled.append(by_hyp[hid].pop(0))
    chosen = Counter(c["hypothesis_id"] for c in sampled)
    snapshot = deepcopy(bundle)
    snapshot["candidates"] = sampled
    snapshot["hypothesis_coverage"] = []
    for context in sorted(contexts, key=lambda c: c["hypothesis"]["id"]):
        hid = context["hypothesis"]["id"]
        n = chosen[hid]
        snapshot["hypothesis_coverage"].append({
            "hypothesis_id": hid,
            "candidate_count": n,
            "state": "generated" if n else "no_viable_queries",
            "reason": None if n else "Excluded by explicit bounded A/B sampling",
        })
    return snapshot


def compare_jina_bundle(bundle, contexts, policy, *, expander,
                        query_embeddings, passage_embeddings,
                        candidate_limit=20, expansion_words=100):
    """Pure coordination over injected real adapters (fake adapters allowed in QA).

    Unlike ordinary production QV, this evaluates two arms independently
    and never creates a QV run/outbox. Values are embedding-space proxies.
    """
    cfg = validate_policy(policy)
    if not isinstance(expander, JinaLLMQueryExpander):
        raise PipelineError("Jina A/B requires a genuine LLM expansion adapter", 422)
    if (not isinstance(query_embeddings, JinaV3RemoteEmbeddings) or
        not isinstance(passage_embeddings, JinaV3RemoteEmbeddings) or
        query_embeddings.task != "retrieval.query" or
        passage_embeddings.task != "retrieval.passage"):
        raise PipelineError("Jina A/B requires hosted Jina v3 asymmetric adapters", 422)
    sampled = _sample(bundle, contexts, candidate_limit)
    candidates = sampled["candidates"]
    if not candidates:
        raise PipelineError("No source queries to compare", 409)

    # First arm is original query text only. There is no LLM in SP4.
    a = select_queries(sampled, contexts, cfg, query_embeddings,
                       reference_embeddings=passage_embeddings)

    queries = {c["candidate_id"]: c["text"] for c in candidates}
    expansions = expander.expand(queries, words=expansion_words)
    if set(expansions) != set(queries):
        raise PipelineError("LLM omitted original query IDs", 409)
    overrides = {cid: text + " " + expansions[cid] for cid, text in queries.items()}
    b = select_queries(sampled, contexts, cfg, query_embeddings,
                       candidate_text_overrides=overrides,
                       reference_embeddings=passage_embeddings)

    # Comparable *query-side* vectors: same v3 LoRA, only input text differs.
    ids = sorted(queries)
    raw_vectors = encode_checked(query_embeddings, [queries[cid] for cid in ids])
    expanded_vectors = encode_checked(query_embeddings, [overrides[cid] for cid in ids])
    drift = {cid: round(1 - cosine(u, v), 8)
             for cid, u, v in zip(ids, raw_vectors, expanded_vectors)}
    decisions_a = {d["candidate_id"]: d for d in a["decisions"]}
    decisions_b = {d["candidate_id"]: d for d in b["decisions"]}
    selected_a = set(a["selected_candidate_ids"])
    selected_b = set(b["selected_candidate_ids"])
    per_query = []
    for cid in ids:
        initial, changed = decisions_a[cid], decisions_b[cid]
        per_query.append({
            "candidate_id": cid,
            "hypothesis_id": next(c["hypothesis_id"] for c in candidates
                                  if c["candidate_id"] == cid),
            "original_query": queries[cid],
            "additional_info": expansions[cid],
            "expanded_text": overrides[cid],
            "semantic_drift_one_minus_cosine": drift[cid],
            "baseline": {
                "decision": initial["decision"],
                "reason_code": initial["reason_code"],
                "positive_similarity": initial["positive_similarity"],
                "negative_similarity": initial["negative_similarity"],
                "mmr_score": initial["mmr_score"],
            },
            "expanded": {
                "decision": changed["decision"],
                "reason_code": changed["reason_code"],
                "positive_similarity": changed["positive_similarity"],
                "negative_similarity": changed["negative_similarity"],
                "mmr_score": changed["mmr_score"],
            },
        })
    return {
        "schema_version": SCHEMA,
        "experiment_only": True,
        "production_qv_mutated": False,
        "collector_queries_dispatched": 0,
        "precision_or_video_recall_measured": False,
        "source_candidate_count": len(bundle["candidates"]),
        "compared_candidate_count": len(candidates),
        "sampling": "deterministic_fair_round_robin_by_hypothesis_uuid",
        "source_bundle_sha256": sha256(bundle),
        "hypothesis_contexts_sha256": sha256(contexts),
        "policy_sha256": sha256(cfg),
        "embedding_model": "jina-embeddings-v3",
        "embedding_query_task": query_embeddings.task,
        "embedding_reference_task": passage_embeddings.task,
        "expansion_llm": expander.model,
        "expansion_words_requested": expansion_words,
        "baseline_selected_count": len(selected_a),
        "expanded_selected_count": len(selected_b),
        "unchanged_selected_count": len(selected_a & selected_b),
        "newly_selected_ids": sorted(selected_b - selected_a),
        "newly_rejected_ids": sorted(selected_a - selected_b),
        "mean_semantic_drift": round(sum(drift.values()) / len(drift), 8),
        "baseline_reason_counts": a["reason_counts"],
        "expanded_reason_counts": b["reason_counts"],
        "per_query": per_query,
    }


def compare_generation_jina(project_id, generation_id, *, policy,
                            llm_model, candidate_limit=20, expansion_words=100,
                            explicit_paid_experiment=False, connect=None):
    """Trusted operator action: bounded external billing; no saved experiments.

    Endpoint must verify local owner token, environment enable and separate
    explicit confirmation. It does not grant QV production or paid SP4 rights.
    """
    if explicit_paid_experiment is not True:
        raise PipelineError("Explicit external-cost experiment approval required", 403)
    import os
    if not os.environ.get("OPENROUTER_API_KEY") or not os.environ.get("JINA_API_KEY"):
        raise PipelineError("OPENROUTER_API_KEY and JINA_API_KEY required", 503)
    pid = _pid(project_id)
    gid = publication_uuid(generation_id)
    # Verify policy and bounds before any network request.
    validate_policy(policy)
    if type(candidate_limit) is not int or not 1 <= candidate_limit <= 20:
        raise PipelineError("Jina A/B candidate count exceeds budgeted cap", 422)
    if type(expansion_words) is not int or expansion_words not in (100, 150, 250):
        raise PipelineError("Unsupported Jina expansion length", 422)
    factory = connect or _connect
    from .service import _contexts
    with factory() as conn:
        row = conn.execute(
            "SELECT status FROM sqg_generations WHERE id=%s::uuid AND project_id=%s",
            (gid, pid),
        ).fetchone()
        if row is None or row[0] != "completed":
            raise PipelineError("Only complete project-scoped SP4 input allowed", 409)
        contexts = _contexts(conn, gid)
    bundle = get_candidates_bundle(pid, gid, connect=factory)
    return compare_jina_bundle(
        bundle, contexts, policy,
        expander=JinaLLMQueryExpander(llm_model),
        query_embeddings=JinaV3RemoteEmbeddings("retrieval.query"),
        passage_embeddings=JinaV3RemoteEmbeddings("retrieval.passage"),
        candidate_limit=candidate_limit,
        expansion_words=expansion_words,
    )
