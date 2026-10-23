"""OpenRouter one-shot structured-output transport. NEVER retries a POST.

This class does NOT grant spending permission. The trusted live worker must
first verify pinned config, approved quote, authorized consent and exact
serialized-request cost, then commit dispatch_intent before invoking it.

OpenRouter JSON schema support depends on the actual provider endpoint;
require_parameters=True, allow_fallbacks=False, strict=True plus local
validation are used to fail closed. An HTTP error may still be billable.
"""
from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from decimal import Decimal
from typing import Callable

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.search_planner.generation_engine.provider_port import (
    ProviderResponse, PossiblyBilled,
)
from intelligence.search_planner.generation_engine.cost_estimator import money

CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_RESPONSE_BYTES = 2_000_000


def _read_usage(body):
    usage = body.get("usage")
    if type(usage) is not dict or "cost" not in usage:
        raise PossiblyBilled("OpenRouter usage/cost missing; cannot release reserve")
    try:
        cost = money(usage["cost"], "provider cost")
        p, c = usage["prompt_tokens"], usage["completion_tokens"]
        if type(p) is not int or type(c) is not int or min(p, c) < 0:
            raise ValueError("Invalid token usage")
        if cost != cost.quantize(Decimal("0.00000001")):
            raise ValueError("Bill exceeds NUMERIC(18,8)")
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        raise PossiblyBilled("OpenRouter billing usage invalid; reconciliation required") from exc
    return str(cost), {"prompt_tokens": p, "completion_tokens": c, "cost": str(cost)}


def parse_completion(body, expected_model):
    """Preserve known billed malformed output for Stage C failed-known settlement."""
    if type(body) is not dict:
        raise PossiblyBilled("Provider response is not an object")
    cost, usage = _read_usage(body)
    choices = body.get("choices")
    choice = choices[0] if type(choices) is list and choices else {}
    if type(choice) is not dict:
        choice = {}
    message = choice.get("message")
    message = message if type(message) is dict else {}
    content = message.get("content")
    if type(content) is list:
        if all(type(x) is dict and type(x.get("text")) is str for x in content):
            content = "".join(x["text"] for x in content)
        else:
            content = None
    try:
        parsed = json.loads(content) if type(content) is str else None
    except (ValueError, TypeError):
        parsed = None
    ident = body.get("id")
    if type(ident) is not str or not ident or len(ident) > 256:
        ident = "unknown-billed-provider-id"
    return ProviderResponse(
        raw_json=parsed, charged_usd=cost,
        model_id=body.get("model") if type(body.get("model")) is str else "",
        provider_response_id=ident,
        finish_reason=choice.get("finish_reason") if type(choice.get("finish_reason")) is str else "",
        usage=usage,
    )


class OpenRouterQueryTransport:
    """Unconditionally ONE invocation (no retries, no redirects, no streaming).

    sender is a trusted test-only function(payload)->response. The default
    transport uses urllib with a pinned HTTPS endpoint, no user URL input.
    """
    def __init__(self, *, sender: Callable | None = None, key: str | None = None,
                 timeout: int = 50):
        if type(timeout) is not int or not 1 <= timeout <= 120:
            raise PipelineError("Invalid OpenRouter timeout", 422)
        self._sender = sender
        self._key = key
        self._timeout = timeout

    def invoke_once(self, payload):
        if type(payload) is not dict or payload.get("provider") != {
            "require_parameters": True, "allow_fallbacks": False,
        } or payload.get("stream") is not False:
            raise PipelineError("Unsafe OpenRouter request options", 422)
        if self._sender is not None:
            try:
                raw = self._sender(payload)
            except Exception as exc:
                raise PossiblyBilled("Mock transport outcome unknown") from exc
        else:
            # Credentials are resolved only AFTER budget + dispatch gate.
            token = self._key or os.environ.get("OPENROUTER_API_KEY")
            if not token:
                from intelligence.taxonomy_builder.provider import resolve_key
                token = resolve_key()
            if not token:
                raise PossiblyBilled("OpenRouter credentials unavailable after dispatch intent")
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(data) > 1_000_000:
                raise PossiblyBilled("Pinned payload too large after dispatch intent")
            req = Request(CHAT_COMPLETIONS_URL, data=data, method="POST", headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "HTTP-Referer": "https://github.com/BanSins7/ContentMachine",
                "X-Title": "ContentMachine Search Planner",
            })
            try:
                # Block redirects: provider is pinned; no credential forwarding.
                from urllib.request import build_opener, HTTPHandler, HTTPSHandler, HTTPRedirectHandler
                class _NoRedirect(HTTPRedirectHandler):
                    def redirect_request(self, req, fp, code, msg, headers, newurl):
                        return None
                opener = build_opener(HTTPSHandler(), _NoRedirect())
                with opener.open(req, timeout=self._timeout) as response:
                    size = response.read(MAX_RESPONSE_BYTES + 1)
                if len(size) > MAX_RESPONSE_BYTES:
                    raise PossiblyBilled("Provider response exceeded size limit")
                raw = json.loads(size)
            except PossiblyBilled:
                raise
            except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
                # Do not log response body, provider headers, Authorization,
                # prompt, user facts, or text of an untrusted provider error.
                raise PossiblyBilled("OpenRouter outcome uncertain: " +
                                     type(exc).__name__ + "; no automatic retry") from exc
        return parse_completion(raw, payload["model"])
