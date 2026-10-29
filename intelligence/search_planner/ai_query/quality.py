"""Deterministic guardrails and trace diagnostics, not semantic validation.

Search intents remain model-derived proposals. Relevance, evidence,
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
        # Generated search intents must be distinct. These checks are
        # lexical/structural only; genuine
        # semantic novelty must be benchmarked after Collector results.
        seen_intents = set()
        facet_keys = set()
        for facet in hyp["facets"]:
            intent = (facet["angle"], normalized_phrase(facet["intent"]))
            if intent in seen_intents:
                raise PipelineError("Duplicate semantic-intent facet", 422)
            seen_intents.add(intent)
            facet_keys.add(facet["facet_key"])
            hints = [normalized_phrase(t) for t in facet["lexical_hints"]]
            if len(hints) != len(set(hints)) or any(
                    hint.strip("#") in _GENERIC for hint in hints):
                raise PipelineError("Duplicate or filler lexical hints", 422)
            for sketch in facet["sketches"]:
                if re.search(r"(?i)\b[0-9]+(?:[.,][0-9]+)?\s*(?:m|k|million|тыс|млн)?\s*(?:views|likes|просмотров|лайков)\b",
                             sketch):
                    raise PipelineError("Synthetic sketch falsely claims engagement metrics", 422)
        if hyp["queries"]:
            used = {q["facet_key"] for q in hyp["queries"]}
            if used != facet_keys:
                raise PipelineError("Every proposed facet must map to a search query", 422)
            if not hyp["semantic_profile"]["core_activity"].strip():
                raise PipelineError("Query generation lacks semantic core activity", 422)
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
                "facets": "grounded_intents_v2",
                "sketches": "disabled_in_live_prompt_v2",
                "vocabulary": "disabled_in_live_prompt_v2",
            }}
