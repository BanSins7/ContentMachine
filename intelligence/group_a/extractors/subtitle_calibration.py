"""GA14 optional HELD-OUT gold rule evaluation, never automatic model fitting.

Threshold is fixed BEFORE consuming holdout labels. The annotation source
must be authenticated externally. No representative human corpus ships in
this repository, so passing synthetic unit fixtures is NOT production QA.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
import re

from ..contracts_v1 import canonical_sha256
from .subtitles import SubtitleCalibration, SubtitleError


@dataclass(frozen=True,slots=True)
class GoldSubtitleCase:
    case_id: str
    language: str
    human_label: str
    visual_correlated: bool
    visual_match_ratio: str
    split: str = "heldout"

    def __post_init__(self):
        if (type(self.case_id) is not str
            or re.fullmatch(r"[a-zA-Z0-9_.-]{8,80}",self.case_id) is None
            or self.language not in ("en","ru")
            or self.human_label not in ("true","false")
            or type(self.visual_correlated) is not bool
            or type(self.visual_match_ratio) is not str
            or re.fullmatch(r"(?:0\.[0-9]{3}|1\.000)",self.visual_match_ratio) is None
            or self.split!="heldout"):
            raise SubtitleError("invalid_subtitle_gold_case")

    def wire(self):
        return {"case_id":self.case_id,"language":self.language,
                "human_label":self.human_label,
                "visual_correlated":self.visual_correlated,
                "visual_match_ratio":self.visual_match_ratio,"split":self.split}


def evaluate_heldout_subtitle_rule(cases, *,decision_threshold,
                                   calibration_id,verify_gold_source):
    """Return immutable calibration ONLY after a trusted independent gold test.

    Fixed rule: correlated OCR-ASR candidate and ratio >= threshold.
    Requires each language's human positives and negatives represented.
    Precision >=95% separately for ru and en and overall; no tuning on the
    same heldout cohort. A callback authenticates human labels and split.
    """
    if (type(cases) not in (list,tuple) or not 160<=len(cases)<=20000
        or not all(isinstance(x,GoldSubtitleCase) for x in cases)):
        raise SubtitleError("subtitle_gold_corpus_insufficient")
    try:
        if type(decision_threshold) is not str or re.fullmatch(
            r"(?:0\.[0-9]{1,3}|1(?:\.0{1,3})?)",decision_threshold
        ) is None:
            raise ValueError()
        threshold=Decimal(decision_threshold)
        if not Decimal("0.5")<=threshold<=1:
            raise ValueError()
    except Exception as exc:
        raise SubtitleError("invalid_subtitle_threshold") from exc
    ids=[x.case_id for x in cases]
    if len(set(ids))!=len(ids):
        raise SubtitleError("subtitle_gold_duplicate_cases")
    by_language={}
    report={}
    for lang in ("en","ru"):
        rows=[x for x in cases if x.language==lang]
        true_count=sum(x.human_label=="true" for x in rows)
        false_count=sum(x.human_label=="false" for x in rows)
        if true_count<50 or false_count<30:
            raise SubtitleError("subtitle_gold_insufficient_language_coverage")
        tp=fp=fn=tn=0
        for c in rows:
            predict=c.visual_correlated and Decimal(c.visual_match_ratio)>=threshold
            if predict and c.human_label=="true":tp+=1
            elif predict:fp+=1
            elif c.human_label=="true":fn+=1
            else:tn+=1
        if tp+fp<40:
            raise SubtitleError("subtitle_gold_too_few_positive_predictions")
        precision=Decimal(tp)*100/Decimal(tp+fp)
        if precision<95:
            raise SubtitleError("subtitle_gold_precision_below_gate")
        by_language[lang]=(tp,fp,fn,tn)
        report[lang]={"true_positive":tp,"false_positive":fp,
                      "false_negative":fn,"true_negative":tn,
                      "precision_pct":format(precision.quantize(Decimal("0.01")),".2f")}
    all_tp=sum(x[0] for x in by_language.values())
    all_fp=sum(x[1] for x in by_language.values())
    total_precision=Decimal(all_tp)*100/Decimal(all_tp+all_fp)
    if total_precision<95:
        raise SubtitleError("subtitle_gold_precision_below_gate")
    digest=canonical_sha256({
        "calibration_id":calibration_id,
        "threshold":decision_threshold,
        "cases":[x.wire() for x in sorted(cases,key=lambda c:c.case_id)],
        "split":"heldout",
        "preselected_threshold":True,
    })
    context={"dataset_sha256":digest,"calibration_id":calibration_id,
             "split":"heldout","preselected_threshold":True,
             "case_count":len(cases),
             "language_scope":["en","ru"]}
    if not callable(verify_gold_source) or verify_gold_source(context) is not True:
        raise SubtitleError("subtitle_gold_human_annotations_unverified")
    calibration=SubtitleCalibration(
        calibration_id=calibration_id,dataset_sha256=digest,
        extractor_method="group-a-subtitle-ocr-asr-rule-v1",
        language_scope=("en","ru"),
        min_precision_pct=format(total_precision.quantize(Decimal("0.01")),".2f"),
        heldout_positive_count=sum(x.human_label=="true" for x in cases),
        decision_threshold=decision_threshold,
    )
    result={"schema_version":"ga-subtitle-calibration-evaluation/v1",
            "calibration":calibration.wire(),
            "case_count":len(cases),"metrics":report,
            "role":"heldout","threshold_preselected":True}
    return calibration,{**result,"observation_sha256":canonical_sha256(result)}
