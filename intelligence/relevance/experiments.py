"""Benchmark metrics with explicit abstentions and no false negatives from gating."""
from __future__ import annotations

def evaluate(predictions: dict, gold: dict) -> dict:
    """Gold: (video_id,category_key)->match/no_match/uncertain.
    Unknown human labels are excluded; model abstentions count in coverage
    and missed positive recall, not in precision denominators.
    """
    tp=fp=fn=tn=abstained=0
    for key,actual in gold.items():
        if actual=="uncertain":
            continue
        if actual not in ("match","no_match"):
            raise ValueError("Invalid gold label")
        predicted=predictions.get(key,"not_evaluated")
        if predicted not in ("match","no_match","uncertain","not_evaluated"):
            raise ValueError("Invalid predicted label")
        if predicted in ("uncertain","not_evaluated"):
            abstained+=1
            if actual=="match":
                fn+=1
        elif predicted=="match":
            if actual=="match": tp+=1
            else: fp+=1
        elif actual=="match": fn+=1
        else: tn+=1
    labeled=sum(v in ("match","no_match") for v in gold.values())
    decided=labeled-abstained
    return {"gold_count":labeled,"decided_count":decided,"abstained_count":abstained,
            "tp":tp,"fp":fp,"fn":fn,"tn":tn,
            "precision":tp/(tp+fp) if tp+fp else None,
            "recall":tp/(tp+fn) if tp+fn else None,
            "coverage":decided/labeled if labeled else None}
