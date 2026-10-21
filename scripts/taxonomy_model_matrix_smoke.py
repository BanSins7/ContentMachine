"""Single one-shot OpenRouter model matrix smoke for the 12 user-selected models.

One shared Health & Fitness questionnaire and facts-v1 prompt for all models.
Different output ceilings/verified reasoning for costly and flash models.
Never a production human review, never a measure of similarity to ChatGPT.
Charge guard: <= $0.65 new charges, <= $1.00 known aggregate for this project.
No paid retries; uncertain billing stops the entire batch.
"""
from __future__ import annotations
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from intelligence.taxonomy_builder.catalog import fetch_openrouter_models
from intelligence.taxonomy_builder.pipeline_contracts import normalize_capability, PipelineError
from intelligence.taxonomy_builder.cost_estimator import rates
from intelligence.taxonomy_builder.provider import invoke, request_payload, reservation_cost
from intelligence.taxonomy_builder.run_contracts import reviewable_fact_batch

PROJECT_CEILING = Decimal("1.00")
PREVIOUS_CONFIRMED = Decimal("0.045898899")
NEW_BATCH_CAP = Decimal("0.65")
PER_CALL_RESERVE_CAP = Decimal("0.20")
OUT = Path("taxonomy_model_matrix_result.json")
MODEL_IDS = (
    "openai/gpt-6.1-sol", "openai/gpt-6-luna",
    "google/gemini-4-argon", "google/gemini-3.8-flash",
    "anthropic/claude-opus-5.5", "anthropic/claude-sonnet-5.5",
    "anthropic/claude-haiku-5.5", "deepseek/deepseek-v4.1-flash",
    "z-ai/glm-5.3-flash", "xiaomi/mimo-v2.6-pro",
    "xiaomi/mimo-v2.6-flash", "qwen/qwen3.8-27b",
)
STRONG = frozenset((
    "openai/gpt-6.1-sol", "openai/gpt-6-luna",
    "google/gemini-4-argon", "anthropic/claude-opus-5.5",
    "anthropic/claude-sonnet-5.5", "xiaomi/mimo-v2.6-pro",
))
PROFILE = {
    "niche": "Health & Fitness",
    "product_type": "mobile_app",
    "product_functions": (
        "Приложение сохраняет блюда в дневнике питания. "
        "Приложение оценивает калории по фотографии порции. "
        "Пользователь может вручную исправить размер порции. "
        "Приложение показывает недельную историю записей."
    ),
    "audience_problems": (
        "Люди забывают записывать перекусы. "
        "Люди ошибаются при оценке размера порций. "
        "Некоторым сложно учитывать еду вне дома."
    ),
    "product_description": (
        "Образовательный пищевой дневник, без диагностики болезней, "
        "назначения лечения и гарантии похудения."
    ),
}
INPUTS = {
    "questionnaire": PROFILE, "niche": "health_fitness",
    "research_goal": "maximize_views", "attachments": "excluded",
}

def save(report):
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

def choose_stage(model_id, cap, pricing, available):
    """Only verified provider capabilities/rates. Fail closed on unknown prices."""
    strong = model_id in STRONG
    efforts = cap.get("reasoning_efforts") or []
    effort = ("high" if strong and "high" in efforts else
              "medium" if "medium" in efforts else None)
    reasoning = {"mode": "effort", "effort": effort} if effort else {"mode": "default"}
    maxima = (3600, 3000, 2600, 2200, 1800, 1400) if strong else (
        2300, 2000, 1700, 1400, 1100)
    snap = {"capabilities": cap, "pricing": pricing}
    for limit in maxima:
        if limit > (cap.get("max_output_tokens") or 0):
            continue
        stage = {
            "role": "fact_extraction", "model_id": model_id,
            "reasoning": reasoning, "temperature": None,
            "max_output_tokens": limit, "prompt_version": "facts-v1",
        }
        try:
            reserve = reservation_cost(request_payload(stage, snap, INPUTS), snap)
        except PipelineError:
            continue
        if reserve <= PER_CALL_RESERVE_CAP and reserve <= available:
            return stage, snap, reserve
    return None

def grade(response):
    """Objective smoke rubric, NOT a measured similarity to ChatGPT."""
    score = 25  # provider returned verifiable usage/cost
    raw = response.get("raw_json")
    if isinstance(raw, dict) and isinstance(raw.get("facts"), list):
        score += 15
    verified, excluded = [], []
    if isinstance(raw, dict):
        try:
            batch = reviewable_fact_batch(raw, PROFILE)
            verified = batch["facts"]
            excluded = batch["excluded_facts"]
        except PipelineError:
            # A wholly invalid batch is rejected as a whole by the production
            # validator. Count unverified proposals without approving any.
            if isinstance(raw.get("facts"), list):
                excluded = [{"reason": "No source-verified facts"} for _ in raw["facts"]]
    score += min(len(verified), 3) * 10
    kinds = {item["kind"] for item in verified}
    if "function" in kinds:
        score += 8
    if "problem" in kinds:
        score += 7
    if len(verified) >= 2:
        score += 15
    return min(score, 100), verified, excluded

