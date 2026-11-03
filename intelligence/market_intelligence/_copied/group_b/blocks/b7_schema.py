"""Stage19 strict OpenRouter-supported JSON Schema for B7 source-bound CTA.

Optional SQL fields travel as empty strings (not model-null). The validator
alone maps them to SQL NULL after independent verbatim source verification.
"""
from __future__ import annotations
from .b7_cta import CTA_TYPES, DESTINATIONS, SERVICES, DELIVERY

def obj(properties):
    return {"type":"object","additionalProperties":False,
            "properties":properties,"required":list(properties)}

def choices(labels):
    return {"type":"string","enum":list(labels)}

def string():
    return {"type":"string"}

OCCURRENCE_SCHEMA=obj({
    "occurrence_index":{"type":"integer"},
    "cta_type":choices(CTA_TYPES),
    "text":string(),"spoken_text":string(),"screen_text":string(),
    "delivery":{"type":"array","items":choices(DELIVERY)},
    "evidence_refs":{"type":"array","items":string()},
    "start_ms":{"type":"integer"},"end_ms":{"type":"integer"},
    "slide_index":{"type":"integer"},
    "destination_type":choices(DESTINATIONS),
    "destination_service":choices(("",)+SERVICES),
    "destination_url":string(),"destination_handle":string(),
    "destination_app_name":string(),"destination_keyword":string(),
    "destination_other_desc":string(),
    "confidence":choices(("high","medium","low")),
})
B7_OUTPUT_SCHEMA=obj({
    "occurrences":{"type":"array","items":OCCURRENCE_SCHEMA},
    "coverage_claim":choices(("complete","partial")),
    "confidence":choices(("high","medium","low")),
})
