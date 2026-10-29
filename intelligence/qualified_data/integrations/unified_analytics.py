"""FIX-14 UAE integration boundary: preparation is NOT completed analysis.

The pinned UA-MOD-05 branch only reads immutable lineage identities and
its pure qualification gate cannot authenticate QDP or finished Group facts.
Never translate a QDP preparation ACK into UAE 'qualified' or 'completed'.
"""
from __future__ import annotations

from .contracts import IntegrationCapability,CapabilityState
from ..contracts import QDPError

UAE_QDP_PORT="uae-qdp-reader/v1"
UAE_HANDOFF_WIRE="qdp-handoff/v1"


def uae_capability(*,observed_reader_schema=None, observed_qdp_wire=None,
                   verified_complete_group_sources=False):
    if observed_reader_schema is None:
        return IntegrationCapability("unified_analytics",
                    CapabilityState.UNAVAILABLE,reason_code="uae_adapter_not_installed")
    if observed_reader_schema!=UAE_QDP_PORT or observed_qdp_wire!=UAE_HANDOFF_WIRE:
        return IntegrationCapability("unified_analytics",
                    CapabilityState.INCOMPATIBLE,
                    observed_schema=str(observed_reader_schema),
                    reason_code="uae_reader_version_mismatch")
    # Even a caller-provided true flag cannot certify current completed
    # Group A/B/C DB results; only an installed UAE owned authoritative
    # reader (future UAE-MOD-07..13) can qualify this.
    return IntegrationCapability("unified_analytics",
                CapabilityState.UNAVAILABLE,
                observed_schema=UAE_QDP_PORT,
                reason_code="completed_a_b_c_evidence_not_installed")


class UAECompletedResults:
    """Production adapter must be explicitly supplied; no synthetic scores."""
    def __init__(self,reader=None):
        self._reader=reader

    def read_completed(self,conn,project_id,qdp_run_id,video_id):
        if self._reader is None or not callable(getattr(self._reader,"read_completed",None)):
            raise QDPError("unified_analytics_unavailable",
                "No authoritative completed A/B/C UAE reader installed",503)
        # The reader is required to perform exact joins, authorization, QA,
        # versioned result checks on every call. No QDP preparation=completion.
        data=self._reader.read_completed(conn,project_id,qdp_run_id,video_id)
        if data is None:
            return None
        if not isinstance(data,dict) or data.get("qualified") is not True or (
             not all(isinstance(data.get(g),dict) and
                 data[g].get("state")=="completed" and
                 data[g].get("verified") is True for g in ("A","B","C"))):
            raise QDPError("unified_analytics_incompatible",
                "Incomplete/unverified Group outputs cannot qualify analytics",409)
        return data
