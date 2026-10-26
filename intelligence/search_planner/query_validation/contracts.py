"""Pinned Query Validation policy with explicit QA thresholds.

Scores are embedding-space proxies, NOT observed retrieval precision.
No guessed language/market filters; taxonomy policy is authoritative.
"""
from __future__ import annotations

from copy import deepcopy
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256

DEFAULT_POLICY = {
    "schema_version": "query-validation-config/v1",
    "min_positive_similarity": 0.36,
    "max_negative_similarity": 0.94,
    "min_positive_negative_margin": -0.12,
    "semantic_duplicate_similarity": 0.94,
    "mmr_lambda": 0.72,
    "max_selected_per_hypothesis": 8,
    "angle_diversity_bonus": 0.03,
    "cross_hypothesis_dedup": True,
}
_FLOAT_FIELDS = {
    "min_positive_similarity": (0.0, 1.0),
    "max_negative_similarity": (0.0, 1.0),
    "min_positive_negative_margin": (-1.0, 1.0),
    "semantic_duplicate_similarity": (0.75, 1.0),
    "mmr_lambda": (0.0, 1.0),
    "angle_diversity_bonus": (0.0, 0.25),
}


def validate_policy(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_POLICY) or (
        value.get("schema_version") != DEFAULT_POLICY["schema_version"]
    ):
        raise PipelineError("Invalid Query Validation configuration", 422)
    for k, (low, high) in _FLOAT_FIELDS.items():
        item = value[k]
        if type(item) not in (int, float) or not low <= item <= high:
            raise PipelineError("Invalid Query Validation threshold: " + k, 422)
    mx = value["max_selected_per_hypothesis"]
    if type(mx) is not int or not 1 <= mx <= 16:
        raise PipelineError("Invalid Query Validation per-hypothesis cap", 422)
    if type(value["cross_hypothesis_dedup"]) is not bool:
        raise PipelineError("Invalid Query Validation dedup policy", 422)
    return deepcopy(value)


def policy_fingerprint(policy, model_key, mode):
    return sha256({"policy": validate_policy(policy), "embedding_model": model_key,
                   "embedding_mode": mode})
