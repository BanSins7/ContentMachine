"""GB-MOD-23 versioned human gold set, stratified deterministic sampling
and conservative per-class precision/recall/consensus/threshold QA.

Gold rows are immutable 055 group_b_review_events decisions. Original
automatic labels MUST come from SQL005 on exactly the same project/run
and analysis version; an effective review overlay is NEVER substituted for
the model prediction being evaluated. Disputes/unknowns are not negatives.
"""
from __future__ import annotations

from collections import Counter,defaultdict
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from statistics import mean
import json

from ..contracts import canonical_hash
from .contracts import (
    GOLD_VERSION,ReviewDecision,ReviewError,_deny,verify_decision,
    gold_sample_key,
)
from .repository import _row


def gold_split(gold_version,sample_key,*,calibration_percent=20):
    """Stable disjoint split; no RNG, no response-dependent leakage."""
    if (type(gold_version) is not str or not gold_version or
        type(sample_key) is not str or not sample_key or
        type(calibration_percent) is not int or
        not 0<=calibration_percent<=50):
        _deny("gold_split_invalid")
    h=sha256((gold_version+"\x00"+sample_key).encode("utf-8")).digest()
    return "calibration" if int.from_bytes(h[:8],"big")%100<calibration_percent else "evaluation"


def select_gold_candidates(candidates,*,gold_version,per_block:int=20):
    """Deterministic block-stratified sample of (analysis,block,target,index,field).

    Does NOT choose labels, rights, human reviewers or declare passed QA.
    """
    if (type(candidates) is not list or type(per_block) is not int or
        not 1<=per_block<=500 or len(candidates)>100000):
        _deny("gold_candidate_input_invalid")
    grouped=defaultdict(dict)
    for item in candidates:
        if (type(item) is not dict or
            set(item)!={"analysis_id","block","target","index","field"}):
            _deny("gold_candidate_shape_invalid")
        key=gold_sample_key(**item)
        b=item["block"]
        if b not in tuple("B"+str(i) for i in range(1,9)):
            _deny("gold_block_invalid")
        grouped[b][key]=dict(item,sample_key=key,
                              split=gold_split(gold_version,key))
    chosen=[]
    for block in sorted(grouped):
        vals=list(grouped[block].values())
        vals.sort(key=lambda x:sha256(
            (gold_version+"/"+x["sample_key"]).encode("utf-8")).hexdigest())
        chosen.extend(vals[:per_block])
    return tuple(chosen)


@dataclass(frozen=True,slots=True)
class GoldSample:
    sample_key:str
    block:str
    field:str
    target:str
    index:int|None
    analysis_id:int
    prediction:object
    annotations:tuple[tuple[str,object],...]
    split:str

    @property
    def consensus(self):
        if len(self.annotations)<2 or len({reviewer for reviewer,_ in self.annotations})!=len(self.annotations):
            return None
        counts=Counter(_label(v) for _,v in self.annotations)
        best,n=counts.most_common(1)[0]
        if n<=len(self.annotations)/2 or n<2:
            return None
        for _,v in self.annotations:
            if _label(v)==best:
                return v
        return None


@dataclass(frozen=True,slots=True)
class GoldSet:
    version:str
    samples:tuple[GoldSample,...]
    gold_sha256:str

    def payload(self):
        return {
            "schema":GOLD_VERSION,"version":self.version,
            "samples":[{
                "sample_key":s.sample_key,"analysis_id":s.analysis_id,
                "block":s.block,"target":s.target,"index":s.index,
                "field":s.field,"prediction":s.prediction,
                "annotations":[[r,v] for r,v in s.annotations],
                "split":s.split,
            } for s in self.samples],
        }

    def verify_digest(self):
        try:
            return canonical_hash(self.payload())==self.gold_sha256
        except (TypeError,ValueError):
            return False


def _label(v):
    if isinstance(v,Decimal):
        v=str(v)
    if type(v) is list:
        v=sorted(v)
    return json.dumps(v,sort_keys=True,ensure_ascii=False,allow_nan=False)


def _classes(v):
    return {_label(x) for x in v} if type(v) is list else ({_label(v)} if v is not None else set())


def make_gold_set(version,samples):
    if (type(version) is not str or not version or
        type(samples) not in (tuple,list) or len(samples)>20000 or
        any(not isinstance(s,GoldSample) for s in samples)):
        _deny("gold_set_shape_invalid")
    keyed={}
    for sample in samples:
        if sample.sample_key in keyed:
            _deny("gold_sample_duplicated")
        if (sample.split!=gold_split(version,sample.sample_key) or
            sample.sample_key!=gold_sample_key(
                analysis_id=sample.analysis_id,block=sample.block,
                target=sample.target,index=sample.index,field=sample.field)
            or type(sample.annotations) is not tuple or
            any(type(r) is not str or not r for r,_ in sample.annotations) or
            len({r for r,_ in sample.annotations})!=len(sample.annotations)):
            _deny("gold_set_provenance_invalid")
        keyed[sample.sample_key]=sample
    seq=tuple(sorted(keyed.values(),key=lambda s:s.sample_key))
    draft=GoldSet(version,seq,"")
    return GoldSet(version,seq,canonical_hash(draft.payload()))


