"""Pipeline executor with explicit candidate ranking, routing and verified evidence."""
from __future__ import annotations
from collections.abc import Callable, Mapping
from math import isfinite
from .contracts import (
    ContractError,InvalidModelOutput,validate_pipeline,validate_taxonomy,validate_labels
)

class StageExecutionError(RuntimeError):
    """Provider or adapter execution failed; NOT a semantic uncertain label."""
    def __init__(self,stage_id: str,kind: str):
        self.stage_id,self.kind=stage_id,kind
        super().__init__(f"{kind} at stage {stage_id}")

def run_pipeline(config: dict, video: dict, categories: list[dict],
                 model_adapters: Mapping[str,Callable]) -> dict:
    """Execute injected adapters. No provider calls, persistence or credentials.

    Embedding returns {category_key: similarity}; LLM returns
    {category_key: {decision, reason, evidence:[{source,quote,...}]}}.
    Embedding ALWAYS ranks. Candidate dropping is opt-in and must cite
    calibrated benchmark metadata; dropped pairs are NOT negatives.
    """
    cfg=validate_pipeline(config)
    rules=validate_taxonomy(categories)
    if not isinstance(video,dict):
        raise ContractError("video must be an object")
    keys=[c["category_key"] for c in rules]
    by_id={c["category_key"]:c for c in rules}
    ordered=list(keys)
    trace=[]
    decisions={}
    for stage in cfg["stages"]:
        if not stage.get("enabled",True):
            continue
        model_ref=stage["model_ref"]
        if model_ref not in model_adapters:
            raise StageExecutionError(stage["id"],"unavailable_adapter")
        typ=stage["type"]
        if typ=="llm_classifier":
            if stage.get("run_when","always")=="on_uncertain":
                chosen=[k for k in ordered if decisions.get(k,{}).get("decision")=="uncertain"]
            else:
                chosen=list(ordered)
            if not chosen:
                trace.append({"stage_id":stage["id"],"type":typ,"skipped":True,"reason":"no_candidates"})
                continue
            try:
                raw=model_adapters[model_ref](stage,video,[by_id[k] for k in chosen])
            except Exception as exc:
                raise StageExecutionError(stage["id"],"provider_error") from exc
            try:
                labels=validate_labels(raw,chosen,video)
            except (InvalidModelOutput,ContractError) as exc:
                raise StageExecutionError(stage["id"],"invalid_output") from exc
            decisions.update(labels)
            trace.append({"stage_id":stage["id"],"type":typ,"model_ref":model_ref,
                         "evaluated":chosen,
                         "decisions":{k:labels[k]["decision"] for k in chosen}})
        elif typ=="embedding_retrieval":
            try:
                raw=model_adapters[model_ref](stage,video,[by_id[k] for k in ordered])
            except Exception as exc:
                raise StageExecutionError(stage["id"],"provider_error") from exc
            if not isinstance(raw,Mapping) or set(raw)!=set(ordered):
                raise StageExecutionError(stage["id"],"invalid_output")
            if any(type(value) not in (float,int) or not isfinite(value) or
                   not -1 <= value <= 1 for value in raw.values()):
                raise StageExecutionError(stage["id"],"invalid_output")
            rank=sorted(ordered,key=lambda k:(-raw[k],k))
            settings=stage.get("settings",{})
            ordered=list(rank)
            if settings.get("allow_candidate_drop",False):
                threshold=settings.get("min_similarity",-1)
                ordered=[k for k in rank if raw[k]>=threshold][:settings.get("top_k",len(rank))]
            trace.append({"stage_id":stage["id"],"type":typ,"model_ref":model_ref,
                          "scores":dict(raw),"ranked_categories":rank,
                          "candidates":list(ordered),
                          "filter_applied":bool(settings.get("allow_candidate_drop",False)),
                          "calibration":settings.get("calibration") if settings.get("allow_candidate_drop",False) else None})
    for key in keys:
        decisions.setdefault(key,{"decision":"not_evaluated","reason":"Excluded by candidate gate",
                                  "evidence":[]})
    return {"labels":decisions,"stage_trace":trace}