def main():
    if os.environ.get("RUN_MODEL_MATRIX_ONCE") != "ALLOW_ONE_MATRIX_BATCH_20261020":
        raise SystemExit("No one-shot opt-in; zero paid calls")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("Missing server-side OPENROUTERKEY; zero paid calls")
    if PREVIOUS_CONFIRMED + NEW_BATCH_CAP > PROJECT_CEILING:
        raise SystemExit("Known aggregate ceiling exceeded; zero paid calls")
    report = {
        "kind": "one shared facts-v1 OpenRouter matrix smoke",
        "scope": "one Fact Extraction call per eligible model; NOT full three-stage E2E",
        "quality_note": "objective citation/JSON rubric, NOT ChatGPT similarity",
        "project_ceiling_usd": str(PROJECT_CEILING),
        "previous_confirmed_usd": str(PREVIOUS_CONFIRMED),
        "new_batch_cap_usd": str(NEW_BATCH_CAP),
        "per_call_reserve_cap_usd": str(PER_CALL_RESERVE_CAP),
        "new_spent_usd": "0", "models": [], "finished": False,
    }
    save(report)
    # Single FREE catalog request; all candidates use this same observed snapshot.
    catalog = {row["id"]: row for row in fetch_openrouter_models()
               if isinstance(row, dict) and isinstance(row.get("id"), str)}
    spent = Decimal(0)
    # Cheap models first to maximize coverage under the fixed dollar ceiling.
    order = [m for m in MODEL_IDS if m not in STRONG] + [
        m for m in MODEL_IDS if m in STRONG]
    for model_id in order:
        row = {"model_id": model_id, "tier": "strong" if model_id in STRONG else "economy",
               "target_rubric_pct": 80 if model_id in STRONG else 65}
        model = catalog.get(model_id)
        if model is None:
            row["status"] = "catalog_missing"
        else:
            try:
                cap = normalize_capability(model)
                rates(model.get("pricing"))  # reject unknown and conditional prices
                if not (cap.get("text_output") and cap.get("supports_structured_outputs")):
                    row["status"] = "unsupported_structured_output"
                else:
                    available = min(NEW_BATCH_CAP - spent,
                                    PROJECT_CEILING - PREVIOUS_CONFIRMED - spent)
                    choice = choose_stage(model_id, cap, model["pricing"], available)
                    if choice is None:
                        row["status"] = "skipped_reservation_or_context"
                    else:
                        stage, snap, reserve = choice
                        row.update(status="attempting", reasoning=stage["reasoning"],
                                   max_output_tokens=stage["max_output_tokens"],
                                   reserved_usd=str(reserve))
                        report["models"].append(row)
                        save(report)  # persist intent BEFORE any charge
                        try:
                            result = invoke(stage, snap, INPUTS, key=key, timeout=210)
                        except Exception:
                            row["status"] = "unknown_provider_outcome_STOP"
                            report["stop_reason"] = "Unknown billing/transport; no retry"
                            save(report)
                            return  # one unknown charge means no more provider calls
                        charge = result["charged_usd"]
                        spent += charge
                        row["charged_usd"] = str(charge)
                        row["usage"] = result["usage"]
                        row["response_received"] = True
                        row["model_matches"] = result["model_id"] in (None, model_id)
                        row["finish_reason"] = result["finish_reason"]
                        score, facts, excluded = grade(result)
                        row["rubric_pct"] = score
                        row["rubric_target_met"] = score >= row["target_rubric_pct"]
                        row["verified_fact_count"] = len(facts)
                        row["excluded_fact_count"] = len(excluded)
                        # Only synthetic fixture data; never save user or secret content.
                        row["synthetic_answer_preview"] = [
                            {"kind": item["kind"], "text": item["text"]}
                            for item in facts[:5]]
                        row["status"] = (
                            "valid_response" if len(facts) >= 2 and row["model_matches"]
                            else "response_needs_review")
                        report["new_spent_usd"] = str(spent)
                        save(report)
                        if charge > reserve or spent > NEW_BATCH_CAP or (
                            PREVIOUS_CONFIRMED + spent > PROJECT_CEILING):
                            row["status"] = "billing_cap_violation_STOP"
                            report["stop_reason"] = "Reported cost exceeded conservative guard"
                            save(report)
                            return
                        continue
            except PipelineError:
                row["status"] = "unsupported_catalog_or_pricing"
        report["models"].append(row)
        save(report)
    report["finished"] = True
    report["known_total_usd"] = str(PREVIOUS_CONFIRMED + spent)
    save(report)
    print("Model matrix finished. Successful JSON/citation responses:",
          sum(r["status"] == "valid_response" for r in report["models"]),
          "of", len(MODEL_IDS), "; known new cost USD", spent)

if __name__ == "__main__":
    main()
