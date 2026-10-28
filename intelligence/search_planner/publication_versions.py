"""Strict publication-shape boundary between approved Deep RTB V1 and future V2.

The numeric taxonomy_version is a *publication revision*, not a Deep schema
version. The emerging C Corrector 14-field Deep V2 remains unavailable until
its reviewed versioned publication and provenance format is actually merged.
Do not silently project V2 -> V1 by feeding it to validate_tree().
"""
from __future__ import annotations

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

DEEP_V1_SCHEMA = "deep-rtb/v1"
V1_NODE_FIELDS = frozenset((
    "id", "parent_id", "kind", "relation", "source_fact_ids",
    "name", "rationale", "definition", "include", "exclude", "examples",
))
V2_EXTENSION_FIELDS = frozenset((
    "source_observation_ids", "search_signals", "validation_focus",
))


def require_deep_v1_tree(nodes: object) -> str:
    """Reject extra/missing fields before legacy tree validation strips them.

    A genuine Deep V2 will need a separately verified publication-mode pin,
    independent A/B provenance and versioned context consumer. That feature
    must not be simulated by accepting the extra fields here.
    """
    if type(nodes) is not list or not nodes:
        raise PipelineError("Approved Deep C9 tree must be a nonempty array", 409)
    for index, raw in enumerate(nodes):
        if type(raw) is not dict:
            raise PipelineError("Approved Deep C9 node must be an object", 409)
        names = set(raw)
        if names & V2_EXTENSION_FIELDS:
            raise PipelineError(
                "Deep C9 V2 fields present but verified Deep V2 publication support is unavailable",
                409,
            )
        if names != V1_NODE_FIELDS:
            raise PipelineError(
                "Deep C9 V1 node fields mismatch at index " + str(index), 409,
            )
    return DEEP_V1_SCHEMA
