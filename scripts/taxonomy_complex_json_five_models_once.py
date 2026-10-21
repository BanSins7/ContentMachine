"""ONE complex, UNMODIFIED taxonomy JSON-Schema request to each of five selected models.

Explicit user request, synthetic prompt, real OpenRouter, no automatic retry.
This is a transport/structured-output comparison, NOT full multi-stage E2E.
Records exact native OpenRouter HTTP errors and strictly validates returned topics.
The $1 key limit includes *conservative holds* for previous uncertain requests.
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from hashlib import sha256

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from intelligence.taxonomy_builder.catalog import fetch_openrouter_models
from intelligence.taxonomy_builder.pipeline_contracts import normalize_capability, PipelineError
from intelligence.taxonomy_builder.provider import request_payload, reservation_cost, URL
from intelligence.taxonomy_builder.cost_estimator import rates
from intelligence.taxonomy_builder.run_contracts import schema_for, reviewable_topic_batch

SOL="openai/gpt-6.1-sol"
OPUS="anthropic/claude-opus-5.5"
PRO="xiaomi/mimo-v2.6-pro"
FLASH="xiaomi/mimo-v2.6-flash"
# Two MiMos, plus OpenAI and Anthropic = FOUR DISTINCT models.
MODELS=(FLASH,PRO,SOL,OPUS)

KNOWN_USD=Decimal("0.221799219708")
# Previous v11 Argon Generation had no provider usage; hold worst-case.
PREVIOUS_UNKNOWN_HOLD=Decimal("0.293347200000")
# v12 HTTP 400 usually unbilled but hold its full preflight reserve anyway.
V12_HTTP400_HOLD=Decimal("0.280459200000")
KEY_GUARDED_CEILING=Decimal("0.99")
NEW_BATCH_CEILING=Decimal("0.18")
MAX_INDIVIDUAL_RESERVE=Decimal("0.15")
OUT=Path("taxonomy_complex_json_four_models_result.json")

FACTS=[
 {"id":"00000000-0000-4000-8000-000000000001","kind":"function",
  "text":"Приложение позволяет вести дневник субъективных ощущений отёчности.",
  "origin":"source_verified","review_status":"approved"},
 {"id":"00000000-0000-4000-8000-000000000002","kind":"function",
  "text":"Приложение показывает историю записей за семь и тридцать дней.",
  "origin":"source_verified","review_status":"approved"},
 {"id":"00000000-0000-4000-8000-000000000003","kind":"problem",
  "text":"Людям сложно вспомнить частоту и обстоятельства появления припухлости.",
  "origin":"source_verified","review_status":"approved"},
 {"id":"00000000-0000-4000-8000-000000000004","kind":"problem",
  "text":"Люди путают симптомы с утверждениями блогеров о чудо-лечении.",
  "origin":"source_verified","review_status":"approved"}
]
INPUTS={
 "approved_facts":FACTS,"niche":"health_fitness","research_goal":"maximize_views",
 "request":(
  "This is a ONE-CALL JSON Schema interoperability diagnostic. "
  "Write exactly THREE distinct short video-research categories: "
  "one direct, one problem_related, one adjacent. Each category needs all "
  "nine required fields, 1-2 positive_examples and 1-2 negative_examples. "
  "Cite EXACT approved UUID fact IDs for direct and problem_related; "
  "an adjacent category may use []. Keep each textual field concise, "
  "preferably below 110 characters, to complete JSON within token limits. "
  "Do not suggest treatment, claim to cure edema, invent product capabilities, "
  "or pretend hypothetical video examples were verified real videos."
 )
}
def save(report):
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")

def sanitized_error(raw,key):
    """Keep provider error verbatim except explicitly personal metadata/secret."""
    t=raw.decode("utf-8","replace")[:16000].replace(key,"[REDACTED]")
    try:
        data=json.loads(t)
    except (ValueError,TypeError):
        return {"error_text":t[:3000]}
    if isinstance(data,dict):
        data.pop("user_id",None)
        e=data.get("error")
        if isinstance(e,dict):
            e.pop("user_id",None)
            meta=e.get("metadata")
            if isinstance(meta,dict):
                meta.pop("user_id",None)
                meta.pop("is_byok",None)
                # raw often includes sensitive provider-side metadata. Preserve
                # only the exact displayed provider error and route name.
                for k in list(meta):
                    if k not in ("raw","provider_name","provider_error_code"):
                        meta.pop(k,None)
    return data

def stage_for(model,cap,pricing,available):
    if (not cap.get("text_output") or not cap.get("supports_structured_outputs")
        or "structured_outputs" not in cap.get("supported_parameters",[])):
        return None,"no_verified_json_schema_support"
    try: rates(pricing)
    except PipelineError as exc:
        return None,"unsupported_or_conditional_pricing: "+str(exc)[:160]
    # Default reasoning isolates schema compatibility without a long analysis
    # using up the entire completion budget.
    if cap.get("reasoning_mandatory"):
        available_efforts=cap.get("reasoning_efforts",[])
        low=next((r for r in ("low","minimal","medium","high") if r in available_efforts),None)
        if not low:
            return None,"mandatory_reasoning_without_verified_effort"
        reasoning={"mode":"effort","effort":low}
    else: reasoning={"mode":"default"}
    for tokens in (2700,2400,2100,1800,1550,1350):
        if tokens > (cap.get("max_output_tokens") or 0): continue
        stage={
          "role":"taxonomy_generation","model_id":model,
          "reasoning":reasoning,"temperature":None,
          "max_output_tokens":tokens,"prompt_version":"taxonomy-v1"
        }
        snap={"capabilities":cap,"pricing":pricing}
        try:
            payload=request_payload(stage,snap,INPUTS)
            reserve=reservation_cost(payload,snap)
        except PipelineError:
            continue
        if reserve<=MAX_INDIVIDUAL_RESERVE and reserve<=available:
            return (stage,snap,payload,reserve),None
    return None,"no_safe_budget_or_token_configuration"

def main():
    if os.environ.get("RUN_COMPLEX_JSON_FOUR_MODELS_ONCE")!="YES_20261020_ONE_CALL_EACH":
        raise SystemExit("Explicit one-shot opt-in missing")
    key=os.getenv("OPENROUTER_API_KEY")
    if not key: raise SystemExit("Missing OpenRouter key; no paid calls")
    if KNOWN_USD+PREVIOUS_UNKNOWN_HOLD+V12_HTTP400_HOLD+NEW_BATCH_CEILING>KEY_GUARDED_CEILING:
        raise SystemExit("Prior unknown holds + projected new tests exceed key guard")
    original=schema_for("taxonomy_generation")
    if not original["strict"] or original["schema"]["properties"]["topics"].get("maxItems")!=20:
        raise SystemExit("Not the original COMPLEX strict JSON Schema: stop unpaid")
    report={
      "kind":"single taxonomy Generation request per model, original strict complex schema",
      "scope":"One independently checked API call per eligible model. Not full E2E.",
      "models_in_requested_order":[SOL,OPUS,PRO,FLASH],
      "note":"User says five models, but names four: Sol, Opus, MiMo Pro, MiMo Flash.",
      "schema_sha256":sha256(json.dumps(original,sort_keys=True).encode()).hexdigest(),
      "known_previous_usd":str(KNOWN_USD),
      "previous_uncertain_hold_usd":str(PREVIOUS_UNKNOWN_HOLD),
      "diagnostic_HTTP400_hold_usd":str(V12_HTTP400_HOLD),
      "new_batch_max_reservation_usd":str(NEW_BATCH_CEILING),
      "sent_calls":0,"new_reported_usd":"0","new_held_reservations_usd":"0",
      "status":"preflight","results":[]}
    save(report)
    catalog={r.get("id"):r for r in fetch_openrouter_models()
             if isinstance(r,dict) and isinstance(r.get("id"),str)}
    reported=Decimal(0)
    held=Decimal(0)
    for model_id in MODELS:
        row={"model_id":model_id,"status":"preflight","request_dispatched":False}
        report["results"].append(row);save(report)
        raw=catalog.get(model_id)
        if not raw:
            row.update(status="skipped",reason="not_in_current_OpenRouter_catalog")
            save(report);continue
        try:
            cap=normalize_capability(raw)
        except PipelineError as exc:
            row.update(status="skipped",reason="invalid_catalog_capabilities: "+str(exc)[:180])
            save(report);continue
        available=min(NEW_BATCH_CEILING-reported-held,
                      KEY_GUARDED_CEILING-KNOWN_USD-PREVIOUS_UNKNOWN_HOLD
                      -V12_HTTP400_HOLD-reported-held)
        candidate,error=stage_for(model_id,cap,raw.get("pricing"),available)
        if not candidate:
            row.update(status="skipped",reason=error)
            save(report);continue
        stage,snap,payload,reserve=candidate
        request_schema=payload.get("response_format",{}).get("json_schema")
        if request_schema!=original:
            raise SystemExit("Complex schema changed in request; no paid call")
        row.update(
           status="dispatched_no_response_yet",
           request_dispatched=True,
           reserved_usd=str(reserve),
           max_output_tokens=stage["max_output_tokens"],
           reasoning=stage["reasoning"],
           json_schema_sent="ORIGINAL_COMPLEX_STRICT",
           input_fact_count=len(FACTS)
        )
        report["sent_calls"]+=1;save(report)
        req=Request(URL,method="POST",
          data=json.dumps(payload,ensure_ascii=False).encode("utf-8"),
          headers={"Authorization":"Bearer "+key,"Content-Type":"application/json",
            "Accept":"application/json",
            "HTTP-Referer":"https://github.com/BanSins7/ContentMachine",
            "X-Title":"ContentMachine Complex Taxonomy JSON Test"})
        try:
            with urlopen(req,timeout=175) as response:
                status=response.status
                wire=response.read(2_000_001)
        except HTTPError as exc:
            row["http_status"]=exc.code
            row["status"]="http_rejected" if 400<=exc.code<500 else "http_provider_failure"
            row["original_error"]=sanitized_error(exc.read(16_000),key)
            # For all missing-usage errors conservatively hold the reservation.
            held+=reserve
            report["new_held_reservations_usd"]=str(held)
            save(report)
            if exc.code>=500:report["status"]="STOP_unknown_billing";break
            continue
        except (OSError,TimeoutError,ValueError,URLError) as exc:
            row.update(status="unknown_transport_STOP",exception_type=type(exc).__name__,
                       exception_message=repr(exc).replace(key,"[REDACTED]")[:300])
            held+=reserve;report["new_held_reservations_usd"]=str(held)
            report["status"]="STOP_unknown_billing";save(report);break
        row["http_status"]=status
        if len(wire)>2_000_000:
            held+=reserve;row["status"]="response_too_large_STOP"
            report["new_held_reservations_usd"]=str(held)
            report["status"]="STOP_unknown_billing";save(report);break
        try: response=json.loads(wire)
        except (ValueError,TypeError) as exc:
            held+=reserve;row.update(status="invalid_provider_response_STOP",
                                     error=type(exc).__name__)
            report["new_held_reservations_usd"]=str(held)
            report["status"]="STOP_unknown_billing";save(report);break
        usage=response.get("usage") if isinstance(response,dict) else None
        cost=usage.get("cost") if isinstance(usage,dict) else None
        if cost is None:
            held+=reserve;row.update(status="provider_missing_usage_STOP")
            report["new_held_reservations_usd"]=str(held)
            report["status"]="STOP_unknown_billing";save(report);break
        charge=Decimal(str(cost))
        reported+=charge
        report["new_reported_usd"]=str(reported)
        choices=response.get("choices") or []
        msg=choices[0].get("message",{}) if choices else {}
        content=msg.get("content") if isinstance(msg,dict) else None
        row.update(status="http_200",reported_cost_usd=str(charge),
                   reported_usage={
                      "prompt_tokens":usage.get("prompt_tokens"),
                      "completion_tokens":usage.get("completion_tokens"),
                      "reasoning_tokens":(usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
                   },provider_model=response.get("model"),
                   finish_reason=choices[0].get("finish_reason") if choices else None,
                   response_id=response.get("id"),output_chars=len(content) if isinstance(content,str) else 0)
        if not isinstance(content,str) or not content.strip():
            row.update(status="accepted_but_no_text")
        else:
            try:
                result=json.loads(content)
                row["json_parsed"]=True
                batch=reviewable_topic_batch(result,FACTS)
                topics=batch["topics"]
                row["valid_topics"]=len(topics)
                row["rejected_topics"]=len(batch["excluded_topics"])
                row["rejection_reasons"]=[x["reason"] for x in batch["excluded_topics"]]
                row["topics"]=[{"name":t["name"],"relation":t["relation"],
                                "definition":t["definition"],"positive_examples":t["positive_examples"],
                                "negative_examples":t["negative_examples"],
                                "fact_ids":t["fact_ids"]} for t in topics]
                row["status"]="accepted_valid_grounded_taxonomy"
            except Exception as exc:
                row.update(status="accepted_invalid_taxonomy",json_parsed=True,
                           validation_error_type=type(exc).__name__,
                           validation_error=str(exc)[:300],
                           output_prefix=content[:6000])
        save(report)
        if charge>reserve or reported+held>NEW_BATCH_CEILING or (
                KNOWN_USD+PREVIOUS_UNKNOWN_HOLD+V12_HTTP400_HOLD+reported+held>KEY_GUARDED_CEILING):
            report["status"]="STOP_billings_outside_budget";save(report);break
    else:
        report["status"]="completed"
    report["finished"]=True
    report["known_cumulative_usd"]=str(KNOWN_USD+reported)
    report["key_guard_with_conservative_holds_usd"]=str(
         KNOWN_USD+PREVIOUS_UNKNOWN_HOLD+V12_HTTP400_HOLD+reported+held)
    save(report)
    for m in report["results"]:
        print(m["model_id"],m["status"],"HTTP",m.get("http_status"),
              "cost",m.get("reported_cost_usd"),"topics",m.get("valid_topics"),
              "reason",m.get("reason") or m.get("validation_error"),
              "provider_error",m.get("original_error"))
    print("NEW USD",reported,"HELD USD",held,"OUTCOME",report["status"])

if __name__=="__main__":
    main()
