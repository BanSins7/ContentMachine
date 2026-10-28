"""GB-MOD-23 human correction and immutable gold-set quality API.

No paid calls, model inference, data rewriting, or automatic expert trust.
"""
from .contracts import (
    VERSION,GOLD_VERSION,ReviewError,ReviewDecision,
    make_decision,verify_decision,validate_effective_row,
)
from .repository import (
    ReviewAppendResult,append_review,read_effective_labels,
)
from .quality import (
    GoldSample,GoldSet,QualityPolicy,gold_sample_key,gold_split,
    select_gold_candidates,make_gold_set,load_gold_set,evaluate_quality,
)

__all__=(
    "VERSION","GOLD_VERSION","ReviewError","ReviewDecision",
    "make_decision","verify_decision","validate_effective_row",
    "ReviewAppendResult","append_review","read_effective_labels",
    "GoldSample","GoldSet","QualityPolicy","gold_sample_key","gold_split",
    "select_gold_candidates","make_gold_set","load_gold_set","evaluate_quality",
)
