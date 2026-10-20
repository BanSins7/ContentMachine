"""Manually approved one-call OpenRouter smoke. Total budget $0.60; this call <=$0.015 reserve. Never included in normal PR CI."""
import json,os,sys
from decimal import Decimal
from pathlib import Path
from urllib.request import Request,urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from intelligence.taxonomy_builder.catalog import CATALOG_URL
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError,normalize_capability
from intelligence.taxonomy_builder.cost_estimator import rates
from intelligence.taxonomy_builder.provider import request_payload,reservation_cost,invoke
from intelligence.taxonomy_builder.run_contracts import validate_facts

ALLOWED=frozenset(("openai/gpt-6.1-sol","openai/gpt-6-luna",
 "google/gemini-4-argon","google/gemini-3.8-flash",
 "anthropic/claude-opus-5.5","anthropic/claude-sonnet-5.5",
 "anthropic/claude-haiku-5.5","deepseek/deepseek-v4.1-flash",
 "z-ai/glm-5.3-flash","xiaomi/mimo-v2.6-pro","xiaomi/mimo-v2.6-flash",
 "qwen/qwen3.8-27b"))
USER_TOTAL_LIMIT=Decimal("0.60")
ONE_CALL_LIMIT=Decimal("0.015")

def main():
    if os.getenv("RUN_LIVE_TAXONOMY_TEST")!="YES_I_APPROVE_ONE_REQUEST":
        raise SystemExit("Explicit live-test opt-in missing: zero paid calls")
    key=os.getenv("OPENROUTER_API_KEY")
    if not key:raise SystemExit("Actions secret OPENROUTERKEY not available: zero paid calls")
    with urlopen(Request(CATALOG_URL,headers={"Accept":"application/json"}),timeout=15) as res:
        data=json.load(res)["data"]
    good=[]
    for item in data:
        if item.get("id") not in ALLOWED:continue
        try:
            cap=normalize_capability(item)
            prices=rates(item.get("pricing"))
            if cap["text_output"] and cap["supports_structured_outputs"] and (
                cap.get("max_output_tokens") or 0)>=256:
                good.append((item,cap,prices))
        except PipelineError:continue
    if not good:raise SystemExit("No verified candidate supports JSON; zero paid calls")
    item,cap,prices=min(good,key=lambda x:(
        x[2]["prompt"]*1000+x[2]["completion"]*256+x[2]["request"]))
    stage={"role":"fact_extraction","model_id":item["id"],
           "max_output_tokens":256,"reasoning":{"mode":"default"},
           "temperature":None,"prompt_version":"facts-v1"}
    snap={"capabilities":cap,"pricing":item["pricing"]}
    profile={"product_functions":"Приложение считает калории по фото",
             "audience_problems":"Людям сложно считать калории"}
    inputs={"questionnaire":profile,"niche":"health_fitness",
            "research_goal":"maximize_views","attachments":"excluded"}
    reservation=reservation_cost(request_payload(stage,snap,inputs),snap)
    if reservation>ONE_CALL_LIMIT or reservation>USER_TOTAL_LIMIT:
        raise SystemExit("Reservation exceeds one-call allowance; zero paid calls")
    print("Verified model:",item["id"],"reservation USD:",reservation,
          "one-call reservation cap:",ONE_CALL_LIMIT,"aggregate authorized cap:",USER_TOTAL_LIMIT)
    # THIS IS THE ONLY PAID CALL. An uncertain result is never retried.
    try:result=invoke(stage,snap,inputs,key=key,timeout=50)
    except Exception as exc:
        raise SystemExit("Unknown provider outcome: STOP and inspect billing before any retry") from exc
    valid=False
    try:valid=bool(validate_facts(result["raw_json"],profile))
    except PipelineError:pass
    print("Provider reported USD",result["charged_usd"],"verified facts",valid,
          "tokens",result["usage"])
    Path("taxonomy_live_smoke_result.json").write_text(json.dumps({
      "model_id":item["id"],"reserved_usd":str(reservation),
      "observed_usd":str(result["charged_usd"]),"valid_facts":valid,
      "usage":result["usage"]},indent=2),encoding="utf-8")
    if result["charged_usd"]>ONE_CALL_LIMIT:
        raise SystemExit("Unexpected provider cost over per-call allowance; STOP")
    if not valid:raise SystemExit("Facts invalid, already billed once; NO retry")

if __name__=="__main__":main()