def load_gold_set(conn,*,project_id,gold_version,verifier):
    """Tenant-scoped SQL005 original predictions + 055 immutable human labels.

    The host verifies each source's independent human annotation and
    actual media/GA/QDP permissions; SQL joins alone are not a signature.
    """
    check=getattr(verifier,"verify_gold_annotation",None)
    if (type(project_id) is not str or not project_id or
        type(gold_version) is not str or not gold_version or
        not callable(check)):
        _deny("gold_source_authorization_missing")
    records=conn.execute(
        """SELECT e.run_id::text,e.reviewer_id,e.decision_sha256,e.decision,
                  r.result_analysis_id,a.analysis_key,a.method_details
           FROM group_b_review_events e
           JOIN group_b_runs r ON r.id=e.run_id AND r.project_id=e.project_id
           JOIN content_b_analyses a ON a.id=r.result_analysis_id
           WHERE e.project_id=%s AND e.decision->>'kind'='gold_annotation'
             AND e.decision->>'gold_version'=%s
           ORDER BY e.run_id,e.revision""",
        (project_id,gold_version)
    ).fetchall()
    grouped={}
    for run_id,reviewer,sha,p,aid,analysis_key,details in records:
        decision=ReviewDecision(p,sha)
        if (not verify_decision(decision) or p["analysis_id"]!=aid or
            p["analysis_key"]!=analysis_key or
            type(details) is not dict or
            p["source_evidence_manifest_sha256"]!=details.get(
                "group_b_media_evidence_manifest_sha256") or
            p["sample_key"]!=gold_sample_key(
                analysis_id=aid,block=p["block"],target=p["target"],
                index=p["index"],field=p["field"])):
            _deny("gold_original_analysis_or_version_changed")
        original=_row(conn,aid,p["block"],p["target"],p["index"])
        if original is None:
            _deny("gold_original_sql005_target_missing")
        prediction=original[p["field"]]
        if isinstance(prediction,Decimal):
            prediction=str(prediction)
        try:
            valid=check(project_id,run_id,reviewer,decision,original)
        except Exception:
            _deny("gold_independent_source_attestation_failed")
        if valid is not True:
            _deny("gold_independent_source_attestation_denied")
        key=(run_id,p["sample_key"])
        if key not in grouped:
            grouped[key]=(p,prediction,[])
        prior,baseline,notes=grouped[key]
        if (prior["analysis_id"]!=p["analysis_id"] or
            prior["block"]!=p["block"] or prior["target"]!=p["target"] or
            prior["index"]!=p["index"] or prior["field"]!=p["field"] or
            _label(baseline)!=_label(prediction)):
            _deny("gold_label_cross_source_collision")
        if reviewer in [r for r,_ in notes]:
            _deny("gold_annotator_duplicate")
        notes.append((reviewer,p["value"]))
    samples=[]
    for (_,key),(p,prediction,labels) in grouped.items():
        samples.append(GoldSample(
            key,p["block"],p["field"],p["target"],p["index"],
            p["analysis_id"],prediction,tuple(sorted(labels)),
            gold_split(gold_version,key),
        ))
    return make_gold_set(gold_version,samples)


@dataclass(frozen=True,slots=True)
class QualityPolicy:
    version:str
    min_evaluation_samples:int=20
    min_class_support:int=3
    min_precision:float=0.80
    min_recall:float=0.80
    min_agreement:float=0.80
    required_classes:tuple[str,...]=()

    def verify(self):
        return (type(self.version) is str and bool(self.version) and
                type(self.min_evaluation_samples) is int and self.min_evaluation_samples>=2 and
                type(self.min_class_support) is int and self.min_class_support>=1 and
                all(type(v) in (int,float) and 0<=v<=1 for v in
                    (self.min_precision,self.min_recall,self.min_agreement)) and
                type(self.required_classes) is tuple and
                all(type(x) is str for x in self.required_classes) and
                len(set(self.required_classes))==len(self.required_classes))


