"""RTB-FIX-02 (part 1): pure Deep V2 C-run planning and fail-closed state gates.

No database writes, billing or LLM dispatch. This is NOT a payable cost
quote/consent and MUST NOT be accepted as authorization by any runner.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

from intelligence.project_sources.context_builder import sha as canonical_sha, canonical
from .cost_estimator import rates
from .pipeline_contracts import PipelineError
from .research_contracts_v2 import _project, _sha, _uuid

PLAN_VERSION = "rtb-deep-v2-preflight/v1"
MODEL_ID = "anthropic/claude-opus-5.5"
MAX_EXPANSIONS = 2
MIN_OUTPUT_TOKENS = 4096
MAX_OUTPUT_TOKENS = 16000
# This is the sequence of *billable stages*; two expansion stages are optional.
BILLABLE_STAGES = ("deep_first", "deep_second", "deep_expand_1", "deep_expand_2")
REVIEW_STATES = ("review1", "review2")
STATES = frozenset((
    "pass1_pending", "pass1_running", "review1",
    "pass2_pending", "pass2_running",
    "expand_pending", "expand_running", "review2",
    "approved", "failed", "uncertain", "blocked_quality",
    "paused_budget", "paused_price", "cancelled",
))
# Each state grants at most one human-initiated next action, never automatic work.
PAID_ACTIONS = {
    "pass1_pending": "deep_first",
    "pass2_pending": "deep_second",
    "expand_pending": "deep_expand",
}
HUMAN_ACTIONS = {"review1": "human_review_1", "review2": "human_review_2"}
TERMINAL = frozenset(("approved", "failed", "uncertain", "blocked_quality", "cancelled"))


def _reject(code: str) -> None:
    raise PipelineError(code, 422)


def validate_frozen_input(data: Any) -> dict:
    if not isinstance(data, dict) or data.get("schema_version") != "rtb-research-input/v2":
        _reject("rtb_v2_research_input_schema_invalid")
    body = {key: deepcopy(value) for key, value in data.items()
            if key != "research_input_sha256"}
    _project(body.get("project_id"))
    _uuid(body.get("b9_snapshot_id"), "rtb_v2_snapshot_id_invalid")
    _uuid(body.get("b9_handoff_id"), "rtb_v2_handoff_id_invalid")
    _uuid(body.get("a_context_id"), "rtb_v2_context_id_invalid")
    for key in ("b9_snapshot_sha256", "b9_handoff_sha256", "a_context_sha256"):
        _sha(body.get(key), "rtb_v2_" + key + "_invalid")
    if data.get("research_input_sha256") != canonical_sha(body):
        _reject("rtb_v2_research_input_integrity")
    if (body.get("input_mode") != "knowledge_snapshot_v1"
            or body.get("facts_are_human_approved") is not True
            or body.get("observations_are_original_a_evidence_not_approved_product_facts") is not True
            or body.get("paid_execution_authorized") is not False
            or body.get("provider_calls") != 0
            or type(body.get("provider_calls")) is not int
            or body.get("research_goal") is not None):
        _reject("rtb_v2_source_state_invalid")
    if (not isinstance(body.get("c1_approved_facts"), list)
            or not body["c1_approved_facts"]
            or not isinstance(body.get("c2_original_video_observations"), list)
            or body.get("fact_count") != len(body["c1_approved_facts"])
            or body.get("observation_count") != len(body["c2_original_video_observations"])
            or body.get("video_count") != len(body.get("video_analysis_refs", []))):
        _reject("rtb_v2_source_counts_invalid")
    return deepcopy(data)


def validate_opus_catalog(model: Any, *, max_output_tokens: int) -> dict:
    """Verified catalog snapshot, not a user-entered price or model nickname."""
    if not isinstance(model, dict):
        _reject("rtb_v2_model_snapshot_missing")
    if model.get("model_id") != MODEL_ID:
        _reject("rtb_v2_requires_opus_55")
    version = model.get("catalog_version_id")
    if type(version) is not int or version <= 0:
        _reject("rtb_v2_model_catalog_version_invalid")
    _sha(model.get("catalog_sha256"), "rtb_v2_catalog_sha_invalid")
    caps = model.get("capabilities")
    if not isinstance(caps, dict) or caps.get("model_id") not in (None, MODEL_ID):
        _reject("rtb_v2_model_caps_invalid")
    if (caps.get("text_output") is not True
            or caps.get("supports_structured_outputs") is not True):
        _reject("rtb_v2_model_json_unsupported")
    if (type(max_output_tokens) is not int
            or not MIN_OUTPUT_TOKENS <= max_output_tokens <= MAX_OUTPUT_TOKENS):
        _reject("rtb_v2_output_tokens_invalid")
    cap_tokens = caps.get("max_output_tokens")
    if cap_tokens is not None and (
            type(cap_tokens) is not int or max_output_tokens > cap_tokens):
        _reject("rtb_v2_model_output_cap")
    context = caps.get("context_length")
    if type(context) is not int or context <= max_output_tokens:
        _reject("rtb_v2_context_length_invalid")
    # Fail closed for conditional pricing; don't silently use an inaccurate rate.
    rates(model.get("pricing"))
    return deepcopy(model)


def stage_permission(status: str, expansion_count: int, *, consent_active: bool) -> dict:
    """Return a *required human action*, never permission to call the provider."""
    if status not in STATES or type(expansion_count) is not int or not 0 <= expansion_count <= MAX_EXPANSIONS:
        _reject("rtb_v2_run_state_invalid")
    if type(consent_active) is not bool:
        _reject("rtb_v2_consent_state_invalid")
    if status in TERMINAL:
        return {"next_action": None, "paid_dispatch_allowed": False,
                "requires_fresh_user_action": False, "terminal": True}
    if not consent_active:
        return {"next_action": "new_explicit_consent", "paid_dispatch_allowed": False,
                "requires_fresh_user_action": True, "terminal": False}
    if status in PAID_ACTIONS:
        role = PAID_ACTIONS[status]
        if role == "deep_expand" and expansion_count >= MAX_EXPANSIONS:
            _reject("rtb_v2_expansion_limit_reached")
        stage = (role + "_" + str(expansion_count + 1)) if role == "deep_expand" else role
        return {"next_action": stage, "paid_dispatch_allowed": False,
                "requires_fresh_user_action": True, "terminal": False}
    if status in HUMAN_ACTIONS:
        return {"next_action": HUMAN_ACTIONS[status],
                "paid_dispatch_allowed": False, "requires_fresh_user_action": True, "terminal": False}
    return {"next_action": None, "paid_dispatch_allowed": False,
            "requires_fresh_user_action": False, "terminal": False}


def prepare_deep_v2_preflight(research_input: dict, model: dict,
                              *, max_output_tokens: int = 8192) -> dict:
    """Immutable advisory preflight; **no quote, no consent, no run**.

    A payable quote in the next FIX-02 substep must use exact Deep V2 JSON
    schema/prompt/requests (FIX-03), verified real catalog and pricing, record
    a new explicit user consent and transactionally reserve each provider call.
    """
    frozen = validate_frozen_input(research_input)
    catalog = validate_opus_catalog(model, max_output_tokens=max_output_tokens)
    if frozen["paid_execution_authorized"] is not False:
        _reject("rtb_v2_already_authorized_source")
    size = len(canonical(frozen))
    context_len = catalog["capabilities"]["context_length"]
    conservative_input_upper = size * 2 + 2048
    if conservative_input_upper + max_output_tokens > context_len:
        _reject("rtb_v2_model_context_too_small")
    price = rates(catalog["pricing"])
    per_stage_advisory = (
        Decimal(conservative_input_upper) * price["prompt"]
        + Decimal(max_output_tokens) * (price["completion"] + price["internal_reasoning"])
        + price["request"]
    ) * Decimal("1.20")
    if not per_stage_advisory.is_finite() or per_stage_advisory <= 0:
        _reject("rtb_v2_advisory_price_invalid")
    body = {
        "schema_version": PLAN_VERSION,
        "project_id": frozen["project_id"],
        "research_input_sha256": frozen["research_input_sha256"],
        "snapshot_id": frozen["b9_snapshot_id"],
        "handoff_id": frozen["b9_handoff_id"],
        "a_context_id": frozen["a_context_id"],
        "model_id": MODEL_ID,
        "model_catalog_version_id": catalog["catalog_version_id"],
        "model_catalog_sha256": catalog["catalog_sha256"],
        "max_output_tokens": max_output_tokens,
        "planned_required_passes": ["deep_first", "deep_second"],
        "possible_optional_expansions": ["deep_expand_1", "deep_expand_2"],
        "human_reviews": ["review1", "review2"],
        "advisory_per_pass_reservation_usd": str(per_stage_advisory),
        "quote_created": False,
        "user_budget_consent_granted": False,
        "provider_dispatch_allowed": False,
        "provider_calls": 0,
        "warning": "Advisory only, NOT a payable quote or authorization; exact prompts, pricing and consent must be revalidated before dispatch.",
    }
    return {**body, "preflight_sha256": canonical_sha(body)}
