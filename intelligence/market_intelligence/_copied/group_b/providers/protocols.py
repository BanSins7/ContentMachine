"""GB-MOD-09: verified OpenRouter metadata snapshots, no inferred capability.

Prices are USD per token as returned in OpenRouter's pricing fields; they
are *catalog evidence*, not an authorized cost estimate or a reservation.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol, Mapping, Any, runtime_checkable

from ..errors import GroupBError

MODEL_CONTRACT = "group-b-model/v1"


class ProviderFailure(GroupBError):
    """Safe retry classification; NEVER includes provider body, URL or key."""
    def __init__(self, reason: str, *, retryable: bool = False):
        self.reason = reason
        super().__init__("provider_unavailable", "openrouter: " + reason,
                         retryable=retryable)


class ModelProbeState(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True,slots=True)
class ModelDescriptor:
    slug: str
    catalog_sha256: str
    context_tokens: int
    max_output_tokens: int
    input_modalities: frozenset[str]
    output_modalities: frozenset[str]
    supported_parameters: frozenset[str]
    prompt_usd_per_token: Decimal
    completion_usd_per_token: Decimal
    image_usd: Decimal
    request_usd: Decimal

    @property
    def image_input(self) -> bool:
        return "image" in self.input_modalities

    @property
    def strict_json(self) -> bool:
        # OpenRouter documents structured_outputs as the catalog filter for
        # schema-validated JSON. response_format alone can mean json_object.
        return "structured_outputs" in self.supported_parameters

    def wire(self) -> dict:
        return {
            "schema_version":MODEL_CONTRACT,"model":self.slug,
            "catalog_sha256":self.catalog_sha256,
            "context_tokens":self.context_tokens,
            "max_output_tokens":self.max_output_tokens,
            "input_modalities":sorted(self.input_modalities),
            "output_modalities":sorted(self.output_modalities),
            "strict_json":self.strict_json,
            "catalog_pricing_usd":{
                "prompt_per_token":str(self.prompt_usd_per_token),
                "completion_per_token":str(self.completion_usd_per_token),
                "image":str(self.image_usd),
                "request":str(self.request_usd),
            },
        }


@dataclass(frozen=True,slots=True)
class ModelCatalogSnapshot:
    models: tuple[ModelDescriptor,...]
    catalog_sha256: str
    source: str
    verified: bool

    def find(self,slug: str) -> ModelDescriptor | None:
        return next((x for x in self.models if x.slug==slug),None)


@dataclass(frozen=True,slots=True)
class ModelProbe:
    model: str
    state: ModelProbeState
    reason_codes: tuple[str,...]
    descriptor: ModelDescriptor | None

    @property
    def can_prepare(self) -> bool:
        return self.state is ModelProbeState.SUPPORTED


@dataclass(frozen=True,slots=True)
class VerifiedChatResponse:
    model: str
    request_sha256: str
    response_id: str
    json_data: dict
    usage: dict
    finish_reason: str


@runtime_checkable
class MetadataTransport(Protocol):
    """Optional explicitly injected HTTP GET; no transport on import."""
    def get_json(self, *, endpoint: str, headers: Mapping[str,str],
                 timeout_seconds: float) -> Mapping[str,Any]: ...


@runtime_checkable
class ChatTransport(Protocol):
    """Future GB10/11 worker-only, used after verified paid authorization."""
    def post_json(self, *, endpoint: str, headers: Mapping[str,str],
                  body: Mapping[str,Any], timeout_seconds: float) -> Mapping[str,Any]: ...
