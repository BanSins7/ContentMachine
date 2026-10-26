"""Offline method ablations: structural/lexical indicators, NOT video retrieval.

Ablation labels denote prompt strategies tested separately with equal budgets.
No model is called here and these scores DO NOT imply TikTok recall uplift.
"""
from __future__ import annotations

from collections import Counter
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from .quality import normalized_phrase, audit_raw_trace


def trace_metrics(raw, contexts, policy):
    audit = audit_raw_trace(raw, contexts, policy)
    phrases = []
    angles = []
    stances = []
    methods = {"sketches": audit["sketches"], "lexical_hints": audit["lexical_hints"]}
    for h in raw["hypotheses"]:
        facets = {f["facet_key"]: f for f in h["facets"]}
        for q in h["queries"]:
            phrases.append((h["hypothesis_id"], normalized_phrase(q["text"])))
            angles.append(q["angle"])
            stances.append(q["research_stance"])
    unique = len(set(phrases))
    return {
        "hypotheses": audit["hypotheses"],
        "query_count": len(phrases),
        "unique_query_count": unique,
        "distinct_angles": sorted(set(angles)),
        "critical_query_count": sum(x in ("critical",) for x in angles),
        "challenge_query_count": stances.count("challenge"),
        "synthetic_sketch_count": methods["sketches"],
        "lexical_hint_count": methods["lexical_hints"],
        "not_a_retrieval_quality_metric": True,
    }


def compare_ablations(experiments):
    """Expect {label:(raw,contexts,policy)}; do not manufacture baselines."""
    allowed = {"A0_direct_only", "A1_facets", "A2_facets_sketches",
               "A3_facets_lexicon", "A4_full"}
    if type(experiments) is not dict or not experiments or set(experiments) - allowed:
        raise PipelineError("Unknown offline ablation experiment labels", 422)
    return {name: trace_metrics(*inputs) for name, inputs in
            sorted(experiments.items())}
