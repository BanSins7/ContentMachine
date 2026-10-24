"""Block 5: deterministic *indicative* Taxonomy Builder cost estimate.

OpenRouter pricing.prompt/completion are USD PER TOKEN, not per 1M tokens.
No tokenizer, real prompt, human-reviewed facts or provider call is available yet;
these assumptions are intentionally displayed and never a guaranteed ceiling.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from .pipeline_contracts import PipelineError, sha256

ESTIMATOR_VERSION="tb-heuristic-v1"
SIX=Decimal("0.000001")
MAX_AUTHORIZED=Decimal("10000")
STAGE_OVERHEAD={"fact_extraction":900,"taxonomy_generation":1600,"taxonomy_critic":1100}
OUTPUT_FACTORS={"low":Decimal("0.15"),"expected":Decimal("0.45"),"high":Decimal("1")}
INPUT_FACTORS={"low":Decimal("0.25"),"expected":Decimal("0.55"),"high":Decimal("0.9")}

def amount(value,field):
    if isinstance(value,bool) or type(value) not in (int,float,str,Decimal):
        raise PipelineError("Unsupported price for "+field,422)
    try:
        parsed=Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PipelineError("Unparseable price for "+field,422) from exc
    if not parsed.is_finite() or parsed<0:
        raise PipelineError("Non-finite or negative price for "+field,422)
    return parsed

def usd(value):
    return str(value.quantize(SIX, rounding=ROUND_CEILING))

def rates(pricing):
    if not isinstance(pricing,dict):
        raise PipelineError("Missing verified OpenRouter pricing")
    if pricing.get("overrides"):
        raise PipelineError("Conditional/long-context pricing needs a separate verified estimator")
    if pricing.get("discount") is not None and amount(pricing["discount"],"discount")!=0:
        raise PipelineError("Conditional pricing discounts need verification")
    result={}
    for name in ("prompt","completion"):
        if pricing.get(name) is None:
            raise PipelineError("Unknown "+name+" token price; quote blocked")
        result[name]=amount(pricing[name],name)
    for name in ("request","internal_reasoning"):
        result[name]=amount(pricing[name],name) if pricing.get(name) is not None else Decimal(0)
    return result

def estimate(profile: dict,documents: list,config: dict, snapshots: dict, *, context_evidence=None) -> dict:
    if not isinstance(profile,dict) or not isinstance(documents,list):
        raise PipelineError("A verified questionnaire snapshot is required")
    pieces=[str(v) for v in profile.values() if isinstance(v,str)]
    if context_evidence is not None:
        if not isinstance(context_evidence,list) or not context_evidence:
            raise PipelineError("Approved context Evidence is required",409)
        pieces.extend(str(row.get('text',''))+' '+str(row.get('citation',''))
                      for row in context_evidence)
    raw_bytes=len("\n".join(pieces).encode("utf-8"))
    if raw_bytes>250_000:
        raise PipelineError("Questionnaire is too long for reliable preflight pricing")
    result=[]
    total={q:Decimal(0) for q in OUTPUT_FACTORS}
    previous_output={q:0 for q in OUTPUT_FACTORS}
    for stage in config.get("stages",[]):
        if not stage.get("enabled",True):
            continue
        role=stage["role"]
        snapshot=snapshots.get(role)
        if not snapshot or snapshot.get("model_id")!=stage["model_id"]:
            raise PipelineError("Pipeline has no verified immutable model snapshot")
        cap=snapshot.get("capabilities",{})
        if not cap.get("text_output") or not cap.get("supports_structured_outputs"):
            raise PipelineError("Model cannot provide verified text JSON output")
        price=rates(snapshot.get("pricing"))
        base=STAGE_OVERHEAD[role]
        max_tokens=stage["max_output_tokens"]
        assumptions={}
        amounts={}
        for scenario in OUTPUT_FACTORS:
            expected_in=base + int(Decimal(raw_bytes)*INPUT_FACTORS[scenario]) + previous_output[scenario]
            expected_out=max(1,int(Decimal(max_tokens)*OUTPUT_FACTORS[scenario]))
            if cap.get("context_length") and expected_in+max_tokens > cap["context_length"]:
                raise PipelineError("Estimated inputs exceed the model context window: "+role)
            # Internal reasoning is a separate possible SKU; count conservatively
            # against all projected output tokens when a separate rate is present.
            value=(Decimal(expected_in)*price["prompt"] +
                   Decimal(expected_out)*(price["completion"]+price["internal_reasoning"]) +
                   price["request"])
            amounts[scenario]=value
            assumptions[scenario]={"estimated_input_tokens":expected_in,
                                    "estimated_billable_output_tokens":expected_out,
                                    "estimated_usd":usd(value)}
            previous_output[scenario]=expected_out
            total[scenario]+=value
        result.append({
            "role":role,"model_id":stage["model_id"],
            "catalog_version_id":stage["catalog_version_id"],
            "reasoning":stage["reasoning"],"temperature":stage["temperature"],
            "max_output_tokens":max_tokens,"pricing_source":"https://openrouter.ai/api/v1/models",
            "pricing_verified_at":snapshot.get("verified_at"),
            "usd_per_million_input":usd(price["prompt"]*1000000),
            "usd_per_million_output":usd(price["completion"]*1000000),
            "usd_per_request":usd(price["request"]),
            "additional_reasoning_usd_per_million":usd(price["internal_reasoning"]*1000000),
            "scenarios":assumptions,
        })
    if not result or len(result)<2:
        raise PipelineError("A quote requires both mandatory LLM stages")
    return {
        "currency":"USD","estimator_version":ESTIMATOR_VERSION,
        "stages":result,
        "low_usd":usd(total["low"]),
        "expected_usd":usd(total["expected"]),
        "high_usd":usd(total["high"]),
        "assumptions":{
            "input_basis":("UTF-8 bytes of questionnaire AND selected approved contextual Evidence "
                           "with heuristic scenario factors" if context_evidence is not None else
                           "UTF-8 bytes of current saved questionnaire with heuristic scenario factors"),
            "prompt_allowances":"900/1600/1100 tokens for extraction/generation/critique respectively",
            "output_basis":"15% / 45% / 100% of per-stage configured max_output_tokens",
            "reasoning_note":"Reasoning tokens are estimated as part of billable output; separate reasoning rates are conservatively added where present",
            "uncertainty":"Indicative scenario range, NOT a guaranteed maximum or a tokenizer-based estimate",
            "documents_included":context_evidence is not None,
            "documents_note":("Only chosen, approved document fragments are included, not every page"
                              if context_evidence is not None else "Research attachments excluded"),
            "excluded_documents_count":len(documents) if context_evidence is None else 0,
            "excluded_documents_bytes":sum(int(d.get("size") or 0) for d in documents if isinstance(d,dict)),
            "retrieval_scraping_and_other_fees_included":False,
            "provider_credit_purchase_fees_included":False,
            "retries_included":False,
            "human_review_can_change_generation_input":True,
            "price_tiers_or_special_routes_allowed":False,
        },
    }

def check_budget(raw_value,high_usd):
    authorized=amount(raw_value,"max_authorized_spend")
    if authorized>MAX_AUTHORIZED or authorized==0:
        raise PipelineError("Budget must be greater than zero and at most 10000 USD")
    if authorized<amount(high_usd,"estimated_high"):
        raise PipelineError("Budget must cover the high scenario; this is still not a guaranteed cap")
    if authorized != authorized.quantize(SIX):
        raise PipelineError("Budget supports up to six decimal places")
    return authorized
