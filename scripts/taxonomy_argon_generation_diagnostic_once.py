"""ONE direct paid request reproducing Argon High reasoning taxonomy-v2 transport.

Capture the ORIGINAL HTTP status and provider body, not the normal generic
ProviderUncertain wrapper. Redact only the API key; fixture data is synthetic.
One unique attempt; no retries. No DB or publication.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json
import os
import socket
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from intelligence.taxonomy_builder.catalog import fetch_openrouter_models
from intelligence.taxonomy_builder.pipeline_contracts import normalize_capability, PipelineError
from intelligence.taxonomy_builder.provider import request_payload, reservation_cost, URL
from intelligence.taxonomy_builder.cost_estimator import rates
from intelligence.taxonomy_builder.run_contracts import reviewable_topic_batch
from scripts.taxonomy_edema_five_e2e import PROFILE, ARGON

# Known ledger: old E2E $0.045898899 + matrix $0.045069680708 +
# v10 $0.119808640000 + v11 Argon facts $0.011022000000.
KNOWN_SPEND=Decimal("0.221799219708")
# The prior unknown v11 Generation is conservatively reserved as potentially
# charged at the 14-fact preflight maximum, even though only 10 facts were sent.
PREVIOUS_UNKNOWN_HOLD=Decimal("0.293347200000")
SINGLE_RESERVATION_CAP=Decimal("0.35")
PROJECT_CEILING=Decimal("0.95")
OUT=Path("taxonomy_argon_generation_diagnostic_once.json")

def publish(obj):
    OUT.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")

def redact(value,secret):
    if not isinstance(value,str):
        value=str(value)
    return value.replace(secret,"[REDACTED_API_KEY]").replace("Bearer "+secret,"Bearer [REDACTED_API_KEY]")

def synthetic_approved_facts():
    # Match exact substrings in the SAME synthetic 6k-character profile,
    # represent 10 human-reviewed facts from the previous disposable test.
    entries=[
      ("function","product_functions",
       "позволяет взрослому человеку вести дневник субъективного ощущения отёчности лица, кистей, стоп и голеней по утрам и вечерам."),
      ("function","product_functions",
       "Пользователь самостоятельно отмечает участок тела, время суток, приблизительную выраженность ощущения по шкале от одного до пяти"),
      ("function","product_functions",
       "можно фиксировать, когда ощущалась тяжесть после длительного сидения или стояния"),
      ("function","product_functions",
       "предоставляет отдельные поля для учёта примерной продолжительности сна, времени отхода ко сну"),
      ("function","product_functions",
       "образовательная программа ежедневных привычек для людей"),
      ("problem","audience_problems",
       "Некоторые взрослые замечают по утрам субъективную припухлость лица"),
      ("problem","audience_problems",
       "Другие ощущают тяжесть в ногах после работы за столом"),
      ("problem","audience_problems",
       "Люди путают временное субъективное ощущение тяжести с клиническим отёком"),
      ("problem","audience_problems",
       "Некоторым неловко рассказывать врачу о нерегулярных симптомах"),
      ("problem","audience_problems",
       "Людям сложно отличить осторожный образовательный разбор от рекламы"),
    ]
    output=[]
    for i,(kind,field,quote) in enumerate(entries,1):
        if quote not in PROFILE[field]:
            raise RuntimeError("Synthetic fixture quote mismatch. No paid calls.")
        output.append({
          "id":f"00000000-0000-4000-8000-{i:012d}",
          "kind":kind,"text":quote[:150],
          "source_field":field,"source_quote":quote,
          "origin":"source_verified","review_status":"approved",
        })
    return output

def main():
    if os.environ.get("RUN_ARGON_DIAG_ONCE")!="I_APPROVE_ONE_RAW_ARGON_GENERATION_20261020":
        raise SystemExit("No one-shot approval: zero paid calls")
    key=os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("No server-side secret: zero paid calls")
    if KNOWN_SPEND+PREVIOUS_UNKNOWN_HOLD+SINGLE_RESERVATION_CAP>PROJECT_CEILING:
        raise SystemExit("Known + previous unknown reserve + new cap over limit: zero paid calls")
    report={
        "test":"ONE raw-HTTP Argon taxonomy_generation high-reasoning diagnostic",
        "known_prior_usd":str(KNOWN_SPEND),
        "previous_uncertain_held_usd":str(PREVIOUS_UNKNOWN_HOLD),
        "new_request_reservation_cap_usd":str(SINGLE_RESERVATION_CAP),
        "new_attempt_sent":False,
        "outcome":"not_sent",
        "caveat":"Previous exact 10 LLM facts were not persisted; using ten deterministic source-verified facts from the same synthetic profile.",
    }
    publish(report)
    # Catalog endpoint is FREE and does not use the key.
    model=next((m for m in fetch_openrouter_models() if isinstance(m,dict)
               and m.get("id")==ARGON),None)
    if model is None:
        raise SystemExit("Argon not present in provider catalog: no paid calls")
    cap=normalize_capability(model)
    rates(model.get("pricing"))
    if not cap.get("text_output") or not cap.get("supports_structured_outputs"):
        raise SystemExit("Unsupported provider JSON capability: no paid calls")
    if "high" not in cap.get("reasoning_efforts",[]):
        raise SystemExit("High reasoning not catalog-verified: no paid calls")
    if (cap.get("max_output_tokens") or 0)<10000:
        raise SystemExit("Argon max completion insufficient: no paid calls")
    stage={
      "role":"taxonomy_generation","model_id":ARGON,
      "reasoning":{"mode":"effort","effort":"high"},
      "temperature":None,"max_output_tokens":10000,
      "prompt_version":"taxonomy-v2"
    }
    facts=synthetic_approved_facts()
    inputs={
      "approved_facts":facts,"niche":"health_fitness",
      "research_goal":"maximize_views",
      "request":(
        "Generate 8-12 distinct, useful Health & Fitness video-research "
        "directions across direct, problem_related, and adjacent. "
        "Include grounded links to approved facts, precise inclusion "
        "and exclusion rules, and concrete illustrative examples "
        "for each category. Do not invent product capabilities.")
    }
    snap={"capabilities":cap,"pricing":model["pricing"]}
    body=request_payload(stage,snap,inputs)
    reserve=reservation_cost(body,snap)
    report.update({
      "model":ARGON,
      "reasoning":stage["reasoning"],
      "max_tokens":stage["max_output_tokens"],
      "input_fact_count":len(facts),
      "reserved_usd":str(reserve),
      "json_schema":"taxonomy_generation",
      "provider_flags":body["provider"]
    })
    publish(report)
    if reserve>SINGLE_RESERVATION_CAP or KNOWN_SPEND+PREVIOUS_UNKNOWN_HOLD+reserve>PROJECT_CEILING:
        raise SystemExit("Provider reservation exceeds conservative dollar cap: zero paid calls")

    data=json.dumps(body,ensure_ascii=False).encode("utf-8")
    req=Request(URL,data=data,method="POST",headers={
      "Authorization":"Bearer "+key,
      "Content-Type":"application/json",
      "Accept":"application/json",
      "HTTP-Referer":"https://github.com/BanSins7/ContentMachine",
      "X-Title":"ContentMachine Taxonomy Builder",
    })
    report["new_attempt_sent"]=True
    report["outcome"]="request_dispatched_outcome_pending"
    publish(report)
    # Exactly ONE chargeable request. No retries, no client fallback.
    try:
        with urlopen(req,timeout=300) as response:
            status=response.status
            raw=response.read(2_000_001)
        report["http_status"]=status
        report["raw_response_bytes"]=len(raw)
        if len(raw)>2_000_000:
            report["outcome"]="response_too_large_billing_uncertain"
            publish(report)
            return
        parsed=json.loads(raw.decode("utf-8"))
        report["provider_response_id"]=parsed.get("id") if isinstance(parsed,dict) else None
        report["usage"]=parsed.get("usage") if isinstance(parsed,dict) else None
        report["model_returned"]=parsed.get("model") if isinstance(parsed,dict) else None
        choice=(parsed.get("choices") or [{}])[0] if isinstance(parsed,dict) else {}
        report["finish_reason"]=choice.get("finish_reason")
        message=choice.get("message") or {}
        content=message.get("content")
        if isinstance(content,str):
            report["completion_characters"]=len(content)
            try:
                doc=json.loads(content)
                batch=reviewable_topic_batch(doc,facts)
                report["valid_topic_count"]=len(batch["topics"])
                report["excluded_topic_count"]=len(batch["excluded_topics"])
                report["topic_names"]=[t["name"] for t in batch["topics"]]
                report["outcome"]="HTTP_200_VALID_TAXONOMY"
            except Exception as e:
                report["outcome"]="HTTP_200_INVALID_TAXONOMY"
                report["validation_error_type"]=type(e).__name__
                report["validation_error_message"]=redact(str(e),key)
                report["raw_completion_prefix"]=redact(content[:12000],key)
        else:
            report["outcome"]="HTTP_200_NO_TEXT"
            report["raw_success_body"]=redact(raw[:12000].decode("utf-8","replace"),key)
    except HTTPError as e:
        # Print the actual OpenRouter HTTP status + error JSON body.
        # No abstraction into ProviderUncertain, no retry.
        report["outcome"]="HTTP_ERROR"
        report["http_status"]=e.code
        report["http_reason"]=redact(e.reason,key)
        report["raw_provider_error_body"]=redact(e.read(20000).decode("utf-8","replace"),key)
    except Exception as e:
        # No HTTP response exists; report the native Python exception class.
        report["outcome"]="TRANSPORT_OR_PARSE_ERROR"
        report["exception_class"]=type(e).__name__
        report["raw_exception"]=redact(repr(e),key)
        if isinstance(e,URLError):
            report["underlying_reason"]=redact(repr(e.reason),key)
        if isinstance(e,(TimeoutError,socket.timeout)):
            report["timeout_seconds"]=300
    finally:
        publish(report)
        # Do not print authorization headers, request body or secret.
        for name in ("outcome","http_status","http_reason","raw_provider_error_body",
                     "exception_class","raw_exception","underlying_reason",
                     "finish_reason","usage","valid_topic_count","validation_error_message"):
            if name in report:
                print(name+":",report[name])

if __name__=="__main__":
    main()
