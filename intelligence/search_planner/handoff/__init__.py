"""SP-11 verified QV to Collector-compatible held handoff projection.
No Collector imports, provider IO or downstream acknowledgment.
"""
from .projection import (
    HANDOFF_SCHEMA, COLLECTOR_QUERY_SET_SCHEMA, MAX_CHUNK_SIZE,
    project_verified_query_handoff, verify_handoff_projection,
    VerifiedHandoffProjector,
)
__all__ = (
    "HANDOFF_SCHEMA", "COLLECTOR_QUERY_SET_SCHEMA", "MAX_CHUNK_SIZE",
    "project_verified_query_handoff", "verify_handoff_projection",
    "VerifiedHandoffProjector",
)
