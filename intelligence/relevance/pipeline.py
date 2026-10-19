"""Interchangeable model stages. All provider calls are injected, never implicit.

The embedding stage ranks/selects candidates, but never asserts no_match.
Skipping by a calibrated gate produces not_evaluated, not a negative label.
"""
from __future__ import annotations
from collections.abc import Callable, Mapping
from .contracts import ContractError, validate_pipeline, validate_taxonomy, validate_labels

def run_pipeline(config: dict, video: dict, categories: list[dict],
                 model_adapters: Mapping[str, Callable]) -> dict:
    """Run synchronous stages using injected model_ref -> function(stage,video,categories).

    Adapters for embedding_retrieval return {category_key: score (-1..1)}.
    Adapters for llm_classifier return {category_key: {decision,evidence}}.
    No API calls, credentials, caching, or persistence are hidden here.
    """
    pipeline = validate_pipeline(config)
    rules = validate_taxonomy(categories)
    ids = [row["category_key"] for row in rules]
    by_id = {row["category_key"]: row for row in rules}
    active_ids = list(ids)
    decisions = {}
    trace = []
    for stage in pipeline["stages"]:
        if not stage.get("enabled", True):
            continue
        model_ref = stage["model_ref"]
        if model_ref not in model_adapters:
            raise ContractError("No model adapter is registered for " + model_ref)
        typ = stage["type"]
        if typ == "llm_classifier":
            if stage.get("run_when", "always") == "on_uncertain":
                current = [key for key in active_ids
                           if key in decisions and decisions[key]["decision"] == "uncertain"]
            else:
                current = list(active_ids)
            if not current:
                trace.append({"stage_id":stage["id"],"skipped":True,"reason":"no_candidates"})
                continue
            raw = model_adapters[model_ref](stage, video, [by_id[key] for key in current])
            labels = validate_labels(raw, current)
            decisions.update(labels)
            trace.append({"stage_id":stage["id"],"type":typ,"model_ref":model_ref,
                          "evaluated":current, "decisions":{k:v["decision"] for k,v in labels.items()}})
            continue
        if typ == "embedding_retrieval":
            raw = model_adapters[model_ref](stage, video, [by_id[key] for key in active_ids])
            if not isinstance(raw, Mapping) or set(raw) != set(active_ids):
                raise ContractError("Embedding adapter must score every incoming candidate")
            if any(type(value) not in (float,int) or not (-1 <= value <= 1)
                   for value in raw.values()):
                raise ContractError("Embedding score must be a finite cosine similarity in [-1,1]")
            rank = sorted(active_ids, key=lambda key: (-raw[key],key))
            settings = stage.get("settings", {})
            if settings.get("allow_candidate_drop",False):
                top_k = settings.get("top_k",len(rank))
                threshold = settings.get("min_similarity", -1)
                if type(threshold) not in (float,int) or not -1 <= threshold <= 1:
                    raise ContractError("min_similarity must be numeric in [-1,1]")
                retained = [key for key in rank if raw[key] >= threshold][:top_k]
                active_ids = retained
            trace.append({"stage_id":stage["id"],"type":typ,"model_ref":model_ref,
                          "scores":dict(raw),"candidates":list(active_ids),
                          "filter_applied":bool(settings.get("allow_candidate_drop",False))})
    for key in ids:
        decisions.setdefault(key, {"decision":"not_evaluated","evidence":[]})
    return {"labels":decisions,"stage_trace":trace}
