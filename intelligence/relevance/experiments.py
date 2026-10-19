"""Transparent benchmark calculation; uncertain and not_evaluated are NOT negatives."""
from __future__ import annotations

def evaluate(predictions: dict, gold: dict) -> dict:
    """Key=(video_id,category_key), value=match/no_match/(uncertain for gold).

    Evaluation excludes human unsure labels from denominators, but counts
    missing/uncertain model predictions as abstentions and missed positives.
    """
    tp=fp=fn=tn=abstained=0
    for key, actual in gold.items():
        if actual == "uncertain":
            continue
        if actual not in ("match","no_match"):
            raise ValueError("Invalid gold label")
        predicted = predictions.get(key,"not_evaluated")
        if predicted not in ("match","no_match","uncertain","not_evaluated"):
            raise ValueError("Invalid predicted label")
        if predicted in ("uncertain","not_evaluated"):
            abstained += 1
            if actual == "match":
                fn += 1
        elif predicted == "match":
            if actual == "match": tp += 1
            else: fp += 1
        elif actual == "match": fn += 1
        else: tn += 1
    total=tp+fp+fn+tn+abstained-(0)  # FN already includes positive abstentions
    decided=tp+fp+(fn-(sum(1 for k,v in gold.items() if v=="match" and
                   predictions.get(k,"not_evaluated") in ("uncertain","not_evaluated"))))+tn
    labeled=sum(1 for v in gold.values() if v in ("match","no_match"))
    return {
      "gold_count":labeled, "decided_count":max(0,decided),"abstained_count":abstained,
      "tp":tp,"fp":fp,"fn":fn,"tn":tn,
      "precision":tp/(tp+fp) if tp+fp else None,
      "recall":tp/(tp+fn) if tp+fn else None,
      "coverage":(labeled-abstained)/labeled if labeled else None
    }
