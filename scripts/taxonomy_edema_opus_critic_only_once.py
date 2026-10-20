"""One real Opus 5.5 Critic ONLY, continuing source-verified v14 taxonomy.

- Reads the exact 12 original generated topics / 10 approved source facts
  from immutable GitHub Actions v14 artifact, verified with SHA256.
- Never reruns MiMo extraction or Opus taxonomy generation.
- 8,000->5,000 token output choices, same medium reasoning + critic-v2.
- Live OpenRouter key usage check and conservative reservation below $1.
- Exactly ONE chargeable call, no retry or fallback, no SRE publication.
- Native status/body recorded on errors (credentials redacted).
"""
from __future__ import annotations
import io,os,json,sys,hashlib,zipfile
from collections import Counter
from decimal import Decimal,ROUND_FLOOR
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from intelligence.taxonomy_builder.catalog import fetch_openrouter_models
from intelligence.taxonomy_builder.pipeline_contracts import normalize_capability,PipelineError
from intelligence.taxonomy_builder.cost_estimator import rates
from intelligence.taxonomy_builder.provider import request_payload,reservation_cost,URL
from intelligence.taxonomy_builder.run_contracts import schema_for,validate_critic

OPUS="anthropic/claude-opus-5.5"
ARTIFACT_ID=12278138025
SOURCE_SHA256="055a98afd084c6e7292051d8f58bd1f86b7787937d38c64f901c342dcc11cbe2"
SOURCE_NAME="taxonomy_edema_mimo_flash_opus_full_result.json"
OUT=Path("taxonomy_edema_opus_critic_only_v15_result.json")
USD_CEILING=Decimal("0.97")
MAX_ONE_REQUEST_RESERVE=Decimal("0.47")
EXTRA_SAFETY_MARGIN=Decimal("0.03")
MIN_OUTPUT_TOKENS=5000

def save(obj):
    OUT.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")

def github_source():
    token=os.environ.get("GH_TOKEN")
    repo=os.environ.get("GH_REPO")
    if not token or repo!="BanSins7/ContentMachine":
        raise RuntimeError("Missing exact GitHub artifact credentials")
    uri=f"https://api.github.com/repos/{repo}/actions/artifacts/{ARTIFACT_ID}/zip"
    req=Request(uri,headers={
        "Authorization":"Bearer "+token,
        "Accept":"application/vnd.github+json",
        "X-GitHub-Api-Version":"2022-11-28"})
    with urlopen(req,timeout=35) as response:
        binary=response.read(2_000_001)
    if len(binary)>2_000_000:raise RuntimeError("Source artifact archive too large")
    with zipfile.ZipFile(io.BytesIO(binary)) as archive:
        if archive.namelist()!=[SOURCE_NAME]:
            raise RuntimeError("Unexpected v14 artifact members")
        raw=archive.read(SOURCE_NAME)
    if hashlib.sha256(raw).hexdigest()!=SOURCE_SHA256:
        raise RuntimeError("Source artifact sha256 mismatch: do not call model")
    parsed=json.loads(raw.decode("utf-8"))
    original=parsed.get("tests") or []
    if len(original)!=1 or original[0].get("valid_topics")!=12:
        raise RuntimeError("Source run did not contain exactly 12 verified topics")
    test=original[0]
    if test.get("excluded_topics")!=0:
        raise RuntimeError("Source topics were excluded")
    if test.get("models_by_stage",{}).get("taxonomy_generation")!=OPUS:
        raise RuntimeError("Wrong source generation model")
    topics=test["generated_topics_full"]
    facts=test["extracted_facts_full"]
    approved=set(test["fixture_approved_fact_ids"])
    facts=[f for f in facts if f["id"] in approved]
    if len(topics)!=12 or len(facts)!=10:
        raise RuntimeError("Unverified original topic/fact count")
    names=[x["name"] for x in topics]
    if len(set(names))!=12: raise RuntimeError("Duplicated original topic names")
    valid_ids={f["id"] for f in facts}
    if any(not set(t["fact_ids"]).issubset(valid_ids) for t in topics):
        raise RuntimeError("Topic linked to missing approved fact")
    return topics,facts

