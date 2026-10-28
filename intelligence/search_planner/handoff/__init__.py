"""SP-11 verified QV to Collector-compatible held handoff projection.
No Collector imports, provider IO or downstream acknowledgment.
"""
from .projection import (
    HANDOFF_SCHEMA, COLLECTOR_QUERY_SET_SCHEMA, MAX_CHUNK_SIZE,
    project_verified_query_handoff, VerifiedHandoffProjector,
)
from .verification import verify_handoff_projection
from .repository import ingest_qv_to_sp_inbox, get_sp_handoff_status
from .dispatcher import deliver_next_held_chunk, reconcile_uncertain_chunk
from .collector_adapter import EmptyCollectorAdapter, MockCollectorAdapter, RealCollectorAdapter, verify_collector_receipt

__all__ = (
    "HANDOFF_SCHEMA", "COLLECTOR_QUERY_SET_SCHEMA", "MAX_CHUNK_SIZE",
    "project_verified_query_handoff", "verify_handoff_projection",
    "VerifiedHandoffProjector",
    "ingest_qv_to_sp_inbox", "get_sp_handoff_status",
    "deliver_next_held_chunk", "reconcile_uncertain_chunk",
    "EmptyCollectorAdapter", "MockCollectorAdapter", "RealCollectorAdapter", "verify_collector_receipt",
)
