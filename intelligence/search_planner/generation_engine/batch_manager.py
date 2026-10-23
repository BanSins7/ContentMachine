"""Deterministic direction-aware batching of full RTB HypothesisContext.

No LLM or provider calls; byte ceilings are conservative planning guards,
NOT a tokenizer or proof of a model-specific context budget.
"""
from __future__ import annotations

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, canonical, sha256


PROMPT_SCHEMA_OVERHEAD_BYTES = 4096  # planning margin, verified at transport stage


def make_batches(contexts: list[dict], config: dict) -> list[dict]:
    execution = config["execution"]
    desired = execution["target_hypotheses_per_batch"]
    hard_cap = execution["hard_max_hypotheses_per_batch"]
    byte_limit = execution["max_input_bytes_per_batch"]
    if not isinstance(contexts, list) or not contexts:
        raise PipelineError("No approved hypotheses available to plan", 409)
    if byte_limit <= PROMPT_SCHEMA_OVERHEAD_BYTES:
        raise PipelineError("Insufficient input budget", 422)
    hypothesis_ids: set[str] = set()
    sorted_contexts = sorted(contexts, key=lambda c: tuple(c["ancestry_path"]))
    batches: list[dict] = []
    pending: list[dict] = []
    pending_direction: str | None = None

    def flush():
        nonlocal pending, pending_direction
        if not pending:
            return
        ids = [c["hypothesis"]["id"] for c in pending]
        hashes = [sha256(c) for c in pending]
        # Estimate is intentionally pessimistic and must be recalculated once
        # the exact LLM prompt and JSON schema are known.
        input_bytes = len(canonical(pending).encode("utf-8")) + PROMPT_SCHEMA_OVERHEAD_BYTES
        batches.append({
            "batch_index": len(batches),
            "direction_id": pending_direction,
            "hypothesis_ids": ids,
            "context_hashes": hashes,
            "input_sha256": sha256({
                "direction_id": pending_direction,
                "contexts": hashes,
                "config_model_sha256": sha256(config["model"]),
            }),
            "estimated_input_tokens": input_bytes,
            "output_token_cap": config["model"]["max_output_tokens"],
        })
        pending = []
        pending_direction = None

    for context in sorted_contexts:
        ident = context["hypothesis"]["id"]
        direction = context["direction"]["id"]
        if ident in hypothesis_ids:
            raise PipelineError("Duplicate hypothesis in Context Builder output", 409)
        hypothesis_ids.add(ident)
        if context["ancestry_path"][0] != direction or context["ancestry_path"][-1] != ident:
            raise PipelineError("Invalid hypothesis ancestry", 409)

        # A single context must fit WITHOUT truncation; no silent loss of
        # definition/include/exclude, source facts or optional branch.
        alone_bytes = len(canonical([context]).encode("utf-8")) + PROMPT_SCHEMA_OVERHEAD_BYTES
        if alone_bytes > byte_limit:
            raise PipelineError("context_too_large; full RTB input cannot be shortened", 422)
        if pending and (
            direction != pending_direction or len(pending) >= min(desired, hard_cap)
            or len(canonical(pending + [context]).encode("utf-8"))
                + PROMPT_SCHEMA_OVERHEAD_BYTES > byte_limit
        ):
            flush()
        if not pending:
            pending_direction = direction
        pending.append(context)
    flush()
    assert len(hypothesis_ids) == sum(len(b["hypothesis_ids"]) for b in batches)
    return batches
