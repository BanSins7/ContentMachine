"""SourceProviderPort wrapper for the ONE installed local Evil0ctal DTK transport.

No adapter creation or import submits a TikTok request. Diagnostics (status)
and provider submit/poll require explicit caller action and existing worker
authorization. Registry remains the authority for supported capabilities.
"""
from __future__ import annotations

from intelligence.collector.contracts import CollectorError


class DTKSourceProvider:
    """Use existing intelligence.connectors.dtk, never a second HTTP client."""

    def status(self) -> dict:
        from intelligence.connectors import dtk
        return dtk.status()

    def submit(self, action: str, params: dict) -> dict:
        if action != "search_posts" or type(params) is not dict or set(params) != {"keyword"}:
            raise CollectorError("unsupported_operation", "Only DTK keyword search supported", 409)
        from intelligence.connectors import dtk
        # Caller MUST have durable release/submit intent; this port is not auth.
        return dtk.submit(action, params)

    def task(self, task_id: str) -> dict:
        if type(task_id) is not str or not 1 <= len(task_id) <= 255:
            raise CollectorError("invalid_identifier", "Invalid provider task id")
        from intelligence.connectors import dtk
        return dtk.task(task_id)
