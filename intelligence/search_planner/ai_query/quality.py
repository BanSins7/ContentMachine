"""Deterministic guardrails and trace diagnostics, not semantic validation.

ThinkQE/MuGI/Jina stages remain model-derived proposals. Relevance, evidence,
embeddings, MMR and real video recall are the next Query Validation stage.
"""
from __future__ import annotations

import re
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.search_planner.generation_engine.raw_validation import validate_raw_batch

_GENERIC = {"fyp", "foryou", "foryoupage", "viral", "trending", "#fyp",
            "#foryou", "#viral", "#trending"}


def normalized_phrase(phrase):
    return " ".join(phrase.casefold().split())


def audit_raw_trace(raw, contexts, policy):
    """All structural contracts + safe cheap lexical checks.

    Never silently drops candidates: a failed batch is billed/recorded as
    invalid in the engine, NOT auto-retried or declared successful.
    """
    validate_raw_batch(raw, contexts, policy)
    counts = {}
    for hyp in raw["hypotheses"]:
        queried = set()
        for query in hyp["queries"]:
            text = normalized_phrase(query["text"])
            if not text or text.strip("#") in _GENERIC or text in _GENERIC:
                raise PipelineError("Generic/empty platform query not permitted", 422)
            if text in queried:
                raise PipelineError("Duplicate query inside one hypothesis", 422)
            queried.add(text)
            if query["query_mode"] == "hashtag":
                # API-neutral slug; do not invent an existing platform hashtag.
                if not re.fullmatch(r"#?[\w]{2,80}", query["text"], re.UNICODE):
                    raise PipelineError("Hashtag is not a single search token", 422)
        counts[hyp["hypothesis_id"]] = len(queried)
    return {"hypotheses": len(counts), "candidates": sum(counts.values()),
            "nonempty": sum(bool(n) for n in counts.values()),
            "facets": sum(len(x["facets"]) for x in raw["hypotheses"]),
            "sketches": sum(len(f["sketches"]) for x in raw["hypotheses"] for f in x["facets"]),
            "lexical_hints": sum(len(f["lexical_hints"]) for x in raw["hypotheses"] for f in x["facets"]),
            "method_versions": {
                "facets": "thinkqe_inspired_facets_v1",
                "sketches": "mugi_inspired_sketches_v1",
                "vocabulary": "jina_inspired_lexicon_v1",
            }}
