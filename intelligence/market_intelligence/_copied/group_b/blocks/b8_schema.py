"""Strict GB20 B8 JSON Schema: model proposes labels, never registry IDs.

SQL005's category/topic/subtopic integer IDs are resolved by a separate
deterministic, live-registry reader, not generated in the model JSON.
"""
from __future__ import annotations

B8_OUTPUT_SCHEMA={
    "type":"object",
    "additionalProperties":False,
    "properties":{
        "category_label":{"type":"string"},
        "topic_label":{"type":"string"},
        "subtopic_label":{"type":"string"},
        "evidence_refs":{"type":"array","items":{"type":"string"}},
        "confidence":{"type":"string","enum":["high","medium","low"]},
    },
    "required":["category_label","topic_label","subtopic_label",
                "evidence_refs","confidence"],
}
