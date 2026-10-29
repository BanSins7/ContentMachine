"""Opt-in model-provider metadata and request verification (GB-MOD-09).

No provider import, credentials, network access, or paid invocation on import.
"""
from .protocols import (
    MODEL_CONTRACT, ModelCatalogSnapshot, ModelDescriptor,
    ModelProbe, ModelProbeState, ProviderFailure, VerifiedChatResponse,
)
from .openrouter import (
    OpenRouterProvider, OpenRouterTransport, parse_model_catalog,
    decode_chat_response, prepare_chat_request,
)

__all__ = (
    "MODEL_CONTRACT", "ModelCatalogSnapshot", "ModelDescriptor",
    "ModelProbe", "ModelProbeState", "ProviderFailure", "VerifiedChatResponse",
    "OpenRouterProvider", "OpenRouterTransport", "parse_model_catalog",
    "decode_chat_response", "prepare_chat_request",
)
