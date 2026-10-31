"""Reuse Content Machine's existing server-side OpenRouter credential.

An optional Marketing-specific key overrides the shared worker key; if absent,
worker.openrouter_transcribe.get_key reads worker/openrouter_key.txt or the
OPENROUTER_API_KEY environment variable. Never persist a duplicate secret.
Neither import nor key lookup sends a network request.
"""
from __future__ import annotations

from intelligence import config


def get_market_openrouter_key() -> str:
    direct=config.setting("CM_MARKET_OPENROUTER_API_KEY","").strip()
    if direct:
        return direct
    from worker.openrouter_transcribe import get_key
    return get_key(config.ROOT / "worker")
