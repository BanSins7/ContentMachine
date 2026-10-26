"""Stage C provider port: offline deterministic fake only.

This module deliberately contains no HTTP client or OpenRouter credentials.
A real implementation belongs to the separately authorized AI Query stage.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from intelligence.taxonomy_builder.pipeline_contracts import canonical, sha256


@dataclass(frozen=True)
class BatchRequest:
    generation_id: str
    batch_id: str
    model_id: str
    contexts: tuple[dict, ...]
    request_sha256: str
    max_output_tokens: int


@dataclass(frozen=True)
class ProviderResponse:
    raw_json: dict
    charged_usd: str
    model_id: str
    provider_response_id: str
    finish_reason: str = "stop"
    usage: dict | None = None


class PossiblyBilled(Exception):
    """Request may have incurred a charge; NEVER retry automatically."""


class KnownProviderFailure(Exception):
    """Failure with provider-confirmed charge, not a free error."""
    def __init__(self, cost: str, reason: str = "known_provider_failure"):
        super().__init__(reason)
        self.cost = cost
        self.reason = reason


class ProviderPort(Protocol):
    def invoke_once(self, request: BatchRequest) -> ProviderResponse: ...


class FakeProviderPort:
    """Opt-in TEST ONLY transport, no network, no actual billing.

    Modes: success, no_viable, invalid_json, truncated, known_failure, uncertain.
    """
    def __init__(self, mode: str = "success", charged_usd: str = "0.00000001"):
        if mode not in {"success", "no_viable", "invalid_json", "truncated",
                        "known_failure", "uncertain"}:
            raise ValueError("Unknown fake mode")
        if not Decimal(charged_usd).is_finite() or Decimal(charged_usd) < 0:
            raise ValueError("Bad fake charge")
        self.mode = mode
        self.charged_usd = charged_usd
        self.calls = []

    def invoke_once(self, request: BatchRequest) -> ProviderResponse:
        self.calls.append(request.request_sha256)
        if self.mode == "uncertain":
            raise PossiblyBilled("Simulated ambiguous provider outcome")
        if self.mode == "known_failure":
            raise KnownProviderFailure(self.charged_usd)
        hypotheses = []
        for c in request.contexts:
            hyp = c["hypothesis"]
            if self.mode == "no_viable":
                facets, queries, reason = [], [], "Не удалось сформировать допустимые запросы"
            else:
                facets = [{
                    "facet_key": "direct_research",
                    "angle": "direct",
                    "intent": ("Найти ролики по гипотезе " + hyp["name"])[:240],
                    "anchor_terms": [hyp["name"][:80]],
                    "sketches": [("Пример ролика по теме " + hyp["name"])[:160]],
                    "lexical_hints": [hyp["name"][:90]],
                }]
                queries = [{
                    "facet_key": "direct_research",
                    "text": hyp["name"][:120],
                    "query_mode": "keyword",
                    "angle": "direct",
                    "language": c["search_policy"]["languages"][0],
                    "research_stance": "neutral",
                    "rationale": "Проверить наличие тематических коротких видео",
                }]
                reason = None
            hypotheses.append({
                "hypothesis_id": hyp["id"],
                "semantic_profile": {
                    "core_activity": hyp["name"][:200], "audience": "",
                    "problem": "", "environment": "",
                    "mandatory_constraints": [], "unknowns": [],
                },
                "facets": facets, "queries": queries, "no_viable_reason": reason,
            })
        raw = {"hypotheses": hypotheses}
        if self.mode == "invalid_json":
            raw["hypotheses"][0]["queries"][0]["facet_key"] = "unknown_facet"
        return ProviderResponse(
            raw_json=raw, charged_usd=self.charged_usd,
            model_id=request.model_id,
            provider_response_id="fake-" + sha256({
                "request": request.request_sha256, "raw": raw,
            })[:24],
            finish_reason="length" if self.mode == "truncated" else "stop",
            usage={"prompt_tokens": 100, "completion_tokens": 100,
                   "cost": self.charged_usd},
        )
