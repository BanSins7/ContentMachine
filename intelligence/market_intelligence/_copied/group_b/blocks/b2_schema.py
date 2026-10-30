"""Strict, stage09-compatible B2 model JSON Schema; output has no primary 'type' leakage.

Empty strings model optional SQL NULLs; the validator enforces *content-kind*
cross-field constraints that basic JSON Schema cannot express here.
"""
from __future__ import annotations

from .b2_format import VIDEO_FORMATS, CAROUSEL_FORMATS

def _enum(items):
    return {"type":"string","enum":list(items)}

B2_OUTPUT_SCHEMA = {
    "type":"object",
    "additionalProperties":False,
    "properties":{
        "video_primary":_enum(("",)+VIDEO_FORMATS),
        "video_secondary":_enum(("",)+VIDEO_FORMATS),
        "carousel_format":_enum(("",)+CAROUSEL_FORMATS),
        "confidence":_enum(("high","medium","low")),
        "evidence_refs":{"type":"array","items":{"type":"string"}},
    },
    "required":[
        "video_primary","video_secondary","carousel_format",
        "confidence","evidence_refs",
    ],
}
