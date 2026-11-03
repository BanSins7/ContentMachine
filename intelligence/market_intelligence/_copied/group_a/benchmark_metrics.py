"""GA-MOD-22 deterministic, unambiguous quality metrics for authorized gold.

These are *scores of supplied predictions*, not model execution or claims
that annotations are genuinely independently verified. Unknown stays unknown.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import isfinite
import re
import unicodedata


class BenchmarkError(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__("GA22: "+code)


def decimal_time(value):
    if type(value) is bool or type(value) not in (str,int,float,Decimal):
        raise BenchmarkError("time_invalid")
    try:
        n=Decimal(str(value))
    except InvalidOperation as exc:
        raise BenchmarkError("time_invalid") from exc
    if not n.is_finite() or n<0 or n>Decimal("86400"):
        raise BenchmarkError("time_invalid")
    return n


def _rate(top,bottom):
    return None if bottom==0 else round(top/bottom,6)


def _counts(tp,fp,fn,*,tn=0,unknown=0):
    if min(tp,fp,fn,tn,unknown)<0:
        raise BenchmarkError("negative_count")
    return {
        "tp":tp,"fp":fp,"fn":fn,"tn":tn,"unknown":unknown,
        "precision":_rate(tp,tp+fp),
        "recall":_rate(tp,tp+fn),
        "f1":_rate(2*tp,2*tp+fp+fn),
    }


def edit_distance(reference,prediction):
    """Exact Levenshtein; O(min(n,m)) memory, explicit work cap."""
    if not isinstance(reference,(str,list,tuple)) or not isinstance(prediction,(str,list,tuple)):
        raise BenchmarkError("invalid_sequence")
    if len(reference)>15000 or len(prediction)>15000:
        raise BenchmarkError("edit_distance_work_limit")
    if len(prediction)>len(reference):
        reference,prediction=prediction,reference
    previous=list(range(len(prediction)+1))
    for i,x in enumerate(reference,1):
        current=[i]
        for j,y in enumerate(prediction,1):
            current.append(min(
                previous[j]+1,current[-1]+1,
                previous[j-1]+(x!=y)
            ))
        previous=current
    return previous[-1]


WORD=re.compile(r"[^\W_]+(?:['’][^\W_]+)*",re.UNICODE)


def normalized(text,*,for_words=False):
    if type(text) is not str or len(text)>15000:
        raise BenchmarkError("invalid_gold_text")
    text=" ".join(unicodedata.normalize("NFC",text).casefold().split())
    if for_words:
        return WORD.findall(text)
    return text


def text_error(gold,prediction,*,mode):
    if mode not in ("wer","cer"):
        raise BenchmarkError("text_metric_unknown")
    g=normalized(gold,for_words=mode=="wer")
    p=normalized(prediction,for_words=mode=="wer")
    errors=edit_distance(g,p)
    return {
        "metric":mode,
        "reference_units":len(g),
        "predicted_units":len(p),
        "edit_errors":errors,
        "error_rate":_rate(errors,len(g)),
        "empty_reference":len(g)==0,
    }


def score_asr(gold,prediction):
    return {"wer":text_error(gold,prediction,mode="wer"),
            "cer":text_error(gold,prediction,mode="cer")}


def _maximum_matching(edges,n_right):
    """Maximum bipartite cardinality, NOT greedy best-IoU matching."""
    owner=[-1]*n_right
    def go(left,seen):
        for right in edges[left]:
            if right in seen:
                continue
            seen.add(right)
            if owner[right]==-1 or go(owner[right],seen):
                owner[right]=left
                return True
        return False
    return sum(int(go(i,set())) for i in range(len(edges)))


def scene_boundaries(gold,predicted,*,tolerance_sec="0.250"):
    if type(gold) not in (list,tuple) or type(predicted) not in (list,tuple):
        raise BenchmarkError("scene_rows_invalid")
    if len(gold)>512 or len(predicted)>512:
        raise BenchmarkError("scene_rows_limit")
    tol=decimal_time(tolerance_sec)
    g=[decimal_time(x) for x in gold]
    p=[decimal_time(x) for x in predicted]
    if (len(g)!=len(set(g)) or len(p)!=len(set(p)) or
        g!=sorted(g) or p!=sorted(p)):
        raise BenchmarkError("scene_points_not_strictly_increasing")
    edges=[[j for j,y in enumerate(p) if abs(x-y)<=tol] for x in g]
    tp=_maximum_matching(edges,len(p))
    return {"tolerance_sec":str(tol),**_counts(tp,len(p)-tp,len(g)-tp)}


def _interval(row):
    if (type(row) not in (list,tuple) or len(row)!=2):
        raise BenchmarkError("invalid_event_interval")
    start,end=[decimal_time(v) for v in row]
    if end<=start:
        raise BenchmarkError("event_time_order_invalid")
    return start,end


def _iou(a,b):
    overlap=max(Decimal(0),min(a[1],b[1])-max(a[0],b[0]))
    if overlap==0:
        return Decimal(0)
    return overlap/(max(a[1],b[1])-min(a[0],b[0]))


def event_intervals(gold,predicted,*,labels=("music","sound_effect","other"),
                    min_iou="0.500"):
    if type(gold) not in (tuple,list) or type(predicted) not in (tuple,list):
        raise BenchmarkError("event_rows_invalid")
    if len(gold)>256 or len(predicted)>256:
        raise BenchmarkError("event_rows_limit")
    threshold=Decimal(str(min_iou))
    if not threshold.is_finite() or not Decimal(0)<threshold<=Decimal(1):
        raise BenchmarkError("event_iou_invalid")
    if type(labels) not in (tuple,list) or not labels or any(type(x) is not str for x in labels):
        raise BenchmarkError("event_labels_invalid")
    allowed=set(labels)
    def collect(rows):
        out={label:[] for label in labels}
        for item in rows:
            if type(item) is not dict or set(item)!={"label","start_sec","end_sec"}:
                raise BenchmarkError("event_fields_invalid")
            if item["label"] not in allowed:
                raise BenchmarkError("unrecognized_event_class")
            out[item["label"]].append(_interval([item["start_sec"],item["end_sec"]]))
        return out
    g,p=collect(gold),collect(predicted)
    totals={"tp":0,"fp":0,"fn":0}
    by={}
    for label in labels:
        edges=[[j for j,y in enumerate(p[label]) if _iou(x,y)>=threshold]
               for x in g[label]]
        tp=_maximum_matching(edges,len(p[label]))
        r=_counts(tp,len(p[label])-tp,len(g[label])-tp)
        by[label]=r
        for key in totals:
            totals[key]+=r[key]
    return {
        "min_iou":str(threshold),"classes":by,
        "micro":_counts(**totals),
    }


def subtitle_truth(gold,predicted):
    """Unknown prediction is abstention, not a false negative by fiat.

    Gold 'unknown' is excluded from TP/FP/TN/FN but counted. Recall against
    all known-positive gold counts abstentions as missed positive coverage.
    """
    if type(gold) not in (list,tuple) or type(predicted) not in (list,tuple):
        raise BenchmarkError("subtitle_rows_invalid")
    if len(gold)!=len(predicted) or len(gold)>10000:
        raise BenchmarkError("subtitle_pairing_invalid")
    allowed={"true","false","unknown"}
    if any(type(x) is not str or x not in allowed for x in [*gold,*predicted]):
        raise BenchmarkError("subtitle_value_invalid")
    tp=fp=fn=tn=abstain_pos=abstain_neg=gold_unknown=0
    for g,p in zip(gold,predicted):
        if g=="unknown":
            gold_unknown+=1
        elif p=="unknown":
            if g=="true":abstain_pos+=1
            else:abstain_neg+=1
        elif g=="true" and p=="true":tp+=1
        elif g=="false" and p=="true":fp+=1
        elif g=="true" and p=="false":fn+=1
        else:tn+=1
    known=len(gold)-gold_unknown
    missed=fn+abstain_pos
    return {
        **_counts(tp,fp,missed,tn=tn,unknown=abstain_pos+abstain_neg),
        "abstained_positive":abstain_pos,
        "abstained_negative":abstain_neg,
        "gold_unknown":gold_unknown,
        "eligible_gold_cases":known,
        "coverage":_rate(known-abstain_pos-abstain_neg,known),
        "false_positive_rate":_rate(fp,fp+tn+abstain_neg),
        "false_negative_rate":_rate(fn+abstain_pos,tp+fn+abstain_pos),
    }


def ocr_recall(gold_lines,predicted_lines,*,max_cer="0.250"):
    """Bipartite line recall; Unicode NFC casefold with explicit CER threshold.

    Empty gold has undefined recall (not a synthetic zero or 100% score).
    """
    if type(gold_lines) not in (tuple,list) or type(predicted_lines) not in (tuple,list):
        raise BenchmarkError("ocr_lines_invalid")
    if len(gold_lines)>128 or len(predicted_lines)>128:
        raise BenchmarkError("ocr_lines_limit")
    threshold=Decimal(str(max_cer))
    if not threshold.is_finite() or not 0<=threshold<=1:
        raise BenchmarkError("ocr_cer_threshold_invalid")
    g=[normalized(x) for x in gold_lines]
    p=[normalized(x) for x in predicted_lines]
    if any(not x for x in g+p):
        raise BenchmarkError("ocr_empty_line_invalid")
    edges=[]
    for line in g:
        neighbors=[]
        for j,target in enumerate(p):
            cer=Decimal(edit_distance(line,target))/len(line)
            if cer<=threshold:
                neighbors.append(j)
        edges.append(neighbors)
    tp=_maximum_matching(edges,len(p))
    return {"max_line_cer":str(threshold),**_counts(tp,len(p)-tp,len(g)-tp)}
