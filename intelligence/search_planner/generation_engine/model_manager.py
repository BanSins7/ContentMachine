"""Verify current OpenRouter catalog snapshot before creating/authorizing a quote.

No chat/completions request; uses existing read-only model catalog data.
"""
from __future__ import annotations

from intelligence.taxonomy_builder.catalog import latest_verified
from intelligence.taxonomy_builder.cost_estimator import rates
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError


def current_priced_model(conn, config: dict) -> dict:
    model = config["model"]
    name = model["model_id"]
    found = latest_verified(conn, {name})[name]
    if (found["catalog_version_id"] != model["catalog_snapshot_id"]
            or found["catalog_sha256"] != model["model_catalog_sha256"]):
        raise PipelineError("Search Planner model/pricing changed; approve a new config and quote", 409)
    capability = found["capabilities"]
    if not (capability.get("text_output")
            and capability.get("supports_structured_outputs")
            and "response_format" in capability.get("supported_parameters", [])):
        raise PipelineError("Verified model cannot provide strict JSON responses", 409)
    output_cap = capability.get("max_output_tokens")
    if output_cap is not None and model["max_output_tokens"] > output_cap:
        raise PipelineError("Configured response exceeds verified model output limit", 409)
    length = capability.get("context_length")
    if type(length) is not int or length <= model["max_output_tokens"]:
        raise PipelineError("Verified model context window unavailable/too small", 409)
    prices = rates(found["pricing"])
    return {
        "model_id": name,
        "snapshot_id": found["catalog_version_id"],
        "catalog_sha256": found["catalog_sha256"],
        "verified_at": found["verified_at"],
        "context_length": length,
        "max_output_tokens": model["max_output_tokens"],
        "rates": {key: str(value) for key, value in prices.items()},
    }
