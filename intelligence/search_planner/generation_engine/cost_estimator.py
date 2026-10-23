"""Conservative indicative USD quote for already-persisted full-context batches.

The Stage-A stored input estimate is a UTF-8 byte upper proxy, NOT exact
tokenizer usage or a verified OpenRouter request payload. Therefore this
quote is indicative only; future paid dispatch must recheck actual request,
model capabilities, pricing, output reasoning and reservation limits.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256

CENT = Decimal("0.00000001")
MAX_BUDGET = Decimal("10000")
SAFETY_MARGIN = Decimal("1.20")
QUOTE_VERSION = "search-query-byte-upper-proxy-v1"


def money(value, name: str, *, positive=False) -> Decimal:
    if isinstance(value, bool) or type(value) not in (str, int, Decimal):
        raise PipelineError("Invalid USD value: " + name, 422)
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PipelineError("Unparseable USD value: " + name, 422) from exc
    if not d.is_finite() or d < 0 or (positive and d <= 0):
        raise PipelineError("Invalid negative/zero USD value: " + name, 422)
    return d


def usd(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_CEILING), "f")


def approved_budget(value, high: str) -> Decimal:
    amount = money(value, "authorized_usd", positive=True)
    if amount > MAX_BUDGET or amount < money(high, "quote_high"):
        raise PipelineError("Budget must cover high estimate and not exceed 10000 USD", 422)
    if amount != amount.quantize(CENT):
        raise PipelineError("Budget exceeds NUMERIC(18,8) precision", 422)
    return amount


def estimate_generation(batches: list[dict], model: dict, *,
                        generation_id: str, input_sha256: str,
                        config_sha256: str) -> dict:
    if not batches:
        raise PipelineError("Generation has no batches to estimate", 409)
    rate = {name: money(value, name) for name, value in model["rates"].items()}
    if set(rate) != {"prompt", "completion", "request", "internal_reasoning"}:
        raise PipelineError("Incomplete OpenRouter pricing snapshot", 409)
    total_high = Decimal(0)
    batch_amounts = []
    seen = set()
    for batch in batches:
        ident = str(batch["id"])
        if ident in seen:
            raise PipelineError("Duplicate batch in quote input", 409)
        seen.add(ident)
        tokens = batch["estimated_input_tokens"]
        output = batch["output_token_cap"]
        if type(tokens) is not int or type(output) is not int or tokens < 1 or output < 1:
            raise PipelineError("Invalid planned token envelope", 409)
        if tokens + output > model["context_length"]:
            raise PipelineError("Batch input/output exceed verified model context window", 409)
        if output > model["max_output_tokens"]:
            raise PipelineError("Planned batch uses unapproved output token maximum", 409)
        maximum = (Decimal(tokens) * rate["prompt"]
                   + Decimal(output) * (rate["completion"] + rate["internal_reasoning"])
                   + rate["request"]) * SAFETY_MARGIN
        # Even a zero-priced model gets a minimum reserved ledger quantum.
        high = max(CENT, maximum.quantize(CENT, rounding=ROUND_CEILING))
        total_high += high
        batch_amounts.append({
            "batch_id": ident,
            "batch_input_sha256": batch["input_sha256"],
            "reserve_high_usd": usd(high),
            "estimated_input_token_proxy": tokens,
            "max_output_tokens": output,
        })
    high_text = usd(total_high)
    expected_text = usd(total_high * Decimal("0.60"))
    low_text = usd(total_high * Decimal("0.25"))
    details = {
        "estimator_version": QUOTE_VERSION,
        "currency": "USD",
        "generation_id": generation_id,
        "generation_input_sha256": input_sha256,
        "config_sha256": config_sha256,
        "model_id": model["model_id"],
        "catalog_snapshot_id": model["snapshot_id"],
        "model_catalog_sha256": model["catalog_sha256"],
        "catalog_verified_at": model["verified_at"],
        "rates_usd_per_token": model["rates"],
        "context_length": model["context_length"],
        "model_max_output_tokens": model["max_output_tokens"],
        "batch_reservations": batch_amounts,
        "assumptions": {
            "input_proxy": "UTF-8 bytes plus deterministic overhead; not actual tokenizer",
            "output": "full configured max_output_tokens per batch",
            "safety_margin": str(SAFETY_MARGIN),
            "not_guaranteed_cap": True,
            "extra_provider_fees_or_price_tiers": "not included; conditional pricing is blocked",
            "actual_request_requires_recheck_before_paid_dispatch": True,
        },
    }
    return {
        "low_usd": low_text, "expected_usd": expected_text,
        "high_usd": high_text, "pricing_snapshot": details,
        "batch_manifest_sha256": sha256([
            (x["batch_id"], x["batch_input_sha256"], x["reserve_high_usd"])
            for x in batch_amounts
        ]),
    }
