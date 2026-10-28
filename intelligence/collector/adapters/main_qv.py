"""Narrow, opt-in adapter to current main's Query Validation service.

This is not a second Search Planner. It never reselects, regenerates or ACKs
events. The future VC-MOD-03/04 publisher owns the transactional DB trust
checks and ACK, using this read-only adapter or a dedicated locked reader.
"""
from __future__ import annotations

from intelligence.collector.contracts import (
    PROJECT_RE, CollectorError, require_id, require_int, require_uuid,
)


class MainQVAdapter:
    """Read-only producer port backed by main's existing QV service."""

    def __init__(self, *, connect=None):
        self._connect = connect

    def pending_validated_outbox(self, project_id: str, *, limit: int = 50) -> list[dict]:
        require_id(project_id, "project_id", PROJECT_RE)
        require_int(limit, "limit", 1, 100)
        from intelligence.search_planner.query_validation.service import pending_validated_outbox
        return pending_validated_outbox(
            project_id, limit=limit, include_test=False, connect=self._connect
        )

    def get_validated_bundle(self, project_id: str, validation_run_id: str) -> dict:
        require_id(project_id, "project_id", PROJECT_RE)
        require_uuid(validation_run_id, "validation_run_id")
        from intelligence.search_planner.query_validation.service import get_validated_bundle
        return get_validated_bundle(
            project_id, validation_run_id, include_test=False, connect=self._connect
        )


def make_qv_adapter(*, connect=None) -> MainQVAdapter:
    """Factory, not a worker startup; no DB connections until method invoked."""
    return MainQVAdapter(connect=connect)
