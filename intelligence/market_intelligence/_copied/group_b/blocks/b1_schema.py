"""GB-MOD-13 schema exactly supported by stage09 strict JSON provider codec.

Unknown nullable SQL flags are emitted by a *string enum* ('unknown') and
normalized to NULL only after independent provider/evidence validation.
No provider invocation occurs here.
"""
from __future__ import annotations

from .b1_hook import MECHANISMS, DELIVERY, FLAGS

def _obj(fields):
    return {"type":"object","properties":fields,
            "required":list(fields),"additionalProperties":False}

def _enum(items):
    return {"type":"string","enum":list(items)}

def _arr(item):
    return {"type":"array","items":item}

_LOCATION = {
    "start_ms":{"type":"integer"},"end_ms":{"type":"integer"},
    "slide_index":{"type":"integer"},
}
_part = _obj({
    "part_index":{"type":"integer"},**_LOCATION,
    "verbatim_text":{"type":"string"},
    "mechanisms":_arr(_enum(MECHANISMS)),
    "evidence_refs":_arr({"type":"string"}),
})
B1_OUTPUT_SCHEMA = _obj({
    "classification_status":_enum(("inferred",)),
    **_LOCATION,
    "spoken_text":{"type":"string"},
    "screen_text":{"type":"string"},
    "visual_description":{"type":"string"},
    "mechanisms":_arr(_enum(MECHANISMS)),
    "flags":_obj({k:_enum(("true","false","unknown")) for k in FLAGS}),
    "delivery":_arr(_enum(DELIVERY)),
    "evidence_refs":_arr({"type":"string"}),
    "hook_parts":_arr(_part),
})