def evaluate_quality(gold:GoldSet,policy:QualityPolicy):
    """Per-field and class precision/recall/F1, agreement and abstention.

    Strictly evaluation split; calibration split is never used for passing
    the quality gate. Disputes, unreviewed, absent class support => NO PASS.
    """
    if (not isinstance(gold,GoldSet) or not gold.verify_digest() or
        not isinstance(policy,QualityPolicy) or not policy.verify()):

        _deny("gold_quality_policy_or_digest_invalid")
    eval_samples=[s for s in gold.samples if s.split=="evaluation"]
    calibrated=sum(s.split=="calibration" for s in gold.samples)
    agreed=[s for s in eval_samples if s.consensus is not None]
    disputed=[s for s in eval_samples if s.consensus is None]
    rows={}
    class_stats=defaultdict(lambda:{"tp":0,"fp":0,"fn":0,"support":0})
    disagreements=0
    pairs=0
    double_labels=[]
    for sample in agreed:
        namespace=sample.block+"."+sample.field
        truth=_classes(sample.consensus)
        predicted=_classes(sample.prediction)
        for label in truth|predicted:
            k=namespace+":"+label
            stat=class_stats[k]
            stat["tp"]+=int(label in truth and label in predicted)
            stat["fp"]+=int(label in predicted and label not in truth)
            stat["fn"]+=int(label in truth and label not in predicted)
            stat["support"]+=int(label in truth)
    for sample in eval_samples:
        if len(sample.annotations)==2:
            double_labels.append((_label(sample.annotations[0][1]),
                                  _label(sample.annotations[1][1])))
        labels=[_label(v) for _,v in sample.annotations]
        for i in range(len(labels)):
            for j in range(i+1,len(labels)):
                pairs+=1
                disagreements+=int(labels[i]!=labels[j])
    for key,stat in sorted(class_stats.items()):
        tp,fp,fn=(stat["tp"],stat["fp"],stat["fn"])
        precision=tp/(tp+fp) if tp+fp else None
        recall=tp/(tp+fn) if tp+fn else None
        f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None
        rows[key]={**stat,"precision":precision,"recall":recall,"f1":f1}
    agreement=1-disagreements/pairs if pairs else None
    # Chance-adjusted Cohen kappa on the subset with exactly two independent
    # annotators. A constant unanimous label has undefined kappa, NOT 1.
    kappa=None
    if double_labels:
        n=len(double_labels)
        left=Counter(a for a,_ in double_labels)
        right=Counter(b for _,b in double_labels)
        observed=sum(a==b for a,b in double_labels)/n
        expected=sum(left[label]*right[label]/(n*n)
                     for label in set(left)|set(right))
        if expected<1:
            kappa=(observed-expected)/(1-expected)
    required=(set(policy.required_classes) if policy.required_classes else
              set(rows))
    scoreable=(len(agreed)>=policy.min_evaluation_samples and
               not disputed and agreement is not None and
               agreement>=policy.min_agreement and bool(required) and
               all(k in rows and rows[k]["support"]>=policy.min_class_support
                   for k in required))
    passed=(scoreable and all(
        rows[k]["precision"] is not None and
        rows[k]["recall"] is not None and
        rows[k]["precision"]>=policy.min_precision and
        rows[k]["recall"]>=policy.min_recall for k in required
    ))
    policy_payload={
        "version":policy.version,"min_evaluation_samples":policy.min_evaluation_samples,
        "min_class_support":policy.min_class_support,
        "min_precision":policy.min_precision,"min_recall":policy.min_recall,
        "min_agreement":policy.min_agreement,
        "required_classes":list(policy.required_classes),
    }
    return {
        "gold_version":gold.version,"gold_sha256":gold.gold_sha256,
        "policy_sha256":canonical_hash(policy_payload),
        "evaluation_count":len(eval_samples),"calibration_count":calibrated,
        "reviewed_consensus_count":len(agreed),
        "disputed_or_under_review_count":len(disputed),
        "annotator_pair_count":pairs,"inter_annotator_agreement":agreement,
        "cohen_kappa_two_annotators":kappa,
        "per_class":rows,
        "macro_precision":mean(v["precision"] for v in rows.values()
                               if v["precision"] is not None) if any(
                                   v["precision"] is not None for v in rows.values()) else None,
        "macro_recall":mean(v["recall"] for v in rows.values()
                            if v["recall"] is not None) if any(
                                v["recall"] is not None for v in rows.values()) else None,
        "quality_gate":"pass" if passed else "blocked",
    }


def evaluate_project_quality(conn,*,project_id,gold_version,policy,verifier):
    """Production-oriented entry: get ORIGINAL SQL005 baseline and trusted
    immutable 055 review annotations for this project, then score.
    Does not authorize itself to publish the report downstream.
    """
    gold=load_gold_set(conn,project_id=project_id,gold_version=gold_version,
                       verifier=verifier)
    return evaluate_quality(gold,policy)
