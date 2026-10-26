"""RTB Deep V2 single-use OpenRouter adapter. Never retries paid requests.

Unlike provider.invoke(stage,...), this adapter sends the *exact already
SHA-pinned V2 JSON request* and never silently dispatches the legacy Deep V1
schema. No call is possible from quote/consent/start/GET.
"""
from __future__ import annotations

from decimal import Decimal
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .cost_estimator import amount
from .pipeline_contracts import PipelineError
from .provider import (
    URL, MAX_RESPONSE_BYTES, ProviderUncertain, resolve_key,
    safe_openrouter_http_error,
)


def single_v2_request(payload: dict, *, sender=None, api_key=None, timeout=300) -> dict:
    """Execute exactly one explicitly reserved V2 request, no retries.

    A mock sender is intentionally injectable for offline/PG regression.
    Missing/incomplete provider usage is UNKNOWN spend: caller MUST retain
    its previously committed reservation and disable repeated dispatch.
    """
    if (not isinstance(payload, dict)
            or payload.get("response_format", {}).get("type") != "json_schema"
            or payload.get("provider") != {
                "require_parameters": True, "allow_fallbacks": False}
            or payload.get("stream") is not False
            or payload.get("usage") != {"include": True}):
        raise PipelineError("rtb_v2_paid_request_not_strictly_pinned", 422)
    if sender is None:
        token = api_key if api_key is not None else resolve_key()
        if not token:
            raise PipelineError("rtb_v2_provider_key_missing", 503)

        def sender(body):
            req = Request(URL, method="POST",
                          data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                          headers={
                              "Authorization": "Bearer " + token,
                              "Content-Type": "application/json",
                              "Accept": "application/json",
                              "HTTP-Referer": "https://github.com/BanSins7/ContentMachine",
                              "X-Title": "ContentMachine Research Taxonomy Builder V2",
                          })
            try:
                with urlopen(req, timeout=timeout) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ProviderUncertain("rtb_v2_provider_response_too_large")
                return json.loads(raw)
            except HTTPError as exc:
                raise ProviderUncertain(
                    safe_openrouter_http_error(exc, token)) from exc
            except (URLError, TimeoutError, OSError, ValueError) as exc:
                raise ProviderUncertain(
                    "rtb_v2_provider_transport_uncertain:" + type(exc).__name__) from exc
    try:
        response = sender(payload)
    except ProviderUncertain:
        raise
    except Exception as exc:
        raise ProviderUncertain("rtb_v2_provider_outcome_uncertain") from exc
    if not isinstance(response, dict):
        raise ProviderUncertain("rtb_v2_provider_invalid_response")
    usage = response.get("usage")
    if not isinstance(usage, dict) or usage.get("cost") is None:
        raise ProviderUncertain("rtb_v2_provider_missing_cost")
    try:
        cost = amount(usage["cost"], "rtb_v2_provider_cost_usd")
        prompt = usage["prompt_tokens"]
        output = usage["completion_tokens"]
    except (PipelineError, KeyError, ValueError, TypeError) as exc:
        raise ProviderUncertain("rtb_v2_provider_usage_invalid") from exc
    if any(type(v) is not int or v < 0 for v in (prompt, output)):
        raise ProviderUncertain("rtb_v2_provider_tokens_invalid")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return {
            "charged_usd": cost, "model_id": response.get("model"),
            "response_id": str(response.get("id", "")), "finish_reason": None,
            "raw_json": None, "usage": {
                "prompt_tokens": prompt, "completion_tokens": output,
                "cost_usd": str(cost),
            },
        }
    choice = choices[0]
    content = choice.get("message", {}).get("content")
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict))
    decoded = None
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
        except (ValueError, TypeError):
            pass
    return {
        "model_id": response.get("model"),
        "response_id": str(response.get("id", "")),
        "finish_reason": choice.get("finish_reason"),
        "raw_json": decoded,
        "charged_usd": cost,
        "usage": {
            "prompt_tokens": prompt, "completion_tokens": output,
            "cost_usd": str(cost),
        },
    }
