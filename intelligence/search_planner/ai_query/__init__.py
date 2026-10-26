"""Generation-stage D: grounded, auditable query synthesis (no video search)."""
from .prompts import build_completion_payload, stage_d_fingerprints, prompt_messages
from .transport import OpenRouterQueryTransport
from .quality import audit_raw_trace
__all__ = ("build_completion_payload","stage_d_fingerprints","prompt_messages","OpenRouterQueryTransport","audit_raw_trace")