def key_balance(key):
    req=Request("https://openrouter.ai/api/v1/key",headers={
        "Authorization":"Bearer "+key,"Accept":"application/json"})
    with urlopen(req,timeout=20) as response:
        obj=json.load(response)["data"]
    usage=Decimal(str(obj["usage"]))
    remaining=obj.get("limit_remaining")
    rem=Decimal(str(remaining)) if remaining is not None else None
    if not usage.is_finite() or usage<0 or (rem is not None and (not rem.is_finite() or rem<0)):
        raise RuntimeError("Invalid account usage/balance")
    return usage,rem

def safe_provider_error(binary,secret):
    raw=binary.decode("utf-8","replace")[:12000].replace(secret,"[REDACTED]")
    try:obj=json.loads(raw)
    except ValueError:return {"raw_text":raw[:3000]}
    if not isinstance(obj,dict):return {"raw_text":raw[:3000]}
    obj.pop("user_id",None)
    if isinstance(obj.get("error"),dict):
        obj["error"].pop("user_id",None)
        metadata=obj["error"].get("metadata")
        if isinstance(metadata,dict):
            for k in list(metadata):
                if k not in ("raw","provider_name","provider_error_code"):metadata.pop(k,None)
    return obj

def main():
    if os.environ.get("RUN_ONE_OPUS_EDMA_CRITIC")!="YES_SINGLE_OPUS_CRITIC_20261020":
        raise SystemExit("One-time paid approval not set: no calls")
    key=os.environ.get("OPENROUTER_API_KEY")
    if not key:raise SystemExit("Missing server-side key: no calls")
    record={"kind":"Opus 5.5 standalone v15 Critic for original 12 v14 research topics",
            "model":OPUS,"role":"taxonomy_critic","prompt_version":"critic-v2",
            "reasoning":{"mode":"effort","effort":"medium"},
            "original_source_artifact_id":ARTIFACT_ID,
            "original_source_sha256":SOURCE_SHA256,
            "uses_original_topics_no_generation":True,"sre_published":False,
            "paid_request_sent":False,"status":"preflight"}
    save(record)
    # All fallible no-cost preparation, including ORIGINAL source artifact retrieval,
    # happens before dispatching the sole paid model request.
    try:
        topics,facts=github_source()
        usage,remaining=key_balance(key)
        record.update(source_topic_count=len(topics),
                      source_topic_names=[t["name"] for t in topics],
                      approved_fact_count=len(facts),
                      openrouter_key_usage_before_usd=str(usage),
                      openrouter_key_limit_remaining_usd=(str(remaining)
                                                       if remaining is not None else None))
        catalog=fetch_openrouter_models()
        row=next((x for x in catalog if isinstance(x,dict) and x.get("id")==OPUS),None)
        if row is None:raise RuntimeError("Opus 5.5 absent from current OpenRouter catalog")
        cap=normalize_capability(row)
        pricing=row["pricing"]
        rates(pricing)
        if not cap.get("supports_structured_outputs") or not cap.get("text_output"):
            raise RuntimeError("Opus structured JSON output not currently verified")
        if "structured_outputs" not in cap.get("supported_parameters",[]):
            raise RuntimeError("OpenRouter strict JSON Schema transport missing")
        if "medium" not in cap.get("reasoning_efforts",[]):
            raise RuntimeError("Opus medium reasoning not available")
        maximum=min(MAX_ONE_REQUEST_RESERVE,USD_CEILING-usage-EXTRA_SAFETY_MARGIN)
        if remaining is not None:maximum=min(maximum,remaining-EXTRA_SAFETY_MARGIN)
        # Exact same scientific input objects as persisted from v14; no invented
        # topic IDs or facts. critic-v2 validates each cited topic name.
        inputs={"approved_facts":facts,"topics":topics,"review_only":True}
        snapshot={"capabilities":cap,"pricing":pricing}
        choice=None
        for limit in (8000,7600,7200,6800,6400,6000,5600,5200,5000):
            if limit>int(cap.get("max_output_tokens") or 0):continue
            stage={"role":"taxonomy_critic","model_id":OPUS,
                   "max_output_tokens":limit,"reasoning":{"mode":"effort","effort":"medium"},
                   "temperature":None,"prompt_version":"critic-v2"}
            body=request_payload(stage,snapshot,inputs)
            if body.get("response_format",{}).get("json_schema")!=schema_for("taxonomy_critic"):
                raise RuntimeError("Original critic JSON schema unexpectedly changed")
            required=reservation_cost(body,snapshot)
            if required<=maximum and limit>=MIN_OUTPUT_TOKENS:
                choice=(stage,body,required);break
        record["max_affordable_usd"]=str(maximum)
        if choice is None:
            record.update(status="preflight_no_affordable_5000_plus_token_request",
                          error="Safe key balance insufficient for 5000-8000 output tokens")
            save(record);return
        stage,body,reserve=choice
        record.update(status="request_prepared",output_token_limit=stage["max_output_tokens"],
                      old_output_limit=2000,conservative_reserved_usd=str(reserve),
                      prompt_topics_count=len(inputs["topics"]))
        save(record)
    except Exception as exc:
        record.update(status="preflight_failed_no_paid_calls",
                      error_type=type(exc).__name__,error_message=str(exc)[:300])
        save(record);return

    req=Request(URL,method="POST",
        data=json.dumps(body,ensure_ascii=False).encode("utf-8"),
        headers={"Authorization":"Bearer "+key,
                 "Content-Type":"application/json","Accept":"application/json",
                 "HTTP-Referer":"https://github.com/BanSins7/ContentMachine",
                 "X-Title":"ContentMachine v15 Opus Critic Only"})
    record["status"]="dispatched_pending_provider_response"
    record["paid_request_sent"]=True
    save(record)
    # Absolutely one billable POST, never retry or change model.
    try:
        with urlopen(req,timeout=300) as response:
            code=response.status
            wire=response.read(2_000_001)
    except HTTPError as exc:
        record.update(status="http_provider_rejected",http_status=exc.code,
            original_provider_error=safe_provider_error(exc.read(16000),key),
            reported_cost_usd=None,billing_uncertain=True)
        save(record);return
    except (URLError,OSError,TimeoutError,ValueError) as exc:
        record.update(status="transport_uncertain_no_retry",
                      error_class=type(exc).__name__,
                      original_transport_error=repr(exc).replace(key,"[REDACTED]")[:350],
                      reported_cost_usd=None,billing_uncertain=True)
        save(record);return
    record["http_status"]=code
    if len(wire)>2_000_000:
        record.update(status="response_too_large_billing_uncertain",billing_uncertain=True)
        save(record);return
    try:
        obj=json.loads(wire)
    except ValueError:
        record.update(status="invalid_openrouter_json_billing_uncertain",billing_uncertain=True,
                      provider_body_prefix=wire[:2500].decode("utf-8","replace").replace(key,"[REDACTED]"))
        save(record);return
    provider_usage=obj.get("usage") if isinstance(obj,dict) else None
    if not isinstance(provider_usage,dict) or provider_usage.get("cost") is None:
        record.update(status="missing_usage_billing_uncertain",billing_uncertain=True,
                      provider_message=obj.get("error") if isinstance(obj,dict) else None)
        save(record);return
    cost=Decimal(str(provider_usage["cost"]))
    choices=obj.get("choices") or []
    completion=(choices[0].get("message",{}).get("content") or "") if choices else ""
    finish=choices[0].get("finish_reason") if choices else None
    record.update(response_id=obj.get("id"),reported_cost_usd=str(cost),
                  finish_reason=finish,usage=provider_usage,
                  model_returned=obj.get("model"),output_chars=len(completion) if isinstance(completion,str) else 0)
    if cost>reserve:
        record["budget_warning"]="Provider cost unexpectedly exceeded conservative reservation"
    if finish!="stop":
        record.update(status="incomplete_critic_response",
                      raw_output_prefix=completion[:4500] if isinstance(completion,str) else None)
        save(record);return
    if not isinstance(completion,str) or not completion.strip():
        record["status"]="complete_response_no_text";save(record);return
    try:
        parsed=json.loads(completion)
        findings=validate_critic(parsed,topics)
    except Exception as exc:
        record.update(status="critic_json_or_validation_failed",
                      validation_error_type=type(exc).__name__,
                      validation_error=str(exc)[:350],
                      raw_output_prefix=completion[:4500])
        save(record);return
    counts=Counter(f["severity"] for f in findings)
    record.update(status="critic_completed",
                  findings=findings,findings_count=len(findings),
                  findings_by_severity=dict(counts),
                  reviewed_categories=len(topics),
                  topic_names_with_findings=list(dict.fromkeys(f["topic_name"] for f in findings)),
                  note="LLM critic review only; suggestions NOT automatically applied; no SRE publication")
    save(record)
    print("Opus Critic VALID",len(findings),"findings",dict(counts),"cost USD",cost,
          "max tokens",stage["max_output_tokens"])

if __name__=="__main__":
    main()
