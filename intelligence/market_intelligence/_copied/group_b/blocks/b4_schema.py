"""Strict GB-MOD-17 B4 JSON Schema: only three observed visual features.

AI origin is intentionally NOT in a visual-language-model schema. It is
null unless a separate trusted origin attestor establishes provenance.
"""
from __future__ import annotations
from .b4_visual import FEATURES, ASSESSMENTS, CONFIDENCES

def _obj(fields):
    return {"type":"object","properties":fields,"required":list(fields),
            "additionalProperties":False}

_B4_VISIBILITY=_obj({
    "assessment":{"type":"string","enum":list(ASSESSMENTS)},
    "confidence":{"type":"string","enum":list(CONFIDENCES)},
    "evidence_refs":{"type":"array","items":{"type":"string"}},
})
B4_OUTPUT_SCHEMA=_obj({name:_B4_VISIBILITY for name in FEATURES})
