"""GB-MOD-16 strict JSON schema for *candidates*, never proven source truth.

String enums represent optional SQL NULL phases. All semantic validations,
lineage/receipt gates and independently attested 'known' boundaries live
in b3_story.py rather than a permissive JSON Schema oneOf.
"""
from __future__ import annotations
from .b3_story import ROLES,PHASES,BOUNDARIES,CONFIDENCE

def obj(fields):
    return {"type":"object","properties":fields,
            "required":list(fields),"additionalProperties":False}

def labels(values):
    return {"type":"string","enum":list(values)}

def array(element):
    return {"type":"array","items":element}

common={
    "role":labels(ROLES),
    "before_after_phase":labels(("",)+PHASES),
    "confidence":labels(CONFIDENCE),
    "evidence_refs":array({"type":"string"}),
}
B3_STORY_SCHEMA=obj({
    "frames":array(obj({"slot_index":{"type":"integer"},**common})),
    "intervals":array(obj({
        "start_ms":{"type":"integer"},"end_ms":{"type":"integer"},
        **common,"boundary_quality":labels(BOUNDARIES),
    })),
    "slides":array(obj({
        "slide_index":{"type":"integer"},**common,
        "screen_text":{"type":"string"},
        "visual_description":{"type":"string"},
    })),
})
